"""Code execution tools."""
import os
import shlex
import subprocess

from codegen.infrastructure.tools.registry import register, runtime

# 传入容器的代理变量名：宿主机代理地址（如 127.0.0.1:7897）在容器内
# 指向容器自己，pip/网络全部失败 —— docker 模式下必须剔除
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")

def _sandbox_env() -> dict:
    """容器执行环境：去掉代理变量，容器直连（Docker Desktop NAT 出网）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for k in _PROXY_ENV_VARS:
        env.pop(k, None)
    return env


@register(
    name="run_code",
    description="Run the project's main entry point and get output or errors. "
                "Returns stdout and stderr. Timeout after 30 seconds.",
    parameters={
        "type": "object",
        "properties": {
            "entry": {
                "type": "string",
                "description": "Entry file to run, e.g., 'main.py'",
                "default": "main.py"
            }
        }
    }
)
def run_code(entry: str = "main.py") -> str:
    # 防呆：测试文件必须用 run_tests（pytest）—— python test_x.py 不会
    # 收集测试，直接跑必然空跑/报错，前端显示 ✗ 会误导成"项目坏了"
    base = os.path.basename(entry)
    if (base.startswith("test_") and base.endswith(".py")) \
            or base == "conftest.py":
        return (f"'{entry}' is a TEST file — do NOT run it with run_code. "
                "Use the run_tests tool (pytest) instead.")
    project_dir = runtime().project_dir
    python = runtime().venv_python()
    try:
        from codegen.infrastructure.tools.registry import docker_script
        script = docker_script(project_dir, _docker_run_script(entry, install_deps=True))
        if script:
            cmd = script
        else:
            blocked = _scan_dangerous_code(project_dir)
            if blocked:
                return (f"Execution refused: the project contains dangerous "
                        f"operations blocked outside the sandbox:\n"
                        f"{chr(10).join(blocked)}\n"
                        "Run with tools.sandbox=docker (isolated) instead.")
            cmd = [python, entry]
        result = subprocess.run(
            cmd, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            # stdin 置空：生成的 CLI 若读 stdin，立即 EOF 返回而不是阻塞 30s
            stdin=subprocess.DEVNULL,
            env=_sandbox_env() if script else {**os.environ,
                                                "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or f"exit code {result.returncode}")
        return f"Execution successful\n---\n{result.stdout or '(no output)'}"
    except subprocess.TimeoutExpired:
        return "Execution failed: timed out after 30s"
    except Exception as e:
        return f"Execution failed: {e}"

def _docker_run_script(entry: str, *, install_deps: bool) -> str:
    """容器内执行脚本：先补装依赖（镜像无宿主机 venv 的包），再跑入口。

    requirements.txt 缺失/装失败用 `|| true` 容错 —— 失败只会让入口
    import 报错，agent 能看到真实 Traceback 自己修复。
    """
    lines = []
    if install_deps:
        lines.append("python -m pip install -q --default-timeout 30 "
                     "-r requirements.txt 2>/dev/null || true")
    lines.append(f"python {shlex.quote(entry)}")
    return "; ".join(lines)

def _scan_dangerous_code(directory: str) -> list[str]:
    """宿主机直跑前的静态安全检查：AST 扫描项目 .py 找危险操作。

    返回命中列表（空 = 安全）。docker 沙箱内不调用 —— 容器只读挂载
    + 内联文件系统，删文件/跑命令出不了容器。
    """
    import ast
    from pathlib import Path

    _SKIP = {".venv", "__pycache__", ".git", ".task_outputs", ".devforge"}
    # 高危调用名：命令执行 / 删除文件 / 动态代码 —— 宿主机直跑一律拒绝
    _EXEC_CALLS = {"os.system", "os.popen", "os.execl", "os.execv",
                   "os.spawnl", "os.spawnv", "__import__"}
    _DELETE_CALLS = {"os.remove", "os.unlink", "os.rmdir", "os.removedirs",
                     "os.replace", "shutil.rmtree", "shutil.move",
                     "shutil.copy", "shutil.copy2", "Path.unlink",
                     "Path.rmdir", "Path.replace"}
    _DYNAMIC_CALLS = {"eval", "exec", "compile"}
    # 危险命令字符串（无论通过 os.system / subprocess / 其他途径执行）
    _CMD_MARKERS = ("rm -rf", "rm -fr", "rd /s", "del /s", "format ",
                    "curl | sh", "wget | sh", "> /dev/sda", "mkfs.")
    findings: list[str] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if isinstance(func, ast.Attribute):
                    base = func.value
                    # 链式调用 Path("x").unlink() —— base 是 Call 而非 Name
                    if isinstance(base, ast.Name):
                        full = f"{base.id}.{func.attr}"
                    elif (isinstance(base, ast.Call)
                          and isinstance(base.func, ast.Name)
                          and base.func.id == "Path"):
                        full = f"Path.{func.attr}"
                    else:
                        full = func.attr or ""
                else:
                    full = name or ""
                # 命令执行 / 删除 / 动态代码（builtins.eval 经 name 兜底）
                if full in _EXEC_CALLS or name in _DYNAMIC_CALLS:
                    findings.append(f"{path.relative_to(directory)}: "
                                    f"危险操作 {full or name} (line {node.lineno})")
                    continue
                if full in _DELETE_CALLS:
                    findings.append(f"{path.relative_to(directory)}: "
                                    f"删除/移动文件 {full} (line {node.lineno})")
                    continue
                # subprocess 系仅当 shell=True 或 shell 字符串含危险命令时拦截
                # （纯列表参数是安全的，测试代码常用）
                if full.startswith("subprocess.") and full != "subprocess":
                    shell_kw = next(
                        (kw.value for kw in node.keywords if kw.arg == "shell"), None)
                    shell_pos = (node.args[0] if len(node.args) > 0 else None)
                    shell_true = (isinstance(shell_kw, ast.Constant)
                                  and shell_kw.value is True)
                    if shell_true or (isinstance(shell_pos, ast.Constant)
                                      and isinstance(shell_pos.value, str)
                                      and any(m in shell_pos.value
                                              for m in _CMD_MARKERS)):
                        findings.append(
                            f"{path.relative_to(directory)}: "
                            f"{full} shell=True/危险命令 (line {node.lineno})")
    return findings

@register(
    name="run_tests",
    description="Run the project's pytest suite (test_*.py) and return results. "
                "Timeout after 120 seconds.",
    parameters={
        "type": "object",
        "properties": {
            "entry": {
                "type": "string",
                "description": "Optional single test file to run, e.g., "
                               "'test_utils.py'. Default: all test_*.py files "
                               "in the project root.",
                "default": ""
            }
        }
    }
)
def run_tests(entry: str = "") -> str:
    """Run pytest over the project's test files (``test_*.py``).

    Unlike ``run_code`` (which runs ``python <file>`` — useless for pytest),
    this invokes ``python -m pytest`` so the Tester's verification is real.
    """
    project_dir = runtime().project_dir
    python = runtime().venv_python()
    if entry:
        # 防呆：tester 常传 "test_scanner"（漏 .py）→ pytest 报 file not
        # found；自动补全再校验存在
        if not entry.endswith(".py"):
            entry = entry + ".py"
        if not os.path.exists(os.path.join(project_dir, entry)):
            return (f"Error: '{entry}' not found. Use list_files to see the "
                    "actual test file names (e.g. 'test_scanner.py').")
        test_files = [entry]
    else:
        test_files = sorted(
            f for f in os.listdir(project_dir)
            if f.startswith("test_") and f.endswith(".py"))
    if not test_files:
        return "No test_*.py files found in the project."
    from codegen.infrastructure.tools.registry import docker_script
    script = docker_script(project_dir, _docker_test_script(test_files))
    if script:
        cmd = script
    else:
        # venv 里没有 pytest 时先装（幂等缓存），装不上再失败而不是报
        # "No module named pytest"（日志里 tester/fixer 的 run_tests 全挂的根因）
        from codegen.infrastructure.tools.registry import ensure_pytest
        if not ensure_pytest(python):
            raise RuntimeError("pytest is not installed in the project venv "
                               "and automatic install failed.")
        blocked = _scan_dangerous_code(project_dir)
        if blocked:
            return (f"Execution refused: the project contains dangerous "
                    f"operations blocked outside the sandbox:\n"
                    f"{chr(10).join(blocked)}\n"
                    "Run with tools.sandbox=docker (isolated) instead.")
        cmd = [python, "-m", "pytest", "-q", "--no-header", *test_files]
    try:
        result = subprocess.run(
            cmd, cwd=project_dir,
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
            env=_sandbox_env() if script else {**os.environ,
                                                "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return "Execution failed: timed out after 120s"
    except Exception as e:
        return f"Execution failed: {e}"
    if result.returncode != 0:
        stderr = result.stderr or ""
        if "No module named pytest" in stderr:
            # 防诊断螺旋：pytest 缺失是环境问题（沙箱内 pip 没装上），
            # 不是项目代码问题 —— 模型曾为此狂写 20 个 _xxx.py 排查脚本
            raise RuntimeError(
                "pytest is missing in the sandbox (pip install failed) — "
                "this is an ENVIRONMENT issue, not a project code issue. "
                "Do NOT write diagnostic scripts or reinstall pytest. "
                "Report the failure and continue with other work.\n"
                + stderr[-300:])
        raise RuntimeError(stderr or f"exit code {result.returncode}")
    return f"Execution successful\n---\n{result.stdout or '(no output)'}"

def _docker_test_script(test_files: list[str]) -> str:
    """容器内跑 pytest 脚本 —— 统一走 registry.docker_pytest_script，
    保证 run_tests 工具与 run_project_tests 两条路径环境一致。"""
    from codegen.infrastructure.tools.registry import docker_pytest_script
    return docker_pytest_script(test_files)
