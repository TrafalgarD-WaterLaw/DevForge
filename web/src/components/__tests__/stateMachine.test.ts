import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { init, dispatch, tick, getAgentStates, getActiveZone, getStage, setEggsEnabled, blackoutBubble } from '../stateMachine'
import { AGENT_MAP } from '../spriteDefs'

function agent(id: string) {
  return getAgentStates().find((a) => a.id === id)!
}

describe('stateMachine 事件映射', () => {
  beforeEach(() => init())

  it('init: 全员 idle 坐在 home', () => {
    const states = getAgentStates()
    expect(states.length).toBe(16)
    for (const a of states) {
      expect(a.activity).toBe('idle')
      expect(a.pos.x).toBe(AGENT_MAP[a.id].deskX)
      expect(a.pos.y).toBe(AGENT_MAP[a.id].deskY)
    }
  })

  it('phase_start: 该阶段 agent → think，其余 idle', () => {
    dispatch({ event: 'phase_start', phase: 'Design' })
    expect(agent('chief_technology_officer').activity).toBe('think')
    expect(agent('coder_0').activity).toBe('idle')
    expect(getActiveZone()).toBe('design')
    expect(getStage().banner).toBe('设计')
  })

  it('conversation_turn: talk + 角色动词气泡', () => {
    dispatch({ event: 'phase_start', phase: 'RequirementsDiscussion' })
    dispatch({ event: 'conversation_turn', agent: 'product_manager', content: '{}' })
    const pm = agent('product_manager')
    expect(pm.activity).toBe('talk')
    expect(pm.bubble?.text).toBe('询问需求')
  })

  it('tool_pre_use: work + 工具动词气泡', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' })
    expect(agent('coder_0').activity).toBe('work')
    expect(agent('coder_0').bubble?.text).toBe('写代码')
  })

  it('tool_post_use → think', () => {
    dispatch({ event: 'tool_pre_use', agent: 'coder_1', tool: 'run_tests' })
    dispatch({ event: 'tool_post_use', agent: 'coder_1' })
    expect(agent('coder_1').activity).toBe('think')
  })

  it('work/talk 3s 衰减 → think', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' })
    tick(2900)
    expect(agent('coder_0').activity).toBe('work')
    tick(200)
    expect(agent('coder_0').activity).toBe('think')
  })

  it('requirements_submitted: PM happy + 需求确定气泡', () => {
    dispatch({ event: 'requirements_submitted', agent: 'product_manager' })
    expect(agent('product_manager').mood).toBe('happy')
    expect(agent('product_manager').bubble?.text).toBe('需求确定 ✅')
  })

  it('phase_end: 阶段 agents 短暂 celebrate 后 idle', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'phase_end', phase: 'Coding' })
    expect(agent('coder_0').activity).toBe('celebrate')
    tick(1300)
    expect(agent('coder_0').activity).toBe('idle')
    expect(getActiveZone()).toBe('')
  })

  it('气泡 3s 后消失', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' })
    tick(3100)
    expect(agent('coder_0').bubble).toBeNull()
  })

  it('未知 agent 名（模块 tag）→ 映射到 coder 工作', () => {
    dispatch({ event: 'conversation_turn', agent: 'nobody_here', content: '{}' })
    expect(agent('coder_0').activity).toBe('talk')
  })
})

describe('stateMachine 硬边界修复', () => {
  beforeEach(() => init())

  it('phase_start 清空 walkQueue：错峰窗口内换阶段不再出发', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })   // coder_0 @0ms, coder_1 @300ms, coder_2 @600ms
    dispatch({ event: 'phase_end', phase: 'Coding' })  // 边界：队列应清空
    dispatch({ event: 'phase_start', phase: 'Verification' })
    tick(125)
    tick(1000)
    for (let i = 0; i < 3; i++) {
      expect(agent(`coder_${i}`).activity).not.toBe('walk')
      expect(agent(`coder_${i}`).target).toBeNull()
    }
  })

  it('到家精确归位：talk 衰减不再触发虚假回座位', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    tick(3800)   // coder_0 到达 integrator（交付停留中）
    tick(2100)   // 停留结束 → 回座位
    tick(4000)   // 到家
    expect(agent('coder_0').pos.x).toBeCloseTo(530, 0)
    expect(agent('coder_0').activity).toBe('idle')
    dispatch({ event: 'conversation_turn', agent: 'coder_0', content: '{}' })  // 触发 talk
    tick(3100)   // talk 衰减 → think
    tick(125)
    const a = agent('coder_0')
    expect(a.activity).toBe('think')          // 不得变 walk
    expect(a.target).toBeNull()
  })
})

describe('stateMachine 走路系统', () => {
  beforeEach(() => init())

  it('走动：速度 140px/s，朝向主轴，帧交替', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)                 // coder_0 出发
    const a = agent('coder_0')
    expect(a.activity).toBe('walk')
    expect(a.target).toEqual({ x: 880, y: 330 })
    tick(1000)
    const moved = agent('coder_0')
    const d0 = Math.hypot(moved.pos.x - 530, moved.pos.y - 710)
    expect(d0).toBeGreaterThan(120)   // 140×1.125s ≈ 157px
    expect(d0).toBeLessThan(160)
    expect(moved.facing).toBe('U')    // 530,710 → 880,330：|dy|>|dx| 且向上
    expect([1, 2]).toContain(moved.frame)
  })

  it('串门错开 300ms：coder_1 晚于 coder_0 出发', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    expect(agent('coder_1').activity).not.toBe('walk')
    tick(400)
    expect(agent('coder_1').activity).toBe('walk')
  })

  it('走路中收到自己的事件 → 取消走路原地响应', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    dispatch({ event: 'conversation_turn', agent: 'coder_0', content: '{}' })
    const a = agent('coder_0')
    expect(a.activity).toBe('talk')
    expect(a.target).toBeNull()
  })

  it('取消走路后干完活自动回座位', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    dispatch({ event: 'conversation_turn', agent: 'coder_0', content: '{}' })
    tick(3100)                // talk 衰减 → think
    tick(125)
    const a = agent('coder_0')
    expect(a.activity).toBe('walk')
    expect(a.target).toEqual({ x: 530, y: 710 })
  })

  it('phase_end 走路中 → 瞬移归位 idle', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    dispatch({ event: 'phase_end', phase: 'Coding' })
    const a = agent('coder_0')
    expect(a.pos).toEqual({ x: 530, y: 710 })
    expect(a.activity).toBe('idle')
    expect(a.target).toBeNull()
  })

  it('F8 取消排队中的串门：未出发的 coder 不再从残留队列出发', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })   // c0@0ms, c1@300ms, c2@600ms
    dispatch({ event: 'conversation_turn', agent: 'coder_1', content: '{}' })  // 取消 c1 的排队串门
    tick(500)                                   // 越过 c1 的出发点（300ms）
    expect(agent('coder_1').activity).not.toBe('walk')
    expect(agent('coder_1').target).toBeNull()
    expect(agent('coder_0').activity).toBe('walk')   // 未取消的 c0 正常串门
    tick(200)                                   // 越过 c2 的出发点（600ms）
    expect(agent('coder_2').activity).toBe('walk')   // 未取消的 c2 正常错峰出发
    expect(agent('coder_1').target).toBeNull()       // c1 已取消 → 始终不出发
  })

  it('F8 取消走路后自动回座位完成，不再有新的串门走路', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    dispatch({ event: 'conversation_turn', agent: 'coder_0', content: '{}' })  // 取消走路
    tick(3100)                 // talk 衰减 → think
    tick(125)                  // 自动回座位出发
    expect(agent('coder_0').target).toEqual({ x: 530, y: 710 })
    tick(4000)                 // 走回家
    expect(agent('coder_0').activity).toBe('idle')
    expect(agent('coder_0').target).toBeNull()
    tick(2000)                 // 越过 c1/c2 出发点后再观察
    expect(agent('coder_0').target).toBeNull()   // 无残留队列重走
    expect(agent('coder_0').activity).not.toBe('walk')
  })

  it('走路到达 → 交付停留 2s → 回座位 → idle', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'integrator_handoff' })
    tick(125)
    tick(3800)                // 4s 总时间 ≥ 3.69s 单程
    const at = agent('coder_0')
    expect(at.pos.x).toBeCloseTo(880, 0)
    expect(at.pos.y).toBeCloseTo(330, 0)
    expect(at.deliverHoldUntil).toBeGreaterThan(0)
    tick(2100)                // 停留结束 → 开始回座位
    tick(4000)                // 走回家（3.69s）
    const home = agent('coder_0')
    expect(home.pos.x).toBeCloseTo(530, 0)
    expect(home.pos.y).toBeCloseTo(710, 0)
    expect(home.activity).toBe('idle')
  })
})

describe('stateMachine 串门推导', () => {
  beforeEach(() => init())

  it('coding_progress 后 integrator 发言 → coder 串门交付', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'coding_progress' })
    dispatch({ event: 'conversation_turn', agent: 'integrator', content: '{}' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    expect(agent('coder_0').target).toEqual({ x: 880, y: 330 })
    expect(agent('coder_0').bubble?.text).toBe('去交付')
  })

  it('review_submitted 后 fixer 发言 → 有问题的 reviewer 串门递交', () => {
    dispatch({ event: 'phase_start', phase: 'Verification' })
    dispatch({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [{}] })
    dispatch({ event: 'conversation_turn', agent: 'fixer', content: '{}' })
    tick(125)
    expect(agent('reviewer_0').activity).toBe('walk')
    expect(agent('reviewer_0').target).toEqual({ x: 880, y: 160 })
    expect(agent('reviewer_1').activity).not.toBe('walk')   // 未发现问题 → 不递交
  })

  it('只有出问题的审阅者递交', () => {
    dispatch({ event: 'phase_start', phase: 'Verification' })
    dispatch({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [{}] })   // → reviewer_0
    dispatch({ event: 'review_submitted', agent: 'PerformanceReviewer', issues: [] })  // → 无
    dispatch({ event: 'review_submitted', agent: 'LogicReviewer', issues: [{}] })      // → reviewer_2
    dispatch({ event: 'review_submitted', agent: 'CorrectnessReviewer', issues: [] })  // → 无
    dispatch({ event: 'conversation_turn', agent: 'fixer', content: '{}' })
    tick(125)
    expect(agent('reviewer_0').activity).toBe('walk')
    expect(agent('reviewer_2').activity).not.toBe('walk')   // 错峰未到
    tick(400)
    expect(agent('reviewer_2').activity).toBe('walk')
    expect(agent('reviewer_1').activity).not.toBe('walk')
    expect(agent('reviewer_3').activity).not.toBe('walk')
  })

  it('无人发现问题则不递交', () => {
    dispatch({ event: 'phase_start', phase: 'Verification' })
    for (let i = 0; i < 4; i++) dispatch({ event: 'review_submitted', issues: [] })
    dispatch({ event: 'conversation_turn', agent: 'fixer', content: '{}' })
    tick(125)
    tick(600)
    for (let i = 0; i < 4; i++) expect(agent(`reviewer_${i}`).activity).not.toBe('walk')
  })

  it('到达交付：气泡变为 📦 交付模块', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'coding_progress' })
    dispatch({ event: 'conversation_turn', agent: 'integrator', content: '{}' })
    tick(125)
    tick(4000)                // ≥ 单程 3.69s
    expect(agent('coder_0').bubble?.text).toBe('📦 交付模块')
  })

  it('未出现 integrator 发言 → 不串门', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'coding_progress' })
    tick(1000)
    for (let i = 0; i < 3; i++) expect(agent(`coder_${i}`).activity).toBe('work')
  })
})

describe('stateMachine FAIL 回跳', () => {
  beforeEach(() => init())

  it('phase_retry: inspector + 验证 agent worried，横幅带轮次', () => {
    dispatch({ event: 'phase_retry', phase: 'Verification', loop: 2 })
    expect(agent('inspector').mood).toBe('worried')
    expect(agent('fixer').mood).toBe('worried')
    expect(agent('fixer').bubble?.text).toBe('还有问题？')
    expect(getStage().banner).toContain('第 2 轮')
    expect(getStage().glowZone).toBe('')
  })

  it('回跳后 phase_start(Verification) 保持 worried（正交叠加）', () => {
    dispatch({ event: 'phase_retry', phase: 'Verification', loop: 1 })
    dispatch({ event: 'phase_start', phase: 'Verification' })
    expect(agent('fixer').activity).toBe('think')
    expect(agent('fixer').mood).toBe('worried')
  })

  it('验证通过后 phase_end → celebrate，mood 重置', () => {
    dispatch({ event: 'phase_retry', phase: 'Verification', loop: 1 })
    dispatch({ event: 'phase_start', phase: 'Verification' })
    dispatch({ event: 'phase_end', phase: 'Verification' })
    const f = agent('fixer')
    expect(f.activity).toBe('celebrate')
    expect(f.mood).toBe('happy')
    tick(1300)
    expect(agent('fixer').mood).toBe('calm')
  })

  it('横幅 5s 后消失', () => {
    dispatch({ event: 'phase_retry', phase: 'Verification', loop: 1 })
    tick(5100)
    expect(getStage().banner).toBeNull()
  })

  it('回跳横幅不被紧随的 phase_start 覆盖，5s 后消失', () => {
    dispatch({ event: 'phase_retry', phase: 'Verification', loop: 1 })
    dispatch({ event: 'phase_start', phase: 'Verification' })
    expect(getStage().banner).toContain('质检未通过')
    tick(5100)
    expect(getStage().banner).toBeNull()
  })

  it('phase_retry reason=error → 阶段出错横幅 + 出错气泡', () => {
    dispatch({ event: 'phase_retry', phase: 'Coding', loop: 2, reason: 'error' })
    expect(getStage().banner).toContain('阶段出错')
    expect(getStage().banner).toContain('第 2 次')
    expect(agent('coder_0').bubble?.text).toBe('出错了？')
  })
})

describe('stateMachine 真实流程串门（tool 事件触发）', () => {
  beforeEach(() => init())

  it('tool_pre_use(integrator) → coder 串门交付；再次触发不重放', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    expect(agent('coder_0').target).toEqual({ x: 880, y: 330 })
    // 走完交付 + 停留 + 回家（含错峰最后出发的 coder，最远单程约 4.3s）
    tick(125)
    let guard = 0
    while (guard++ < 12 &&
           [0, 1, 2].some((i) => agent(`coder_${i}`).activity === 'walk')) {
      tick(4000)
    }
    for (let i = 0; i < 3; i++) expect(agent(`coder_${i}`).activity).toBe('idle')
    // 本阶段内第二次 integrator 工具事件 → 不再触发
    dispatch({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file' })
    tick(500)
    for (let i = 0; i < 3; i++) {
      expect(agent(`coder_${i}`).activity).not.toBe('walk')
      expect(agent(`coder_${i}`).target).toBeNull()
    }
  })

  it('tool_pre_use(fixer) → 有问题的 reviewer 串门递交', () => {
    dispatch({ event: 'phase_start', phase: 'Verification' })
    dispatch({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [{}] })   // reviewer_0 发现问题（真实流程：审查先于 fixer 开工）
    dispatch({ event: 'tool_pre_use', agent: 'fixer', tool: 'write_file' })
    tick(125)
    expect(agent('reviewer_0').activity).toBe('walk')
    expect(agent('reviewer_0').target).toEqual({ x: 880, y: 160 })
  })

  it('phase_error: 相关区域小人 worried + 出错横幅，不被紧随的 phase_start 覆盖', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'phase_error', phase: 'Coding' })
    expect(agent('coder_0').mood).toBe('worried')
    expect(agent('coder_0').bubble?.text).toBe('出错了？')
    expect(agent('integrator').mood).toBe('worried')
    expect(agent('product_manager').mood).toBe('calm')   // 非本阶段 agent 不受影响
    expect(getStage().banner).toBe('⚠️ 阶段出错')
    dispatch({ event: 'phase_start', phase: 'Coding' })
    expect(getStage().banner).toBe('⚠️ 阶段出错')        // 3s 持续期内不被阶段横幅覆盖
    tick(3100)
    expect(getStage().banner).toBeNull()
  })

  it('phase_start 重置串门标记：新阶段可再次触发', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    dispatch({ event: 'phase_end', phase: 'Coding' })
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file' })
    tick(125)
    expect(agent('coder_0').activity).toBe('walk')
    expect(agent('coder_0').target).toEqual({ x: 880, y: 330 })
  })
})

describe('stateMachine 模块 tag 映射（编码可见性）', () => {
  beforeEach(() => init())

  it('未知 agent 名（coder 模块 tag）稳定映射到 coder 座位', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'cli', tool: 'write_file' })
    expect(agent('coder_0').activity).toBe('work')
    dispatch({ event: 'tool_pre_use', agent: 'counter', tool: 'write_file' })
    expect(agent('coder_1').activity).toBe('work')
    // 同一 tag 再次出现 → 仍映射到同一 coder
    dispatch({ event: 'tool_pre_use', agent: 'cli', tool: 'read_file' })
    expect(agent('coder_0').activity).toBe('work')
    expect(agent('coder_2').activity).not.toBe('work')   // 未分到活的 coder（phase_start 后为 think）
  })

  it('阶段名里程碑（Coding 等）不映射到 coder', () => {
    dispatch({ event: 'conversation_turn', agent: 'Coding', content: '{}' })
    for (let i = 0; i < 3; i++) expect(agent(`coder_${i}`).activity).toBe('idle')
  })

  it('新阶段重置 tag 映射', () => {
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'cli', tool: 'write_file' })
    expect(agent('coder_0').activity).toBe('work')
    dispatch({ event: 'phase_end', phase: 'Coding' })
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'other_mod', tool: 'write_file' })
    expect(agent('coder_0').activity).toBe('work')   // 新阶段重新轮转 → coder_0
  })
})

describe('stateMachine 办公室彩蛋', () => {
  beforeEach(() => { init(); setEggsEnabled(true) })
  afterEach(() => { vi.restoreAllMocks(); setEggsEnabled(false) })

  it('摸鱼接水：idle 小人随机走向饮水机', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)
    const pm = agent('product_manager')   // agents Map 首项 = AGENT_MAP 首项
    expect(pm.activity).toBe('walk')
    expect(pm.target).toEqual({ x: 24, y: 110 })
    expect(pm.waterBreak).toBe(true)
    expect(pm.bubble?.text).toBe('去接水')
  })

  it('占用互斥：有人接水时其他人不去', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)
    const walkers = getAgentStates().filter((a) => a.activity === 'walk')
    expect(walkers.length).toBe(1)
  })

  it('接水完成：到达 → 💧 接水… → 停留 → 回座位', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)          // PM (580,250) → (24,110) 距离 ≈573px ≈ 4.1s
    tick(4200)         // 到达（落地 tick）
    expect(agent('product_manager').bubble?.text).toBe('💧 接水…')
    expect(agent('product_manager').deliverHoldUntil).toBeGreaterThan(0)
    tick(2200)         // 停留结束 → 回座位
    tick(4500)         // 走回家
    const pm = agent('product_manager')
    expect(pm.activity).toBe('idle')
    expect(pm.pos.x).toBeCloseTo(580, 0)
    expect(pm.waterBreak).toBe(false)
  })

  it('收到自己的事件 → 取消接水', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)
    dispatch({ event: 'conversation_turn', agent: 'product_manager', content: '{}' })
    expect(agent('product_manager').activity).toBe('talk')
    expect(agent('product_manager').waterBreak).toBe(false)
  })

  it('打盹：idle 小人冒 💤 气泡 3s 后消失', () => {
    // 序列：water 掷骰 0.5（不触发）→ snooze 掷骰 0.001（触发）→ 💤/🥱 掷骰 0.001（💤）
    const seq = [0.5, 0.001, 0.001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)
    const snoozer = getAgentStates().find((a) => a.bubble && (a.bubble.text === '💤' || a.bubble.text === '🥱'))
    expect(snoozer).toBeDefined()
    tick(3100)
    expect(snoozer!.bubble).toBeNull()
  })

  it('工作中不接水：coding 阶段 coder 保持 work，PM（idle）去接水', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    dispatch({ event: 'phase_start', phase: 'Coding' })
    dispatch({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' })
    tick(125)
    for (let i = 0; i < 3; i++) expect(agent(`coder_${i}`).activity).not.toBe('walk')
    expect(agent('product_manager').activity).toBe('walk')   // idle 的 PM 不受影响
  })

  it('默认关闭：不启用时 tick 无彩蛋', () => {
    setEggsEnabled(false)
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)
    for (const a of getAgentStates()) expect(a.activity).toBe('idle')
  })
})

describe('stateMachine 彩蛋二批', () => {
  beforeEach(() => { init(); setEggsEnabled(true) })
  afterEach(() => { vi.restoreAllMocks(); setEggsEnabled(false) })

  it('摸鱼池：气泡来自 [💤🥱☕📱🍪]', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    tick(125)
    const snoozer = getAgentStates().find((a) => a.bubble && a.bubble.text !== '去接水')
    expect(snoozer).toBeDefined()
    expect(['💤', '🥱', '☕', '📱', '🍪']).toContain(snoozer!.bubble!.text)
  })

  it('下午茶：idle 小人走向休息位 (100,240) 并朝右入座', () => {
    // 序列：接水掷骰 0.5（不触发）→ 喝茶掷骰 0.001（触发）
    const seq = [0.5, 0.001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)
    const drinker = getAgentStates().find((a) => a.teaSpot >= 0)
    expect(drinker).toBeDefined()
    expect(drinker!.activity).toBe('walk')
    expect(drinker!.target).toEqual({ x: 100, y: 240 })
    expect(drinker!.bubble?.text).toBe('去喝茶')
    // 到达入座 → 席位 0 朝右（PM 距席位 ~480px ≈ 3.4s）
    tick(3500)
    expect(drinker!.facing).toBe('R')
  })

  it('下午茶成对：两人各占一个休息位，朝向相反', () => {
    const seq = [0.5, 0.001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)
    expect(agent('product_manager').teaSpot).toBe(0)            // Map 首项
    expect(agent('product_manager').target).toEqual({ x: 100, y: 240 })
    expect(agent('chief_technology_officer').teaSpot).toBe(1)   // 成对加入 → 第二席位
    expect(agent('chief_technology_officer').target).toEqual({ x: 220, y: 240 })
    // 两人同时入座 → 席位 0 朝右、席位 1 朝左
    tick(3500)
    expect(agent('product_manager').facing).toBe('R')
    expect(agent('chief_technology_officer').facing).toBe('L')
  })

  it('冷却：休息开始后 90s 冷却期内不再触发新的，冷却结束才放行', () => {
    // 水掷骰 0.9 不触发 → 茶掷骰 0.001 触发；冷却期内水/茶分支都要求 cooldownOk
    const seq = [0.9, 0.001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)                       // PM 开喝 → 冷却 90250 结束
    expect(getAgentStates().filter((a) => a.teaSpot >= 0).length).toBe(2)  // 成对
    tick(125)                       // 冷却中
    expect(getAgentStates().filter((a) => a.waterBreak).length).toBe(0)
    expect(getAgentStates().filter((a) => a.teaSpot >= 0).length).toBe(2)  // 无新开席
    tick(3000); tick(25000); tick(3000); tick(25000)    // 时钟 56250，仍冷却中
    tick(5000)                      // 起身回座位 → 61250 到家
    expect(getAgentStates().filter((a) => a.waterBreak).length).toBe(0)
    expect(getAgentStates().filter((a) => a.teaSpot >= 0).length).toBe(0)  // 已回座位
    tick(35000)                     // 96250 — 仍冷却中（冷却随机项 0.9 → 117125 结束）
    expect(getAgentStates().filter((a) => a.waterBreak || a.teaSpot >= 0).length).toBe(0)
    tick(25000)                     // 121250 ≥ 117125 → 冷却结束
    const breaks = getAgentStates().filter((a) => a.waterBreak || a.teaSpot >= 0)
    expect(breaks.length).toBeGreaterThan(0)            // 冷却结束才允许新的休息
  })

  it('下午茶坐久点：入座后停留 15s 才起身回座位', () => {
    const seq = [0.5, 0.001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)
    const pm = agent('product_manager')
    expect(pm.teaSpot).toBe(0)
    tick(3500)                      // 到达入座（PM 距席位 ~480px ≈ 3.4s）
    expect(pm.facing).toBe('R')     // 席位 0 朝右
    expect(pm.frame).toBe(0)        // 站立帧（不走动帧）
    tick(12000)                     // 时钟 ~15.5s：仍就座（TEA_HOLD_MS = 15s）
    expect(pm.teaSpot).toBe(0)
    expect(pm.facing).toBe('R')
    tick(4000)                      // ~19.5s ≥ 入座+15s → 起身回座位
    expect(pm.activity).toBe('walk')
    expect(pm.target).toEqual({ x: 580, y: 250 })
    tick(5000)                      // 到家 → 精确归位
    expect(pm.teaSpot).toBe(-1)
    expect(pm.activity).toBe('idle')
  })

  it('PM 提问：discuss_choice → 进入交流中并冒气泡', () => {
    dispatch({ event: 'discuss_choice', question: '做什么？', options: ['A'] })
    const pm = agent('product_manager')
    expect(pm.activity).toBe('talk')
    expect(pm.mood).toBe('happy')
    expect(pm.bubble?.text).toBe('请选择…')
  })

  it('PM 提问等待期间气泡持续刷新；阶段结束复位', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)   // pool[floor(0.5*3)] = '想好了吗？'
    dispatch({ event: 'discuss_choice', question: '做什么？', options: ['A'] })
    tick(3000)                      // 提问气泡过期 → 等待期间同 tick 立即刷新
    expect(agent('product_manager').bubble?.text).toBe('想好了吗？')
    tick(125)
    expect(agent('product_manager').bubble).not.toBeNull()   // 仍在刷新
    dispatch({ event: 'phase_end', phase: 'RequirementsDiscussion' })
    const pm = agent('product_manager')
    expect(pm.activity).not.toBe('talk')                     // 阶段边界复位
    expect(pm.bubble?.text ?? '').not.toMatch(/请选择|想好了|等你/)
  })

  it('摸鱼碎碎念：低频小消息气泡', () => {
    // 序列：接水 0.5 不触发 → 喝茶 0.9 不触发 → 打盹 0.9 不触发 → 碎碎念 0.0001 触发（< 0.001）
    const seq = [0.5, 0.9, 0.9, 0.0001]
    let i = 0
    vi.spyOn(Math, 'random').mockImplementation(() => seq[i++ % seq.length])
    tick(125)
    const chatter = getAgentStates().find((a) => a.bubble && a.bubble.text.length > 2)
    expect(chatter).toBeDefined()
    expect(['等会下班吃什么呢…', '好想涨工资…', '这个任务真的好难…',
            '周末去哪玩呢…', '这代码谁写的…', '摸鱼一时爽，加班火葬场…'])
      .toContain(chatter!.bubble!.text)
  })

  it('阶段边界归位重置朝向：接水走路中被 phase_end 拉回 → 朝向复原', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    dispatch({ event: 'phase_start', phase: 'Coding' })
    tick(125)                       // PM idle → 去接水（朝左走）
    expect(agent('product_manager').activity).toBe('walk')
    expect(agent('product_manager').facing).toBe('L')
    dispatch({ event: 'phase_end', phase: 'Coding' })   // 硬边界拉回
    const pm = agent('product_manager')
    expect(pm.pos).toEqual({ x: 580, y: 250 })
    expect(pm.activity).toBe('idle')
    expect(pm.facing).toBe('D')     // char07_D_f0 → 正面
  })

  it('断电：blackoutBubble 随机 2 个 idle 小人冒 ❓', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    blackoutBubble()
    const q = getAgentStates().filter((a) => a.bubble?.text === '❓')
    expect(q.length).toBe(2)
    expect(q[0].id).toBe('product_manager')
    expect(q[1].id).toBe('chief_technology_officer')
  })

  it('竣工典礼：pipeline_complete 成功 → 全员走向中央环形站位并正面 celebrate', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    dispatch({ event: 'pipeline_complete', failed: false })
    for (const a of getAgentStates()) {
      expect(a.activity).toBe('walk')
      expect(a.ceremony).toBe(true)
      expect(a.target).toBeTruthy()
    }
    tick(6000)                      // 环形半径 170 + 抖动 ≤20px；最远 agent ~600px ≈ 4.3s
    for (const a of getAgentStates()) {
      const d = Math.hypot(a.pos.x - 608, a.pos.y - 439.5)
      expect(d).toBeLessThan(200)   // 中央环形区域
      expect(a.activity).toBe('celebrate')
      expect(a.mood).toBe('happy')
      expect(a.facing).toBe('D')    // 全员正面
    }
  })

  it('竣工典礼：环形站位，两两间距 > 20px（不重叠）', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    dispatch({ event: 'pipeline_complete', failed: false })
    const targets = getAgentStates().map((a) => a.target!)
    for (let i = 0; i < targets.length; i++) {
      for (let j = i + 1; j < targets.length; j++) {
        const d = Math.hypot(targets[i].x - targets[j].x, targets[i].y - targets[j].y)
        expect(d).toBeGreaterThan(20)
      }
    }
  })

  it('竣工典礼：失败不举行', () => {
    dispatch({ event: 'pipeline_complete', failed: true })
    for (const a of getAgentStates()) {
      expect(a.activity).toBe('idle')
      expect(a.ceremony).toBe(false)
      expect(a.target).toBeNull()
    }
  })

  it('典礼后不再触发休息：全员留在中央', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    dispatch({ event: 'pipeline_complete', failed: false })
    tick(5000)                     // 全部到达 → celebrate
    tick(2000)                     // celebrate 衰减 → idle
    tick(30000)                    // 冷却早已过期；若彩蛋仍激活会有人去接水/喝茶
    for (const a of getAgentStates()) {
      expect(a.waterBreak).toBe(false)
      expect(a.teaSpot).toBe(-1)
      expect(a.activity).toBe('idle')
      const d = Math.hypot(a.pos.x - 608, a.pos.y - 439.5)
      expect(d).toBeLessThan(200)  // 仍在中央环
    }
  })
})
