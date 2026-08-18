"""宿主机回退模式的运行期沙箱 shim（T9-A）。

静态 AST 扫描（_scan_dangerous_code）分不清"删系统文件"和"移动项目目录内
文件"。真正的边界由这里提供：通过 sitecustomize 注入子进程，patch 破坏性
文件操作 —— 目标路径逃逸 {项目根, 系统临时目录} 即抛 PermissionError。

信任边界说明：这只是宿主回退模式的防呆护栏，不是安全边界（生成代码可
绕过 sitecustomize）；需要真正隔离请用 tools.sandbox=docker。
"""
import os
import tempfile

_SITECUSTOMIZE_SRC = '''"""DevForge host-mode sandbox shim (injected via sitecustomize).

Patches destructive file operations so paths escaping the sandbox root
(project dir) or the system temp dir are refused. Docker is the real
isolation boundary; this shim is only the host-fallback guard.
"""
import os as _os
import shutil as _shutil
import tempfile as _tempfile

_ROOT = _os.environ.get("DEVFORGE_SANDBOX_ROOT", "")
if not _ROOT:
    raise ImportError("DevForge sandbox shim requires DEVFORGE_SANDBOX_ROOT")
_ROOT = _os.path.realpath(_ROOT)
_SAFE_BASES = (_ROOT, _os.path.realpath(_tempfile.gettempdir()))


def _guard(path):
    rp = _os.path.realpath(path)
    for base in _SAFE_BASES:
        if rp == base or rp.startswith(base + _os.sep):
            return
    raise PermissionError(
        "DevForge sandbox: file operation escapes project/temp: %r" % (path,))


def _wrap(func, arg_idx, kw_names):
    def wrapper(*args, **kwargs):
        for i in arg_idx:
            if i < len(args) and isinstance(args[i], (str, bytes, _os.PathLike)):
                _guard(args[i])
        for k in kw_names:
            if k in kwargs and isinstance(kwargs[k], (str, bytes, _os.PathLike)):
                _guard(kwargs[k])
        return func(*args, **kwargs)
    wrapper.__name__ = getattr(func, "__name__", "wrapped")
    return wrapper


for _name, _idx, _kws in (
    ("remove", (0,), ("path",)),
    ("unlink", (0,), ("path",)),
    ("rmdir", (0,), ("path",)),
    ("removedirs", (0,), ("name",)),
    ("replace", (0, 1), ("src", "dst")),
    ("rename", (0, 1), ("src", "dst")),
):
    _orig = getattr(_os, _name)
    setattr(_os, _name, _wrap(_orig, _idx, _kws))

for _name, _idx, _kws in (
    ("rmtree", (0,), ("path",)),
    ("move", (0, 1), ("src", "dst")),
    ("copy", (0, 1), ("src", "dst")),
    ("copy2", (0, 1), ("src", "dst")),
    ("copyfile", (0, 1), ("src", "dst")),
):
    _orig = getattr(_shutil, _name)
    setattr(_shutil, _name, _wrap(_orig, _idx, _kws))
'''

_shim_dir: str | None = None


def sandbox_shim_dir() -> str:
    """返回含 sitecustomize.py 的目录（进程内缓存，只写一次）。"""
    global _shim_dir
    if _shim_dir is None:
        _shim_dir = tempfile.mkdtemp(prefix="devforge-shim-")
        with open(os.path.join(_shim_dir, "sitecustomize.py"),
                  "w", encoding="utf-8") as f:
            f.write(_SITECUSTOMIZE_SRC)
    return _shim_dir


def sandbox_env(project_dir: str) -> dict:
    """宿主机执行的子进程环境：注入沙箱根 + 前置 PYTHONPATH（sitecustomize）。

    *project_dir* 为沙箱根（项目目录）。系统临时目录自动豁免（pytest 的
    tmp_path 清理等）。"""
    env = {
        **os.environ,
        "DEVFORGE_SANDBOX_ROOT": os.path.realpath(project_dir),
    }
    shim = sandbox_shim_dir()
    py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = shim + (os.pathsep + py if py else "")
    return env
