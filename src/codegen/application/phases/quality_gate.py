"""QualityGate — verify that generated code covers user requirements.

A3/B5: 先跑自动化测试（e2e 行为证据）→ 测试输出注入 inspector prompt →
测试失败或覆盖率低于阈值（quality_gate_min_coverage）时追加 feature 项
并降级 verdict（PASS→WARN）—— 质检不再只看"文本里有没有提到功能"。
Q1: 平台契约硬门禁 —— AST 复查契约 exports，缺口直接 FAIL（inspector
按需求 features 打分，模块间契约缺口它看不到，平台兜底）。
"""

import json
import logging
import re
from core.config import load_pipeline_config
from core.events import HookRegistry
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from codegen.domain.validate import validated_react

_log = logging.getLogger(__name__)

@register_phase
class QualityGate(Phase):
    """Final quality check — compares requirements to generated code."""

    def run(self):
        req = self.blackboard.get("requirements", {})
        if not req or not self.blackboard.codes:
            # 无需求或无代码 → 显式 FAIL（此前静默 return，verdict 为空，
            # 回跳/失败标记全部失效 —— Coding 阶段"未生成任何代码文件"
            # 被无声放过。platform 项会触发回跳 → 二跳升级 Design 重设计）
            notes = ("缺少需求定义（PM 未产出 summary）" if not req
                     else "未生成任何代码文件（coder 输出缺失）")
            result = {
                "verdict": "FAIL",
                "score": 0,
                "features": [{
                    "name": "代码产出",
                    "status": "NO",
                    "notes": notes,
                    "source": "platform",
                }],
            }
            self.blackboard["quality_gate"] = result
            HookRegistry.trigger("quality_gate", data=result)
            return
        # 质检阶段静默工具事件（inspector read_file 刷屏）
        self.blackboard["_quiet_tools"] = True
        try:
            has_bugs, test_output, infra_failed = self._run_e2e_tests()
            result = self._inspect(req, test_output)
            result = self._apply_evidence_gates(
                result, has_bugs and not infra_failed, test_output)
            result = self._apply_platform_gates(result)
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

    def _apply_platform_gates(self, result: dict) -> dict:
        """平台契约硬门禁：AST 复查契约 exports vs 源码，缺口 → 追加
        source="platform" 的 feature 项 + verdict 降为 FAIL。

        inspector 按需求 features 打分，模块间契约缺口（设计声明但源码
        缺失的导出）它看不到 —— 平台在此兜底。pipeline 的回跳 missing
        只排除 evidence 项，platform 项会触发回跳/升级（同缺口二跳
        Design 重新设计），直到契约完整才交付。
        """
        directory = self.blackboard.get("directory", "")
        modules = self.blackboard.get("modules", [])
        gaps = []
        if directory and modules:
            try:
                from codegen.application.phases.coding import contract_gap_check
                gaps = contract_gap_check(directory, modules)
            except Exception:
                _log.exception("Platform contract gate failed")
        if not gaps:
            return result
        missing_names = "、".join(
            g.strip().lstrip("- ").split(" 契约声明但源码未定义")[0]
            for g in gaps[:5])
        features = list(result.get("features") or [])
        features.append({
            "name": "模块契约完整性",
            "status": "NO",
            "notes": f"契约声明但源码缺失: {missing_names}"
                     + (f" 等 {len(gaps)} 处" if len(gaps) > 5 else ""),
            "source": "platform",
        })
        result["features"] = features
        result["verdict"] = "FAIL"
        print(f"  [QualityGate] 平台契约检查：{len(gaps)} 处缺口 → FAIL",
              flush=True)
        return result

    @staticmethod
    def _parse_coverage(output: str) -> int | None:
        """从 pytest-cov 输出提取总覆盖率（TOTAL ... 42%）。"""
        m = re.search("TOTAL\\s+\\d+\\s+\\d+\\s+(\\d{1,3})%", output)
        if m:
            return int(m.group(1))
        m = re.search("Coverage:\\s*([\\d.]+)%", output)
        return int(round(float(m.group(1)))) if m else None
