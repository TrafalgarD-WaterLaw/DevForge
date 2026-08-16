import { describe, expect, it } from 'vitest'
import { AGENT_MAP, PHASE_ZONE, VERBS, TOOL_VERBS, DEFAULT_VERBS, zoneBBox, spriteFor } from '../spriteDefs'

describe('spriteDefs', () => {
  it('spriteFor 按朝向+帧拼路径', () => {
    const pm = AGENT_MAP.product_manager
    expect(spriteFor(pm, 'D', 0)).toBe('npc/char07_D_f0.png')
    expect(spriteFor(pm, 'L', 2)).toBe('npc/char07_L_f2.png')
  })

  it('PHASE_ZONE 覆盖六个阶段且 agent 存在', () => {
    for (const [, v] of Object.entries(PHASE_ZONE)) {
      expect(v.agents.length).toBeGreaterThan(0)
      for (const id of v.agents) expect(AGENT_MAP[id]).toBeDefined()
    }
  })

  it('角色动词表覆盖 talk 活动', () => {
    for (const [id, verbs] of Object.entries(VERBS)) {
      expect(verbs.talk, `${id} 缺 talk 动词`).toBeTruthy()
      expect(AGENT_MAP[id], `${id} 不在 AGENT_MAP`).toBeDefined()
    }
  })

  it('工具动词表 + 兜底', () => {
    expect(TOOL_VERBS.write_file).toBe('写代码')
    expect(DEFAULT_VERBS.think).toBe('思考')
  })

  it('zoneBBox 由座位推导且非空', () => {
    const b = zoneBBox('coding')
    expect(b.w).toBeGreaterThan(0)
    expect(b.h).toBeGreaterThan(0)
  })
})
