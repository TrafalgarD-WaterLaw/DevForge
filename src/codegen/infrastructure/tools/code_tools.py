"""Code execution tools."""
import os
import subprocess

from codegen.infrastructure.tools.registry import register, runtime

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
    project_dir = runtime().project_dir
    python = runtime().venv_python()
    try:
        from codegen.infrastructure.tools.registry import sandbox_prefix
        prefix = sandbox_prefix(project_dir)
        # 沙箱（docker）模式下容器内 python 由前缀提供（venv 不挂载进容器）
        cmd = prefix + ([python, entry] if not prefix else [entry])
        result = subprocess.run(
            cmd, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            # stdin 置空：生成的 CLI 若读 stdin，立即 EOF 返回而不是阻塞 30s
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or f"exit code {result.returncode}")
        return f"Execution successful\n---\n{result.stdout or '(no output)'}"
    except subprocess.TimeoutExpired:
        return "Execution failed: timed out after 30s"
    except Exception as e:
        return f"Execution failed: {e}"

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
        test_files = [entry]
    else:
        test_files = sorted(
            f for f in os.listdir(project_dir)
            if f.startswith("test_") and f.endswith(".py"))
    if not test_files:
        return "No test_*.py files found in the project."
    # venv 里没有 pytest 时先装（幂等缓存），装不上再失败而不是报
    # "No module named pytest"（日志里 tester/fixer 的 run_tests 全挂的根因）
    from codegen.infrastructure.tools.registry import cov_args, ensure_pytest, sandbox_prefix
    if not ensure_pytest(python):
        raise RuntimeError("pytest is not installed in the project venv "
                           "and automatic install failed.")
    try:
        prefix = sandbox_prefix(project_dir)
        cmd = prefix + ([python, "-m", "pytest", "-q", "--no-header",
                         *cov_args(python, project_dir), *test_files] if not prefix
                        else ["-m", "pytest", "-q", "--no-header",
                              *test_files])
        result = subprocess.run(
            cmd, cwd=project_dir,
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return "Execution failed: timed out after 120s"
    except Exception as e:
        return f"Execution failed: {e}"
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"exit code {result.returncode}")
    return f"Execution successful\n---\n{result.stdout or '(no output)'}"
