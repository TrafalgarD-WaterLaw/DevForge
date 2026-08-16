"""Pipeline runner — called in a background thread."""
import threading
import time

from core.config import load_pipeline_config
from codegen.application.chat_chain import ChatChain
from serving.application.ws_manager import emit
from serving.infrastructure.run_repository import (
    complete_run, fail_run, persist_run)

# ── 任务队列：FIFO，同一时刻只跑一个 run ──────────────────
_pending: list[dict] = []            # {run_id, task, kwargs}
_started: set[str] = set()           # 已被 worker 启动的 run_id
_queue_lock = threading.Lock()
_worker_started = False

# starting 状态的宽限期：/api/run 与启动线程之间的窗口期视为活跃，
# 超过这个时间的 starting = 启动线程已死（构造崩溃等），不堵死队列
_STARTING_GRACE_SECONDS = 10.0

def _has_active_run() -> bool:
    """当前是否有正在跑的 run。

    running = 流水线执行中；starting 且刚创建（宽限期内）= 即将启动，
    也视为活跃 —— 堵住 /api/run 与 run_pipeline 线程之间的并发窗口。
    starting 超宽限期 = 启动线程已死，
    不视为活跃，避免队列被僵尸 run 永久堵死。
    """
    from serving.application.ws_manager import _active_runs
    now = time.time()
    for e in _active_runs.values():
        status = e.get("status")
        if status == "running":
            return True
        if status == "starting" and \
                now - e.get("started_at", 0) < _STARTING_GRACE_SECONDS:
            return True
    return False

def enqueue_or_run(run_id: str, task: str, **kwargs) -> dict:
    """有 run 在跑 → 入队（status=queued）；否则直接启动。返回
    {queued, position}。run 的 init_run 也在这里做 —— 必须先判活跃再
    init，否则刚 init 的 "starting" 会把 run 自己当成活跃者。"""
    global _worker_started
    from serving.application.ws_manager import init_run, set_run_status
    with _queue_lock:
        if not _worker_started:
            _worker_started = True
            threading.Thread(target=_queue_worker, daemon=True).start()
        if _has_active_run():
            init_run(run_id)
            set_run_status(run_id, "queued")
            _pending.append({"run_id": run_id, "task": task, "kwargs": kwargs})
            return {"queued": True, "position": len(_pending)}
    init_run(run_id)
    threading.Thread(
        target=run_pipeline, args=(run_id, task),
        kwargs=kwargs, daemon=True,
    ).start()
    return {"queued": False, "position": 0}

def _worker_tick_once():
    """出队一次（若允许）。拆成独立函数便于单测（worker 线程循环调用）。"""
    with _queue_lock:
        if not _pending or _has_active_run():
            return
        item = _pending.pop(0)
        # 出队瞬间置回 starting（新鲜时间戳）—— 防 run 线程真正置
        # "running" 之前新请求误判"无活跃"而并发启动
        from serving.application.ws_manager import set_run_status
        set_run_status(item["run_id"], "starting")
    _started.add(item["run_id"])
    print(f"  [Queue] 启动排队任务 {item['run_id']}", flush=True)
    threading.Thread(
        target=run_pipeline, args=(item["run_id"], item["task"]),
        kwargs=item["kwargs"], daemon=True,
    ).start()

def _queue_worker():
    """轮询：无活跃 run 时依次启动队首任务。"""
    while True:
        time.sleep(2)
        _worker_tick_once()

def queue_status(run_id: str) -> dict:
    """前端轮询：{position, started}。started = 已在跑/已出队。"""
    if run_id in _started:
        return {"position": 0, "started": True}
    with _queue_lock:
        for i, item in enumerate(_pending):
            if item["run_id"] == run_id:
                return {"position": i + 1, "started": False}
    # 不在队列也不在 started：要么已失败要么从未入队
    from serving.application.ws_manager import get_run
    return {"position": 0, "started": get_run(run_id) is not None}

def run_pipeline(run_id: str, task: str, start_from: str = "",
                 pipeline: str = "default", project_dir: str = ""):
    """Start a pipeline, manage lifecycle, persist events, notify frontend.

    *project_dir* — A2 增量迭代：在已有项目目录上跑（不新建 WareHouse 目录）。
    """
    _started.add(run_id)   # 直接启动的 run 也标记 started（队列轮询语义一致）
    from serving.application.ws_manager import set_run_status
    set_run_status(run_id, "running")
    try:
        # ChatChain 构造也可能失败（start_from 无历史项目等）——
        # 必须进 try，否则状态永久 "starting" 堵死整个队列
        chain = ChatChain(
            config=load_pipeline_config(pipeline), task_prompt=task,
            run_id=run_id, start_from=start_from, project_dir=project_dir)
        started = time.time()
        project_dir = chain.run()
        complete_run(run_id, project_dir)
        persist_run(run_id, task)
        emit(run_id, {
            "event": "pipeline_complete",
            "timestamp": time.time(),
            "project_dir": project_dir,
            "duration": round(time.time() - started, 1),
            "failed": bool(chain.blackboard.get("quality_gate_failed")),
            # 质检结论透传：前端据此不把 WARN/未达标说成"全部完成"
            "verdict": chain.blackboard.get("quality_gate", {}).get("verdict", ""),
            "qg_loops": chain.blackboard.get("quality_gate_loops", 0),
        })
    except Exception as exc:
        fail_run(run_id, str(exc))
        emit(run_id, {"event": "error", "timestamp": time.time(), "message": str(exc)})
        raise
