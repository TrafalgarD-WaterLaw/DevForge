"""project 路由 —— 历史项目/断点/运行产物。"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter

from core.config import _project_root

_log = logging.getLogger(__name__)
router = APIRouter()
WAREHOUSE = _project_root() / "WareHouse"

# ═══════════════════════════════════════════
# Checkpoints
# ═══════════════════════════════════════════

@router.get("/api/checkpoint/latest")
async def latest_checkpoint():
    """Return the most recent checkpoint for resume prompting."""
    if not WAREHOUSE.exists():
        return {"checkpoint": None}
    from codegen.application.chat_chain import artifact_path
    dirs = sorted(
        [d for d in WAREHOUSE.iterdir()
         if d.is_dir() and _BENCH_MARKER not in d.name],
        key=lambda d: d.stat().st_mtime, reverse=True,
    )
    for d in dirs[:10]:
        ckpt = Path(artifact_path(str(d), "checkpoint.json"))
        if ckpt.exists():
            try:
                data = json.loads(ckpt.read_text(encoding="utf-8"))
                task_file = Path(artifact_path(str(d), "task.txt"))
                task = task_file.read_text(encoding="utf-8").strip() if task_file.exists() else ""
                return {
                    "checkpoint": {
                        "phase": data.get("phase", ""),
                        "directory": str(d),
                        "task": task,
                        "time": d.stat().st_mtime,
                    }
                }
            except (json.JSONDecodeError, OSError):
                _log.warning("Failed to read checkpoint in %s", d)
    return {"checkpoint": None}

# ═══════════════════════════════════════════
# Projects
# ═══════════════════════════════════════════

_SKIP = {'.venv', '__pycache__', '.git', '.task_outputs', '.devforge'}
# Pipeline-run artifacts — internal state, not part of the deliverable.
_ARTIFACT_NAMES = {"run_events.json", "task.txt"}
# 基准评测产物目录名标记（bench-*），不出现在项目列表/历史页
_BENCH_MARKER = "_bench-"

def _is_skipped(path: Path, base: Path) -> bool:
    """True when *path* sits under one of the skip dirs (venv etc.)."""
    return bool(set(path.relative_to(base).parts) & _SKIP)

def _is_artifact(path: Path) -> bool:
    """True for pipeline artifacts : run_events.json, checkpoint*.json,
    task.txt — excluded from project file listings."""
    name = path.name
    return name in _ARTIFACT_NAMES or (
        name.startswith("checkpoint") and name.endswith(".json"))

def _project_status(d: Path) -> str:
    """从 run_events.json 推导项目状态：done / defect / interrupted。"""
    from codegen.application.chat_chain import artifact_path
    ev_path = Path(artifact_path(str(d), "run_events.json"))
    if not ev_path.exists():
        return "interrupted"
    try:
        data = json.loads(ev_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "interrupted"
    events = data.get("events", data) if isinstance(data, dict) else data
    if not isinstance(events, list):
        return "interrupted"
    last = next((e for e in reversed(events)
                 if isinstance(e, dict) and e.get("event") == "pipeline_complete"), None)
    if last is None:
        return "interrupted"
    return "defect" if last.get("failed") else "done"

@router.get("/api/projects")
async def list_projects():
    from codegen.application.chat_chain import artifact_path
    projects = []
    if WAREHOUSE.exists():
        dirs = sorted(
            [d for d in WAREHOUSE.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for d in dirs[:50]:
            # 与 get_project 相同的过滤 — 不把 .venv/__pycache__ 下的
            # 依赖文件列进项目文件清单；B11: 运行工件（run_events.json 等）也不列。
            # os.walk 边遍历边剪枝：rglob 会走进每个项目的 .venv（数千文件），
            # 项目一多接口明显变慢
            files = []
            for root, dirnames, filenames in os.walk(d):
                dirnames[:] = [x for x in dirnames if x not in _SKIP]
                for name in filenames:
                    if name.endswith(".py"):
                        f = Path(root) / name
                        if not _is_artifact(f):
                            files.append(str(f.relative_to(d)))
            task = ""
            tf = Path(artifact_path(str(d), "task.txt"))
            if tf.exists():
                task = tf.read_text(encoding="utf-8", errors="replace").strip()[:120]
            commit = ""
            try:
                r = subprocess.run(
                    ["git", "-C", str(d), "rev-parse", "--short", "HEAD"],
                    capture_output=True, timeout=10)
                if r.returncode == 0:
                    commit = r.stdout.decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            projects.append({
                "id": d.name, "name": d.name, "files": files,
                "task": task, "status": _project_status(d),
                "commit": commit,
                "created": d.stat().st_ctime, "updated": d.stat().st_mtime,
            })
    return {"projects": projects}

@router.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    path = (WAREHOUSE / project_id).resolve()
    if not str(path).startswith(str(WAREHOUSE.resolve()) + os.sep):
        return {"error": "Access denied"}
    if not path.exists():
        return {"error": "Project not found"}

    files = {}
    for f in path.rglob("*"):
        if f.is_file() and not _is_skipped(f, path) \
                and not _is_artifact(f):
            try:
                files[str(f.relative_to(path))] = f.read_text(
                    encoding="utf-8", errors="replace")
            except Exception:
                files[str(f.relative_to(path))] = "[binary]"
    return {"id": project_id, "files": files}

@router.post("/api/projects/{project_id}/run")
async def run_project(project_id: str):
    """D4: 运行生成的项目入口（main.py 优先，其次 cli.py），返回输出。"""
    path = (WAREHOUSE / project_id).resolve()
    if not str(path).startswith(str(WAREHOUSE.resolve()) + os.sep):
        return {"error": "Access denied"}
    if not path.exists():
        return {"error": "Project not found"}
    # 入口：main.py 优先（有 __main__ 检查的最可能），无则 cli.py
    entry = ""
    for cand in ("main.py", "cli.py", "app.py"):
        if (path / cand).exists():
            entry = cand
            break
    if not entry:
        return {"error": "No entry point found (main.py/cli.py/app.py)"}
    # 优先项目 venv（依赖装在里面）
    venv_py = path / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python3")
    python = str(venv_py) if venv_py.exists() else sys.executable
    try:
        result = subprocess.run(
            [python, entry], cwd=str(path), capture_output=True, timeout=30,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"output": "（运行超时 — 程序可能在等待输入）", "code": -1}
    output = ((result.stdout or b"") + (result.stderr or b"")).decode(
        "utf-8", errors="replace")
    return {"output": output[:4000] or "（无输出）", "code": result.returncode}

@router.get("/api/projects/{project_id}/events")
async def get_project_events(project_id: str):
    from codegen.application.chat_chain import artifact_path
    events_path = Path(artifact_path(str(WAREHOUSE / project_id), "run_events.json"))
    if not events_path.exists():
        return {"events": []}
    return json.loads(events_path.read_text(encoding="utf-8"))
