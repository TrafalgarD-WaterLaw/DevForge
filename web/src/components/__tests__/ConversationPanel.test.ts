import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, nextTick } from 'vue'
import ConversationPanel from '../ConversationPanel.vue'
import { createFeed } from '../feed'
import type { FeedItem } from '../feed'

function mountWithFeed() {
  const feed = createFeed({ items: reactive<FeedItem[]>([]) })
  // 问答发生在运行中：interactive=true（N14 修复后流程结束/出错时
  // 问题卡禁交互，测试须显式模拟"运行中"）
  const wrapper = mount(ConversationPanel, { props: { items: feed.items, interactive: true } })
  return { feed, wrapper }
}

function btnWithText(wrapper: ReturnType<typeof mount>, text: string) {
  const btn = wrapper.findAll('.q-btn').find((b) => b.text() === text)
  if (!btn) throw new Error(`option button not found: ${text}`)
  return btn
}

describe('ConversationPanel 问答流', () => {
  it('discuss_choice（真实 wire 形状）→ 渲染选项按钮 + （单选）标签', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '开发什么？', options: ['A', 'B'], allow_multiple: false })
    await nextTick()
    expect(wrapper.findAll('.q-btn').map((b) => b.text())).toEqual(['A', 'B', '其他'])
    expect(wrapper.text()).toContain('（单选）')
  })

  it('allow_multiple 时显示（可多选）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '选择技术栈', options: ['Vue', 'React'], allow_multiple: true })
    await nextTick()
    expect(wrapper.text()).toContain('（可多选）')
  })

  it('点击选项 + 确认 → 触发 confirm 事件（selected/custom 载荷）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '开发什么？', options: ['A', 'B'], allow_multiple: false })
    await nextTick()
    await btnWithText(wrapper, 'A').trigger('click')
    const confirmBtn = wrapper.find('.q-confirm')
    // 有选择 → 确认按钮可用
    expect(confirmBtn.attributes('disabled')).toBeUndefined()
    await confirmBtn.trigger('click')
    const emitted = wrapper.emitted('confirm')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toEqual({ id: 0, selected: ['A'], custom: '' })
  })

  it('其他 → 展开自定义输入 → 确认携带 custom 文本', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '开发什么？', options: ['A', 'B'], allow_multiple: false })
    await nextTick()
    await btnWithText(wrapper, '其他').trigger('click')
    const input = wrapper.find('.q-input')
    expect(input.exists()).toBe(true)
    await input.setValue('聊天机器人')
    await wrapper.find('.q-confirm').trigger('click')
    expect(wrapper.emitted('confirm')![0][0]).toEqual({ id: 0, selected: [], custom: '聊天机器人' })
  })

  it('已回答 → 默认折叠单行摘要；展开后按钮“已选择”、选项禁用、展示已选内容', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '开发什么？', options: ['A', 'B'], allow_multiple: false })
    await nextTick()
    feed.answerQuestion(0, ['A'], '')
    await nextTick()
    // 已回答 → 折叠成单行摘要
    const collapsed = wrapper.find('.q-collapsed')
    expect(collapsed.exists()).toBe(true)
    expect(collapsed.text()).toContain('✅ 已选择：A')
    // 点击展开 → 完整卡：已选择/禁用/已选内容
    await collapsed.trigger('click')
    await nextTick()
    expect(wrapper.find('.q-collapsed').exists()).toBe(false)
    expect(wrapper.find('.q-confirm').text()).toContain('已选择')
    expect(wrapper.find('.q-confirm').attributes('disabled')).toBeDefined()
    for (const b of wrapper.findAll('.q-btn')) {
      expect(b.attributes('disabled')).toBeDefined()
    }
    expect(wrapper.find('.q-done').text()).toContain('A')
    // 收起按钮 → 回到折叠单行
    await wrapper.find('.q-collapse-btn').trigger('click')
    await nextTick()
    expect(wrapper.find('.q-collapsed').exists()).toBe(true)
  })
})

describe('ConversationPanel 工作消息', () => {
  it('tool_pre_use → work 行渲染动词/文件/+N 行/摘要；合并计数；失败错误摘要', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({
      event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file',
      args: { filename: 'src/main.py', content: 'import os\nprint("hi")\n' },
    })
    await nextTick()
    let row = wrapper.find('.work-row')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('coder_0')
    expect(row.text()).toContain('写代码')
    expect(row.text()).toContain('src/main.py')
    expect(row.text()).toContain('+2 行')
    expect(row.text()).toContain('“import os”')

    // 同窗口同 agent 同工具 → 合并计数 ×2
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { filename: 'src/main.py' } })
    await nextTick()
    expect(wrapper.find('.work-row').text()).toContain('×2')

    // 失败 → fail 样式 + 去路径错误摘要
    feed.addEvent({
      event: 'tool_post_use', agent: 'coder_0', tool: 'write_file',
      result_preview: 'Traceback (most recent call last):\n  E:\\projects\\x\\main.py:1 in <module>',
    })
    await nextTick()
    row = wrapper.find('.work-row')
    expect(row.classes()).toContain('fail')
    expect(row.text()).toContain('main.py:1 in <module>')
  })
})

describe('ConversationPanel 名称头像标准化', () => {
  it('agent id → 中文名 + emoji（chief_technology_officer → 技术总监 🏗️）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_technology_officer', content: JSON.stringify({ message: '架构方案' }) })
    await nextTick()
    expect(wrapper.find('.who').text()).toBe('技术总监')
    expect(wrapper.find('.avatar').text()).toBe('🏗️')
  })

  it('未注册 tag（如模块名 cli）→ 原名回退 + 🤖', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'conversation_turn', agent: 'cli', content: JSON.stringify({ message: 'x' }) })
    await nextTick()
    expect(wrapper.find('.who').text()).toBe('cli')
    expect(wrapper.find('.avatar').text()).toBe('🤖')
  })

  it('question 卡标题也用标准名（product_manager → 产品经理 📝）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'discuss_choice', question: '做什么？', options: ['A'], allow_multiple: false })
    await nextTick()
    expect(wrapper.find('.who').text()).toBe('产品经理')
    expect(wrapper.find('.avatar').text()).toBe('📝')
  })
})

describe('ConversationPanel 等待占位与系统卡片', () => {
  it('空对话 + placeholder → 显示思考中占位（跳动点）', async () => {
    const feed = createFeed({ items: reactive<FeedItem[]>([]) })
    const wrapper = mount(ConversationPanel, {
      props: { items: feed.items, placeholder: '产品经理正在梳理需求…' },
    })
    await nextTick()
    expect(wrapper.find('.thinking-text').text()).toBe('产品经理正在梳理需求…')
    expect(wrapper.findAll('.thinking-dots i').length).toBe(3)
  })

  it('无 placeholder → 默认"等待 Agent 对话..."', async () => {
    const feed = createFeed({ items: reactive<FeedItem[]>([]) })
    const wrapper = mount(ConversationPanel, { props: { items: feed.items } })
    await nextTick()
    expect(wrapper.text()).toContain('等待 Agent 对话')
  })

  it('error 事件 → 红色错误卡片（system-error）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'error', message: 'Traceback: boom' })
    await nextTick()
    // .system 类在 msg 根 div 与内层卡片上都存在 — 检查内层卡片
    const sys = wrapper.find('.msg.system .system')
    expect(sys.classes()).toContain('system-error')
    expect(sys.text()).toContain('运行出错')
    expect(sys.text()).toContain('boom')
  })

  it('phase_error 事件 → 错误卡片含阶段名', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'phase_error', phase: 'Coding', error: 'ModuleNotFoundError: x' })
    await nextTick()
    const sys = wrapper.find('.msg.system .system')
    expect(sys.classes()).toContain('system-error')
    expect(sys.text()).toContain('编码 阶段出错')
    expect(sys.text()).toContain('ModuleNotFoundError')
  })

  it('气泡按角色着色：--accent 注入（CTO 蓝）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_technology_officer', content: JSON.stringify({ message: '架构' }) })
    await nextTick()
    const msg = wrapper.find('.msg')
    expect(msg.attributes('style')).toContain('--accent: #2563eb')
  })
})

describe('ConversationPanel 结构化内容', () => {
  it('JSON 多键内容 → 文档卡（主标题 + 分节要点）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({
      event: 'conversation_turn', agent: 'chief_product_officer',
      content: JSON.stringify({
        message: '产品定位：命令行字数统计工具',
        core_features: ['统计行数', '统计单词数'],
        priorities: { p0: '核心统计', p1: '多文件支持' },
      }),
    })
    await nextTick()
    expect(wrapper.find('.doc-card').exists()).toBe(true)
    expect(wrapper.find('.doc-head').text()).toBe('产品定位：命令行字数统计工具')
    const titles = wrapper.findAll('.doc-sec-title').map((t) => t.text())
    expect(titles).toContain('core_features')
    expect(titles).toContain('priorities')
    expect(wrapper.find('.doc-sec-lines').text()).toContain('统计行数')
  })

  it('纯 message JSON → 普通气泡（非文档卡）', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'conversation_turn', agent: 'coder_0', content: JSON.stringify({ message: '完成' }) })
    await nextTick()
    expect(wrapper.find('.doc-card').exists()).toBe(false)
    expect(wrapper.find('.bbl').text()).toContain('完成')
  })
})

describe('ConversationPanel 聊天气泡', () => {
  it('短文本直接渲染；长文本（>300 字符）折叠并可展开', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'conversation_turn', agent: 'coder_0', content: JSON.stringify({ message: '短消息' }) })
    await nextTick()
    expect(wrapper.find('.bbl').text()).toContain('短消息')
    expect(wrapper.find('.toggle-btn').exists()).toBe(false)

    const long = '甲'.repeat(400)
    feed.addEvent({ event: 'conversation_turn', agent: 'coder_0', content: JSON.stringify({ message: long }) })
    await nextTick()
    const bbls = wrapper.findAll('.bbl')
    const longBbl = bbls[bbls.length - 1]
    expect(longBbl.classes()).toContain('doc')
    expect(wrapper.find('.toggle-btn').text()).toContain('展开全文')
    expect(longBbl.text().length).toBeLessThan(400)

    await wrapper.find('.toggle-btn').trigger('click')
    const bbls2 = wrapper.findAll('.bbl')
    expect(bbls2[bbls2.length - 1].classes()).not.toContain('doc')
    expect(bbls2[bbls2.length - 1].text().length).toBe(400)
    expect(wrapper.find('.toggle-btn').text()).toContain('收起')
  })
})

describe('ConversationPanel todo 卡片', () => {
  it('todo_update → 渲染 📋 卡片 + 完成/进行中标记', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({
      event: 'todo_update', agent: 'coder', done: 1, total: 3,
      todos: [
        { content: '写 main', status: 'completed' },
        { content: '写 utils', status: 'in_progress' },
        { content: '写 cli', status: 'pending' },
      ],
    })
    await nextTick()
    const card = wrapper.find('.todo-card')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('任务清单 1/3')
    expect(card.text()).toContain('写 main')
    const marks = wrapper.findAll('.todo-mark').map((m) => m.text())
    expect(marks).toEqual(['✓', '●', '○'])
  })
})

describe('ConversationPanel 人工审阅与追加需求', () => {
  it('review_request → 审阅卡 + 通过/拒绝按钮', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'review_request', files: ['counter.py'], diff: '--- counter.py\n+++ counter.py\n-old\n+new' })
    await nextTick()
    expect(wrapper.find('.review-card').exists()).toBe(true)
    expect(wrapper.find('.review-title').text()).toContain('counter.py')
    expect(wrapper.findAll('.rv-btn').length).toBe(2)
  })

  it('点通过 → 触发 review-decision 事件', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'review_request', files: ['a.py'], diff: 'x' })
    await nextTick()
    await wrapper.find('.rv-approve').trigger('click')
    const emitted = wrapper.emitted('review-decision')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toEqual({ id: 0, approved: true })
  })

  it('interactive 时显示输入框；发送触发 user-message 并清空', async () => {
    const feed = createFeed({ items: reactive<FeedItem[]>([]) })
    const wrapper = mount(ConversationPanel, { props: { items: feed.items, interactive: true } })
    await nextTick()
    const input = wrapper.find('.composer-input')
    expect(input.exists()).toBe(true)
    await input.setValue('加一个导出功能')
    await wrapper.find('.composer-send').trigger('click')
    const emitted = wrapper.emitted('user-message')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toBe('加一个导出功能')
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('输入框常驻底部；非 interactive 时禁用', async () => {
    const feed = createFeed({ items: reactive<FeedItem[]>([]) })
    const wrapper = mount(ConversationPanel, { props: { items: feed.items, interactive: false } })
    const input = wrapper.find('.composer-input')
    expect(input.exists()).toBe(true)                       // 常驻显示
    expect(input.attributes('disabled')).toBeDefined()      // 未运行禁用
    expect(wrapper.find('.composer-send').attributes('disabled')).toBeDefined()
  })
})

describe('ConversationPanel 流式渲染', () => {
  it('流式条目 → 纯文本 + 光标；结束后 markdown 渲染', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'llm_delta', agent: 'chief_technology_officer', delta: '架构' })
    feed.addEvent({ event: 'llm_delta', agent: 'chief_technology_officer', delta: '方案' })
    await nextTick()
    const bbl = wrapper.find('.bbl.streaming')
    expect(bbl.exists()).toBe(true)
    expect(bbl.text()).toContain('架构方案')
    expect(bbl.find('.stream-caret').exists()).toBe(true)
    feed.addEvent({ event: 'llm_stream_end', agent: 'chief_technology_officer' })
    await nextTick()
    expect(wrapper.find('.bbl.streaming').exists()).toBe(false)
  })
})

describe('ConversationPanel 阶段协作面板', () => {
  it('面板渲染：每 agent 一个小窗口，完成标绿', async () => {
    const { feed, wrapper } = mountWithFeed()
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [] })
    await nextTick()
    const panel = wrapper.find('.stage-panel')
    expect(panel.exists()).toBe(true)
    const win = wrapper.find('.stage-win')
    expect(win.exists()).toBe(true)
    expect(win.text()).toContain('安全审查')
    expect(win.classes()).toContain('done')          // 完成标绿
    feed.addEvent({ event: 'phase_end', phase: 'Verification' })
    await nextTick()
    expect(wrapper.find('.stage-panel.done').exists()).toBe(true)
    expect(wrapper.find('.stage-head').text()).toContain('协作完成')
  })
})
