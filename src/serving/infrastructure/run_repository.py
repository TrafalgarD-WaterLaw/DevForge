"""InMemoryRunRepository —— 运行状态存取（
只负责 _active_runs 的存取与状态迁移校验；不含 ws 连接、事件推送。
状态迁移规则来自 serving/domain/run_state.py（RunStatus）。
"""
import json
import time
from pathlib import Path

from serving.domain.exceptions import IllegalRunStateTransitionError
from serving.domain.run_state import RunStatus

_active_runs: dict[str, dict] = {}

_EXPIRE_SECONDS = 3600.0

# 合法状态迁移（RunStatus 语义的单一事实来源的镜像；RunState 实体
# 供将来把 dict 升级为实体时直接替换）
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    RunStatus.STARTING: {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.ERROR},
    RunStatus.QUEUED: {RunStatus.STARTING, RunStatus.RUNNING, RunStatus.ERROR},
    RunStatus.RUNNING: {RunStatus.COMPLETE, RunStatus.ERROR},
    RunStatus.COMPLETE: set(),
    RunStatus.ERROR: set(),
}

def _prune_active_runs() -> None:
    """Lazily drop runs whose status is done and older than 1h."""
    now = time.time()
    for run_id, entry in list(_active_runs.items()):
        if entry.get("status") in ("complete", "error") and \
                now - entry.get("started_at", 0) > _EXPIRE_SECONDS:
            _active_runs.pop(run_id, None)
            # 顺带清掉连接映射（函数内 import 防环）
            from serving.application.ws_manager import _ws_connections, _ws_loops
            _ws_connections.pop(run_id, None)
            _ws_loops.pop(run_id, None)

def init_run(run_id: str) -> None:
    _prune_active_runs()
    _active_runs[run_id] = {
        "status": RunStatus.STARTING, "events": [], "project_dir": None,
        "error": None, "started_at": time.time(), "_seq": 0,
    }

def _require_transition(run_id: str, current: str, target: str) -> None:
    """非法迁移在入口处拒绝 —— 状态机语义不进 Service/Controller。"""
    if target not in _LEGAL_TRANSITIONS.get(current, set()):
        raise IllegalRunStateTransitionError(run_id, current, target)

def set_run_status(run_id: str, status: str) -> None:
    """队列状态流转：starting → queued → running → complete/error。

    P0-1 修复：此前 status 只有 starting/complete/error 三态，worker 把
    刚 init 的 run 自己当成活跃 run，导致所有 /api/run 永远入队永不启动。
    "running" 是真正开始执行的标记，run_pipeline 线程第一行置位。
    """
    entry = _active_runs.get(run_id)
    if entry is not None:
        _require_transition(run_id, entry["status"], status)
        entry["status"] = status
        # starting 重新入队时间戳：活跃判定用"starting 且新鲜"识别
        # 即将启动的 run，旧时间戳会把它误判为死线程
        if status == RunStatus.STARTING:
            entry["started_at"] = time.time()

def get_run(run_id: str) -> dict:
    _prune_active_runs()
    return _active_runs.get(run_id)

def complete_run(run_id: str, project_dir: str) -> None:
    if run_id in _active_runs:
        _require_transition(run_id, _active_runs[run_id]["status"],
                            RunStatus.COMPLETE)
        _active_runs[run_id]["status"] = RunStatus.COMPLETE
        _active_runs[run_id]["project_dir"] = project_dir

def set_run_dir(run_id: str, project_dir: str) -> None:
    """运行中设置 project_dir（不改状态）—— 供阶段边界增量落盘
    persist_run 使用（此前 project_dir 只在 complete/fail 时才有值，
    中途崩溃时 run_events.json 无从写入）。"""
    entry = _active_runs.get(run_id)
    if entry is not None:
        entry["project_dir"] = project_dir

def fail_run(run_id: str, error: str) -> None:
    if run_id in _active_runs:
        _require_transition(run_id, _active_runs[run_id]["status"],
                            RunStatus.ERROR)
        _active_runs[run_id]["status"] = RunStatus.ERROR
        _active_runs[run_id]["error"] = error

def persist_run(run_id: str, task: str = "") -> None:
    """Write run events to disk for post-mortem browsing."""
    run_data = get_run(run_id)
    if not run_data:
        return
    project_dir = run_data.get("project_dir", "")
    if not project_dir:
        return
    # 工件收进 .devforge/，不混入交付目录
    from codegen.application.chat_chain import ARTIFACT_DIR
    artifacts = Path(project_dir) / ARTIFACT_DIR
    artifacts.mkdir(exist_ok=True)
    events_path = artifacts / "run_events.json"
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id, "task": task,
            "events": run_data["events"], "project_dir": project_dir,
        }, f, ensure_ascii=False, indent=2, default=str)
