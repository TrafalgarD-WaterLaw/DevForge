// web/src/phases.ts
// 阶段名/中文标签单一来源（前端各处不再各自维护列表）。
// 与后端 pipeline 阶段名保持一致（大小写敏感）。

export const PHASES = ['RequirementsDiscussion', 'Design', 'Coding', 'Verification', 'Documentation', 'QualityGate']

export const PHASE_LABELS_CN: Record<string, string> = {
  RequirementsDiscussion: '需求讨论', Design: '设计', Coding: '编码',
  Verification: '验证', Documentation: '文档', QualityGate: '质检',
  Integration: '整合联调',   // 编码阶段内的整合子面板
  Iterate: '迭代',           // A2 增量迭代（已有项目上做修改）
}
