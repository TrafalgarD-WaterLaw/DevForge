"""JSON config loader — shared data access layer."""
import json
import logging
from pathlib import Path
from typing import Any, Dict

_log = logging.getLogger(__name__)

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

def _load_json(name: str) -> Dict[str, Any]:
    path = _project_root() / "configs" / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# 已知配置键清单 —— 拼错 key 会静默用默认值，启动即告警便于发现
_KNOWN_PIPELINE_KEYS = {
    "pipeline", "llm", "phase_retries", "tools",
    "ask_choice_timeout", "review_timeout", "pm",
    "quality_gate_max_loops", "verification_rounds",
    "quality_gate_min_coverage", "auth_token", "memory",
}
_KNOWN_LLM_KEYS = {
    "model", "base_url", "api_key", "max_retries", "max_tokens",
    "max_context_chars", "token_budget", "token_budget_stop",
    "max_tool_rounds", "disable_thinking", "timeout",
}
_KNOWN_TOOLS_KEYS = {"sandbox", "max_tool_result_chars", "max_events"}
_warned_keys: set[str] = set()

def _warn_unknown(cfg: dict, known: set[str], section: str):
    """对未知配置键打一次告警（避免每次加载都刷日志）。"""
    for key in cfg:
        if key not in known and key not in _warned_keys:
            _warned_keys.add(key)
            _log.warning("configs/default.json: 未知配置键 %r%s",
                         key, f"（{section}）" if section else "")

# ── Pipeline config ──────────────────────────────────

def load_pipeline_config(name: str = "default") -> Dict[str, Any]:
    """Load a named pipeline config (e.g. ``"default"`` → configs/default.json).

    找不到指定配置名时回退 ``default`` 并告警（P1-4：自定义 pipeline 名
    不再让阶段直接崩溃）。

    非 default 的命名配置是 default 的增量覆盖：iterate.json 只写
    pipeline 段，直接使用会让 llm/tools/memory 全部回落默认值，
    与 default.json 的调参（max_tool_rounds 等）脱节。
    """
    try:
        cfg = _load_json(name)
    except FileNotFoundError:
        _log.warning("pipeline config %r 不存在 — 回退 default", name)
        cfg = _load_json("default")
    if name != "default":
        base = _load_json("default")
        merged = {**base, **cfg}
        # 字典段深合并：命名配置只覆盖写到的键
        for section in ("llm", "tools", "memory", "pm"):
            if isinstance(base.get(section), dict) \
                    or isinstance(cfg.get(section), dict):
                merged[section] = {
                    **dict(base.get(section) or {}),
                    **dict(cfg.get(section) or {}),
                }
        cfg = merged
    _warn_unknown(cfg, _KNOWN_PIPELINE_KEYS, "")
    _warn_unknown(cfg.get("llm", {}), _KNOWN_LLM_KEYS, "llm")
    _warn_unknown(cfg.get("tools", {}), _KNOWN_TOOLS_KEYS, "tools")
    return cfg

# ── Phase config ─────────────────────────────────────

_phases_cache = None

def load_phases_config() -> dict:
    """Load phases.json (cached) and validate ``roles`` ⊆ ``agents``."""
    global _phases_cache
    if _phases_cache is None:
        cfg = _load_json("phases")
        for name, phase in cfg.items():
            declared = set(phase.get("roles", []))
            configured = set(phase.get("agents", {}).keys())
            missing = declared - configured
            extra = configured - declared
            if missing:
                raise ValueError(
                    f"Phase '{name}': roles {sorted(missing)} declared but "
                    f"not configured in agents.")
            if extra:
                raise ValueError(
                    f"Phase '{name}': agents {sorted(extra)} exist but "
                    f"not declared in roles.")
        _phases_cache = cfg
    return _phases_cache

def phase_config(phase_name: str, agent_key: str) -> dict:
    """Return ``{tools, prompt, schema}`` for one agent in one phase."""
    return load_phases_config()[phase_name]["agents"][agent_key]

# ── Role config ──────────────────────────────────────

_roles_cache = None

def load_roles_config() -> dict:
    """Load roles.json (cached)."""
    global _roles_cache
    if _roles_cache is None:
        _roles_cache = _load_json("roles")
    return _roles_cache

# ── System messages（提示词不硬编码在代码里）───────────

_sys_messages_cache: dict | None = None

def load_sys_message(key: str, **fmt) -> str:
    """Load a system message from configs/sys_messages.json (cached).

    ``{placeholder}`` in the message is filled from *fmt* (str.format).
    提示词统一放配置文件，代码只引用 key —— 改措辞不用动代码。
    """
    global _sys_messages_cache
    if _sys_messages_cache is None:
        try:
            _sys_messages_cache = _load_json("sys_messages")
        except FileNotFoundError:
            _sys_messages_cache = {}
    msg = _sys_messages_cache.get(key, "")
    return msg.format(**fmt) if fmt else msg
