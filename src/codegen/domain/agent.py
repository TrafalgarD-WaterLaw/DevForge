"""Agent — wraps an LLM with dialogue history, ReAct loop, and inter-agent messaging."""

from __future__ import annotations
import json as _json
import logging as _logging
from core.text import parse_llm_output
from codegen.domain.blackboard import Blackboard
from codegen.domain.ports import LlmPort
from codegen.infrastructure.llm_client import LLMClient

_logger = _logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 16

class Agent:
    """Owns dialogue history and the ReAct loop.  Uses DeepSeek's
    native tool calling when tools are configured."""

    def __init__(
        self,
        config_key: str,
        blackboard: Blackboard,
        *,
        tag: str = "",
        llm: LlmPort | None = None,
    ):
        from core.config import (
            load_phases_config,
            load_pipeline_config,
            load_roles_config,
        )
        from codegen.infrastructure.tools.registry import describe

        self.name = tag or config_key
        self.blackboard = blackboard or Blackboard()
        self._llm: LlmPort = llm or LLMClient()
        self._max_context_chars = int(
            load_pipeline_config().get("llm", {}).get("max_context_chars", 60000)
        )
        self._max_tool_rounds = int(
            load_pipeline_config()
            .get("llm", {})
            .get("max_tool_rounds", MAX_TOOL_ROUNDS)
        )
        tools: list[str] = []
        self._max_tokens: int | None = None
        for phase in load_phases_config().values():
            agents = phase.get("agents", {})
            if config_key in agents:
                tools = agents[config_key].get("tools", [])
                self._max_tokens = agents[config_key].get("max_tokens")
                break
        self._tool_schemas = describe(tools) if tools else []
        system_prompt = load_roles_config().get(config_key, "")
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def react(
        self, user_msg: str, *, json_mode: bool = False, stream: bool = False
    ) -> dict:
        """Run one ReAct loop.  Emits ``agent_done`` on every exit path —
        the frontend stage panel marks the agent's window done (green) on it."""
        try:
            return self._react_inner(user_msg, json_mode=json_mode, stream=stream)
        finally:
            from core.events import HookRegistry

            HookRegistry.trigger("agent_done", agent=self.name)

    def _react_inner(
        self, user_msg: str, *, json_mode: bool = False, stream: bool = False
    ) -> dict:
        from core.events import HookRegistry
        from codegen.infrastructure.tools.registry import runtime

        runtime().ctx.blackboard = self.blackboard
        if json_mode and "json" not in user_msg.lower():
            user_msg = f"{user_msg}\n\nRespond in JSON."
        self._messages.append({"role": "user", "content": user_msg})
        print(f"  [{self.name}] prompt={len(user_msg)} chars", flush=True)
        tools = self._tool_schemas or None
        stream_ok = stream and (not tools) and (not json_mode)
        if not stream_ok:
            HookRegistry.trigger("agent_typing", agent=self.name)
        for _ in range(self._max_tool_rounds if tools else 1):
            if stream_ok:
                return self._react_stream()
            result = self._call_with_retry(tools=tools, json_mode=json_mode)
            if result is None:
                return {"_terminated": "llm_error", "message": ""}
            if result["tool_calls"]:
                malformed = self._record_tool_calls(result["tool_calls"])
                if malformed:
                    continue
                self._run_tools(result["tool_calls"])
                continue
            text = result["content"]
            finish = result.get("finish_reason")
            if finish == "length" and text:
                return self._handle_truncation(text, json_mode)
            if not text:
                return {"_terminated": "empty_response", "message": ""}
            self._messages.append({"role": "assistant", "content": text})
            return self._parse(text)
        return self._force_final_answer(json_mode)

    def _call_with_retry(self, *, tools, json_mode: bool) -> dict | None:
        """非流式调用；首次失败去掉 max_tokens 重试一次，仍败返回 None。"""
        self._compact_if_needed()
        try:
            result = self._llm.call(
                self._messages,
                tools=tools,
                json_mode=json_mode and (not tools),
                max_tokens=self._max_tokens,
            )
        except Exception:
            _logger.warning(
                "[%s] LLM call failed — retrying without max_tokens", self.name
            )
            try:
                result = self._llm.call(
                    self._messages,
                    tools=tools,
                    json_mode=json_mode and (not tools),
                    max_tokens=None,
                )
            except Exception:
                _logger.exception("[%s] LLM call failed twice", self.name)
                return None
        self._record_usage(result.get("usage"))
        return result

    def _record_tool_calls(self, tool_calls: list) -> bool:
        """工具调用回填历史（参数损坏用 "{}" 占位保证 API 往返合法）。
        返回 True 表示存在损坏参数（调用方应反馈后重出）。"""
        malformed = [tc for tc in tool_calls if tc.get("args") is None]
        self._messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": _json.dumps(tc["args"], ensure_ascii=False)
                            if tc.get("args") is not None
                            else "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )
        if not malformed:
            return False
        names = ", ".join((tc["name"] for tc in malformed))
        _logger.warning(
            "[%s] malformed tool args: %s — re-requesting", self.name, names
        )
        self._messages.append(
            {
                "role": "user",
                "content": f"Your tool call arguments for {names} were truncated or invalid JSON and could not be executed. Re-output the tool call with complete, valid JSON arguments.",
            }
        )
        return True

    def _handle_truncation(self, text: str, json_mode: bool) -> dict:
        """finish_reason=length：JSON 模式整份重出；文本模式 Continue 续写。"""
        if json_mode:
            _logger.warning(
                "[%s] JSON truncated — re-requesting concise output", self.name
            )
            self._messages.append(
                {
                    "role": "user",
                    "content": "Your previous response hit the output limit and was cut off. Output the COMPLETE JSON again, being concise.",
                }
            )
            try:
                retry = self._llm.call(
                    self._messages, json_mode=True, max_tokens=self._max_tokens
                )
            except Exception:
                return {"_terminated": "llm_error", "message": ""}
            cont = retry.get("content") or ""
            if cont:
                self._messages.append({"role": "assistant", "content": cont})
                return self._parse(cont)
            return {"_terminated": "empty_response", "message": ""}
        _logger.warning("[%s] Response truncated — attempting continue", self.name)
        self._messages.append({"role": "assistant", "content": text})
        self._messages.append({"role": "user", "content": "Continue."})
        try:
            retry = self._llm.call(self._messages)
            continuation = retry["content"]
            if continuation:
                combined = text.rstrip() + continuation
                self._messages.append({"role": "assistant", "content": continuation})
                return self._parse(combined)
        except Exception:
            _logger.warning("[%s] Continue retry failed", self.name)
        return self._parse(text)

    def _force_final_answer(self, json_mode: bool) -> dict:
        """工具轮次耗尽：强制收尾 —— 去掉工具声明直接要最终答案。

        reviewer 常 10 轮 × 每轮多个工具调用后仍没输出结论，此前直接
        返回 _terminated → schema 必失败 → "N 份审查输出无效"的系统性根因。
        json_mode 此时才真正生效（json_object 强制合法 JSON）。
        """
        self._messages.append(
            {
                "role": "user",
                "content": "You have used all your tool-call rounds. Output your final answer now — do not call tools again.",
            }
        )
        try:
            result = self._llm.call(
                self._messages,
                tools=None,
                json_mode=json_mode,
                max_tokens=self._max_tokens,
            )
        except Exception:
            return {"_terminated": "llm_error", "message": ""}
        text = result.get("content") or ""
        if text:
            self._messages.append({"role": "assistant", "content": text})
            return self._parse(text)
        return {"_terminated": "max_tool_rounds", "message": ""}

    def _react_stream(self) -> dict:
        """流式文本输出：每 chunk 经 llm_delta 事件推送到前端，实时可见。

        完成后正常解析（与 react 相同语义），历史照常入 messages。
        """
        from core.events import Events, HookRegistry

        self._compact_if_needed()
        deltas: list[str] = []

        def on_delta(text: str):
            deltas.append(text)
            HookRegistry.trigger("llm_delta", agent=self.name, delta=text)

        try:
            text = self._llm.stream_call(self._messages, on_delta=on_delta)
            stream_usage = getattr(self._llm, "last_stream_usage", None)
        except Exception:
            _logger.warning("[%s] stream call failed — falling back", self.name)
            stream_usage = None
            try:
                result = self._llm.call(self._messages)
                stream_usage = result.get("usage")
            except Exception:
                return {"_terminated": "llm_error", "message": ""}
            text = result.get("content") or ""
        self._record_usage(stream_usage)
        HookRegistry.trigger("llm_stream_end", agent=self.name)
        if text:
            self._messages.append({"role": "assistant", "content": text})
        return self._parse(text or "".join(deltas))

    def _compact_if_needed(self):
        """Sliding-window compaction before each LLM call.

        历史超预算（默认 60k 字符 ≈ 60k token）时丢弃最旧的普通
        user/assistant 消息 —— 零 LLM 成本（不做摘要调用）。规则：
        - system 与当前问题（最后一条 user）永不丢
        - tool_calls 消息与其 tool 结果必须成对 —— 全部保留（工具结果
          已截断 6000 字符/条，不是主要膨胀源）
        压缩是损失性的：丢弃的早期对话以一条说明消息代替，让模型知道
        上下文被裁剪过。
        """
        total = sum((len(m.get("content") or "") for m in self._messages))
        if total <= self._max_context_chars:
            return
        kept: list[dict] = []
        dropped = 0
        budget = self._max_context_chars
        for m in reversed(self._messages):
            role = m.get("role")
            is_plain = role in ("user", "assistant") and bool(m.get("content"))
            cost = len(m.get("content") or "") if is_plain else 0
            if is_plain and budget - cost < 0 and kept:
                dropped += 1
                continue
            kept.append(m)
            budget -= cost
        if dropped == 0:
            return
        kept.reverse()
        self._messages = [
            {
                "role": "system",
                "content": f"[上下文已压缩：丢弃 {dropped} 条早期消息。继续基于最近的内容工作，不要假设被省略的部分。]",
            },
            *kept,
        ]
        _logger.warning(
            "[%s] context compacted: dropped %d msgs (%d chars)",
            self.name,
            dropped,
            total,
        )

    def _record_usage(self, usage: dict | None):
        """Accumulate token usage per agent into the blackboard."""
        if not usage or self.blackboard is None:
            return
        log = self.blackboard.setdefault("usage_log", {})
        entry = log.setdefault(
            self.name, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        )
        entry["prompt_tokens"] += usage.get("prompt_tokens", 0)
        entry["completion_tokens"] += usage.get("completion_tokens", 0)
        entry["calls"] += 1

    def receive(self, llm_response: dict):
        """Convert another agent's output into a readable user message.

        If *data* has a ``message`` key it becomes the primary text;
        any remaining keys are appended as structured context."""
        if "message" in llm_response:
            content = llm_response["message"]
            other = {k: v for k, v in llm_response.items() if k != "message"}
            if other:
                content += "\n---\n" + _json.dumps(other, ensure_ascii=False, indent=2)
        else:
            content = _json.dumps(llm_response, ensure_ascii=False, indent=2)
        self._messages.append({"role": "user", "content": content})

    WRITE_TOOLS = frozenset({"write_file", "todo_write"})

    def _run_tools(self, tool_calls: list[dict]):
        from concurrent.futures import ThreadPoolExecutor
        from core.events import Events, HookRegistry
        from codegen.infrastructure.tools.registry import runtime, set_runtime

        rt = runtime()
        executed: list[dict] = []
        reuse_of: dict[str, str] = {}
        seen: dict[tuple[str, str], str] = {}
        for tc in tool_calls:
            key = (
                tc["name"],
                _json.dumps(tc.get("args", {}), sort_keys=True, default=str),
            )
            if key in seen:
                reuse_of[tc["id"]] = seen[key]
            else:
                seen[key] = tc["id"]
                executed.append(tc)
        for tc in executed:
            HookRegistry.trigger(
                Events.TOOL_PRE_USE,
                tool=tc["name"],
                args=tc.get("args", {}),
                agent=self.name,
            )

        def run_one(tc: dict) -> tuple[str, str]:
            set_runtime(rt)
            return (tc["id"], rt.execute(tc["name"], tc.get("args", {})))

        readonly = [tc for tc in executed if tc["name"] not in self.WRITE_TOOLS]
        writes = [tc for tc in executed if tc["name"] in self.WRITE_TOOLS]
        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(len(readonly), 4))) as pool:
            for tid, result in pool.map(run_one, readonly):
                results[tid] = result
        for tc in writes:
            rt.current_agent = self.name
            results[tc["id"]] = rt.execute(tc["name"], tc.get("args", {}))
        for tid, src in reuse_of.items():
            results[tid] = results[src]
        for tc in tool_calls:
            HookRegistry.trigger(
                Events.TOOL_POST_USE,
                tool=tc["name"],
                result_preview=results[tc["id"]][:500],
                agent=self.name,
            )
            self._messages.append(
                {"role": "tool", "tool_call_id": tc["id"], "content": results[tc["id"]]}
            )

    @staticmethod
    def _parse(text: str) -> dict:
        """兼容别名 → core.text.parse_llm_output（已搬迁，
        把外部调用方改为直接 import core.text 后移除）。"""
        return parse_llm_output(text)
