"""Pipeline spec —— 阶段描述符表驱动（O6 编排 DSL）。

把散落在 pipeline.py 的硬编码控制流（错误重试、token 预算、质检回跳/
升级目标与条件）提升为声明式 spec，engine 统一解释：

- 阶段内逻辑保持命令式（DSL 表达不了 agent 级协作）；
- 条件只用内置谓词枚举（``escalate_condition``），不用 eval；
- 默认表 = 当前 default.json 语义，行为不变（纯重构）。

配置来源（优先级从高到低）：
1. ``pipeline_spec`` 段（声明式 DSL）：``pipeline_spec.phases.<name>``
   与 ``pipeline_spec.quality_gate``；
2. 旧散配置键（向后兼容）：``phase_retries`` / ``phase_budget`` /
   ``quality_gate_max_loops``。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 内置升级条件谓词（枚举，不是任意表达式）
ESCALATE_CONDITION_SAME_GAPS = "same_missing_names_twice"


@dataclass
class PhaseSpec:
    """单个阶段的执行描述符。"""
    name: str
    retry_on_error: int = 0      # 错误重试次数（原 phase_retries）
    budget: int = 0              # token 预算（原 phase_budget）


@dataclass
class QualityGateSpec:
    """质检回跳/升级的描述符（原 pipeline 硬编码的跳转逻辑）。"""
    max_loops: int = 3
    fail_jump: str = "Verification"      # FAIL/WARN 含未达标项 → 回跳目标
    escalate_jump: str = "Design"        # 同缺口二次 → 升级目标（重设计）
    escalate_condition: str = ESCALATE_CONDITION_SAME_GAPS


@dataclass
class PipelineSpec:
    """整条流水线的执行描述符表。"""
    phases: list[PhaseSpec] = field(default_factory=list)
    quality_gate: QualityGateSpec = field(default_factory=QualityGateSpec)

    def get(self, name: str) -> PhaseSpec | None:
        for p in self.phases:
            if p.name == name:
                return p
        return None

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "PipelineSpec":
        """从流水线配置构建（默认表 = 当前 default.json 语义）。

        优先 ``pipeline_spec`` 段；缺失时回落旧散配置键（兼容 iterate.json
        等只写 pipeline 段的命名配置，以及旧版测试注入的配置形状）。
        除 pipeline 列表外，``pipeline_spec.phases`` / ``phase_retries`` /
        ``phase_budget`` 引用的阶段名也纳入 spec（测试/自定义直接传
        阶段名时也能拿到重试与预算）。
        """
        cfg = cfg or {}
        names = list(cfg.get("pipeline", []))
        ds = cfg.get("pipeline_spec", {}) or {}
        phase_cfg = ds.get("phases", {}) or {}
        qg_cfg = ds.get("quality_gate", {}) or {}
        retries = cfg.get("phase_retries", {}) or {}
        budgets = cfg.get("phase_budget", {}) or {}

        # 合并散配置键引用的阶段名（可能不在 pipeline 列表）
        for src in (phase_cfg, retries, budgets):
            if isinstance(src, dict):
                for name in src:
                    if name not in names:
                        names.append(name)

        phases = []
        for name in names:
            p = phase_cfg.get(name, {}) or {}
            phases.append(PhaseSpec(
                name=name,
                retry_on_error=int(p.get(
                    "retry_on_error", retries.get(name, 0)) or 0),
                budget=int(p.get("budget", budgets.get(name, 0)) or 0),
            ))
        return cls(
            phases=phases,
            quality_gate=QualityGateSpec(
                max_loops=int(qg_cfg.get(
                    "max_loops",
                    cfg.get("quality_gate_max_loops", 3)) or 3),
                fail_jump=qg_cfg.get("fail_jump", "Verification"),
                escalate_jump=qg_cfg.get("escalate_jump", "Design"),
                escalate_condition=qg_cfg.get(
                    "escalate_condition", ESCALATE_CONDITION_SAME_GAPS),
            ),
        )
