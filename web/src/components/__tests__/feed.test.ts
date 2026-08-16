import { describe, expect, it } from 'vitest'
import { reactive, effect } from 'vue'
import { createFeed, readableContent, zhErrorHint, type FeedItem } from '../feed'

function feedAt(base: number) {
  let t = base
  return { feed: createFeed({ now: () => t }), advance: (ms: number) => { t += ms } }
}

describe('feed 归一化', () => {
  it('conversation_turn → chat（可读内容提取）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'conversation_turn', agent: 'product_manager', content: '{"message": "hello"}' })
    expect(feed.items[0].type).toBe('chat')
    expect(feed.items[0].agent).toBe('product_manager')
    expect(feed.items[0].content).toBe('hello')
  })

  it('里程碑 agent（Coding/Verification）→ milestone', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'conversation_turn', agent: 'Coding', content: '{"message": "编码完成"}' })
    feed.addEvent({ event: 'conversation_turn', agent: 'Verification', content: '{"message": "审查完成"}' })
    expect(feed.items.map((i) => i.type)).toEqual(['milestone', 'milestone'])
  })

  it('discuss_choice → question 内嵌卡（待回答）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({
      event: 'discuss_choice',
      question: { text: '开发什么类型的产品？', options: ['Web 应用', '移动 App'], allow_multiple: false },
    })
    const q = feed.items[0]
    expect(q.type).toBe('question')
    expect(q.question?.text).toBe('开发什么类型的产品？')
    expect(q.question?.options).toEqual(['Web 应用', '移动 App'])
    expect(q.question?.answered).toBe(false)
  })

  it('tool_pre_use → work 条目（TOOL_VERBS 动词 + 路径细节）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { path: 'main.py' } })
    const w = feed.items[0]
    expect(w.type).toBe('work')
    expect(w.content).toBe('写代码')
    expect(w.detail).toBe('main.py')
    expect(w.status).toBe('running')
    expect(w.count).toBe(1)
  })

  it('phase_retry → system 警示', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_retry', phase: 'Verification', loop: 2 })
    expect(feed.items[0].type).toBe('system')
    expect(feed.items[0].content).toContain('第 2 轮')
    expect(feed.items[0].content).toContain('质检未通过')   // 无 reason → 质检回跳文案
  })

  it('phase_retry reason=error → 阶段出错重试文案', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_retry', phase: 'Coding', loop: 2, reason: 'error' })
    expect(feed.items[0].type).toBe('system')
    expect(feed.items[0].content).toContain('阶段出错')
    expect(feed.items[0].content).toContain('第 2 次')
    expect(feed.items[0].content).not.toContain('质检未通过')
  })

  it('requirements_submitted / design_submitted → 里程碑', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'requirements_submitted', agent: 'product_manager' })
    feed.addEvent({ event: 'design_submitted', agent: 'chief_technology_officer' })
    expect(feed.items[0].type).toBe('milestone')
    expect(feed.items[0].agent).toBe('PM')
    expect(feed.items[0].content).toBe('需求确定 ✅')
    expect(feed.items[1].type).toBe('milestone')
    expect(feed.items[1].agent).toBe('CTO')
    expect(feed.items[1].content).toBe('设计完成 ✅')
  })

  it('quality_gate → QA 里程碑（verdict 分支）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'quality_gate', data: { verdict: 'PASS' } })
    feed.addEvent({ event: 'quality_gate', data: { verdict: 'FAIL', features: [{ name: '导出', status: 'NO' }] } })
    feed.addEvent({ event: 'quality_gate', data: { verdict: 'WARN', features: [{ name: '提醒', status: 'PARTIAL' }] } })
    feed.addEvent({ event: 'quality_gate', data: {} })
    expect(feed.items[0].agent).toBe('QA')
    expect(feed.items[0].content).toBe('质检通过 ✅')
    expect(feed.items[1].content).toContain('质检未通过 ❌')   // FAIL 列出未达标项
    expect(feed.items[1].content).toContain('导出')
    expect(feed.items[2].content).toContain('有未达标项')      // WARN 同样列出
    expect(feed.items[2].content).toContain('提醒')
    expect(feed.items[3].content).toContain('WARN')            // 无 features → 兜底
  })

  it('pipeline_complete failed → system 警示；成功 → 🎉 里程碑', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'pipeline_complete', failed: true })
    expect(feed.items[0].type).toBe('system')
    expect(feed.items[0].content).toContain('质检 3 次未通过')
    feed.addEvent({ event: 'pipeline_complete', failed: false })
    expect(feed.items[1].type).toBe('milestone')
    expect(feed.items[1].content).toContain('🎉')
  })

  it('readableContent 提取 message / 结构摘要 / 兜底原文', () => {
    expect(readableContent('{"message": "hi"}')).toBe('hi')
    expect(readableContent('{"modality": "Web", "modules": [{"name": "a"}, {"name": "b"}]}'))
      .toContain('Modules: a, b')
    expect(readableContent('plain text')).toBe('plain text')
  })
})

describe('feed 合并与状态', () => {
  it('8s 窗口内同 agent 同 tool 同文件合并（count++）', () => {
    const { feed, advance } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Design' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { path: 'a.py' } })
    advance(1000)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { path: 'a.py' } })
    const works = feed.items.filter((i) => i.type === 'work')
    expect(works.length).toBe(1)
    expect(works[0].count).toBe(2)
    expect(works[0].detail).toBe('a.py')
  })

  it('不同文件不合并（写代码 a.py 与 b.py 各一行，不能 ×2）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Design' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { path: 'a.py' } })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { path: 'b.py' } })
    const works = feed.items.filter((i) => i.type === 'work')
    expect(works.length).toBe(2)
    expect(works[0].detail).toBe('a.py')
    expect(works[1].detail).toBe('b.py')
  })

  it('超过 8s / agent 变化 / tool 变化 → 新开条目', () => {
    const { feed, advance } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: {} })
    advance(9000)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: {} })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_1', tool: 'write_file', args: {} })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'read_file', args: {} })
    const works = feed.items.filter((i) => i.type === 'work')
    expect(works.length).toBe(4)
  })

  it('阶段边界强制断开合并（跨面板不合并）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'counter', tool: 'write_file', args: {} })
    feed.addEvent({ event: 'phase_end', phase: 'Coding' })
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'SecurityReviewer', tool: 'read_file', args: {} })
    const panels = feed.items.filter((i) => i.type === 'stage')
    expect(panels.length).toBe(2)
    const w1 = panels[0].stage!.windows.find((w) => w.agent === 'counter')!
    const w2 = panels[1].stage!.windows.find((w) => w.agent === 'SecurityReviewer')!
    expect(w1.items.length).toBe(1)
    expect(w2.items.length).toBe(1)
    expect(feed.items.filter((i) => i.type === 'work').length).toBe(0)
  })

  it('tool_post_use → 标记最后一条 running 为 ok / fail（Traceback 检测，err 摘要）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'run_tests', args: {} })
    feed.addEvent({ event: 'tool_post_use', agent: 'coder_0', tool: 'run_tests', result_preview: '1 failed' })
    expect(feed.items[0].status).toBe('ok')
    expect(feed.items[0].err).toBeUndefined()
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'run_tests', args: {} })
    feed.addEvent({ event: 'tool_post_use', agent: 'coder_0', tool: 'run_tests', result_preview: 'Traceback (most recent call last):\n  File "E:\\projects\\ChatDev\\x.py"\nAssertionError: boom' })
    expect(feed.items[0].status).toBe('fail')
    expect(feed.items[0].err).toContain('AssertionError: boom')   // 最后一行错误信息
    expect(feed.items[0].err).not.toContain('E:\\projects')       // 绝对路径已剔除
    expect(feed.items[0].content).not.toContain('✗')              // 动词不携带 ✗
  })

  it('超过 200 条丢弃最旧；未答 question 保留', () => {
    const { feed } = feedAt(0)
    for (let i = 0; i < 210; i++) {
      feed.addEvent({ event: 'conversation_turn', agent: 'x', content: `{"message": "m${i}"}` })
    }
    expect(feed.items.length).toBe(200)
    expect(feed.items[0].content).toBe('m10')
    // 未答 question 在裁剪中保留（把 200 条填到 198 再放 question）
    const f2 = createFeed({ now: () => 0 })
    for (let i = 0; i < 199; i++) {
      f2.addEvent({ event: 'conversation_turn', agent: 'x', content: '{}' })
    }
    f2.addEvent({ event: 'discuss_choice', question: { text: 'q?', options: ['a'], allow_multiple: false } })
    for (let i = 0; i < 5; i++) {
      f2.addEvent({ event: 'conversation_turn', agent: 'x', content: '{}' })
    }
    expect(f2.items.some((i) => i.type === 'question')).toBe(true)
  })

  it('answerQuestion：标记已答 + 追加"你"的气泡；重复回答忽略', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'discuss_choice', question: { text: 'q?', options: ['a', 'b'], allow_multiple: true } })
    const qid = feed.items[0].id
    feed.answerQuestion(qid, ['a'], '')
    feed.answerQuestion(qid, ['b'], '')   // 已答 → 忽略
    expect(feed.items[0].question?.answered).toBe(true)
    expect(feed.items[0].question?.selected).toEqual(['a'])
    const answers = feed.items.filter((i) => i.type === 'answer')
    expect(answers.length).toBe(1)
    expect(answers[0].agent).toBe('你')
    expect(answers[0].content).toBe('a')
  })

  it('setQuestionSending / clearSending', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'discuss_choice', question: { text: 'q?', options: ['a'], allow_multiple: false } })
    const qid = feed.items[0].id
    feed.setQuestionSending(qid, true)
    expect(feed.items[0].question?.sending).toBe(true)
    feed.clearSending()
    expect(feed.items[0].question?.sending).toBe(false)
  })

  it('phase_end 将未答问题标记为超时未作答（ask_choice 300s 超时收尾）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'discuss_choice', question: 'q?', options: ['a'], allow_multiple: false })
    expect(feed.items[0].question?.answered).toBe(false)
    feed.addEvent({ event: 'phase_end', phase: 'RequirementsDiscussion' })
    expect(feed.items[0].question?.answered).toBe(true)
    expect(feed.items[0].question?.selected).toEqual(['(超时未作答)'])
    // 已作答的问题不受影响
    const f2 = feedAt(0).feed
    f2.addEvent({ event: 'discuss_choice', question: 'q2?', options: ['a'], allow_multiple: false })
    f2.answerQuestion(f2.items[0].id, ['a'], '')
    f2.addEvent({ event: 'phase_end', phase: 'Design' })
    expect(f2.items[0].question?.selected).toEqual(['a'])
  })
})

describe('feed 真实 wire 形状', () => {
  it('真实 discuss_choice（question 为字符串，options 顶层）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'discuss_choice', question: '开发什么类型的产品？', options: ['Web 应用', '移动 App'], allow_multiple: false })
    const q = feed.items[0]
    expect(q.content).toBe('开发什么类型的产品？')
    expect(q.question?.options).toEqual(['Web 应用', '移动 App'])
    expect(q.question?.allowMultiple).toBe(false)
  })

  it('真实 tool_pre_use（filename 参数）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file', args: { filename: 'main.py', content: '...' } })
    expect(feed.items[0].detail).toBe('main.py')
  })

  it('I1 回归：失败→重合并→成功，err 无残留', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'run_tests', args: {} })
    feed.addEvent({ event: 'tool_post_use', agent: 'coder_0', tool: 'run_tests', result_preview: 'Traceback (most recent call last):\nRuntimeError: bad' })
    expect(feed.items[0].status).toBe('fail')
    expect(feed.items[0].err).toContain('RuntimeError')
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'run_tests', args: {} })
    feed.addEvent({ event: 'tool_post_use', agent: 'coder_0', tool: 'run_tests', result_preview: 'all passed' })
    expect(feed.items[0].status).toBe('ok')
    expect(feed.items[0].err).toBeUndefined()
  })

  it('I4 回归：setUndelivered 标记与清除', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'discuss_choice', question: 'q?', options: ['a'], allow_multiple: false })
    const qid = feed.items[0].id
    feed.setQuestionSending(qid, true)
    feed.setUndelivered(qid, true)
    expect(feed.items[0].question?.undelivered).toBe(true)
    feed.setUndelivered(qid, false)
    expect(feed.items[0].question?.undelivered).toBe(false)
  })
})

describe('feed Vue 响应式集成', () => {
  it('宿主数组为 reactive 时：push 与原地修改都触发依赖（重渲染的根基）', () => {
    const items = reactive<FeedItem[]>([])
    const feed = createFeed({ items })
    let reads = 0
    effect(() => { void items[0]?.question?.answered; reads++ })
    // push 触发
    feed.addEvent({ event: 'discuss_choice', question: 'q?', options: ['a'], allow_multiple: false })
    expect(items.length).toBe(1)
    expect(reads).toBeGreaterThan(1)
    // 原地修改（answerQuestion 标记 answered）触发
    const before = reads
    feed.answerQuestion(items[0].id, ['a'], '')
    expect(items[0].question?.answered).toBe(true)
    expect(reads).toBeGreaterThan(before)
    // reset 清空也触发
    feed.reset()
    expect(items.length).toBe(0)
  })
})

describe('feed 设计/阶段可见性', () => {
  it('结构化架构设计 → design 数据（渲染为设计卡片）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({
      event: 'conversation_turn', agent: 'chief_technology_officer',
      content: JSON.stringify({
        modality: 'CLI tool', language: 'Python',
        modules: [{ name: 'cli', purpose: '解析参数', exports: [{ name: 'run_cli', signature: '(argv) -> int', description: '入口' }] }],
      }),
    })
    const it = feed.items[0]
    expect(it.type).toBe('chat')
    expect(it.design?.modality).toBe('CLI tool')
    expect(it.design?.language).toBe('Python')
    expect(it.design?.modules[0].name).toBe('cli')
    expect(it.design?.modules[0].exports?.[0].signature).toBe('(argv) -> int')
    // 无 message 的结构化输出：content 为摘要（Modality/Language/Modules）
    expect(it.content).toContain('Modality: CLI tool')
  })

  it('phase_start → 阶段开始里程碑（消除"卡住"感）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Design' })
    const m = feed.items[0]
    expect(m.type).toBe('milestone')
    expect(m.content).toContain('▶')
    expect(m.content).toContain('设计')
  })

  it('新增工具动词：run_code → 跑程序，todo_write → 更新任务清单', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'tester', tool: 'run_code', args: {} })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'todo_write', args: {} })
    const works = feed.items.filter((i) => i.type === 'work')
    expect(works[0].content).toBe('跑程序')
    expect(works[1].content).toBe('更新任务清单')
  })
})

describe('feed 写入内容摘要', () => {
  it('write_file 携带 content → 行数 + 首行摘要', () => {
    const { feed } = feedAt(0)
    feed.addEvent({
      event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file',
      args: { filename: 'main.py', content: 'import argparse\n\ndef main():\n    pass\n' },
    })
    const w = feed.items[0]
    expect(w.lines).toBe(4)
    expect(w.snippet).toBe('import argparse')
  })

  it('非写入工具（run_code）无摘要', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'tool_pre_use', agent: 'tester', tool: 'run_code', args: { entry: 'main.py' } })
    expect(feed.items[0].snippet).toBeUndefined()
    expect(feed.items[0].lines).toBeUndefined()
  })

  it('合并时摘要更新为最新写入', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Design' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file',
                    args: { filename: 'a.py', content: 'first version\n' } })
    feed.addEvent({ event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file',
                    args: { filename: 'a.py', content: 'second version\nmore\n' } })
    const w = feed.items.filter((i) => i.type === 'work')[0]
    expect(w.count).toBe(2)
    expect(w.snippet).toBe('second version')
    expect(w.lines).toBe(2)
  })
})

describe('feed 结构化内容保留', () => {
  it('message + 其他键 → content 为 message 文本，rawContent 保留原始 JSON', () => {
    const { feed } = feedAt(0)
    const raw = JSON.stringify({ message: '产品定位', core_features: ['a', 'b'] })
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_product_officer', content: raw })
    const it = feed.items[0]
    expect(it.type).toBe('chat')
    expect(it.content).toBe('产品定位')        // readableContent 剥掉键
    expect(it.rawContent).toBe(raw)           // 原始载荷保留供文档卡
  })

  it('纯 message JSON → 无 rawContent（普通气泡）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'conversation_turn', agent: 'coder_0', content: JSON.stringify({ message: '完成' }) })
    expect(feed.items[0].rawContent).toBeUndefined()
  })
})

describe('feed 系统卡片', () => {
  it('error 事件 → system 错误卡（variant=error）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'error', message: 'Traceback (most recent call last)' })
    const it = feed.items[0]
    expect(it.type).toBe('system')
    expect(it.variant).toBe('error')
    expect(it.content).toContain('运行出错')
    expect(it.content).toContain('Traceback')
  })

  it('phase_error 事件 → system 错误卡（含阶段名）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_error', phase: 'Coding', error: 'E:\\x\\main.py: boom' })
    const it = feed.items[0]
    expect(it.type).toBe('system')
    expect(it.variant).toBe('error')
    expect(it.content).toContain('编码 阶段出错')
    expect(it.content).not.toContain('E:\\')   // errSnippet 去路径
  })

  it('phase_retry error 原因 → variant=error；fail 原因 → warn', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_retry', phase: 'Coding', loop: 1, reason: 'error' })
    expect(feed.items[0].variant).toBe('error')
    feed.addEvent({ event: 'phase_retry', phase: 'Verification', loop: 1, reason: 'fail' })
    expect(feed.items[1].variant).toBe('warn')
    expect(feed.items[1].content).toContain('质检未通过')
  })
})

describe('feed todo 清单', () => {
  it('todo_update → todo 卡片（N/M + 清单）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'todo_update', agent: 'coder', done: 1, total: 3,
                    todos: [{ content: '写 main', status: 'completed' }, { content: '写 utils', status: 'in_progress' }] })
    const it = feed.items[0]
    expect(it.type).toBe('todo')
    expect(it.content).toContain('任务清单 1/3')
    expect(it.todoList?.length).toBe(2)
  })

  it('按 agent 固定一条：无论何时更新都就地刷新（不割裂）', () => {
    let now = 0
    const feed = createFeed({ now: () => now, items: reactive([]) })
    feed.addEvent({ event: 'todo_update', agent: 'coder', done: 1, total: 3, todos: [{ content: 'a', status: 'in_progress' }] })
    feed.addEvent({ event: 'todo_update', agent: 'coder', done: 2, total: 3, todos: [{ content: 'a', status: 'completed' }, { content: 'b', status: 'in_progress' }] })
    expect(feed.items.length).toBe(1)                       // 同 agent 永远一条
    expect(feed.items[0].content).toContain('2/3')
    expect(feed.items[0].todoList?.[0].status).toBe('completed')   // 完成就地打勾
    now = 10000                                             // 超长时间后仍合并
    feed.addEvent({ event: 'todo_update', agent: 'coder', done: 3, total: 3, todos: [] })
    expect(feed.items.length).toBe(1)
    // 不同 agent → 各自一条
    feed.addEvent({ event: 'todo_update', agent: 'tester', done: 0, total: 2, todos: [] })
    expect(feed.items.length).toBe(2)
    expect(feed.items[0].content).toContain('开发工程师')
  })

  it('reviewer 聚合卡：审查中刷新同一张，完成标绿', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    expect(feed.items.length).toBe(1)                       // 固定一张
    expect(feed.items[0].type).toBe('reviewer')
    expect(feed.items[0].reviewerDone).toBe(false)
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [] })
    expect(feed.items.length).toBe(1)                       // 仍在原卡
    expect(feed.items[0].reviewerDone).toBe(true)
    expect(feed.items[0].content).toContain('审查完成')
    // 不同 reviewer → 各一张
    feed.addEvent({ event: 'conversation_turn', agent: 'LogicReviewer', content: '{}' })
    expect(feed.items.length).toBe(2)
  })
})

describe('feed 人工审阅与追加需求', () => {
  it('review_request → 审阅卡（files + diff + 待决策）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'review_request', files: ['a.py', 'b.py'], diff: '--- a.py\n+++ a.py' })
    const it = feed.items[0]
    expect(it.type).toBe('review')
    expect(it.review?.files).toEqual(['a.py', 'b.py'])
    expect(it.review?.diff).toContain('+++ a.py')
    expect(it.review?.approved).toBeNull()
  })

  it('decideReview 标记决策并回显用户消息；已决策不可再改', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'review_request', files: ['a.py'], diff: 'x' })
    feed.decideReview(0, true)
    expect(feed.items[0].review?.approved).toBe(true)
    expect(feed.items[1].type).toBe('answer')
    expect(feed.items[1].content).toContain('已通过')
    feed.decideReview(0, false)   // 已决策 → 忽略
    expect(feed.items[0].review?.approved).toBe(true)
    expect(feed.items.length).toBe(2)
  })

  it('phase_retry feedback → 追加需求回退文案', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_retry', phase: 'Design', loop: 1, reason: 'feedback' })
    expect(feed.items[0].content).toContain('补充需求')
  })

  it('token_warning → 预算黄卡', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'token_warning', used: 600000, budget: 500000 })
    expect(feed.items[0].type).toBe('system')
    expect(feed.items[0].content).toContain('预算')
  })

  it('addChat → 用户消息进对话流', () => {
    const { feed } = feedAt(0)
    feed.addChat('加一个导出功能')
    const it = feed.items[0]
    expect(it.type).toBe('chat')
    expect(it.agent).toBe('你')
    expect(it.content).toBe('加一个导出功能')
  })
})

describe('feed 流式输出', () => {
  it('llm_delta 逐字追加到同 agent 流式条目；结束标记完成', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'llm_delta', agent: 'chief_technology_officer', delta: '架构' })
    feed.addEvent({ event: 'llm_delta', agent: 'chief_technology_officer', delta: '方案' })
    expect(feed.items.length).toBe(1)
    expect(feed.items[0].type).toBe('chat')
    expect(feed.items[0].content).toBe('架构方案')
    expect(feed.items[0].streaming).toBe(true)
    feed.addEvent({ event: 'llm_stream_end', agent: 'chief_technology_officer' })
    expect(feed.items[0].streaming).toBe(false)
  })

  it('不同 agent 的流式互不干扰', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'llm_delta', agent: 'chief_technology_officer', delta: 'A' })
    feed.addEvent({ event: 'llm_delta', agent: 'chief_product_officer', delta: 'B' })
    expect(feed.items.length).toBe(2)
    expect(feed.items[0].content).toBe('A')
    expect(feed.items[1].content).toBe('B')
  })
})

describe('feed 阶段协作面板', () => {
  it('Coding 阶段：子 agent 事件进各自小窗口，不进主对话流', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'counter', tool: 'write_file', args: { filename: 'counter.py' } })
    feed.addEvent({ event: 'tool_pre_use', agent: 'cli', tool: 'write_file', args: { filename: 'cli.py' } })
    const panel = feed.items.find((i) => i.type === 'stage')
    expect(panel?.stage).toBeTruthy()
    expect(panel?.stage?.windows.length).toBe(2)
    const counter = panel?.stage?.windows.find((w) => w.agent === 'counter')
    expect(counter?.items[0].kind).toBe('work')
    expect(counter?.items[0].text).toContain('counter.py')
    // 主对话流没有被 work 行刷屏（只有 milestone + stage）
    expect(feed.items.filter((i) => i.type === 'work').length).toBe(0)
  })

  it('Verification 阶段：review_submitted 标绿该窗口；阶段结束面板定格', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [] })
    let panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.windows[0].done).toBe(true)
    feed.addEvent({ event: 'phase_end', phase: 'Verification' })
    panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.done).toBe(true)
    // 阶段结束后恢复主对话流
    feed.addEvent({ event: 'conversation_turn', agent: 'inspector', content: JSON.stringify({ message: '后续' }) })
    expect(feed.items.filter((i) => i.type === 'chat').length).toBe(1)
  })

  it('流式 delta 进面板窗口', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'llm_delta', agent: 'counter', delta: '模块' })
    feed.addEvent({ event: 'llm_delta', agent: 'counter', delta: '完成' })
    const panel = feed.items.find((i) => i.type === 'stage')!
    const win = panel.stage?.windows.find((w) => w.agent === 'counter')!
    expect(win.items[0].text).toBe('模块完成')
    expect(win.items[0].streaming).toBe(true)
  })

  it('quality_gate FAIL → 完整列出未达标项', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'quality_gate', data: { verdict: 'FAIL', features: [
      { name: '统计行数', status: 'NO', notes: '未实现' },
      { name: '多文件', status: 'PARTIAL' },
      { name: '正常项', status: 'YES' },
    ] } })
    const it = feed.items[0]
    expect(it.type).toBe('system')
    expect(it.variant).toBe('error')
    expect(it.content).toContain('统计行数')
    expect(it.content).toContain('多文件')
    expect(it.content).toContain('未实现')
    expect(it.content).not.toContain('正常项')
  })
})

describe('feed 阶段面板：完成/轮次/无效/审阅语义', () => {
  it('agent_done → 窗口标绿（coder 无 review_submitted 也能变绿）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'agent_done', agent: 'counter' })
    const win = feed.items.find((i) => i.type === 'stage')
      ?.stage?.windows.find((w) => w.agent === 'counter')!
    expect(win.done).toBe(true)
  })

  it('review_round 第 2 轮 → 清空窗口 + 轮次分隔线', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{"message": "第一轮"}' })
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [{ file: 'a.py' }], loop: 1 })
    let win = feed.items.find((i) => i.type === 'stage')
      ?.stage?.windows.find((w) => w.agent === 'SecurityReviewer')!
    expect(win.done).toBe(true)
    expect(win.issues).toBe(1)

    feed.addEvent({ event: 'review_round', phase: 'Verification', loop: 2 })
    win = feed.items.find((i) => i.type === 'stage')
      ?.stage?.windows.find((w) => w.agent === 'SecurityReviewer')!
    expect(win.done).toBe(false)
    expect(win.issues).toBe(0)
    expect(win.items.length).toBe(1)                    // 只剩分隔线
    expect(win.items[0].kind).toBe('sep')
    expect(win.items[0].text).toContain('第 2 轮')
  })

  it('review_discarded → 窗口 ⚠️ 无效（不是完成）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'review_discarded', agent: 'SecurityReviewer', loop: 1 })
    const win = feed.items.find((i) => i.type === 'stage')
      ?.stage?.windows.find((w) => w.agent === 'SecurityReviewer')!
    expect(win.invalid).toBe(true)
    expect(win.done).toBe(false)
    // agent_done 不覆盖无效标记
    feed.addEvent({ event: 'agent_done', agent: 'SecurityReviewer' })
    expect(win.invalid).toBe(true)
    expect(win.done).toBe(false)
  })

  it('review_submitted 带问题数 → 窗口 issues 计数 + 聚合卡显示问题数', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer',
                    issues: [{ file: 'a.py' }, { file: 'b.py' }] })
    expect(feed.items[0].reviewerDone).toBe(true)
    expect(feed.items[0].issues).toBe(2)
    expect(feed.items[0].content).toContain('2 个问题')
    // 无问题 → 聚合卡"无问题"
    feed.addEvent({ event: 'conversation_turn', agent: 'LogicReviewer', content: '{}' })
    feed.addEvent({ event: 'review_submitted', agent: 'LogicReviewer', issues: [] })
    const card = [...feed.items].reverse().find((i) => i.agent === 'LogicReviewer')!
    expect(card.content).toContain('无问题')
  })

  it('review_timed_out → 待决策审阅卡收尾为超时自动通过', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'review_request', files: ['a.py'], diff: 'x' })
    expect(feed.items[0].review?.approved).toBeNull()
    feed.addEvent({ event: 'review_timed_out' })
    expect(feed.items[0].review?.approved).toBe(true)
    expect(feed.items[0].review?.timedOut).toBe(true)
  })

  it('agent_done 不给 Reviewer 窗口标绿（审查完成只能来自 review_submitted）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'agent_done', agent: 'SecurityReviewer' })
    let win = feed.items.find((i) => i.type === 'stage')
      ?.stage?.windows.find((w) => w.agent === 'SecurityReviewer')!
    expect(win.done).toBe(false)                     // 工具循环没结束，不算完成
    feed.addEvent({ event: 'review_submitted', agent: 'SecurityReviewer', issues: [] })
    expect(win.done).toBe(true)                      // 合法输出 → 才标绿
  })

  it('Coding 面板排除 integrator/tester（顺序收尾进主对话流）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'counter', tool: 'write_file', args: { filename: 'counter.py' } })
    feed.addEvent({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file', args: { filename: 'main.py' } })
    const panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.windows.length).toBe(1)      // 只有 coder 窗口
    expect(panel.stage?.windows[0].agent).toBe('counter')
    const work = feed.items.find((i) => i.type === 'work')!
    expect(work.agent).toBe('integrator')            // integrator 走主对话流
  })

  it('里程碑 agent（Coding/Verification…）不进面板（幻影窗口修复）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    // "编码完成: N 个模块"里程碑在 Coding 面板期间到达 → 必须走主对话流
    feed.addEvent({ event: 'conversation_turn', agent: 'Coding', content: '{"message": "编码完成: 3 个模块"}' })
    const panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.windows.some((w) => w.agent === 'Coding')).toBe(false)
    expect(feed.items.some((i) => i.type === 'milestone')).toBe(true)  // 里程碑在主流程
  })

  it('review_round 不创建 "Agent" 幻影窗口，且记录轮次', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' })
    feed.addEvent({ event: 'review_round', phase: 'Verification', loop: 2 })
    const panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.round).toBe(2)
    expect(panel.stage?.windows.some((w) => w.agent === 'Agent')).toBe(false)
    expect(panel.stage?.windows.length).toBe(1)      // 只有真实 agent 窗口
  })

  it('fixer 在验证面板内（修复过程不把面板顶走）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'fixer', tool: 'write_file', args: { filename: 'report.py' } })
    const panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.windows.some((w) => w.agent === 'fixer')).toBe(true)
    expect(feed.items.filter((i) => i.type === 'work').length).toBe(0)  // 不进主流程
  })

  it('integration_start → 编码面板定格，打开整合联调面板（integrator+tester）', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'tool_pre_use', agent: 'counter', tool: 'write_file', args: { filename: 'counter.py' } })
    feed.addEvent({ event: 'integration_start' })
    const panels = feed.items.filter((i) => i.type === 'stage')
    expect(panels.length).toBe(2)
    expect(panels[0].stage?.done).toBe(true)                // 编码面板定格完成
    expect(panels[1].stage?.phase).toBe('Integration')
    // integrator/tester 的事件进第二个面板
    feed.addEvent({ event: 'tool_pre_use', agent: 'integrator', tool: 'write_file', args: { filename: 'main.py' } })
    feed.addEvent({ event: 'tool_pre_use', agent: 'tester', tool: 'write_file', args: { filename: 'test_counter.py' } })
    expect(panels[1].stage?.windows.length).toBe(2)
    expect(panels[1].stage?.windows.map((w) => w.agent).sort())
      .toEqual(['integrator', 'tester'])
    expect(feed.items.filter((i) => i.type === 'work').length).toBe(0)
    // phase_end(Coding) 关闭整合面板
    feed.addEvent({ event: 'phase_end', phase: 'Coding' })
    expect(panels[1].stage?.done).toBe(true)
  })
})

describe('feed 思考占位与标题顺序', () => {
  it('agent_typing → "思考中"占位；内容到达即移除', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'agent_typing', agent: 'chief_technology_officer' })
    expect(feed.items[0].type).toBe('typing')
    expect(feed.items[0].content).toContain('技术总监')
    // 内容到达 → 占位移除
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_technology_officer',
                    content: '{"modality": "CLI"}' })
    expect(feed.items.filter((i) => i.type === 'typing').length).toBe(0)
  })

  it('流式已展示的内容 + conversation_turn 同内容 → 不重复渲染', () => {
    const { feed } = feedAt(0)
    // converse 双路径：llm_delta 流式展示全文 + conversation_turn 带完整内容
    feed.addEvent({ event: 'llm_delta', agent: 'chief_product_officer', delta: '架构合理' })
    feed.addEvent({ event: 'llm_stream_end', agent: 'chief_product_officer' })
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_product_officer',
                    content: JSON.stringify({ message: '架构合理' }) })
    expect(feed.items.length).toBe(1)                     // 只渲染一次
    expect(feed.items[0].content).toBe('架构合理')
    // 内容不同 → 正常新增
    feed.addEvent({ event: 'conversation_turn', agent: 'chief_product_officer',
                    content: JSON.stringify({ message: '新的意见' }) })
    expect(feed.items.length).toBe(2)
  })

  it('面板阶段内的 agent_typing 不进主对话流', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'agent_typing', agent: 'counter' })
    expect(feed.items.filter((i) => i.type === 'typing').length).toBe(0)
  })

  it('phase_start：阶段标题在协作面板上方', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    const idx = feed.items.findIndex((i) => i.type === 'milestone')
    const pidx = feed.items.findIndex((i) => i.type === 'stage')
    expect(idx).toBe(0)                              // 标题先出现
    expect(pidx).toBe(1)                             // 面板紧随其后
  })

  it('pipeline_complete 带非 PASS verdict → 不显示"全部完成"', () => {
    const { feed } = feedAt(0)
    feed.addEvent({ event: 'pipeline_complete', verdict: 'WARN', failed: false })
    expect(feed.items[0].content).toContain('WARN')
    expect(feed.items[0].content).not.toContain('全部完成')
    feed.addEvent({ event: 'pipeline_complete', verdict: 'PASS', failed: false })
    expect(feed.items[1].content).toBe('🎉 全部完成')
  })

  it('setStagePhases 下发后端路由 → 新增 lens 自动进面板', () => {
    const { feed } = feedAt(0)
    // 后端 /api/config 下发一个自定义 lens 名
    feed.setStagePhases({
      Coding: { allow: [], exclude: ['integrator', 'tester'] },
      Verification: { allow: ['SecurityReviewer', 'MyCustomLensReviewer'], exclude: [] },
      Documentation: { allow: ['dependency_analyst', 'technical_writer'], exclude: [] },
    })
    feed.addEvent({ event: 'phase_start', phase: 'Verification' })
    feed.addEvent({ event: 'conversation_turn', agent: 'MyCustomLensReviewer', content: '{}' })
    const panel = feed.items.find((i) => i.type === 'stage')!
    expect(panel.stage?.windows.some((w) => w.agent === 'MyCustomLensReviewer')).toBe(true)
  })
})

describe('feed C4 关键卡保留 + D2 错误中文化', () => {
  it('超 200 条裁剪时 review/stage/system 卡保留', () => {
    const feed = createFeed({ now: () => 0 })
    for (let i = 0; i < 195; i++) {
      feed.addEvent({ event: 'conversation_turn', agent: 'x', content: `{"message": "m${i}"}` })
    }
    feed.addEvent({ event: 'review_request', files: ['a.py'], diff: 'x' })
    feed.addEvent({ event: 'phase_start', phase: 'Coding' })
    feed.addEvent({ event: 'phase_retry', phase: 'Verification', loop: 2 })   // → system 卡
    for (let i = 0; i < 20; i++) {
      feed.addEvent({ event: 'conversation_turn', agent: 'x', content: `{"message": "more${i}"}` })
    }
    expect(feed.items.length).toBeLessThanOrEqual(200)
    expect(feed.items.some((i) => i.type === 'review')).toBe(true)
    expect(feed.items.some((i) => i.type === 'stage')).toBe(true)
    expect(feed.items.some((i) => i.type === 'system')).toBe(true)
  })

  it('zhErrorHint 常见错误中文提示', () => {
    expect(zhErrorHint('ModuleNotFoundError: No module named x')).toContain('依赖')
    expect(zhErrorHint('AssertionError: boom')).toContain('断言')
    expect(zhErrorHint('Connection timed out')).toContain('超时')
    expect(zhErrorHint('totally fine')).toBe('')
  })
})
