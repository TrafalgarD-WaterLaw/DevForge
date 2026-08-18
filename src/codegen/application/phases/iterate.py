"""Iterate — 在已有项目上做增量修改。

用户对已交付项目提出修改意见 → 迭代工程师分析影响面 → 只改相关文件 →
跑回归测试 → 输出修改摘要。与"重新生成"的区别：代码层复用，diff 级变更。
"""

import json
from core.events import Events, HookRegistry
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase

@register_phase
class Iterate(Phase):
    """Incremental change on an existing project."""

    def run(self):
        directory = self.blackboard.get("directory", "")
        fb = self.blackboard.get("user_feedback", []) or []
        if fb:
            feedback = "\n".join((str(f) for f in fb))
            self.blackboard["user_feedback"] = []
        else:
            feedback = self.blackboard.get("task_prompt", "").strip()
        requirements = self.blackboard.get("requirements", {})
        self.blackboard.reload_codes(directory)
        before = dict(self.blackboard.codes)
        engineer = self.agent("iteration_engineer")
        engineer.react(
            self.prompt(
                "iteration_engineer",
                feedback=feedback,
                requirements=json.dumps(requirements, ensure_ascii=False),
                files=self.files,
            ),
            stream=False,
        )
        self.blackboard.reload_codes(directory)
        changed = self._changed_since(before)
        removed = self._removed_since(before)
        has_bugs, test_output, _infra = self._run_tests_here()
        # 回归失败 ≠ 交付：反馈 iteration_engineer 修复源码（≤1 次）——
        # 之前只报告不修，用户追加需求改挂了现有功能也照样交付
        if has_bugs and not _infra:
            print("  [Iterate] 回归测试失败 — 反馈迭代工程师修复", flush=True)
            from core.config import load_sys_message
            engineer.react(load_sys_message(
                "iteration_engineer_regression", output=test_output[:1500]))
            self.blackboard.reload_codes(directory)
            changed = self._changed_since(before)
            removed = self._removed_since(before)
            has_bugs, test_output, _infra = self._run_tests_here()
        # 人工审阅：拒绝 → 携带反馈重做 1 次（用户拒绝 ≠ 交付，此前
        # iterate_rejected 写入后无人消费，拒绝被静默忽略）
        if changed:
            self._request_review(changed)
        rejected = self.blackboard.get("iterate_rejected", "")
        if rejected:
            self.blackboard["iterate_rejected"] = ""
            print("  [Iterate] 用户拒绝迭代改动 — 携带反馈重做 1 次", flush=True)
            from core.config import load_sys_message
            engineer.react(load_sys_message(
                "iteration_engineer_rejected", feedback=rejected))
            self.blackboard.reload_codes(directory)
            changed = self._changed_since(before)
            removed = self._removed_since(before)
            has_bugs, test_output, _infra = self._run_tests_here()
            if has_bugs and not _infra:
                engineer.react(load_sys_message(
                    "iteration_engineer_regression", output=test_output[:1500]))
                self.blackboard.reload_codes(directory)
                has_bugs, test_output, _infra = self._run_tests_here()
        summary = {
            "message": f"迭代完成: 修改 {len(changed)} 个文件"
            + (f"，删除 {len(removed)} 个" if removed else "")
            + ("；⚠️ 回归测试未通过" if has_bugs else "；回归测试通过 ✅"),
            "changed": changed,
            "test_output": test_output[:400],
        }
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="Iterate",
            content=json.dumps(summary, ensure_ascii=False),
            turn=0,
        )
        self.blackboard["iterate_changed"] = changed
        try:
            from codegen.application.phases.coding import Coding
            from codegen.application.phases.documentation import Documentation

            Coding(self.blackboard)._auto_install(directory)
            Documentation(self.blackboard).run()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Iterate: 文档同步失败（不影响代码交付）"
            )

    def _changed_since(self, before: dict) -> list:
        return [f for f, c in self.blackboard.codes.items() if before.get(f) != c]

    def _removed_since(self, before: dict) -> list:
        return [f for f in before if f not in self.blackboard.codes]

    def _run_tests_here(self):
        """跑回归测试（返回 (has_bugs, output, infra_failed)）。"""
        from codegen.application.phases.verification import Verification
        return Verification(self.blackboard)._run_tests()

    def _request_review(self, changed: list):
        """迭代改动的 diff 送人工审阅（headless 无 ws 自动通过）。"""
        import difflib

        diff_lines = []
        for f in changed[:10]:
            new = (self.blackboard.codes.get(f) or "").splitlines()
            diff_lines.append(f"--- {f}")
            diff_lines.append(f"+++ {f}")
            diff_lines.append(f"[迭代修改 — 完整新内容]")
            diff_lines.extend([f"+ {line}" for line in new[:80]])
        from serving.application.ws_manager import ask_approval

        run_id = self.blackboard.get("_run_id", "")
        approved = ask_approval(
            run_id, {"files": changed[:10], "diff": "\n".join(diff_lines)[:8000]}
        )
        HookRegistry.trigger("review_decision", approved=approved, files=changed[:10])
        if not approved:
            self.blackboard["iterate_rejected"] = (
                f"迭代修改被用户拒绝（{', '.join(changed[:5])}）— 需重新处理"
            )
