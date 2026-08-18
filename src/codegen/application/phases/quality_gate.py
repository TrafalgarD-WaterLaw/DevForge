"""QualityGate — verify that generated code covers user requirements.

A3/B5: 先跑自动化测试（e2e 行为证据）→ 测试输出注入 inspector prompt →
测试失败或覆盖率低于阈值（quality_gate_min_coverage）时追加 feature 项
并降级 verdict（PASS→WARN）—— 质检不再只看"文本里有没有提到功能"。
"""

import json
import re
from core.config import load_pipeline_config
from core.events import HookRegistry
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from codegen.domain.validate import validated_react

@register_phase
class QualityGate(Phase):
    """Final quality check — compares requirements to generated code."""

    def run(self):
        req = self.blackboard.get("requirements", {})
        if not req or not self.blackboard.codes:
            return
        # 质检阶段静默工具事件（inspector read_file 刷屏）
        self.blackboard["_quiet_tools"] = True
        try:
            has_bugs, test_output, infra_failed = self._run_e2e_tests()
            result = self._inspect(req, test_output)
            result = self._apply_evidence_gates(
                result, has_bugs and not infra_failed, test_output)
        finally:
            self.blackboard["_quiet_tools"] = False
        self.blackboard["quality_gate"] = result
        HookRegistry.trigger("quality_gate", data=result)

    # ── 子步骤（二轮拆分：run 37 行 → 编排 + 子步骤）──

    def _run_e2e_tests(self) -> tuple[bool, str, bool]:
        """A3: 真实跑测试作为行为证据（无目录则跳过）。

        Returns (has_bugs, test_output, infra_failed)。
        """
        directory = self.blackboard.get("directory", "")
        if not directory:
            return False, "", False
        from codegen.application.phases.verification import run_project_tests
        from codegen.application.process import run_process

        return run_project_tests(
            directory,
            run_process,
            entry_point=self.blackboard.get("entry_point", ""),
        )

    def _inspect(self, req: dict, test_output: str) -> dict:
        inspector = self.agent("inspector")
        return validated_react(
            inspector,
            self.prompt(
                "inspector",
                requirements=json.dumps(req, indent=2),
                code_files=self.files,
                test_output=test_output[:2000] or "(no tests run)",
            ),
            self.schema("inspector"),
        )

    def _apply_evidence_gates(
        self, result: dict, has_bugs: bool, test_output: str
    ) -> dict:
        """测试失败 / 覆盖率不达标 → 追加 feature 项 + verdict 降级。

        追加项带 source="evidence" 标记 —— pipeline 回跳条件排除这类项：
        测试框架参数问题修复者无法解决，回跳只会白烧三轮验证。
        """
        verdict = result.get("verdict", "WARN")
        features = list(result.get("features") or [])
        if has_bugs:
            features.append(
                {
                    "name": "自动化测试通过",
                    "status": "NO",
                    "notes": "测试执行失败: " + test_output.replace("\n", " ")[:80],
                    "source": "evidence",
                }
            )
            verdict = "FAIL" if verdict == "FAIL" else "WARN"
        cov = self._parse_coverage(test_output)
        min_cov = int(load_pipeline_config().get("quality_gate_min_coverage", 0) or 0)
        if cov is not None and min_cov > 0 and (cov < min_cov):
            features.append(
                {
                    "name": f"测试覆盖率 ≥ {min_cov}%",
                    "status": "PARTIAL",
                    "notes": f"实际覆盖率 {cov}%",
                    "source": "evidence",
                }
            )
            if verdict == "PASS":
                verdict = "WARN"
        result["features"] = features
        result["verdict"] = verdict
        return result

    @staticmethod
    def _parse_coverage(output: str) -> int | None:
        """从 pytest-cov 输出提取总覆盖率（TOTAL ... 42%）。"""
        m = re.search("TOTAL\\s+\\d+\\s+\\d+\\s+(\\d{1,3})%", output)
        if m:
            return int(m.group(1))
        m = re.search("Coverage:\\s*([\\d.]+)%", output)
        return int(round(float(m.group(1)))) if m else None
