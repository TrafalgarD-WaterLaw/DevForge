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
            # 行为豁免条件：测试真实跑过且全过（"All tests passed." 等）。
            # 契约缺口（导出名与设计不一致）但行为正确 → 降级 WARN 交付，
            # 不再 FAIL 回跳空转（ledger 实测：golden 通过但门禁按导出名
            # 比对 FAIL，回跳升级烧到 96.5 万 token）。接线缺口不豁免
            #（CLI 跑不起来是真问题，测试 import 函数发现不了）。
            tests_ok = (
                not has_bugs and not infra_failed
                and "passed" in (test_output or "").lower())
            result = self._apply_platform_gates(result, tests_ok=tests_ok)
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

    def _apply_platform_gates(self, result: dict, *, tests_ok: bool = False
                              ) -> dict:
        """平台契约硬门禁：AST 复查契约 exports vs 源码，缺口 → 追加
        source="platform" 的 feature 项 + verdict 降为 FAIL。

        inspector 按需求 features 打分，模块间契约缺口（设计声明但源码
        缺失的导出）它看不到 —— 平台在此兜底。pipeline 的回跳 missing
        只排除 evidence 项，platform 项会触发回跳/升级（同缺口二跳
        Design 重新设计），直到契约完整才交付。

        *tests_ok*（测试真实跑过且全过）时**行为豁免**：契约缺口
        （形式不符但行为正确）降级 WARN 交付 —— status 用 "WARN"，
        pipeline 回跳谓词只认 NO/PARTIAL，不会空转；入口接线缺口
        不豁免（CLI 跑不起来是真问题，测试 import 函数发现不了）。
        """
        directory = self.blackboard.get("directory", "")
        modules = self.blackboard.get("modules", [])
        gaps = []
        gate_error = ""
        if directory and modules:
            try:
                from codegen.application.phases.coding import contract_gap_check
                gaps = contract_gap_check(directory, modules)
            except Exception:
                _log.exception("Platform contract gate failed")
                # 硬门禁 FAIL-closed：检查器自身出错不能静默放行
                #（此前 except 吞掉异常 → gaps 保持 [] → 门禁无声通过，
                # 坏交付物带着"契约完整的假象交付）
                gate_error = "平台契约检查异常（无法确认契约完整，视为缺口）"
        # 入口接线检查（独立于契约）：质检 PASS 但 CLI 无输出 = 缺
        # `if __name__ == "__main__"` 接线 —— 契约门禁查不到这一类
        #（unit_converter / file_organizer 实测中招）
        wiring_gaps = []
        if directory:
            try:
                from codegen.application.phases.coding import entry_wiring_check
                wiring_gaps = entry_wiring_check(directory)
            except Exception:
                _log.exception("Platform entry-wiring gate failed")
                wiring_gaps = ["平台入口接线检查异常（视为缺口）"]
        if not gaps and not wiring_gaps and not gate_error:
            return result
        features = list(result.get("features") or [])
        # 行为豁免判定：仅契约缺口、无接线缺口、无检查异常、无其他
        # 未达标项（inspector 判的功能缺口/证据失败仍按原逻辑回跳）
        other_missing = [
            f for f in features
            if isinstance(f, dict) and f.get("status") in ("NO", "PARTIAL")
            and f.get("source") != "platform"]
        if (gaps and not wiring_gaps and not gate_error
                and tests_ok and not other_missing):
            missing_names = "、".join(
                g.strip().lstrip("- ").split(" 契约声明但源码未定义")[0]
                for g in gaps[:5])
            features.append({
                "name": "模块契约完整性",
                "status": "WARN",
                "notes": (f"契约声明与源码导出不一致: {missing_names}"
                          + (f" 等 {len(gaps)} 处" if len(gaps) > 5 else "")
                          + " — 但测试全部通过，行为正确，降级 WARN 交付"),
                "source": "platform",
            })
            result["features"] = features
            if result.get("verdict") == "PASS":
                result["verdict"] = "WARN"
            print(f"  [QualityGate] 平台契约检查：{len(gaps)} 处缺口但"
                  f"测试通过 → 降级 WARN（行为豁免）", flush=True)
            return result
        if gate_error:
            features.append({
                "name": "模块契约完整性",
                "status": "NO",
                "notes": gate_error,
                "source": "platform",
            })
        elif gaps:
            missing_names = "、".join(
                g.strip().lstrip("- ").split(" 契约声明但源码未定义")[0]
                for g in gaps[:5])
            features.append({
                "name": "模块契约完整性",
                "status": "NO",
                "notes": f"契约声明但源码缺失: {missing_names}"
                         + (f" 等 {len(gaps)} 处" if len(gaps) > 5 else ""),
                "source": "platform",
            })
        if wiring_gaps:
            features.append({
                "name": "入口接线完整性",
                "status": "NO",
                "notes": "；".join(wiring_gaps[:3])
                         + (f" 等 {len(wiring_gaps)} 处"
                            if len(wiring_gaps) > 3 else ""),
                "source": "platform",
            })
        result["features"] = features
        # FAIL 必须同时归零 score —— 否则 inspector 的 100 分 + FAIL
        # verdict 自相矛盾（前端展示"100 分未通过"），且 pipeline 记忆
        # 门槛（score < min_score 才拦截）会把 FAIL 项目当高分写进记忆，
        # 直接违背"FAIL 自动清除"的设计
        result["verdict"] = "FAIL"
        result["score"] = 0
        print(f"  [QualityGate] 平台检查：契约 {len(gaps)} 处缺口 + "
              f"入口接线 {len(wiring_gaps)} 处缺口 → FAIL", flush=True)
        return result

    @staticmethod
    def _parse_coverage(output: str) -> int | None:
        """从 pytest-cov 输出提取总覆盖率（TOTAL ... 42%）。"""
        m = re.search("TOTAL\\s+\\d+\\s+\\d+\\s+(\\d{1,3})%", output)
        if m:
            return int(m.group(1))
        m = re.search("Coverage:\\s*([\\d.]+)%", output)
        return int(round(float(m.group(1)))) if m else None
