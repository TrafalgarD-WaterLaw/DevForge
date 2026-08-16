import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import HistoryPanel from '../HistoryPanel.vue'

const PROJECTS = {
  projects: [
    { id: 'demo_DevForge_20260802_1', name: 'demo_DevForge_1', task: '命令行字数统计', status: 'done', files: ['main.py', 'counter.py'], updated: 1754090000 },
    { id: 'demo2_DevForge_20260802_2', name: 'demo2_DevForge_2', task: '待办看板', status: 'defect', files: ['app.py'], updated: 1754091000 },
  ],
}
const EVENTS = {
  events: [
    { event: 'phase_start', phase: 'Design', timestamp: 100 },
    { event: 'phase_end', phase: 'Design', timestamp: 160 },
    { event: 'quality_gate', data: { verdict: 'PASS', features: [{ name: '统计', status: 'YES' }] } },
    { event: 'pipeline_complete', failed: false },
  ],
}
const EVENTS_FAIL = {
  events: [
    { event: 'quality_gate', data: { verdict: 'FAIL', features: [
      { name: '统计行数', status: 'NO', notes: '未实现' },
      { name: '多文件', status: 'PARTIAL' },
    ] } },
    { event: 'pipeline_complete', failed: true },
  ],
}

// URL 路由式 fetch mock：HistoryPanel 打开时会同时请求 /api/projects 与
// /api/memory（E1 记忆概览），Once 序列会被额外调用打乱
function stubFetch(handlers: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn((url: string) =>
    Promise.resolve({ json: async () => (handlers[url] ?? {}) })))
}

describe('HistoryPanel 历史记录', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('打开 → 加载项目列表（任务名 + 状态徽章）', async () => {
    stubFetch({ '/api/projects': PROJECTS })
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    const items = wrapper.findAll('.hp-item')
    expect(items.length).toBe(2)
    expect(items[0].text()).toContain('命令行字数统计')
    expect(items[0].text()).toContain('✅ 完成')
    expect(items[1].text()).toContain('⚠️ 有缺陷')
    // E1: 记忆概览同时加载（无数据 → 头部显示 0）
    expect(wrapper.find('.hp-memory-head').text()).toContain('记忆库')
  })

  it('点开项目 → 质检报告（PASS 绿卡 + 无缺失）', async () => {
    stubFetch({
      '/api/projects': PROJECTS,
      '/api/projects/demo_DevForge_20260802_1/events': EVENTS,
      '/api/projects/demo_DevForge_20260802_1': { id: 'x', files: { 'main.py': 'x' } },
    })
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.findAll('.hp-item')[0].trigger('click')
    await flushPromises()
    const qg = wrapper.find('.hp-qg')
    expect(qg.classes()).toContain('PASS')
    expect(qg.text()).toContain('✅ 质检通过')
    expect(wrapper.find('.hp-phase').text()).toContain('设计')
    expect(wrapper.find('.hp-phase em').text()).toContain('60s')
    expect(wrapper.find('.hp-file').text()).toBe('main.py')
  })

  it('FAIL 项目 → 红卡 + 缺失功能列表', async () => {
    stubFetch({
      '/api/projects': PROJECTS,
      '/api/projects/demo2_DevForge_20260802_2/events': EVENTS_FAIL,
      '/api/projects/demo2_DevForge_20260802_2': { id: 'x', files: {} },
    })
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.findAll('.hp-item')[1].trigger('click')
    await flushPromises()
    const qg = wrapper.find('.hp-qg')
    expect(qg.classes()).not.toContain('PASS')
    expect(qg.text()).toContain('质检未通过')
    const missing = wrapper.findAll('.hp-qg-missing li').map((li) => li.text())
    expect(missing[0]).toContain('统计行数')
    expect(missing[1]).toContain('多文件')
  })

  it('加载失败 → 错误提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')))
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('.hp-error').text()).toContain('后端未连接')
  })

  it('记忆区：展开显示最近条目 + 清空按钮', async () => {
    stubFetch({
      '/api/projects': PROJECTS,
      '/api/memory': {
        phases: { count: 1, recent: [{ id: 'x-Design', phase: 'Design', summary: 'CLI 设计', timestamp: 1 }] },
        functions: { count: 2, recent: [] },
      },
    })
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('.hp-memory-head').text()).toContain('阶段 1 · 函数 2')
    await wrapper.find('.hp-memory-head').trigger('click')
    await flushPromises()
    expect(wrapper.find('.hp-mem-sum').text()).toContain('CLI 设计')
    expect(wrapper.find('.hp-mem-clear').exists()).toBe(true)
  })
})

describe('HistoryPanel 产物文件查看器', () => {
  it('点击文件 → 显示内容；再点关闭', async () => {
    stubFetch({
      '/api/projects': PROJECTS,
      '/api/projects/demo_DevForge_20260802_1/events': EVENTS,
      '/api/projects/demo_DevForge_20260802_1': { id: 'x', files: { 'main.py': 'print(1)\n', 'counter.py': 'def c(): pass\n' } },
    })
    const wrapper = mount(HistoryPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.findAll('.hp-item')[0].trigger('click')
    await flushPromises()
    // 点击 main.py → 查看内容
    const files = wrapper.findAll('.hp-file')
    await files[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.hp-file-view').text()).toContain('print(1)')
    expect(files[0].classes()).toContain('active')
    // 再点 → 关闭
    await wrapper.findAll('.hp-file')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.hp-file-view').exists()).toBe(false)
  })
})
