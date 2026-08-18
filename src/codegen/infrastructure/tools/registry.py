"""Tool registry — define tools globally, run them contextually."""
import contextvars
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

@dataclass
class ToolContext:
    """Mutable runtime context shared with tool functions."""
    project_dir: str = ""
    venv_dir: str = ""
    blackboard: object = None

class _Registry:
    """Global tool definitions — set up at import time, never change."""

    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, parameters: dict):
        def decorator(func: Callable):
            self.tools[name] = Tool(
                name=name, description=description,
                parameters=parameters, function=func,
            )
            return func
        return decorator

    def describe(self, allow: list[str] | None = None) -> list[dict]:
        """Return tool descriptions for the LLM prompt."""
        if allow is not None and not allow:
            return []
        tools = self.tools.values()
        if allow is not None:
            tools = [t for t in tools if t.name in allow]
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description,
                "parameters": t.parameters,
            }}
            for t in tools
        ]

_registry = _Registry()

# ── public decorator (module-level) ──────────────────

def register(name: str, description: str, parameters: dict):
    """Register a tool function (class-level, immutable)."""
    return _registry.register(name, description, parameters)

def describe(allow: list[str] | None = None) -> list[dict]:
    """Describe tools for the LLM prompt."""
    return _registry.describe(allow)

# ── runtime context + execution ──────────────────────

# 工具结果回填 LLM 的最大字符数（read_file 大文件 / run_tests 长报错都走这里截断）。
# 3000：工具结果进消息历史累积成 tester 30 万 tokens 的大头——截断减半，
# 上下文更小，tester 反馈循环的每次调用都更便宜（截断尾部多为模型可忽略内容）
try:
    from core.config import load_pipeline_config as _load_cfg
    MAX_TOOL_RESULT_CHARS = int(
        _load_cfg().get("tools", {}).get("max_tool_result_chars", 3000))
except Exception:
    MAX_TOOL_RESULT_CHARS = 3000

# docker 可用性探测结果（None = 未探测）—— 探测结果会缓存，避免每次调用都起子进程
_docker_ok: bool | None = None
_docker_exe: str = "docker"     # 探测成功后记住可执行文件路径
# daemon 探测失败（不是命令缺失）后的重试时间戳 —— Docker Desktop 启动中/
# 重启时探测会失败，若永久缓存 False 整个会话都回退宿主机，必须可重试
_docker_retry_at: float = 0.0
_DOCKER_RETRY_SECONDS = 30.0

# docker 不在 PATH 时的常见安装位置（服务启动环境可能没有 docker 命令）
_DOCKER_FALLBACK_PATHS = (
    r"D:\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
)

def _find_docker() -> str | None:
    """定位 docker：PATH 优先，其次常见安装路径（环境免疫）。"""
    import shutil
    exe = shutil.which("docker")
    if exe:
        return exe
    for cand in _DOCKER_FALLBACK_PATHS:
        if os.path.exists(cand):
            return cand
    return None

def docker_available() -> bool:
    """True = 沙箱配置为 docker 且 docker 可用（探测结果缓存）。

    chat_chain 据此决定是否预创建项目 venv —— docker 模式下测试/执行
    都走容器，宿主机 venv 无用（省 35MB 磁盘 + 建 venv 时间）；
    宿主机回退模式由 venv_python() 惰性创建兜底。
    """
    try:
        from core.config import load_pipeline_config
        mode = load_pipeline_config().get("tools", {}).get("sandbox", "")
    except Exception:
        return False
    if mode != "docker":
        return False
    return bool(sandbox_prefix("."))

def sandbox_prefix(project_dir: str) -> list[str]:
    """沙箱执行前缀：config ``tools.sandbox`` = "docker" 时生成代码跑在容器里
    （镜像 python:3.12-slim，项目目录挂载为 /work）；默认空 = 宿主机直跑。

    docker 不可用（未安装/未启动）时回退宿主机执行并告警，而不是
    让 run_code 每次都报 docker 命令错误。docker 可执行文件用
    _find_docker() 定位 —— 不依赖服务进程的 PATH 环境。
    """
    try:
        from core.config import load_pipeline_config
        mode = load_pipeline_config().get("tools", {}).get("sandbox", "")
    except Exception:
        return []
    if mode != "docker" or not project_dir:
        return []
    global _docker_ok, _docker_exe, _docker_retry_at
    # daemon 探测失败后等 _DOCKER_RETRY_SECONDS 再重试（Docker Desktop
    # 启动中/重启的场景），而不是永久回退宿主机
    import time as _time
    if _docker_ok is False and _docker_retry_at \
            and _time.time() > _docker_retry_at:
        _docker_ok = None
    if _docker_ok is None:
        import subprocess
        exe = _find_docker()
        if exe is None:
            _docker_ok = False          # 命令不存在 → 永久缓存（真没有）
        else:
            try:
                probe = subprocess.run([exe, "version"],
                                       capture_output=True, timeout=10)
                if probe.returncode == 0:
                    _docker_ok = True
                    _docker_exe = exe
                else:
                    # daemon 未就绪：短暂失败，30s 后重试
                    _docker_ok = False
                    _docker_retry_at = _time.time() + _DOCKER_RETRY_SECONDS
            except (OSError, subprocess.TimeoutExpired):
                _docker_ok = False
                _docker_retry_at = _time.time() + _DOCKER_RETRY_SECONDS
        if not _docker_ok:
            _log.warning("tools.sandbox=docker 但 docker 不可用 — "
                         "回退宿主机执行（30s 后自动重试）")
    if not _docker_ok:
        return []
    # 项目目录读写挂载：生成的项目本身可能就要移动/删除文件
    # （文件整理器测试会真的 move 文件），只读会让这类项目全挂。
    # 安全边界仍在容器：能碰到的只有挂载的项目目录本身（生成产物，
    # 可重建），宿主机其他位置容器内不可见。
    return [_docker_exe, "run", "--rm",
            "-v", f"{project_dir}:/work",
            "-w", "/work",
            "python:3.12-slim", "python"]

def docker_script(project_dir: str, script: str) -> list[str] | None:
    """沙箱内执行 *script* 的完整 docker 命令（sh -c）。

    容器启动后先静默补装依赖（requirements.txt 或 pytest）——容器是
    干净镜像，宿主机 venv 装好的包在容器里不存在。docker 不可用返回
    None（调用方回退宿主机直跑）。

    两点环境免疫（不依赖服务怎么启动）：
    - 容器内 unset 代理变量 —— 宿主机进程带 HTTP_PROXY=127.0.0.1:x
      时，容器内的 127.0.0.1 是容器自己，pip 必挂；unset 后容器直连
    - 挂 pip 缓存卷 —— pytest 等依赖只下载一次，后续 run 秒级复用，
      否则每次 run_tests 都重新下载（"测试很慢"的根因）
    """
    if not sandbox_prefix(project_dir):
        return None
    clean_script = ("unset HTTP_PROXY HTTPS_PROXY ALL_PROXY "
                    "http_proxy https_proxy all_proxy; " + script)
    return [_docker_exe, "run", "--rm",
            "-v", f"{project_dir}:/work",
            "-v", "devforge_pip_cache:/root/.cache/pip",
            "-w", "/work",
            "python:3.12-slim",
            "sh", "-c", clean_script]

def docker_pytest_script(test_files: list[str]) -> str:
    """容器内跑 pytest 的脚本：镜像无 pytest，先静默补装（失败不阻塞，
    测试给出真实 import 错误让 agent 判断）。

    `-p no:cacheprovider`：/work 挂载下 .pytest_cache 写入告警，关掉。
    `--cov=./--cov-report` 与宿主机路径（cov_args）一致 —— 否则
    quality_gate_min_coverage 的覆盖率证据在 docker 模式下永远拿不到
    （_parse_coverage 无输出可解析，降级门禁静默失效）。
    unset 代理由 docker_script 统一处理（环境免疫）。
    """
    import shlex
    files = " ".join(shlex.quote(f) for f in test_files)
    return ("python -m pip install -q --default-timeout 30 "
            "pytest pytest-cov 2>/dev/null || true; "
            "python -m pytest -q --no-header -p no:cacheprovider "
            "--cov=. --cov-report=term-missing:skip-covered " + files)

def cov_args(python: str, project_dir: str = "") -> list[str]:
    """pytest 覆盖率参数：仅当 *python* 是项目 venv（ensure_pytest 保证装有
    pytest-cov）时启用 —— 系统解释器不保证有 cov，加上会让 pytest 报错。

    注意：不传 --cov-data-file —— 该参数在不同 pytest-cov 版本/rootdir
    配置下两次报 "unrecognized arguments"（项目根 pyproject.toml 会改变
    pytest 的 rootdir 解析）。覆盖率统计只需 term-missing 输出，.coverage
    数据文件落项目根即可（.gitignore 已忽略）。
    """
    try:
        rt = runtime()
    except Exception:
        return []
    venv_dir = rt.ctx.venv_dir
    if venv_dir and python.startswith(venv_dir):
        return ["--cov=.", "--cov-report=term-missing:skip-covered"]
    return []
# 结果缓存：只读/幂等工具 + 测试/执行（项目文件未变则结果必然相同）。
# write_file 写后清空整个缓存 —— 修复后复测拿到新结果，不会误读旧缓存。
# run_tests/run_code 缓存直接消灭"同一 entry 反复跑"的重复执行
# （docker 每次 20-40s + 结果重复进历史 ≈ tester 23 次调用的冗余大头）
CACHEABLE_TOOLS = frozenset(
    {"read_file", "read_many", "run_tests", "run_code", "grep_file"})

def _args_key(arguments: dict) -> str:
    """Deterministic key for tool arguments (dict → canonical string)."""
    import json as _json
    return _json.dumps(arguments, sort_keys=True, default=str)

@dataclass
class Tool:
    """An agent-callable tool."""
    name: str
    description: str
    parameters: dict
    function: Callable

@dataclass
class ToolRuntime:
    """Per-pipeline tool executor — owns mutable context."""
    ctx: ToolContext = field(default_factory=ToolContext)
    # (tool_name, args_key) → (cached_at, result) — per-run 缓存，写后失效
    _cache: dict[tuple[str, str], tuple[float, str]] = field(default_factory=dict)
    # agent → 已读文件集（规范化绝对路径）。跨工具去重：read_many 批量读
    # 后再 read_file 单文件是模型高频重复（缓存 key 不同拦不住），
    # 这里记住"本轮读过什么"，重复读返回提示而不是重发内容。
    # 必须 per-agent：不同 agent 的对话历史互不可见，A 读过的 B 读是正常的。
    _read_files: dict[str, set[str]] = field(default_factory=dict)
    # agent tag → 该角色的工具白名单（硬约束）。Agent._react_inner 登记；
    # execute 校验当前 agent 的调用，未授权工具直接拒绝 —— 白名单不再是
    # "只给 LLM 看 schema"的软限制。未登记的 agent（单元测试直调）不限制。
    _agent_tools: dict[str, frozenset[str]] = field(default_factory=dict)
    # 正在执行工具的 agent 名（todo_write 事件归属用）。
    # ContextVar：并行 coder 共享同一 runtime，普通字段会被别的线程
    # 在"赋值→执行"之间改写，todo 事件归属错乱→ 线程隔离
    _agent_var: contextvars.ContextVar[str] = field(
        default_factory=lambda: contextvars.ContextVar(
            "df_current_agent", default=""),
        repr=False)

    @property
    def current_agent(self) -> str:
        return self._agent_var.get()

    @current_agent.setter
    def current_agent(self, value: str):
        self._agent_var.set(value)

    # ── 已读文件跟踪（跨工具去重）──────────────────────

    def mark_read(self, path: str):
        """记录当前 agent 已读 *path*（规范化绝对路径）。"""
        agent = self.current_agent
        if agent:
            self._read_files.setdefault(agent, set()).add(path)

    def is_read(self, path: str) -> bool:
        """当前 agent 是否已在本轮对话中读过 *path*。"""
        agent = self.current_agent
        return bool(agent) and path in self._read_files.get(agent, ())

    def invalidate_file(self, path: str):
        """文件被写（内容变化）→ 所有 agent 对该文件的已读记录失效，
        允许重新读取新内容。"""
        for s in self._read_files.values():
            s.discard(path)

    # ── 工具白名单（硬约束）────────────────────────────

    def register_agent(self, name: str, tools: list[str]) -> None:
        """登记 agent 允许的工具集（Agent._react_inner 调用）。
        并行 coder 各以模块名为名，登记同一 "coder" 角色工具集。"""
        if name:
            self._agent_tools[name] = frozenset(tools)

    def execute(self, name: str, arguments: dict) -> str:
        """Execute a tool by name, return its result as a string."""
        tool = _registry.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        # 硬约束：白名单不再是"只给 LLM 看 schema"的软限制 —— 当前 agent
        # 调用未授权工具直接拒绝（并行 coder 不能越权调 run_code 等）
        agent = self.current_agent
        allowed = self._agent_tools.get(agent)
        if allowed is not None and name not in allowed:
            return (f"Error: tool '{name}' is not allowed for agent "
                    f"'{agent or '?'}'.")
        # 写文件会改变 read_file 的结果 — 写后清缓存，避免读到旧内容
        if name in ("write_file", "edit_file"):
            self._cache.clear()
        key = (name, _args_key(arguments))
        if name in CACHEABLE_TOOLS:
            hit = self._cache.get(key)
            if hit is not None:
                return hit[1]
        try:
            result = str(tool.function(**arguments))
        except Exception as e:
            # WARNING 而非 ERROR：工具失败（如 run_tests 红测）是正常业务流程，
            # ERROR 级会让运维日志淹没在每次测试失败里
            _log.warning("Tool '%s' failed: %s", name, e)
            return f"ToolError: {e}"
        # 统一截断：大文件/长输出原样回填 LLM 会爆上下文、烧 token。
        # 保留头尾（头部 40% + 尾部 60%）：pytest 失败断言/错误信息在
        # 输出末尾，此前只留头部会把真正错误砍掉（fixer 拿不到关键行）。
        # 截断点在这里 → 事件预览（[:500]）与 LLM 回填一致。
        if len(result) > MAX_TOOL_RESULT_CHARS:
            head_chars = int(MAX_TOOL_RESULT_CHARS * 0.4)
            tail_chars = MAX_TOOL_RESULT_CHARS - head_chars
            marker = (f"\n…(输出过长，已截断 {len(result)} → "
                      f"{MAX_TOOL_RESULT_CHARS} 字符，保留开头 {head_chars}"
                      f" + 结尾 {tail_chars})\n")
            result = result[:head_chars] + marker + result[-tail_chars:]
        if name in CACHEABLE_TOOLS:
            import time as _time
            self._cache[key] = (_time.time(), result)
        return result

    @property
    def project_dir(self) -> str:
        return self.ctx.project_dir or os.getcwd()

    @property
    def blackboard(self):
        return self.ctx.blackboard

    def venv_python(self) -> str:
        import sys
        if self.ctx.venv_dir:
            # 惰性兜底：后台线程未建完（或失败）时同步补齐，保证路径存在
            ensure_venv(self.ctx.venv_dir)
            if os.name == "nt":
                return os.path.join(self.ctx.venv_dir, "Scripts", "python.exe")
            return os.path.join(self.ctx.venv_dir, "bin", "python3")
        return sys.executable

# 全局锁：venv 创建幂等且线程安全（后台预创建线程 + venv_python 兜底共用）
_venv_lock = threading.Lock()

def ensure_venv(venv_dir: str) -> None:
    """Create *venv_dir* if its python binary is missing.  Idempotent + locked.

    后台线程预创建与 venv_python() 惰性兜底共用同一把锁，避免两个
    ``python -m venv`` 同时写同一目录。
    """
    if not venv_dir:
        return
    python = os.path.join(venv_dir, "Scripts", "python.exe") if os.name == "nt" \
        else os.path.join(venv_dir, "bin", "python3")
    if os.path.exists(python):
        return
    with _venv_lock:
        if os.path.exists(python):
            return
        import subprocess
        import sys
        _log.info("Creating venv at %s", venv_dir)
        subprocess.run(
            [sys.executable, "-m", "venv", venv_dir],
            capture_output=True, timeout=120, check=True)
        # 顺手装上 pytest：tester/verification 的 run_tests 直接用，
        # 不用等首次跑测试时再补装（后台预创建线程里做，无感知开销）。
        ensure_pytest(python)

# 已验证装有 pytest 的解释器路径缓存（避免每次 run_tests 重复探测）
_pytest_verified: set[str] = set()
_pytest_lock = threading.Lock()

def _install_pytest(python: str) -> bool:
    """Install pytest+pytest-cov into *python*'s env.

    默认源失败后回退清华镜像重试 —— 国内网络直连 pypi.org 常超时，
    pip 安装失败会让整个测试链路瘫痪（tester 循环/审查误判 bug）。
    """
    import subprocess
    base = [python, "-m", "pip", "install", "--no-input",
            "--default-timeout", "30", "pytest", "pytest-cov"]
    for extra in ([], ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]):
        try:
            subprocess.run([*base, *extra], capture_output=True,
                           timeout=180, check=True)
            return True
        except (subprocess.TimeoutExpired, OSError,
                subprocess.CalledProcessError):
            _log.info("pip install pytest attempt failed (%s)",
                      "default index" if not extra else "tsinghua mirror")
    return False

def ensure_pytest(python: str) -> bool:
    """Ensure *python* can run pytest (+ pytest-cov); returns True if usable.

    幂等 + 锁 + 缓存：探测 ``pytest`` 与 ``pytest_cov`` 能否 import
    （H2：旧 venv 可能只有 pytest 没有 pytest-cov —— 只探测 pytest 会缓存
    命中跳过补装，随后 --cov-data-file 参数不被识别直接报错），缺失时
    ``pip install pytest pytest-cov`` 并重试一次。安装失败返回 False，
    调用方（run_tests / Verification）回退到入口运行。
    """
    if python in _pytest_verified:
        return True
    import subprocess
    with _pytest_lock:
        if python in _pytest_verified:
            return True
        try:
            probe = subprocess.run(
                [python, "-c", "import pytest, pytest_cov"],
                capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if probe.returncode == 0:
            _pytest_verified.add(python)
            return True
        _log.info("pytest/pytest-cov missing at %s — installing", python)
        if not _install_pytest(python):
            _log.warning("pip install pytest failed for %s", python)
            return False
        _pytest_verified.add(python)
        return True

# Per-run active runtime — contextvar so concurrent runs stay isolated.
# Each pipeline run sets its own ToolRuntime in its own thread.
# NOTE: contextvars do NOT propagate into ThreadPoolExecutor workers —
# patterns.parallel() captures the submitting thread's runtime and re-sets
# it inside each worker thread via set_runtime().
_runtime_var: contextvars.ContextVar[ToolRuntime | None] = (
    contextvars.ContextVar("tool_runtime", default=None))
_log = logging.getLogger(__name__)

def runtime() -> ToolRuntime:
    """Return the active tool runtime for the current run."""
    rt = _runtime_var.get()
    if rt is None:
        raise RuntimeError("ToolRuntime not initialised — call init() first")
    return rt

def set_runtime(rt: ToolRuntime):
    """Bind a ToolRuntime to the current thread (worker threads inherit
    it from the submitting thread via parallel())."""
    _runtime_var.set(rt)

def init(project_dir: str = "", venv_dir: str = "", blackboard=None) -> ToolRuntime:
    """Create and activate a ToolRuntime for the current pipeline run."""
    ctx = ToolContext(project_dir=project_dir, venv_dir=venv_dir,
                      blackboard=blackboard)
    rt = ToolRuntime(ctx=ctx)
    _runtime_var.set(rt)
    return rt
