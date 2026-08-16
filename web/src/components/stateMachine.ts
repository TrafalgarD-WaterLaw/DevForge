// web/src/components/stateMachine.ts
// WebSocket 事件 → agent 状态模拟器。纯逻辑，无渲染。
// 确定性：内部 clock 由 tick(dtMs) 推进；walk 用场景坐标（1216×878）。

import {
  AGENT_MAP, PHASE_ZONE, VERBS, TOOL_VERBS, DEFAULT_VERBS,
  type Activity, type Mood, type Facing, type ZoneName,
} from './spriteDefs'
import { phaseBanner, celebrateIds, retryBanner, retryWorried } from './director'
import { PHASES } from '../phases'

export interface Bubble { text: string; until: number }
export interface AgentState {
  id: string
  displayName: string
  activity: Activity
  /** 兼容别名 — 旧渲染仍读 state */
  state: Activity
  mood: Mood
  bubble: Bubble | null
  pos: { x: number; y: number }
  home: { x: number; y: number }
  target: { x: number; y: number } | null
  facing: Facing
  frame: number            // 0=立正；走路时 1/2 交替
  walkFrameTick: number    // 走路帧计数器（仅走路时递增）
  deliverHoldUntil: number // 0 = 无交付停留
  deliverBubble: string    // 到达时的交付气泡文本（''=无）
  waterBreak: boolean       // 彩蛋：正在/已安排去饮水机接水
  teaSpot: number          // 彩蛋：下午茶休息位索引（-1 = 无）
  ceremony: boolean        // 彩蛋：竣工典礼中（到达中央后 celebrate 并留下）
  lastEvent: number
}
export interface StageState {
  glowZone: ZoneName
  banner: string | null
  bannerUntil: number
}

// ── 常量（全局约束，勿改）──
const WALK_SPEED = 140          // px/s（场景坐标）
const BUBBLE_MS = 3000
const DECAY_MS = 3000
const CELEBRATE_MS = 1200
const DELIVER_HOLD_MS = 2000
const STAGGER_MS = 300
const WALK_FRAME_TICKS = 2
const RETRY_BANNER_MS = 5000

// ── 彩蛋常量 ──
export const WATER_BREAK_CHANCE = 0.002   // 每 tick 每 idle 小人的接水概率（冷却为主节流）
export const SNOOZE_CHANCE = 0.003        // 每 tick 每 idle 小人打盹概率
export const WATER_HOLD_MS = 2000
export const WATER_DISPENSER = { x: 24, y: 110 }
export const TEA_BREAK_CHANCE = 0.0012    // 每 tick 每 idle 小人的喝茶概率（比接水更低频）
export const TEA_SPOTS = [{ x: 100, y: 240 }, { x: 220, y: 240 }]
export const BREAK_COOLDOWN_BASE = 90000  // 休息冷却基础值（90-120s 一次休息，频率别太密）
export const BREAK_COOLDOWN_RAND = 30000  // 休息冷却随机增量
export const TEA_HOLD_MS = 15000          // 下午茶入座停留时长（喝水 2s，喝茶坐久点）
export const TEA_PAIR_WINDOW_MS = 2000    // 首杯茶的成对窗口：期间第二人可入座；之后必须等冷却
export const SLACK_MSG_CHANCE = 0.001     // 每 tick 每 idle 小人的摸鱼碎碎念概率
// 摸鱼气泡池
const SNOOZE_POOL = ['💤', '🥱', '☕', '📱', '🍪']
// 摸鱼碎碎念（低频小消息）
const FLAVOR_POOL = [
  '等会下班吃什么呢…', '好想涨工资…', '这个任务真的好难…',
  '周末去哪玩呢…', '这代码谁写的…', '摸鱼一时爽，加班火葬场…',
]
let breakCooldownUntil = 0                // 水/茶共用冷却（休息开始时重置）
let teaPairWindowUntil = 0                // 首杯茶的成对窗口截止时间（2s 后补位必须等冷却）
let ceremonyHeld = false                  // 竣工典礼后抑制彩蛋休息（新运行重置）
let eggsEnabled = false
// PM 提问中（discuss_choice 置位，阶段边界复位）：等待作答期间小人持续互动
let questionPending = false
const PM_PROMPT_POOL = ['请选择…', '想好了吗？', '等你哦…']
export function setEggsEnabled(v: boolean) { eggsEnabled = v }

/** 断电彩蛋：随机 2 个 idle 小人冒 ❓（由 PixelOffice 定时器触发） */
export function blackoutBubble() {
  const idle = [...agents.values()].filter((a) => a.activity === 'idle')
  for (let i = 0; i < 2 && idle.length; i++) {
    const idx = Math.floor(Math.random() * idle.length)
    const a = idle.splice(idx, 1)[0]
    a.bubble = makeBubble('❓')
  }
}

// ── 状态 ──
const agents: Map<string, AgentState> = new Map()
let clock = 0
let activeZone: ZoneName = ''
let banner: string | null = null
let bannerUntil = 0
let handoffFired = false       // 本阶段是否已触发过串门（integrator/fixer 一次）
let retryBannerActive = false  // 回跳横幅期间不被 phase_start 覆盖
// 有问题的审阅者（按 review_submitted 提交者标记；递交时只走这些人）
let reviewersWithIssues: string[] = []

// 串门队列：到点出发
interface WalkPlan {
  id: string
  departAt: number
  departBubble: string
  arriveBubble: string
  targetX: number
  targetY: number
}
let walkQueue: WalkPlan[] = []

export function init() {
  agents.clear()
  walkQueue = []
  clock = 0
  activeZone = ''
  banner = null
  bannerUntil = 0
  handoffFired = false
  retryBannerActive = false
  breakCooldownUntil = 0
  teaPairWindowUntil = 0
  ceremonyHeld = false
  questionPending = false
  reviewersWithIssues = []
  unknownNameMap.clear()
  nextCoderSlot = 0
  for (const meta of Object.values(AGENT_MAP)) {
    agents.set(meta.id, {
      id: meta.id,
      displayName: meta.displayName,
      activity: 'idle',
      state: 'idle',
      mood: 'calm',
      bubble: null,
      pos: { x: meta.deskX, y: meta.deskY },
      home: { x: meta.deskX, y: meta.deskY },
      target: null,
      facing: spriteFacing(meta.id),
      frame: 0,
      walkFrameTick: 0,
      deliverHoldUntil: 0,
      deliverBubble: '',
      waterBreak: false,
      teaSpot: -1,
      ceremony: false,
      lastEvent: 0,
    })
  }
}

/** 初始朝向 = 精灵文件里的朝向（char07_D_f0 → D） */
function spriteFacing(id: string): Facing {
  const f = AGENT_MAP[id].spriteFile.match(/_([DLRU])_f0/)?.[1] as Facing | undefined
  return f ?? 'D'
}

// ── 事件入口 ──
export function dispatch(event: Record<string, unknown>) {
  const evType = event.event as string
  switch (evType) {
    case 'phase_start': {
      const phaseName = event.phase as string
      // 硬边界：未知阶段同样清空串门队列与触发标记，错峰出发计划作废
      walkQueue = []
      handoffFired = false
      ceremonyHeld = false
      questionPending = false
      reviewersWithIssues = []
      unknownNameMap.clear()
      nextCoderSlot = 0
      const mapping = PHASE_ZONE[phaseName]
      if (!mapping) break
      // 硬边界：非本阶段 agent 归位 idle；本阶段 agent 开工
      for (const a of agents.values()) {
        a.target = null
        a.deliverHoldUntil = 0
        a.deliverBubble = ''
        a.waterBreak = false
        a.teaSpot = -1
        a.ceremony = false
        if (mapping.agents.includes(a.id)) {
          a.activity = 'think'
          a.state = 'think'
          a.bubble = makeBubble('开工')
          a.lastEvent = clock
        } else {
          a.activity = 'idle'
          a.state = 'idle'
          a.pos = { ...a.home }
          a.frame = 0
          a.facing = spriteFacing(a.id)   // 归位时重置朝向
        }
      }
      activeZone = mapping.zone
      // 回跳（FAIL）横幅持续期内不被阶段横幅覆盖
      if (!retryBannerActive) {
        banner = phaseBanner(phaseName)
        bannerUntil = clock + BUBBLE_MS
      }
      break
    }
    case 'phase_end': {
      const phaseName = event.phase as string
      const ids = celebrateIds(phaseName)
      for (const id of ids) {
        const a = agents.get(id)
        if (!a) continue
        if (a.activity === 'walk') {
          // 走路中 → 先归位，不庆祝
          a.pos = { ...a.home }
          a.target = null
          a.deliverHoldUntil = 0
          a.deliverBubble = ''
          a.facing = spriteFacing(a.id)   // 归位时重置朝向
          a.waterBreak = false
          a.teaSpot = -1
          a.ceremony = false
          a.activity = 'idle'
          a.state = 'idle'
          a.frame = 0
          continue
        }
        a.target = null
        a.deliverHoldUntil = 0
        a.deliverBubble = ''
        a.waterBreak = false
        a.teaSpot = -1
        a.ceremony = false
        a.activity = 'celebrate'
        a.state = 'celebrate'
        a.mood = 'happy'
        a.bubble = makeBubble('✓ 完成')
        a.lastEvent = clock
      }
      // 其余走路中的 agent 直接归位
      for (const a of agents.values()) {
        if (ids.includes(a.id)) continue
        a.pos = { ...a.home }
        a.target = null
        a.deliverHoldUntil = 0
        a.deliverBubble = ''
        a.facing = spriteFacing(a.id)   // 归位时重置朝向
        a.waterBreak = false
        a.teaSpot = -1
        a.ceremony = false
        if (a.activity === 'walk') { a.activity = 'idle'; a.state = 'idle'; a.frame = 0 }
      }
      // 硬边界：清空串门队列，错峰出发计划作废
      walkQueue = []
      questionPending = false
      reviewersWithIssues = []
      activeZone = ''
      banner = null
      retryBannerActive = false
      break
    }
    case 'phase_retry': {
      const phaseName = event.phase as string
      const loop = (event.loop as number) || 1
      const reason = String(event.reason ?? 'fail')   // 'fail' = 质检回跳；'error' = 阶段出错重试
      for (const id of retryWorried(phaseName)) {
        const a = agents.get(id)
        if (!a) continue
        a.mood = 'worried'
        a.bubble = makeBubble(reason === 'error' ? '出错了？' : '还有问题？')
      }
      banner = retryBanner(loop, reason)
      bannerUntil = clock + RETRY_BANNER_MS
      retryBannerActive = true
      break
    }
    case 'phase_error': {
      // 阶段出错：相关区域小人 worried + 出错横幅
      const mapping = PHASE_ZONE[String(event.phase ?? '')]
      if (mapping) {
        for (const id of mapping.agents) {
          const a = agents.get(id)
          if (a) { a.mood = 'worried'; a.bubble = makeBubble('出错了？') }
        }
      }
      banner = '⚠️ 阶段出错'
      bannerUntil = clock + 3000
      retryBannerActive = true   // 不被后续 phase_start 立即覆盖
      break
    }
    case 'conversation_turn': {
      const id = resolveAgentId(event.agent as string)
      if (!id) break
      // ── 串门触发（推导）：接收方发言 = 交付/递交时刻 ──
      if (id === 'integrator') {
        handoffFired = true
        scheduleWalk(['coder_0', 'coder_1', 'coder_2'], 'integrator', '去交付', '📦 交付模块')
      } else if (id === 'fixer') {
        handoffFired = true
        scheduleWalk(reviewerWalkTargets(), 'fixer', '递交问题', '📋 递交问题')
      }
      setActivity(id, 'talk', verbOf(id, 'talk'))
      break
    }
    case 'tool_pre_use': {
      const id = resolveAgentId(event.agent as string)
      if (id) {
        // ── 串门触发（真实流程）：integrator/fixer 的 tool 事件 = 交付/递交时刻 ──
        if (id === 'integrator' && !handoffFired) {
          handoffFired = true
          scheduleWalk(['coder_0', 'coder_1', 'coder_2'], 'integrator', '去交付', '📦 交付模块')
        } else if (id === 'fixer' && !handoffFired) {
          handoffFired = true
          scheduleWalk(reviewerWalkTargets(), 'fixer', '递交问题', '📋 递交问题')
        }
        const tool = (event.tool as string) || ''
        setActivity(id, 'work', TOOL_VERBS[tool] || verbOf(id, 'work'))
      }
      break
    }
    case 'tool_post_use': {
      const id = resolveAgentId(event.agent as string)
      if (id) setActivity(id, 'think', verbOf(id, 'think'))
      break
    }
    case 'requirements_submitted': {
      const pm = agents.get('product_manager')
      if (pm) {
        pm.mood = 'happy'
        setActivity('product_manager', 'work', '需求确定 ✅')
      }
      break
    }
    case 'discuss_choice': {
      // PM 提问：进入"交流中"并冒气泡；等待作答期间 tick 持续刷新互动
      questionPending = true
      const pm = agents.get('product_manager')
      if (pm) {
        pm.mood = 'happy'
        setActivity('product_manager', 'talk', '请选择…')
      }
      break
    }
    case 'coding_progress': {
      for (let i = 0; i < 3; i++) {
        const a = agents.get(`coder_${i}`)
        if (a) setActivity(`coder_${i}`, 'work', '写代码')
      }
      break
    }
    case 'review_round': {
      // 新一轮审查开始：审查者重新开工（第 2 轮起可见"复核"交互）
      const loop = Number(event.loop ?? 1)
      reviewersWithIssues = []
      for (let i = 0; i < 4; i++) {
        const a = agents.get(`reviewer_${i}`)
        if (a) setActivity(`reviewer_${i}`, 'think',
                           loop > 1 ? `第 ${loop} 轮复核` : '开始审查')
      }
      break
    }
    case 'review_submitted': {
      for (let i = 0; i < 4; i++) {
        const a = agents.get(`reviewer_${i}`)
        if (a) setActivity(`reviewer_${i}`, 'work', '提交审查')
      }
      // 按实际提交者标记"有问题的审阅者"（递交时只走这些人）。
      // 此前按固定 REVIEWER_IDS 顺序取未标记座位 —— 只有 CorrectnessReviewer
      // 发现问题时也标记 Security 座位，fixer 递交走向错误座位
      const issues = (event.issues as unknown[] | undefined) ?? []
      if (Array.isArray(issues) && issues.length > 0) {
        const id = resolveAgentId(String(event.agent ?? ''))
        if (id && !reviewersWithIssues.includes(id)) reviewersWithIssues.push(id)
      }
      break
    }
    case 'pipeline_complete': {
      questionPending = false   // 流程结束：PM 不再冒"请选择…"
      if (!event.failed) startCeremony()
      break
    }
    case 'error': {
      // 运行出错：PM 提问互动立即停
      questionPending = false
      break
    }

    // ── 串门触发（内部事件，Task 5 测试用；Task 6 加 conversation_turn 推导）──
    case 'integrator_handoff': {
      scheduleWalk(['coder_0', 'coder_1', 'coder_2'], 'integrator', '去交付', '📦 交付模块')
      break
    }
    case 'fixer_handoff': {
      scheduleWalk(reviewerWalkTargets(), 'fixer', '递交问题', '📋 递交问题')
      break
    }
    default:
      break
  }
}

// ── 每 tick 推进 ──
export function tick(dtMs: number) {
  clock += dtMs

  // 串门队列到点出发
  const ready = walkQueue.filter((w) => w.departAt <= clock)
  walkQueue = walkQueue.filter((w) => w.departAt > clock)
  for (const w of ready) {
    const a = agents.get(w.id)
    if (!a || a.activity === 'walk') continue
    a.target = { x: w.targetX, y: w.targetY }
    a.deliverBubble = w.arriveBubble
    a.activity = 'walk'
    a.state = 'walk'
    a.bubble = makeBubble(w.departBubble)
    a.lastEvent = clock
  }

  // ── 彩蛋：接水 / 下午茶 / 摸鱼（仅 idle，办公室氛围） ──
  // 置于走路/气泡处理之前：本 tick 已安排走路或气泡未过期的小人不会被彩蛋
  // 重复触发；反之若放在 tick 末尾，刚到家/气泡刚过期的小人会在同一 tick 被
  // 立刻再次派去接水或打盹（彩蛋测试断言 回座位后 idle / 打盹气泡消失）。
  if (eggsEnabled && !ceremonyHeld) {
    let waterBusy = [...agents.values()].some((a) => a.waterBreak)
    for (const a of agents.values()) {
      if (a.activity !== 'idle' || a.bubble) continue
      const teaActive = [...agents.values()].some((x) => x.teaSpot >= 0)
      // 空闲休息位（-1 = 无）
      const freeSpot = TEA_SPOTS.findIndex((_, i) =>
        ![...agents.values()].some((x) => x.teaSpot === i))
      const cooldownOk = clock >= breakCooldownUntil
      // 接水：单占用 + 冷却
      if (!waterBusy && cooldownOk && Math.random() < WATER_BREAK_CHANCE) {
        a.target = { ...WATER_DISPENSER }
        a.deliverBubble = '💧 接水…'
        a.waterBreak = true
        a.activity = 'walk'
        a.state = 'walk'
        a.bubble = makeBubble('去接水')
        a.lastEvent = clock
        waterBusy = true
        breakCooldownUntil = clock + BREAK_COOLDOWN_BASE + Math.random() * BREAK_COOLDOWN_RAND
      } else if (freeSpot >= 0 && (clock < teaPairWindowUntil || cooldownOk) && Math.random() < TEA_BREAK_CHANCE) {
        // 下午茶：首杯开 2s 成对窗口（第二人可入座），之后补位必须等冷却 ——
        // 旧规则"teaActive 即免冷却"会让空出的席位被立刻补位，茶永远有人喝
        a.target = { ...TEA_SPOTS[freeSpot] }
        a.deliverBubble = '🍵 下午茶…'
        a.teaSpot = freeSpot
        a.activity = 'walk'
        a.state = 'walk'
        a.bubble = makeBubble('去喝茶')
        a.lastEvent = clock
        if (!teaActive) {
          breakCooldownUntil = clock + BREAK_COOLDOWN_BASE + Math.random() * BREAK_COOLDOWN_RAND
          teaPairWindowUntil = clock + TEA_PAIR_WINDOW_MS
        }
      } else if (Math.random() < SNOOZE_CHANCE) {
        a.bubble = makeBubble(SNOOZE_POOL[Math.floor(Math.random() * SNOOZE_POOL.length)])
      } else if (Math.random() < SLACK_MSG_CHANCE) {
        // 摸鱼碎碎念：低频小消息
        a.bubble = makeBubble(FLAVOR_POOL[Math.floor(Math.random() * FLAVOR_POOL.length)])
      }
    }
  }

  for (const a of agents.values()) {
    // ── 走路推进 ──
    if (a.target) {
      const dx = a.target.x - a.pos.x
      const dy = a.target.y - a.pos.y
      const dist = Math.hypot(dx, dy)
      if (dist < 0.5) {
        handleArrival(a)
      } else {
        const move = Math.min((WALK_SPEED * dtMs) / 1000, dist)
        a.pos.x += (dx / dist) * move
        a.pos.y += (dy / dist) * move
        // 朝向：主轴（|dx|>=|dy| → 左右，否则上下）
        a.facing = Math.abs(dx) >= Math.abs(dy)
          ? (dx >= 0 ? 'R' : 'L')
          : (dy >= 0 ? 'D' : 'U')
        // 走路帧交替（每 2 tick 换）— 每 agent 独立计数，仅走路时递增
        a.walkFrameTick++
        a.frame = Math.floor(a.walkFrameTick / WALK_FRAME_TICKS) % 2 === 0 ? 1 : 2
        a.lastEvent = clock
        // 本 tick 走完剩余路程 → 立即处理到达（同一 tick 内交付/回座位/归位）
        if (move >= dist) {
          a.pos = { x: a.target.x, y: a.target.y }
          handleArrival(a)
        }
      }
    }

    // ── 气泡过期 ──
    if (a.bubble && clock >= a.bubble.until) a.bubble = null

    // ── 喝茶静坐：停留期间偶尔冒个 🍵/😌（气泡随 TEA_HOLD_MS 周期刷新）──
    if (a.teaSpot >= 0 && a.deliverHoldUntil > clock && !a.bubble) {
      a.bubble = makeBubble(a.teaSpot === 0 ? '🍵' : '😌')
    }

    // ── 活动衰减 ──
    // 注意：自动回座位检查须在衰减之前 —— 先衰减为 think，下一 tick 才出发回家，
    // 否则取消走路后仅剩 <1 tick 路程的回家会在同一 tick 完成，walk 状态不可观测
    if (a.activity === 'think' && !a.target && (a.pos.x !== a.home.x || a.pos.y !== a.home.y)) {
      a.target = { ...a.home }
      a.activity = 'walk'
      a.state = 'walk'
      a.deliverBubble = ''
      a.bubble = makeBubble('回座位')
      a.lastEvent = clock
    }
    if ((a.activity === 'work' || a.activity === 'talk') && clock - a.lastEvent > DECAY_MS) {
      a.activity = 'think'
      a.state = 'think'
    }
    if (a.activity === 'celebrate' && clock - a.lastEvent > CELEBRATE_MS) {
      a.activity = 'idle'
      a.state = 'idle'
      a.mood = 'calm'
    }
  }

  // ── PM 提问互动：等待用户作答期间，气泡过期后持续刷新 ──
  if (questionPending) {
    const pm = agents.get('product_manager')
    if (pm && !pm.bubble && (pm.activity === 'talk' || pm.activity === 'think')) {
      pm.bubble = makeBubble(PM_PROMPT_POOL[Math.floor(Math.random() * PM_PROMPT_POOL.length)])
    }
  }

  if (banner && clock >= bannerUntil) {
    banner = null
    retryBannerActive = false
  }
}

// ── 内部工具 ──

/** 竣工典礼：全员走向大厅中央的环形站位（确定性排布 + 小幅抖动，避免重叠） */
function startCeremony() {
  ceremonyHeld = true
  walkQueue = []   // 待执行的串门计划不得拽走典礼小人
  const CX = 608, CY = 439.5, RADIUS = 170
  const ids = [...agents.keys()]
  ids.forEach((id, i) => {
    const a = agents.get(id)
    if (!a) return
    const theta = (i / ids.length) * Math.PI * 2
    // 确定性伪随机抖动（±14px）：不用 Math.random，站位可测试、可复现
    const jx = ((i * 37) % 29) - 14
    const jy = ((i * 53) % 29) - 14
    a.target = {
      x: Math.round(CX + RADIUS * Math.cos(theta) + jx),
      y: Math.round(CY + RADIUS * Math.sin(theta) + jy),
    }
    a.deliverBubble = ''
    a.ceremony = true
    a.activity = 'walk'
    a.state = 'walk'
    a.bubble = makeBubble('🎉')
    a.lastEvent = clock
  })
}

/** 到达目标点：交付停留 → 停留结束回座位 → 到家精确归位（同一 tick 内处理） */
function handleArrival(a: AgentState) {
  const t = a.target!
  const atHome = t.x === a.home.x && t.y === a.home.y
  // 竣工典礼：到达中央 → celebrate 并留在原地（不返回座位）
  if (a.ceremony) {
    a.ceremony = false
    a.waterBreak = false
    a.teaSpot = -1
    a.target = null
    a.deliverHoldUntil = 0
    a.deliverBubble = ''
    a.facing = 'D'          // 典礼全员正面
    a.activity = 'celebrate'
    a.state = 'celebrate'
    a.mood = 'happy'
    a.bubble = makeBubble('🎉 项目完成！')
    a.lastEvent = clock
    return
  }
  if (!atHome && a.deliverHoldUntil === 0) {
    // 到达目标 → 交付停留（下午茶坐久点：TEA_HOLD_MS）
    a.deliverHoldUntil = clock + (a.teaSpot >= 0 ? TEA_HOLD_MS : DELIVER_HOLD_MS)
    a.bubble = makeBubble(a.deliverBubble || '交付')
    // 下午茶入座朝向：席位 0 朝右、席位 1 朝左；站立帧（不走动帧）
    if (a.teaSpot >= 0) {
      a.facing = a.teaSpot === 0 ? 'R' : 'L'
      a.frame = 0
    }
  } else if (!atHome && clock >= a.deliverHoldUntil) {
    // 停留结束 → 回座位
    a.deliverHoldUntil = 0
    a.deliverBubble = ''
    a.target = { ...a.home }
    a.bubble = makeBubble('回座位')
  } else if (atHome) {
    // 已到家：精确归位，避免残留 <0.5px 位置在 talk 衰减后触发虚假"回座位"
    a.pos = { ...a.home }
    a.activity = 'idle'
    a.state = 'idle'
    a.waterBreak = false
    a.teaSpot = -1
    a.ceremony = false
    a.target = null
    a.facing = spriteFacing(a.id)
    a.frame = 0
    a.walkFrameTick = 0
  }
}

function makeBubble(text: string): Bubble {
  return { text, until: clock + BUBBLE_MS }
}

function verbOf(id: string, activity: Activity): string {
  return VERBS[id]?.[activity] || DEFAULT_VERBS[activity]
}

/** 设置活动；若正在走路则取消（自己干活优先，干完自动回座位） */
function setActivity(id: string, activity: Activity, bubbleText: string) {
  const a = agents.get(id)
  if (!a) return
  a.activity = activity
  a.state = activity
  a.target = null
  // 取消串门计划：本 agent 尚未出发的排队条目作废，不得稍后重新拉起走路
  walkQueue = walkQueue.filter((w) => w.id !== a.id)
  a.deliverHoldUntil = 0
  a.deliverBubble = ''
  a.waterBreak = false
  a.teaSpot = -1
  a.ceremony = false
  a.bubble = makeBubble(bubbleText)
  a.lastEvent = clock
}

/** 递交对象 = 有问题的审阅者（无人发现问题则无人递交） */
function reviewerWalkTargets(): string[] {
  return reviewersWithIssues.length ? [...reviewersWithIssues] : []
}

/** 安排一组 agent 错峰串门（STAGGER_MS 间隔出发） */
function scheduleWalk(ids: string[], targetId: string, departBubble: string, arriveBubble: string) {
  const target = AGENT_MAP[targetId]
  if (!target) return
  ids.forEach((id, i) => {
    const a = agents.get(id)
    if (!a || a.activity === 'walk') return
    walkQueue.push({
      id,
      departAt: clock + i * STAGGER_MS,
      departBubble,
      arriveBubble,
      targetX: target.deskX,
      targetY: target.deskY,
    })
  })
}

// 未知名（如 coder 的模块 tag "cli"）→ 稳定映射到 coder，编码期间办公室可见工作过程
const unknownNameMap = new Map<string, string>()
let nextCoderSlot = 0
// 阶段名（小写）→ 不映射为 agent 座位（里程碑 conversation_turn 不走办公室）。
// iterate/integration 不在 PHASES：迭代里程碑此前被当成未知 tag 映射到
// coder_0 座位（迭代运行中一个 coder 假动）
const PHASE_NAME_SET = new Set(
  [...PHASES.map((p) => p.toLowerCase()), 'iterate', 'integration'])

/** 映射事件 agent 名 → 内部 id（Reviewer/Coder 带后缀；未知模块 tag → coder 轮转） */
function resolveAgentId(name: string): string {
  if (!name) return ''
  const lower = name.toLowerCase()
  if (agents.has(lower)) return lower
  // 审查者按 lens 映射到各自座位（此前全部挤在 reviewer_0 —— 四个审查者
  // 并行工作时只有一张椅子在动，第二轮看起来"没有交互"）
  if (lower.includes('reviewer')) {
    if (lower.includes('security')) return 'reviewer_0'
    if (lower.includes('performance')) return 'reviewer_1'
    if (lower.includes('logic')) return 'reviewer_2'
    if (lower.includes('correctness')) return 'reviewer_3'
    return 'reviewer_0'
  }
  if (lower.includes('fixer')) return 'fixer'
  if (lower === 'coder') return 'coder_0'
  // 阶段名/里程碑（如 "Coding"）不映射
  if (PHASE_NAME_SET.has(lower)) return ''
  // 未知 tag（coder 模块名）→ 稳定映射到一个 coder 座位
  const mapped = unknownNameMap.get(lower)
  if (mapped) return mapped
  if (nextCoderSlot < 3) {
    const id = `coder_${nextCoderSlot++}`
    unknownNameMap.set(lower, id)
    return id
  }
  return ''
}

export function getAgentStates(): AgentState[] {
  return [...agents.values()]
}

export function getActiveZone(): ZoneName {
  return activeZone
}

export function getStage(): StageState {
  return { glowZone: activeZone, banner, bannerUntil }
}

/** 竣工典礼是否已举行（典礼后全员保持实体，不再虚影） */
export function isCeremonyHeld(): boolean {
  return ceremonyHeld
}
