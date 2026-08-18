"""进程执行公共函数 —— Coding / Verification / QualityGate 三处共用。

此前 _run_process / _trim_paths 在每个 phase 各写一份（超时杀进程、
路径清理逻辑四处漂移）；统一到这里，改超时/杀进程行为只动一处。
"""
import os
import signal
import subprocess


def run_process(cmd, cwd, timeout, env=None):
    """Run *cmd*, kill on timeout, return (stdout, stderr, returncode).

    Windows 下用 CTRL_BREAK（终止子进程树），POSIX 下 killpg。
    *env* — 可选的环境覆盖（宿主机回退模式注入沙箱 shim）。
    """
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ) if os.name == "nt" else 0,
        )
        try:
            return (*process.communicate(timeout=timeout), process.returncode)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            try:
                return (*process.communicate(timeout=5), process.returncode)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                return (*process.communicate(), process.returncode)
    except OSError as ex:
        return (b"", str(ex).encode(), 1)


def _kill_process(process):
    """Best-effort termination — never raises."""
    try:
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                process.kill()
        elif hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            os.kill(process.pid, signal.SIGTERM)
    except OSError:
        pass


def trim_paths(output: str, directory: str) -> str:
    """从测试输出里剥离项目目录前缀（正/反斜杠两种形式都处理）。

    Windows 下 pytest 输出用反斜杠路径，之前只替换正斜杠版本，
    目录前缀残留导致 fixer/reviewer 看到冗余路径。
    """
    norm = os.path.normpath(directory)
    return (output
            .replace(norm.replace("\\", "/") + "/", "")
            .replace(norm.replace("/", "\\") + "\\", ""))
