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

# 工具结果回填 LLM 的最大字符数（read_file 大文件 / run_tests 长报错都走这里截断）
try:
    from core.config import load_pipeline_config as _load_cfg
    MAX_TOOL_RESULT_CHARS = int(
        _load_cfg().get("tools", {}).get("max_tool_result_chars", 6000))
except Exception:
    MAX_TOOL_RESULT_CHARS = 6000

# docker 可用性探测结果（None = 未探测）—— 只探测一次，避免每次调用都起子进程
_docker_ok: bool | None = None

def sandbox_prefix(project_dir: str) -> list[str]:
    """沙箱执行前缀：config ``tools.sandbox`` = "docker" 时生成代码跑在容器里
    （镜像 python:3.12-slim，项目目录挂载为 /work）；默认空 = 宿主机直跑。

    docker 不可用（未安装/未启动）时回退宿主机执行并告警，而不是
    让 run_code 每次都报 docker 命令错误。
    """
    try:
        from core.config import load_pipeline_config
        mode = load_pipeline_config().get("tools", {}).get("sandbox", "")
    except Exception:
        return []
    if mode != "docker" or not project_dir:
        return []
    global _docker_ok
    if _docker_ok is None:
        import subprocess
        try:
            probe = subprocess.run(["docker", "version"],
                                   capture_output=True, timeout=10)
            _docker_ok = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _docker_ok = False
        if not _docker_ok:
            _log.warning("tools.sandbox=docker 但 docker 不可用 — "
                         "回退宿主机执行")
    if not _docker_ok:
        return []
    return ["docker", "run", "--rm",
            "-v", f"{project_dir}:/work",
            "-w", "/work",
            "python:3.12-slim", "python"]

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
# 结果缓存：只缓存只读/幂等工具；write_file 写后清空整个缓存（文件可能已变）
CACHEABLE_TOOLS = frozenset({"read_file", "search_web"})
SEARCH_CACHE_TTL = 600        # search_web 结果 10 分钟有效（网络信息时效性）

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

    def execute(self, name: str, arguments: dict) -> str:
        """Execute a tool by name, return its result as a string."""
        tool = _registry.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        # 写文件会改变 read_file 的结果 — 写后清缓存，避免读到旧内容
        if name == "write_file":
            self._cache.clear()
        key = (name, _args_key(arguments))
        if name in CACHEABLE_TOOLS:
            import time as _time
            hit = self._cache.get(key)
            if hit is not None:
                cached_at, cached = hit
                if name != "search_web" or _time.time() - cached_at < SEARCH_CACHE_TTL:
                    return cached
        try:
            result = str(tool.function(**arguments))
        except Exception as e:
            # WARNING 而非 ERROR：工具失败（如 run_tests 红测）是正常业务流程，
            # ERROR 级会让运维日志淹没在每次测试失败里
            _log.warning("Tool '%s' failed: %s", name, e)
            return f"ToolError: {e}"
        # 统一截断：大文件/长输出原样回填 LLM 会爆上下文、烧 token。
        # 截断点在这里 → 事件预览（[:500]）与 LLM 回填一致。
        if len(result) > MAX_TOOL_RESULT_CHARS:
            result = (result[:MAX_TOOL_RESULT_CHARS]
                      + f"\n…(输出过长，已截断 {len(result)} → "
                      f"{MAX_TOOL_RESULT_CHARS} 字符)")
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
        try:
            # 一起装 pytest-cov：venv 下 run_tests 输出覆盖率（cov_args 据此启用）
            subprocess.run(
                [python, "-m", "pip", "install", "pytest", "pytest-cov"],
                capture_output=True, timeout=120, check=True)
        except (subprocess.TimeoutExpired, OSError, subprocess.CalledProcessError):
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
