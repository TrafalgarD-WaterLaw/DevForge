// web/src/components/spriteDefs.ts
// Agent metadata — positions, display names, sprite paths, state icons.
// Sprites are cropped from WaterNPCs_Animations.png (16 chars × 4 dirs × 3 frames),
// stored under /sprites/npc/ as char{00-15}_{D|L|R|U}_f{0-2}.png.
// D=front, L=left, R=right, U=back.  f0 = standing pose, f1/f2 = walk frames.

// ── 表达体系类型（stateMachine/PixelOffice 共用）──
export type Activity = 'idle' | 'think' | 'work' | 'talk' | 'walk' | 'celebrate'
export type Mood = 'calm' | 'happy' | 'worried'
export type Facing = 'D' | 'L' | 'R' | 'U'
export type ZoneName = 'design' | 'coding' | 'review' | 'integration' | 'support' | ''

export interface AgentMeta {
  id: string
  displayName: string
  spriteFile: string        // path under /sprites/
  deskX: number             // CSS left (px) in the 340×560 panel
  deskY: number             // CSS top (px)
  zone: 'design' | 'coding' | 'review' | 'integration' | 'support'
}

// Random assignment (seed=42): 16 chars → 13 agents + 3 spare (11, 0, 3)
// Scene: 1216×878 — office-bg.png displayed at full resolution.
// Positions scaled ×2.17 from the 560×404 layout.
export const AGENTS: AgentMeta[] = [
  { id: 'product_manager',          displayName: 'PM',   spriteFile: 'npc/char07_D_f0.png', deskX: 580, deskY: 250, zone: 'support' },
  { id: 'chief_technology_officer', displayName: 'CTO',  spriteFile: 'npc/char09_L_f0.png', deskX: 230, deskY: 580, zone: 'design' },
  { id: 'chief_product_officer',    displayName: 'CPO',  spriteFile: 'npc/char05_R_f0.png', deskX: 77,  deskY: 630, zone: 'design' },
  { id: 'coder_0',                  displayName: 'Dev1', spriteFile: 'npc/char06_U_f0.png', deskX: 530,  deskY: 710, zone: 'coding' },
  { id: 'coder_1',                  displayName: 'Dev2', spriteFile: 'npc/char14_U_f0.png', deskX: 420, deskY: 710, zone: 'coding' },
  { id: 'coder_2',                  displayName: 'Dev3', spriteFile: 'npc/char10_U_f0.png', deskX: 640, deskY: 710, zone: 'coding' },
  { id: 'integrator',               displayName: 'Lead', spriteFile: 'npc/char12_R_f0.png', deskX: 880, deskY: 330, zone: 'support' },
  { id: 'fixer',                    displayName: 'Fix',  spriteFile: 'npc/char08_R_f0.png', deskX: 880,  deskY: 160, zone: 'support' },
  { id: 'reviewer_0',               displayName: 'Rv1',  spriteFile: 'npc/char01_D_f0.png', deskX: 1000, deskY: 550, zone: 'review' },
  { id: 'reviewer_1',               displayName: 'Rv2',  spriteFile: 'npc/char01_R_f0.png', deskX: 860, deskY: 650, zone: 'review' },
  { id: 'reviewer_2',               displayName: 'Rv3',  spriteFile: 'npc/char01_U_f0.png', deskX: 1000, deskY: 790, zone: 'review' },
  { id: 'reviewer_3',               displayName: 'Rv4',  spriteFile: 'npc/char01_L_f0.png', deskX: 1140, deskY: 650, zone: 'review' },
  { id: 'tester',                   displayName: 'Tst',  spriteFile: 'npc/char02_R_f0.png', deskX: 880, deskY: 240, zone: 'support' },
  { id: 'technical_writer',         displayName: 'Doc',  spriteFile: 'npc/char13_L_f0.png', deskX: 1130, deskY: 160, zone: 'support' },
  { id: 'dependency_analyst',       displayName: 'Dep',  spriteFile: 'npc/char15_L_f0.png', deskX: 1130, deskY: 240, zone: 'support' },
  { id: 'inspector',                displayName: 'QA',   spriteFile: 'npc/char04_R_f0.png', deskX: 230, deskY: 680, zone: 'support' },
]

export const AGENT_MAP: Record<string, AgentMeta> = {}
for (const a of AGENTS) AGENT_MAP[a.id] = a

// 对话区角色信息：后端 agent id → 中文名 + 头像 emoji + 主题色。
// 与 AGENT_MAP.displayName（办公室短代号）区分：对话流用全名。
// ConversationPanel / feed 共用，避免各处各自维护一份映射。
export interface AgentInfo { name: string; emoji: string; color: string }
export const AGENT_INFO: Record<string, AgentInfo> = {
  product_manager:          { name: '产品经理', emoji: '📝', color: '#7c3aed' },
  chief_technology_officer: { name: '技术总监', emoji: '🏗️', color: '#2563eb' },
  chief_product_officer:    { name: '产品总监', emoji: '📋', color: '#0891b2' },
  coder:                    { name: '开发工程师', emoji: '🧑‍💻', color: '#ea580c' },
  integrator:               { name: '联调工程师', emoji: '🔗', color: '#d97706' },
  fixer:                    { name: '代码修复', emoji: '🔧', color: '#16a34a' },
  tester:                   { name: '测试工程师', emoji: '🧪', color: '#0d9488' },
  technical_writer:         { name: '文档工程师', emoji: '📄', color: '#64748b' },
  dependency_analyst:       { name: '依赖分析师', emoji: '📦', color: '#9333ea' },
  inspector:                { name: '质量检查', emoji: '🔬', color: '#dc2626' },
  SecurityReviewer:         { name: '安全审查', emoji: '🛡️', color: '#be185d' },
  PerformanceReviewer:      { name: '性能审查', emoji: '⚡', color: '#be185d' },
  LogicReviewer:            { name: '逻辑审查', emoji: '🧠', color: '#be185d' },
  CorrectnessReviewer:      { name: '正确性审查', emoji: '✅', color: '#be185d' },
  PM:                       { name: '产品经理', emoji: '📝', color: '#7c3aed' },
  CTO:                      { name: '技术总监', emoji: '🏗️', color: '#2563eb' },
  CPO:                      { name: '产品总监', emoji: '📋', color: '#0891b2' },
  QA:                       { name: '质量检查', emoji: '🔬', color: '#dc2626' },
  DevForge:                 { name: 'DevForge', emoji: '🤖', color: '#64748b' },
}

/** 后端 agent 名（可能带大小写/未注册 tag 如模块名）→ 显示名 + 头像；未知回退原名 */
export function agentLabel(raw: string): AgentInfo {
  if (raw === '你') return { name: '你', emoji: '🧑', color: '#64748b' }
  return AGENT_INFO[raw] ?? AGENT_INFO[raw.toLowerCase()]
    ?? { name: raw, emoji: '🤖', color: '#64748b' }
}

// State → status icon shown above the character's head
export const STATE_ICONS: Record<string, string> = {
  idle: '☕', think: '💭', work: '⚙️', talk: '💬',
  walk: '🚶', celebrate: '🎉',
}

// Zone → display label
export const ZONE_LABELS: Record<string, string> = {
  design: 'Design', coding: 'Coding', review: 'Review',
  integration: 'Integration', support: 'Support',
}

// ── 阶段 → 区域 + 该阶段工作的 agent（自 stateMachine.ts 迁移，避免循环依赖）──
export const PHASE_ZONE: Record<string, { zone: ZoneName; agents: string[] }> = {
  RequirementsDiscussion: { zone: 'support',     agents: ['product_manager'] },
  Design:                 { zone: 'design',       agents: ['chief_technology_officer', 'chief_product_officer'] },
  Coding:                 { zone: 'coding',       agents: ['coder_0', 'coder_1', 'coder_2', 'integrator', 'tester'] },
  Verification:           { zone: 'review',       agents: ['reviewer_0', 'reviewer_1', 'reviewer_2', 'reviewer_3', 'fixer'] },
  Documentation:          { zone: 'support',      agents: ['technical_writer', 'dependency_analyst'] },
  QualityGate:            { zone: 'support',      agents: ['inspector'] },
}

/** 精灵路径：npc/charXX_{D|L|R|U}_f{0|1|2}.png */
export function spriteFor(meta: AgentMeta, facing: Facing, frame: number): string {
  const num = meta.spriteFile.match(/char(\d+)/)?.[1] ?? '00'
  return `npc/char${num}_${facing}_f${frame}.png`
}

// 角色 × 活动 → 氛围动词（气泡）
export const VERBS: Record<string, Partial<Record<Activity, string>>> = {
  product_manager:          { think: '思考需求', work: '整理需求', talk: '询问需求' },
  chief_technology_officer: { think: '设计架构', work: '画架构图', talk: '评审设计' },
  chief_product_officer:    { think: '规划功能', work: '排优先级', talk: '评审设计' },
  coder_0:                  { think: '理清模块', work: '写代码',   talk: '同步进度' },
  coder_1:                  { think: '理清模块', work: '写代码',   talk: '同步进度' },
  coder_2:                  { think: '理清模块', work: '写代码',   talk: '同步进度' },
  integrator:               { think: '梳理模块', work: '联调代码', talk: '验收模块' },
  fixer:                    { think: '定位问题', work: '修复问题', talk: '重跑验证' },
  reviewer_0:               { think: '读代码',   work: '审查代码', talk: '提交问题' },
  reviewer_1:               { think: '读代码',   work: '审查代码', talk: '提交问题' },
  reviewer_2:               { think: '读代码',   work: '审查代码', talk: '提交问题' },
  reviewer_3:               { think: '读代码',   work: '审查代码', talk: '提交问题' },
  tester:                   { think: '设计用例', work: '写测试',   talk: '报告结果' },
  technical_writer:         { think: '组织文档', work: '写文档',   talk: '同步文档' },
  dependency_analyst:       { think: '检查依赖', work: '分析依赖', talk: '报告风险' },
  inspector:                { think: '核对需求', work: '检查质量', talk: '报告打分' },
}

// 工具名 → 动词（tool_pre_use）。仅收录后端工具注册表中的真实工具
// （devforge/tools/registry：read_file/write_file/list_files/run_code/todo_write/search_web/run_tests）。
export const TOOL_VERBS: Record<string, string> = {
  write_file: '写代码', read_file: '看代码', list_files: '查看文件',
  run_code: '跑程序', run_tests: '跑测试', todo_write: '更新任务清单', search_web: '搜索资料',
}

// 兜底动词
export const DEFAULT_VERBS: Record<Activity, string> = {
  idle: '摸鱼', think: '思考', work: '工作中',
  talk: '说话', walk: '走动', celebrate: '完成',
}

// 活动 → 状态卡文案（PixelOffice 状态徽章，派生自此，勿在各组件重复定义）
export const ACTIVITY_LABELS_CN: Record<Activity, string> = {
  idle: '摸鱼中', think: '思考中', work: '工作中', talk: '交流中',
  walk: '走动中', celebrate: '庆祝中',
}

/** 区域包围盒（由座位坐标推导，场景坐标系 1216×878） */
export function zoneBBox(zone: ZoneName): { x: number; y: number; w: number; h: number } {
  const desks = AGENTS.filter((a) => a.zone === zone).map((a) => ({ x: a.deskX, y: a.deskY }))
  if (!desks.length) return { x: 0, y: 0, w: 0, h: 0 }
  const xs = desks.map((d) => d.x), ys = desks.map((d) => d.y)
  const minX = Math.min(...xs) - 90, minY = Math.min(...ys) - 70
  const maxX = Math.max(...xs) + 90, maxY = Math.max(...ys) + 70
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}
