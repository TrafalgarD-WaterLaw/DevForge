"""Phase base class — convenience properties, agent factory, prompt rendering."""
import json
import logging

from codegen.domain.agent import Agent
from core.config import _project_root, load_phases_config, phase_config

# src/configs → FileNotFoundError）→ 统一走 core.config._project_root()
_CONFIGS_DIR = _project_root() / "configs"
_log = logging.getLogger(__name__)

class Phase:
    """Convenience base for pipeline phases."""

    def __init__(self, blackboard):
        self.blackboard = blackboard

    @property
    def codes(self) -> str:
        return self.blackboard.get_codes()

    @property
    def files(self) -> str:
        return self.blackboard.file_list()

    # ── Agent factory ───────────────────────────────────

    def agent(self, key: str, *, tag: str = "") -> Agent:
        """Create an Agent. *key* must be declared in this phase's
        ``roles`` list in phases.json."""
        return Agent(key, self.blackboard, tag=tag)

    # ── Prompt rendering ────────────────────────────────

    def prompt(self, key: str, **extra) -> str:
        """Render a prompt template for *key*.  Auto-injects relevant
        memories when ``{memories}`` is present in the template."""
        phase_name = type(self).__name__
        prompt_path = phase_config(phase_name, key).get("prompt", "")
        if not prompt_path:
            return ""
        raw = (_CONFIGS_DIR / prompt_path).read_text(encoding="utf-8")
        extra.setdefault("task", self.blackboard.get("task_prompt", ""))
        extra.setdefault("description", self.blackboard.get("task_description", ""))
        schema = self.schema(key)
        if schema:
            extra.setdefault("schema", json.dumps(schema))
        # Memory injection
        if "{memories}" in raw:
            extra.setdefault("memories", self._load_memories(key, **extra))
        try:
            return raw.format(**extra)
        except KeyError as exc:
            # 调试：缺占位符时的完整堆栈（含 raw 与 extra 键）
            _log.exception(
                "Prompt format failed %s/%s: missing key %s — extra keys: %s",
                phase_name, key, exc, sorted(extra.keys()))
            raise

    def _load_memories(self, agent_key: str, **extra) -> str:
        """Retrieve relevant memories for *agent_key*.
        CTO → phase-level summaries.  Coder → function-level source code."""
        try:
            from memory.infrastructure.chroma_store import (
                MemoryStore,
                format_function_memories,
                format_memories,
            )
            directory = self.blackboard.get("directory", "")
            if not directory:
                return ""
            # blackboard 可指定隔离记忆库（benchmark 不污染生产记忆）
            store = MemoryStore(
                chroma_dir=self.blackboard.get("_memory_dir", ""))

            if agent_key == "coder":
                query = f"{extra.get('module_name', '')} {extra.get('module_desc', '')}"
                if not query.strip():
                    return ""
                _log.info("[Memory] %s recalling...", extra.get('module_name', agent_key))
                entries = store.recall_functions(query, n=3)
                result = format_function_memories(entries)
                if result:
                    print(f"  [Memory] → coder '{extra.get('module_name','')}' got {len([e for e in entries if 'verified' in e.get('tags',[])])} verified implementations", flush=True)
                return result

            if agent_key == "chief_technology_officer":
                # 优先用澄清后的需求摘要（字段拼接），原始 task 兜底 ——
                # 用户一句话的信号弱，检索命中率低
                query = (extra.get("description") or extra.get("task")
                         or extra.get("task_prompt") or "")
                if not query:
                    return ""
                entries = store.recall_phases(query)
                result = format_memories(entries)
                if result:
                    print(f"  [Memory] → CTO got {len(entries)} past experiences", flush=True)
                return result

        except Exception:
            # 召回失败不能静默 —— 打日志便于排查
            _log.exception("Failed to load memories for %s", agent_key)
        return ""

    def schema(self, key: str) -> dict:
        """Return the JSON Schema for *key*, or ``{}``."""
        phase_name = type(self).__name__
        path = phase_config(phase_name, key).get("schema", "")
        if not path:
            return {}
        with open(_CONFIGS_DIR / path, encoding="utf-8") as f:
            return json.load(f)

    # ── Subclass contract ───────────────────────────────

    def run(self):
        raise NotImplementedError(f"{type(self).__name__}.run()")
