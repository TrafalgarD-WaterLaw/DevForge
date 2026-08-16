"""WsUserInteractionAdapter —— 阻塞式人机交互（
实现 codegen 域的 UserInteractionPort：PM 提问（ask_choice）、人工审阅
（ask_approval）、运行中反馈队列（push/drain_feedback）。
"""

import asyncio
import json
import threading
import time
from core.config import _project_root
from serving.application.ws_manager import _safe_send, _ws_connections, _ws_loops

_reply_events: dict[str, tuple[threading.Event, str, int]] = {}
_reply_seq: dict[str, int] = {}
_feedback_queues: dict[str, list[str]] = {}
_review_events: dict[str, tuple[threading.Event, bool]] = {}
_timeout_cache: dict | None = None
_timeout_cache_at: float = 0.0

def _config_float(key: str, default: float) -> float:
    """Read a float config key from configs/default.json (cached, 5s TTL)."""
    global _timeout_cache, _timeout_cache_at
    try:
        if _timeout_cache is not None and time.time() - _timeout_cache_at < 5.0:
            return _timeout_cache.get(key, default)
        path = _project_root() / "configs" / "default.json"
        if path.exists():
            config_data = json.loads(path.read_text(encoding="utf-8"))
            _timeout_cache = {
                "ask_choice_timeout": float(config_data.get("ask_choice_timeout", 300)),
                "review_timeout": float(config_data.get("review_timeout", 120)),
            }
        else:
            _timeout_cache = {"ask_choice_timeout": 300.0, "review_timeout": 120.0}
        _timeout_cache_at = time.time()
        return _timeout_cache.get(key, default)
    except Exception:
        return default

def _ask_choice_timeout() -> float:
    """PM 提问等待超时（ask_choice_timeout，默认 300s）。"""
    return _config_float("ask_choice_timeout", 300.0)

def _review_timeout() -> float:
    """人工审阅等待超时（review_timeout，默认 120s）—— 与 PM 提问分开，
    审阅等待太久会让人以为流程卡死。"""
    return _config_float("review_timeout", 120.0)

def ask_choice(
    run_id: str, question: str, options: list[str], allow_multiple: bool = False
) -> dict:
    """Send a multiple-choice question, block until user selects.
    Returns ``{selected: [...], custom: ""}`` or empty dict if no WS."""
    ws = _ws_connections.get(run_id)
    loop = _ws_loops.get(run_id)
    if ws is None or loop is None:
        return {"selected": [], "custom": ""}
    ev = threading.Event()
    seq = _reply_seq.get(run_id, 0)
    _reply_seq[run_id] = seq + 1
    _reply_events[run_id] = (ev, "", seq)
    asyncio.run_coroutine_threadsafe(
        _safe_send(
            ws,
            {
                "event": "discuss_choice",
                "question": question,
                "options": options,
                "allow_multiple": allow_multiple,
                "timestamp": time.time(),
                "qseq": seq,
            },
        ),
        loop,
    )
    ev.wait(timeout=_ask_choice_timeout())
    entry = _reply_events.get(run_id)
    raw = "{}"
    if entry is not None and entry[0] is ev:
        raw = entry[1] or "{}"
        _reply_events.pop(run_id, None)
    try:
        return json.loads(raw)
    except Exception:
        return {"selected": [], "custom": ""}

def submit_reply(run_id: str, reply: str, seq=None) -> None:
    """Called by WebSocket handler when the user sends a reply.

    *seq* — the question seq echoed by the client (optional).  A reply
    carrying a seq that doesn't match the pending question is stale
    (double-click from an earlier question) and is ignored.  Replies
    without a seq (old frontends) are accepted for backward compatibility.
    """
    entry = _reply_events.get(run_id)
    if not entry:
        return
    ev, _old, question_seq = entry
    if seq is not None and seq != question_seq:
        return
    _reply_events[run_id] = (ev, reply, question_seq)
    ev.set()

def push_feedback(run_id: str, content: str) -> None:
    """入队一条运行中用户消息（ws handler 调用）。"""
    _feedback_queues.setdefault(run_id, []).append(content)

def drain_feedback(run_id: str) -> list[str]:
    """消费并清空该 run 的待处理用户消息（无则空列表）。"""
    return _feedback_queues.pop(run_id, [])

def ask_approval(run_id: str, payload: dict) -> bool:
    """发人工审阅请求并阻塞等待决策（headless 无 ws 时自动通过）。

    超时（review_timeout，默认 120s）默认通过 —— 不阻塞流程；前端若在线
    会展示 diff 卡片，用户可拒绝。超时自动通过时向前端发 review_timed_out
    事件，把审阅卡收尾为"超时自动通过"（否则卡片永远停在待决策态）。
    """
    ws = _ws_connections.get(run_id)
    loop = _ws_loops.get(run_id)
    if ws is None or loop is None:
        print("  [Pipeline] 人工审阅: 无在线会话 — 自动通过", flush=True)
        return True
    ev = threading.Event()
    _review_events[run_id] = (ev, True)
    asyncio.run_coroutine_threadsafe(
        _safe_send(
            ws, {"event": "review_request", **payload, "timestamp": time.time()}
        ),
        loop,
    )
    print("  [Pipeline] 人工审阅: 等待你在对话区决定（通过/拒绝）…", flush=True)
    ev.wait(timeout=_review_timeout())
    entry = _review_events.pop(run_id, None)
    if entry is not None and entry[0].is_set():
        approved = entry[1]
        print(
            f"  [Pipeline] 人工审阅: {('通过 ✅' if approved else '拒绝 ❌')}（你已决策）",
            flush=True,
        )
        return approved
    approved = True
    print("  [Pipeline] 人工审阅: 等待超时，自动通过 ⏰", flush=True)
    try:
        asyncio.run_coroutine_threadsafe(
            _safe_send(ws, {"event": "review_timed_out", "timestamp": time.time()}),
            loop,
        )
    except Exception:
        pass
    return approved

def submit_review_decision(run_id: str, approved: bool) -> None:
    """WebSocket handler 收到用户的审阅决策。"""
    entry = _review_events.get(run_id)
    if entry:
        _review_events[run_id] = (entry[0], approved)
        entry[0].set()
