// web/src/components/director.ts
// 轻量导演层 — 阶段级叙事节拍的纯函数（横幅/庆祝/FAIL 焦虑）。
import { PHASE_ZONE } from './spriteDefs'
import { PHASE_LABELS_CN } from '../phases'

export function phaseBanner(phase: string): string {
  return PHASE_LABELS_CN[phase] ?? phase
}

export function celebrateIds(phase: string): string[] {
  return PHASE_ZONE[phase]?.agents ?? []
}

export function retryBanner(loop: number, reason?: string): string {
  if (reason === 'error') return `阶段出错，正在重试（第 ${loop || 1} 次）`
  return `质检未通过，回到验证重新修复（第 ${loop || 1} 轮）`
}

export function retryWorried(phase: string): string[] {
  const base = PHASE_ZONE[phase]?.agents ?? []
  return base.includes('inspector') ? base : [...base, 'inspector']
}
