"""WebSocketConnectionManager —— 连接注册与事件推送（瘦身后）。

运行状态存取 → serving/infrastructure/run_repository.py
阻塞式人机交互 → serving/infrastructure/ws_interaction.py
（本模块底部 re-export 二者，兼容既有调用方；收紧后移除）
"""
import asyncio
import threading

from fastapi import WebSocket

_ws_connections: dict[str, WebSocket] = {}
_ws_loops: dict[str, asyncio.AbstractEventLoop] = {}

# seq 分配锁：并行 agent 线程同时 emit 时读改写三步可交错，两个事件
# 拿到相同 seq → 前端按 seq<=lastSeq 去重会整条丢弃其中一个
_seq_lock = threading.Lock()

# Per-run event storage budget + expiry (done runs are pruned lazily).
try:
    from core.config import load_pipeline_config as _load_cfg
    _MAX_EVENTS = int(_load_cfg().get("tools", {}).get("max_events", 2000))
except Exception:
    _MAX_EVENTS = 2000

# 关键事件 —— 缓冲裁剪时最后才丢（阶段边界/审阅/质检/提问是
# 历史页与重放的骨架，长任务下不能被流式/工具事件挤掉）
_CRITICAL_EVENTS = frozenset({
    "pipeline_start", "pipeline_complete",
    "phase_start", "phase_end", "phase_retry", "phase_error",
    "discuss_choice", "review_request", "review_decision",
    "review_timed_out", "review_round", "review_submitted",
    "review_discarded", "quality_gate", "token_warning",
    "requirements_submitted", "design_submitted", "integration_start",
})

# Per-run context —— 已抽到 core.context（纯 contextvars 工具）。
from core.context import get_current_run, set_current_run  # noqa: F401

def register_ws(run_id: str, ws: WebSocket) -> None:
    _ws_connections[run_id] = ws
    try:
        _ws_loops[run_id] = asyncio.get_running_loop()
    except RuntimeError:
        pass

def unregister_ws(run_id: str, ws=None) -> None:
    """注销连接。*ws* 传入时仅当它仍是当前连接才注销 —— 断线重连场景
    下旧 handler 的 finally 晚于新连接 register 执行时，不能把新连接
    误注销（否则前端画面冻结直到下一次重连）。"""
    if ws is not None and _ws_connections.get(run_id) is not ws:
        return
    _ws_connections.pop(run_id, None)
    _ws_loops.pop(run_id, None)

def has_ws(run_id: str) -> bool:
    """True when a WebSocket is currently connected for *run_id*."""
    return run_id in _ws_connections

def emit(run_id: str, event: dict) -> None:
    """Store event and push to WebSocket.  Thread-safe — uses the main
    asyncio loop captured at WebSocket connect time."""
    from serving.infrastructure.run_repository import _active_runs, _EXPIRE_SECONDS
    import time as _time

    entry = _active_runs.get(run_id)
    if entry is not None:
        done_old = (entry.get("status") in ("complete", "error")
                    and _time.time() - entry.get("started_at", 0) > _EXPIRE_SECONDS)
        if not done_old:
            event = _stamp_sequence(entry, event)
            if not _merge_stream_delta(entry, event):
                _append_with_trim(entry, event)
    _send(run_id, event)

# ── 子步骤（二轮拆分：emit 41 行 → 编排 + 子步骤）──

def _stamp_sequence(entry: dict, event: dict) -> dict:
    """Per-run monotonic seq — the frontend dedupes replays with it.
    分配加锁：并发 emit（并行 reviewer/coder 线程）下保证唯一。"""
    event = dict(event)
    with _seq_lock:
        seq = entry.setdefault("_seq", 0)
        event["seq"] = seq
        entry["_seq"] = seq + 1
    return event

def _merge_stream_delta(entry: dict, event: dict) -> bool:
    """同 agent 的连续 llm_delta 在缓冲里累积为一条。一次长流式输出可达
    上千条 delta，会把缓冲挤满，把 phase_start/里程碑等关键事件全挤出
    （重放/历史页只剩流式片段）。返回 True = 已合并（调用方无需 append）。"""
    if event.get("event") != "llm_delta":
        return False
    events = entry["events"]
    last = events[-1] if events else None
    if last and last.get("event") == "llm_delta" \
            and last.get("agent") == event.get("agent"):
        last["delta"] = ((last.get("delta") or "")
                         + event.get("delta", ""))
        return True
    return False

def _append_with_trim(entry: dict, event: dict) -> None:
    """追加事件 + 超限裁剪：先丢非关键事件，仍超才丢最旧关键事件。"""
    events = entry["events"]
    events.append(event)
    while len(events) > _MAX_EVENTS:
        idx = next((i for i, e in enumerate(events)
                    if e.get("event") not in _CRITICAL_EVENTS), None)
        if idx is not None:
            events.pop(idx)
        else:
            events.pop(0)
    entry["events"] = events

def _send(run_id: str, event: dict) -> None:
    """Push *event* to the run's WebSocket (best-effort)."""
    ws = _ws_connections.get(run_id)
    loop = _ws_loops.get(run_id)
    if ws is None or loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_safe_send(ws, event), loop)
    except Exception:
        _ws_connections.pop(run_id, None)
        _ws_loops.pop(run_id, None)

async def _safe_send(ws: WebSocket, event: dict) -> None:
    """Send, catching disconnects."""
    try:
        await ws.send_json(event)
    except Exception:
        pass  # client disconnected

# ── 兼容 re-export：拆分后旧调用方（含测试）仍从本模块导入 ──
from serving.infrastructure.run_repository import (  # noqa: E402,F401
    _active_runs, complete_run, fail_run, get_run, init_run,
    persist_run, set_run_status)
from serving.infrastructure.ws_interaction import (  # noqa: E402,F401
    _feedback_queues, _reply_events, _reply_seq, _review_events,
    ask_approval, ask_choice, drain_feedback, push_feedback,
    submit_reply, submit_review_decision)
