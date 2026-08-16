import { describe, expect, it } from 'vitest'
import { phaseBanner, celebrateIds, retryBanner, retryWorried } from '../director'

describe('director', () => {
  it('phaseBanner 中文名', () => {
    expect(phaseBanner('Coding')).toBe('编码')
    expect(phaseBanner('QualityGate')).toBe('质检')
    expect(phaseBanner('Unknown')).toBe('Unknown')
  })

  it('celebrateIds 返回该阶段 agents', () => {
    const ids = celebrateIds('Coding')
    expect(ids).toContain('coder_0')
    expect(ids).toContain('integrator')
    expect(ids).not.toContain('product_manager')
  })

  it('retryBanner 带轮次', () => {
    expect(retryBanner(2)).toContain('第 2 轮')
    expect(retryBanner(2)).toContain('重新修复')
  })

  it('retryWorried = 该阶段 agents + inspector', () => {
    const ids = retryWorried('Verification')
    expect(ids).toContain('fixer')
    expect(ids).toContain('inspector')
  })

  it('retryWorried QualityGate：inspector 已在 base，不重复', () => {
    const ids = retryWorried('QualityGate')
    expect(ids).toContain('inspector')
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('retryBanner 默认轮次（0）→ 第 1 轮', () => {
    expect(retryBanner(0)).toContain('第 1 轮')
  })

  it('retryBanner reason=error → 阶段出错文案（第 N 次）', () => {
    expect(retryBanner(2, 'error')).toContain('阶段出错')
    expect(retryBanner(2, 'error')).toContain('第 2 次')
    expect(retryBanner(0, 'error')).toContain('第 1 次')
    expect(retryBanner(1, 'error')).not.toContain('质检')
  })
})
