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
        # O10: 本阶段创建的 agent（阶段结束归档对话历史用）
        self._created_agents: list = []

    @property
    def codes(self) -> str:
        return self.blackboard.get_codes()

    @property
    def files(self) -> str:
        return self.blackboard.file_list()

    # ── Agent factory ───────────────────────────────────

    def agent(self, key: str, *, tag: str = "") -> Agent:
        """Create an Agent. *key* must be declared in this phase's
        ``roles`` list in phases.json.

        O10: 同阶段重跑（retry/回跳/断点恢复）时自动恢复磁盘归档的
        对话历史 —— 重跑的 agent 不再"失忆"（此前只有 system prompt
        + 新任务消息）。归档只作上下文，文件内容以磁盘为准。"""
        agent = Agent(key, self.blackboard, tag=tag)
        hist = self._load_archived_history(agent.name)
        if hist:
            agent.restore_history(hist)
        self._created_agents.append(agent)
        return agent

    def _load_archived_history(self, agent_name: str) -> dict | None:
        """读取本阶段该 agent 的历史归档（.devforge/agent_history/<phase>/）。"""
        try:
            import os
            from codegen.application.chat_chain import ARTIFACT_DIR
            directory = self.blackboard.get("directory", "")
            if not directory:
                return None
            path = os.path.join(
                directory, ARTIFACT_DIR, "agent_history",
                type(self).__name__, f"{agent_name}.json")
            if not os.path.exists(path):
                return None
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

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
        CTO → phase-level summaries.  Coder/Tester → function-level verified
        implementations.  Fixer → FixPattern 修复模式（错误签名召回）。"""
        try:
            from memory.infrastructure.chroma_store import MemoryStore
            from memory.interfaces.prompt_formatter import (
                format_fix_memories,
                format_function_memories,
                format_memories,
            )
            directory = self.blackboard.get("directory", "")
            if not directory:
                return ""
            # blackboard 可指定隔离记忆库（benchmark 不污染生产记忆）
            store = MemoryStore(
                chroma_dir=self.blackboard.get("_memory_dir", ""))

            if agent_key in ("coder", "tester"):
                if agent_key == "coder":
                    query = f"{extra.get('module_name', '')} {extra.get('module_desc', '')}"
                else:
                    # tester：按模块契约/代码内容召回已验证实现（写测试参考）
                    query = (extra.get("contracts", "") or extra.get("codes", ""))[:200]
                if not query.strip():
                    return ""
                _log.info("[Memory] %s recalling...", agent_key)
                entries = store.recall_functions(query, n=3)
                result = format_function_memories(entries)
                if result:
                    print(f"  [Memory] → {agent_key} got verified implementations", flush=True)
                return result

            if agent_key == "fixer":
                # M1：修复模式召回 —— query 从测试输出提取错误签名
                from memory.domain.extract import _extract_error_signature
                query = _extract_error_signature(
                    extra.get("test_output", "") or "")
                if not query:
                    return ""
                _log.info("[Memory] fixer recalling fixes for %s...", query)
                entries = store.recall_fix_patterns(query, n=2)
                result = format_fix_memories(entries)
                if result:
                    print(f"  [Memory] → fixer got {len(entries)} fix patterns", flush=True)
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

    def _phase_over_budget(self) -> bool:
        """当前阶段 token 消耗是否超过 pipeline_spec 阶段预算。

        预算由 pipeline 在阶段开始前写入 blackboard
        （``_phase_budget`` / ``_phase_budget_start``）。阶段实现
        （Verification 修复轮等）在循环边界检查：超预算提前收尾、
        带当前状态进质检降级交付，而不是无限修到撞全局熔断
        （bench 实况：阶段预算此前只发统计事件不拦截，Verification
        可烧到 50 万+ token 才被 90 万全局熔断整任务陪葬）。
        """
        budget = int(self.blackboard.get("_phase_budget", 0) or 0)
        if budget <= 0:
            return False
        start = int(self.blackboard.get("_phase_budget_start", 0) or 0)
        usage = self.blackboard.get("usage_log", {}) or {}
        used = sum(e.get("prompt_tokens", 0) for e in usage.values()) - start
        return used > budget

    # ── Subclass contract ───────────────────────────────

    def run(self):
        raise NotImplementedError(f"{type(self).__name__}.run()")
