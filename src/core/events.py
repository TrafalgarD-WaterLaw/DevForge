"""Hook system — register callbacks on lifecycle events.

Events span the entire agent + pipeline lifecycle::

    pipeline.start / pipeline.complete
    phase.start    / phase.end / phase.error
    agent.message
    tool.pre_use   / tool.post_use / tool.denied
    conversation.turn
"""

import logging
import time
from typing import Callable

_log = logging.getLogger(__name__)

# ── Event constants ───────────────────────────────────

class Events:
    PIPELINE_START    = "pipeline_start"
    PIPELINE_COMPLETE = "pipeline_complete"
    PHASE_START       = "phase_start"
    PHASE_END         = "phase_end"
    PHASE_ERROR       = "phase_error"
    PHASE_RETRY       = "phase_retry"
    AGENT_MESSAGE     = "agent_message"
    TOOL_PRE_USE      = "tool_pre_use"
    TOOL_POST_USE     = "tool_post_use"
    CONVERSATION_TURN = "conversation_turn"
    TODO_UPDATE       = "todo_update"

# ── Registry ───────────────────────────────────────────

class HookRegistry:
    """Register callbacks on named events.  Multiple hooks per event,
    fired in registration order.  A hook returning a non-None value
    short-circuits (useful for intercept / deny patterns).
    """

    _hooks: dict[str, list[Callable]] = {}

    # ── register / unregister ──────────────────────────

    @classmethod
    def on(cls, event: str, callback: Callable):
        """Register *callback* for *event*.  ``"*"`` matches all events."""
        cls._hooks.setdefault(event, []).append(callback)

    @classmethod
    def off(cls, event: str, callback: Callable):
        """Remove a previously registered callback."""
        if event in cls._hooks:
            cls._hooks[event] = [h for h in cls._hooks[event] if h is not callback]

    @classmethod
    def clear(cls):
        """Remove all hooks (mainly for testing)."""
        cls._hooks.clear()

    # ── fire ───────────────────────────────────────────

    @classmethod
    def trigger(cls, event: str, **data) -> list:
        """Fire all callbacks for *event* (and ``"*"`` wildcard).

        Returns a list of non-None results (for intercept-capable hooks).
        Side-effects only in most cases.
        """
        results = []
        data.setdefault("timestamp", time.time())
        for cb in cls._hooks.get(event, []) + cls._hooks.get("*", []):
            try:
                r = cb(event, **data)
                if r is not None:
                    results.append(r)
            except Exception:
                _log.exception("Hook %s failed", event)
        return results

# ── built-in: forward to WebSocket ─────────────────────

def _ws_forward(event: str, **data):
    """Forward every event to the WebSocket frontend (if connected)."""
    from core.context import get_current_run
    from serving.application.ws_manager import emit  # TODO: 端口倒置后移除
    run_id = get_current_run()
    if run_id:
        emit(run_id, {"event": event, **data})

HookRegistry.on("*", _ws_forward)
