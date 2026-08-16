"""Execution patterns — reusable functions for phase run() methods.

    converse(speaker, listener, speaker_prompt, listener_prompt) → two agents take turns
    parallel(tasks)                       → N agents run concurrently
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from codegen.domain.agent import Agent
from core.events import Events, HookRegistry

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Two-agent turn-taking
# ═══════════════════════════════════════════════════════════════════

def converse(speaker: Agent, listener: Agent, speaker_prompt: str,
             listener_prompt: str = "", max_turns: int = 6,
             stream: bool = False) -> dict:
    """Two agents alternate turns. *speaker* goes first with *speaker_prompt*,
    *listener* responds with *listener_prompt* on its first turn.
    *stream* 让双方输出流式推送（设计阶段讨论实时可见）。"""
    current_speaker, current_listener = speaker, listener
    data = {}

    for i in range(max_turns):
        if i == 0:
            prompt = speaker_prompt
        elif i == 1 and listener_prompt:
            prompt = listener_prompt
        else:
            prompt = "Your turn to respond."
        data = current_speaker.react(prompt, stream=stream)
        current_listener.receive(data)

        HookRegistry.trigger(Events.CONVERSATION_TURN,
                             agent=current_speaker.name,
                             content=json.dumps(data, ensure_ascii=False),
                             turn=i)

        if data.get("_terminated"):
            return data
        msg = (data.get("message") or "")
        # 独占一行 I AGREE 才算同意 —— "I agree, but..." 不算
        # 子串匹配会提前终止讨论
        import re as _re
        if _re.search(r"(^|\n)\s*i\s+agree\s*[.!]?\s*($|\n)", msg,
                      _re.IGNORECASE):
            return data

        current_speaker, current_listener = current_listener, current_speaker

    return data

# ═══════════════════════════════════════════════════════════════════
# N-agent parallel
# ═══════════════════════════════════════════════════════════════════

def parallel(tasks: list[tuple]
             ) -> list[tuple[Agent, dict | None]]:
    """Run agents concurrently.  Each task is ``(agent, prompt)``,
    ``(agent, prompt, json_mode)`` or ``(agent, prompt, json_mode, stream)``.

    Returns a list of ``(agent, data)`` in completion order.
    *data* is the parsed dict from ``react()``, or ``None`` on failure.
    """
    results: list[tuple[Agent, dict | None]] = []
    # 空任务直接返回 —— max_workers = min(0, …) = 0 会让
    # ThreadPoolExecutor 抛 ValueError（编码阶段全模块产物缓存时
    # pending 为空会走到这里）
    if not tasks:
        return results
    max_workers = min(len(tasks), max((os.cpu_count() or 4) - 1, 1))

    # contextvars do NOT propagate into ThreadPoolExecutor workers — capture
    # the current ToolRuntime + run id here and re-set them inside each
    # worker thread (the run id keeps _ws_forward routing tool events to
    # the right WebSocket from inside pool workers).
    from codegen.infrastructure.tools.registry import runtime, set_runtime
    try:
        rt = runtime()
    except RuntimeError:
        rt = None
    try:
        from core.context import get_current_run
        run_id = get_current_run()
    except Exception:
        run_id = None

    def _run(agent: Agent, prompt: str, json_mode: bool = False,
             stream: bool = False):
        if rt is not None:
            set_runtime(rt)
        if run_id:
            from core.context import set_current_run
            set_current_run(run_id)
        return agent.react(prompt, json_mode=json_mode, stream=stream)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for t in tasks:
            a, p = t[0], t[1]
            jm = t[2] if len(t) > 2 else False
            st = t[3] if len(t) > 3 else False
            futures[pool.submit(_run, a, p, jm, st)] = a

        for future in as_completed(futures):
            agent = futures[future]
            try:
                data = future.result()
            except Exception:
                _log.exception("[%s] parallel task failed", agent.name)
                data = None
            results.append((agent, data))

    return results

