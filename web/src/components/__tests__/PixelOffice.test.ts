import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import PixelOffice from '../PixelOffice.vue'

interface OfficeVm {
  onEvent(e: Record<string, unknown>): void
}

// PixelOffice 依赖 ResizeObserver（happy-dom 实现不可靠/不一致），统一 stub
class ROStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('PixelOffice 冒烟', () => {
  let wrapper: ReturnType<typeof mount> | null = null

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ROStub)
  })

  afterEach(() => {
    wrapper?.unmount()   // onUnmounted 清除全部定时器
    wrapper = null
    vi.unstubAllGlobals()
  })

  function mountOffice() {
    wrapper = mount(PixelOffice)
    return wrapper
  }

  function agentByName(name: string) {
    const el = wrapper!.findAll('.po-agent').find((a) => a.attributes('data-name') === name)
    if (!el) throw new Error(`agent not found: ${name}`)
    return el
  }

  it('挂载即初始化渲染 16 个 agent', async () => {
    const office = mountOffice()
    await nextTick()
    expect(office.findAll('.po-agent')).toHaveLength(16)
  })

  it('onEvent phase_start Coding → coder_0 进入 think 状态', async () => {
    const office = mountOffice()
    ;(office.vm as unknown as OfficeVm).onEvent({ event: 'phase_start', phase: 'Coding' })
    await nextTick()
    expect(agentByName('Dev1').classes()).toContain('think')
    // 非本阶段 agent 归位 idle
    expect(agentByName('PM').classes()).toContain('idle')
  })

  it('onEvent tool_pre_use coder_0 → work 状态', async () => {
    const office = mountOffice()
    const vm = office.vm as unknown as OfficeVm
    vm.onEvent({ event: 'phase_start', phase: 'Coding' })
    vm.onEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' })
    await nextTick()
    expect(agentByName('Dev1').classes()).toContain('work')
  })

  it('点击 agent → 弹出信息卡（displayName）', async () => {
    const office = mountOffice()
    await nextTick()
    await agentByName('PM').trigger('click')
    expect(office.find('.po-agent-card').exists()).toBe(true)
    expect(office.find('.po-agent-card .pac-name').text()).toBe('PM')
  })
})
