"""LLMClient — pure HTTP transport: send messages, return text."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time

import openai

_logger = logging.getLogger(__name__)

# 完全相同的请求（同模型同消息同参数）短 TTL 缓存 —— 重试/重跑不再
# 重复计费。注意：消息历史含工具结果，内容变化则 key 变化，不会误命中。
_RESPONSE_CACHE: dict[str, tuple[float, dict]] = {}
_RESPONSE_CACHE_LOCK = threading.Lock()
_RESPONSE_CACHE_TTL = 600.0      # 10 分钟
_RESPONSE_CACHE_MAX = 64

class LLMClient:
    """Pure HTTP transport — stateless."""

    def __init__(self):
        from core.config import load_pipeline_config
        cfg = load_pipeline_config().get("llm", {})
        api_key = cfg.get("api_key") or os.environ.get(
            'DEEPSEEK_API_KEY', os.environ.get('OPENAI_API_KEY', ''))
        self._model_name = cfg.get("model", "deepseek-v4-flash")
        self.model = self._model_name
        # 可选输出上限（默认模型自带）。reviewer 长 JSON 默认 4096 会被截断，
        # 配置 8192 大幅减少 finish_reason=length。
        self._max_tokens = cfg.get("max_tokens")
        # thinking 禁用是 DeepSeek 专属的 extra_body —— 换 OpenAI 兼容模型
        # 会 400；仅当模型名/地址指向 DeepSeek 时发送
        self._disable_thinking = (
            bool(cfg.get("disable_thinking", True))
            and "deepseek" in str(cfg.get("base_url", "")).lower())
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=cfg.get("base_url", "https://api.deepseek.com"),
            max_retries=cfg.get("max_retries", 3),
            timeout=cfg.get("timeout", 600),
        )

    def call(self, messages, tools=None, *, json_mode: bool = False,
             max_tokens: int | None = None):
        """HTTP POST → return ``{"content":, "tool_calls":, "finish_reason":, "usage":}``.

        *tools* enables native function calling.
        *json_mode* sets ``response_format: json_object`` (opt-in).
        *max_tokens* overrides the configured output cap (``None`` = config).
        *usage* is ``{prompt_tokens, completion_tokens}`` or ``None``.
        """
        params = self._build_params(messages, tools, json_mode, max_tokens)

        # 缓存：完全相同的请求直接复用响应（节省 token 成本）
        cached_hit = self._lookup_cache(params)
        if cached_hit is not None:
            return cached_hit

        response = self._client.chat.completions.create(**params)
        if response.choices:
            # 缓存结果（截断/工具调用等完整响应原样保存；usage 置 None 防重复计数）
            self._store_cache(params, response.choices[0])
        return self._parse_response(response)

    # ── 子步骤（拆分：call() 88 行 → 编排 + 子步骤）──

    def _build_params(self, messages, tools, json_mode: bool,
                      max_tokens: int | None) -> dict:
        params: dict = {
            "model": self._model_name,
            "messages": messages,
        }
        mt = max_tokens if max_tokens is not None else self._max_tokens
        if mt:
            params["max_tokens"] = mt
        if self._disable_thinking:
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        if tools:
            params["tools"] = tools
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        return params

    def _lookup_cache(self, params: dict) -> dict | None:
        cache_key = hashlib.sha1(json.dumps(
            params, ensure_ascii=False, sort_keys=True,
            default=str).encode("utf-8")).hexdigest()
        with _RESPONSE_CACHE_LOCK:
            hit = _RESPONSE_CACHE.get(cache_key)
            if hit and time.time() - hit[0] < _RESPONSE_CACHE_TTL:
                _logger.info("LLM cache hit (%d keys)", len(_RESPONSE_CACHE))
                return hit[1]
        return None

    def _store_cache(self, params: dict, choice) -> None:
        cache_key = hashlib.sha1(json.dumps(
            params, ensure_ascii=False, sort_keys=True,
            default=str).encode("utf-8")).hexdigest()
        cached = {
            "content": getattr(choice.message, "content", "") or "",
            "tool_calls": self._extract_tool_calls(choice.message),
            "finish_reason": choice.finish_reason,
            "usage": None,
        }
        with _RESPONSE_CACHE_LOCK:
            if len(_RESPONSE_CACHE) >= _RESPONSE_CACHE_MAX:
                _RESPONSE_CACHE.clear()
            _RESPONSE_CACHE[cache_key] = (time.time(), cached)

    def _extract_tool_calls(self, msg) -> list | None:
        if not getattr(msg, "tool_calls", None):
            return None
        tool_calls = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as exc:
                # 模型偶尔输出截断/损坏的工具参数 —— 不崩溃，标记为 None
                # 由 Agent 层用反馈消息让模型重出（见 agent.react 的 malformed 分支）
                _logger.warning(
                    "Malformed tool args for %s: %s — %.80r",
                    tc.function.name, exc, tc.function.arguments)
                args = None
            tool_calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        return tool_calls

    def _parse_response(self, response) -> dict:
        choice = response.choices[0] if response.choices else None
        if choice is None:
            return {"content": "", "tool_calls": None, "finish_reason": None,
                    "usage": None}
        msg = choice.message
        usage = None
        if getattr(response, "usage", None):
            # DeepSeek 自动前缀缓存：usage 返回命中/未命中的 prompt token
            # 分解（prompt_tokens = hit + miss）。不记录就永远无法度量
            # 缓存命中率、无法发现"缓存杀手"（易变前缀/前缀被改写的消息）
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "prompt_cache_hit_tokens": getattr(
                    response.usage, "prompt_cache_hit_tokens", 0) or 0,
                "prompt_cache_miss_tokens": getattr(
                    response.usage, "prompt_cache_miss_tokens", 0) or 0,
            }
        return {
            "content": msg.content or "",
            "tool_calls": self._extract_tool_calls(msg),
            "finish_reason": choice.finish_reason,
            "usage": usage,
        }

    def stream_call(self, messages, tools=None, *, json_mode: bool = False,
                    on_delta=None, max_tokens: int | None = None):
        """Streaming completion — *on_delta(text)* per chunk, returns full text.

        仅用于非 json_mode 的文本输出（CTO/CPO 讨论、coder 输出）。
        """
        params: dict = {
            "model": self._model_name,
            "messages": messages,
            "stream": True,
            # 流式收尾 chunk 返回 usage —— 设计讨论/文档阶段走流式，
            # 不请求 include_usage 的话这两个 agent 成本恒为 0
            "stream_options": {"include_usage": True},
        }
        self.last_stream_usage = None
        mt = max_tokens if max_tokens is not None else self._max_tokens
        if mt:
            params["max_tokens"] = mt
        if self._disable_thinking:
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        if tools:
            params["tools"] = tools
        if json_mode:
            params["response_format"] = {"type": "json_object"}

        stream = self._client.chat.completions.create(**params)
        parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                # include_usage 时最后一个 chunk choices 为空、usage 有值
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    self.last_stream_usage = {
                        "prompt_tokens": usage.prompt_tokens or 0,
                        "completion_tokens": usage.completion_tokens or 0,
                        "prompt_cache_hit_tokens": getattr(
                            usage, "prompt_cache_hit_tokens", 0) or 0,
                        "prompt_cache_miss_tokens": getattr(
                            usage, "prompt_cache_miss_tokens", 0) or 0,
                    }
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                parts.append(text)
                if on_delta:
                    on_delta(text)
        return "".join(parts)
