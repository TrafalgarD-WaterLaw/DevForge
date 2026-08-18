// web/src/components/feed.ts
// 事件 → 对话消息条目 归一化层。纯逻辑、无渲染、可注入时钟。
import { TOOL_VERBS, agentLabel } from './spriteDefs'
import { PHASES, PHASE_LABELS_CN } from '../phases'

export type FeedItemType = 'question' | 'answer' | 'chat' | 'work' | 'milestone' | 'system' | 'todo' | 'review' | 'reviewer' | 'stage' | 'typing'
export type WorkStatus = 'running' | 'ok' | 'fail'
export type SystemVariant = 'warn' | 'error'
export interface TodoEntry { content: string; status: string }
export interface ReviewInfo {
  files: string[]
  diff: string
  approved: boolean | null   // null = 待用户决策
  timedOut?: boolean         // 等待超时 → 后端自动通过
}

// 阶段面板：多子 agent 阶段（Coding/Verification/Documentation）的固定小窗口。
// 每个子 agent 一个窗口，消息在各自窗口内滚动，完成标绿；阶段结束后面板定格。
export interface StageWinItem {
  kind: 'chat' | 'work' | 'todo' | 'sep'
  text: string
  streaming?: boolean
  tool?: string
  detail?: string   // 工作行文件/入口（合并键的一部分：同文件才合并）
  count?: number
  status?: 'running' | 'ok' | 'fail'
  ts?: number
}
export interface StageWin {
  agent: string
  items: StageWinItem[]
  done: boolean            // 该 agent 本轮任务完成（绿框）
  issues?: number          // 审查者发现的问题数（0 = 无问题）
  invalid?: boolean        // 审查输出非法被丢弃（⚠️，不是完成）
  loop?: number            // 当前审查轮次（第 2 轮起窗口重置）
}
export interface StagePanel {
  phase: string
  windows: StageWin[]
  done: boolean
  round?: number           // 当前审查轮次（第 2 轮时前端把面板滚回视野）
}

export interface QuestionState {
  text: string
  options: string[]
  allowMultiple: boolean
  answered: boolean
  selected: string[]
  custom?: string
  sending: boolean
  undelivered: boolean   // 断线时回答未送达，重连后自动重发
}

export interface DesignModule {
  name: string
  purpose?: string
  exports?: { name: string; signature?: string; description?: string }[]
}
export interface DesignInfo {
  modality: string
  language: string
  modules: DesignModule[]
}

export interface FeedItem {
  id: number
  type: FeedItemType
  agent: string
  content: string
  ts: number
  phase: string            // 条目所属阶段（''= 未开始）；阶段边界断开工作消息合并
  question?: QuestionState
  tool?: string
  detail?: string
  count?: number
  status?: WorkStatus
  variant?: SystemVariant  // system 条目的样式变体（warn 黄 / error 红）
  todoList?: TodoEntry[]   // todo 卡片的任务清单
  review?: ReviewInfo      // 人工审阅卡（fixer 修复 diff）
  reviewerDone?: boolean   // reviewer 聚合卡：true = 审查完成（绿色）
  issues?: number          // reviewer 聚合卡：发现的问题数（0 = 无问题）
  streaming?: boolean      // 流式输出中：内容逐字追加，渲染为纯文本+光标
  fromStream?: boolean     // 内容来自 llm_delta 流式（conversation_turn 去重凭据）
  stage?: StagePanel       // 多子 agent 阶段面板（小窗口集合）
  err?: string             // 工具失败的错误摘要（最后一行错误信息，去路径、截断）
  snippet?: string         // 写入内容摘要（首行，单行化截断）— 类似 Claude Code 的改动预览
  lines?: number           // 写入行数（+N 行）
  design?: DesignInfo      // 结构化架构设计（渲染为设计卡片）
  rawContent?: string      // 原始 JSON 载荷（message 之外的键被 readableContent 剥掉，
                           // 文档卡/层次化渲染需要原始结构）
}

export interface Feed {
  items: FeedItem[]
  addEvent(e: Record<string, unknown>): void
  addChat(content: string): void
  answerQuestion(questionId: number, selected: string[], custom: string): void
  setQuestionSending(questionId: number, sending: boolean): void
  setUndelivered(questionId: number, undelivered: boolean): void
  decideReview(reviewId: number, approved: boolean): void
  setStagePhases(phases: Record<string, { allow: string[]; exclude: string[] }>): void
  clearSending(): void
  reset(): void
  resetStage(): void   // 只清阶段面板内部状态（重跑裁剪条目后调用，防幻影窗口）
}

export const MERGE_WINDOW_MS = 8000
export const MAX_ITEMS = 200
// 阶段面板默认路由：allow=[] = 该阶段所有 agent 进面板（Coding 的 coder tag
// 是模块名），exclude 例外 —— integrator/tester 是顺序收尾步骤，不是并行子
// agent，它们的工作走主对话流（work 行 + 里程碑）。
// Verification 含 fixer：修复过程留在面板里，否则其 work 行会把面板顶上去。
// 注意：运行期由后端 /api/config 下发覆盖（P1-5 单一来源），此处是兜底默认。
const DEFAULT_STAGE_PHASES: Record<string, { allow: string[]; exclude: string[] }> = {
  Coding: { allow: [], exclude: ['integrator', 'tester'] },
  // 整合联调子面板：编码完成后 integrator+tester 收进第二个协作面板
  Integration: { allow: ['integrator', 'tester'], exclude: [] },
  Verification: { allow: ['SecurityReviewer', 'PerformanceReviewer',
                          'LogicReviewer', 'CorrectnessReviewer', 'fixer'],
                  exclude: [] },
  Documentation: { allow: ['dependency_analyst', 'technical_writer'],
                   exclude: [] },
}

// 后端阶段级 conversation_turn 的 agent 名（里程碑）。
// 需求讨论/设计是阶段内多人对话（非阶段整体里程碑），从 PHASES 推导时剔除前两个。
const MILESTONE_AGENTS = new Set(PHASES.slice(2))

/** 从事件内容提取可读文本（自 Dashboard._readableContent 迁移，行为不变） */
export function readableContent(raw: string): string {
  try {
    const obj = JSON.parse(raw)
    if (obj.message && typeof obj.message === 'string') return obj.message
    if (obj.modality || obj.modules) {
      const parts: string[] = []
      if (obj.modality) parts.push(`Modality: ${obj.modality}`)
      if (obj.language) parts.push(`Language: ${obj.language}`)
      if (obj.modules?.length) parts.push(`Modules: ${obj.modules.map((m: any) => m.name).join(', ')}`)
      return parts.join('\n')
    }
    return raw
  } catch {
    return raw
  }
}

function toolDetail(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const a = args as Record<string, unknown>
  return String(a.filename ?? a.path ?? a.entry ?? a.directory ?? a.query ?? '')
}

/** 写入内容摘要：首行单行化、去空白、截断 60 字符 */
function contentSnippet(body: string): string {
  const first = body.split('\n').find((l) => l.trim()) ?? ''
  return first.replace(/\s+/g, ' ').trim().slice(0, 60) || '(空文件)'
}

/** 错误摘要：取预览的最后一行非空错误信息，去掉绝对路径前缀，截断 100 字符 */
function errSnippet(preview: string): string {
  const lines = preview.split('\n').map((l) => l.trim()).filter(Boolean)
  let line = lines[lines.length - 1] ?? preview.slice(0, 100)
  // 去掉 Windows/Unix 绝对路径前缀（E:\projects\ChatDev\... → ...）
  line = line.replace(/^[A-Za-z]:\\[\s\S]*?\\/, '').replace(/^\/[\s\S]*?\//, '')
  return line.length > 100 ? line.slice(0, 100) + '…' : line
}

/** D2: 常见错误的中文提示（附在原始错误后面，用户不再面对纯英文堆栈） */
export function zhErrorHint(err: string): string {
  const pairs: [RegExp, string][] = [
    [/ModuleNotFoundError|ImportError/i, '缺少依赖模块 — 检查 import 与依赖安装'],
    [/SyntaxError/i, '语法错误'],
    [/AssertionError|assert/i, '断言失败（测试未通过）'],
    [/FileNotFoundError|No such file/i, '文件不存在'],
    [/timed out|Timeout/i, '执行超时'],
    [/Traceback/i, '程序运行时崩溃'],
    [/exit code|returned non-zero/i, '程序异常退出'],
    [/LLM call failed|llm_error/i, '大模型调用失败（网络或配额）'],
    [/unknown tool/i, '调用了不存在的工具'],
    [/pytest/i, '测试执行失败'],
    [/permission|denied/i, '权限不足'],
    [/unrecognized arguments/i, '命令行参数不被识别（工具链版本问题）'],
  ]
  for (const [re, hint] of pairs) {
    if (re.test(err)) return hint
  }
  return ''
}

export function createFeed(opts?: {
  now?: () => number
  items?: FeedItem[]
  stagePhases?: Record<string, { allow: string[]; exclude: string[] }>
}): Feed {
  // 宿主数组：调用方（Dashboard）可注入 Vue reactive([])，
  // 让原地 push/修改通过响应式代理触发组件重渲染（feed 本身保持纯净）
  const items = opts?.items ?? []
  const now = opts?.now ?? (() => Date.now())
  let id = 0
  let phase = ''
  // 阶段面板路由：默认值兜底，运行期用 /api/config 下发覆盖
  let stagePhases = opts?.stagePhases ?? DEFAULT_STAGE_PHASES
  // 阶段面板：当前活跃面板（null = 无）
  let stagePanel: { id: number; phase: string; windows: Map<string, StageWin>; done: boolean; round?: number } | null = null

  function push(partial: Omit<FeedItem, 'id' | 'ts' | 'phase'> & { ts?: number }): FeedItem {
    const item: FeedItem = { ...partial, id: id++, ts: partial.ts ?? now(), phase }
    items.push(item)
    // 上限裁剪：优先丢普通聊天/工作行/已答问题；关键卡（未答问题/
    // 审阅卡/协作面板/系统卡/里程碑）保留 —— 长任务下用户状态不丢
    while (items.length > MAX_ITEMS) {
      const dropIdx = items.findIndex((i) =>
        !(i.type === 'question' && !i.question?.answered)
        && !['review', 'stage', 'system', 'milestone'].includes(i.type))
      if (dropIdx < 0) break
      items.splice(dropIdx, 1)
    }
    return item
  }

  /** 该 agent 是否属于当前面板阶段（Coding allow=[] 全员；exclude 除外）。
   * 里程碑 agent（Coding/Verification/… 阶段级对话）永远不进面板 ——
   * 否则 Coding 阶段 allow=[] 会把 "编码完成: N 个模块" 里程碑变成幻影窗口。 */
  function stageActive(agent: string): boolean {
    if (!stagePanel) return false
    if (MILESTONE_AGENTS.has(agent)) return false
    const cfg = stagePhases[stagePanel.phase]
    if (!cfg) return false
    if (cfg.exclude.includes(agent)) return false
    if (!cfg.allow || cfg.allow.length === 0) return true
    return cfg.allow.includes(agent)
  }

  /** 阶段事件 → 对应 agent 的小窗口（消息在窗口内滚动，不进主对话流） */
  function stageEvent(e: Record<string, unknown>) {
    if (e.event === 'review_round') {
      // 广播事件（无 agent 字段）—— 直接作用于所有窗口，绝不新建
      // "Agent" 幻影窗口
      const loop = Number(e.loop ?? 1)
      stagePanel!.round = loop
      for (const w of stagePanel!.windows.values()) {
        w.loop = loop
        w.done = false
        w.issues = 0
        w.invalid = false
        if (loop > 1) {
          w.items = [{ kind: 'sep', text: `── 第 ${loop} 轮 ──`, ts: now() }]
        }
      }
      syncStageItem()
      return
    }
    const agent = String(e.agent ?? 'Agent')
    let win = stagePanel!.windows.get(agent)
    if (!win) {
      win = { agent, items: [], done: false }
      stagePanel!.windows.set(agent, win)
    }
    switch (e.event) {
      case 'conversation_turn': {
        const text = readableContent(String(e.content ?? ''))
        win.items.push({ kind: 'chat', text: text.slice(0, 160), ts: now() })
        break
      }
      case 'llm_delta': {
        const delta = String(e.delta ?? '')
        const last = win.items[win.items.length - 1]
        if (last?.kind === 'chat' && last.streaming) last.text += delta
        else win.items.push({ kind: 'chat', text: delta, streaming: true, ts: now() })
        break
      }
      case 'llm_stream_end': {
        const last = win.items[win.items.length - 1]
        if (last?.kind === 'chat' && last.streaming) last.streaming = false
        break
      }
      case 'tool_pre_use': {
        const tool = String(e.tool ?? 'tool')
        const verb = TOOL_VERBS[tool] || tool
        // 与主 feed 同一提取函数：两处键不一致（此处缺 directory/query）
        // 会让同工具不同目录在面板内被错误合并
        const detail = toolDetail(e.args)
        const last = win.items[win.items.length - 1]
        // 同工具 + 同文件才合并（不同文件不能合并成一条 ×N）
        if (last?.kind === 'work' && last.tool === tool
            && last.detail === detail
            && now() - (last.ts ?? 0) < MERGE_WINDOW_MS) {
          last.count = (last.count ?? 1) + 1
          last.status = 'running'
          last.ts = now()
        } else {
          win.items.push({ kind: 'work', text: `${verb} ${detail}`.trim(),
                           tool, detail, count: 1, status: 'running', ts: now() })
        }
        break
      }
      case 'tool_post_use': {
        const tool = String(e.tool ?? '')
        const preview = String(e.result_preview ?? '')
        for (let i = win.items.length - 1; i >= 0; i--) {
          const li = win.items[i]
          if (li.kind === 'work' && li.tool === tool && li.status === 'running') {
            li.status = /Traceback|Error:/i.test(preview) ? 'fail' : 'ok'
            break
          }
        }
        break
      }
      case 'todo_update': {
        const done = Number(e.done ?? 0)
        const total = Number(e.total ?? 0)
        const last = win.items[win.items.length - 1]
        if (last?.kind === 'todo') last.text = `任务 ${done}/${total}`
        else win.items.push({ kind: 'todo', text: `任务 ${done}/${total}`, ts: now() })
        break
      }
      case 'review_submitted': {
        // 审查者输出合法 issues 数组 → 完成；非空 = 找到问题
        win.done = true
        win.issues = Array.isArray(e.issues) ? e.issues.length : 0
        win.invalid = false
        break
      }
      case 'review_discarded': {
        // 审查输出非法被丢弃 → ⚠️（既不是"完成"也不是"无问题"）
        win.done = false
        win.invalid = true
        break
      }
      case 'agent_done': {
        // 子 agent 完成（coder/文档 writer…）→ 窗口标绿。
        // Reviewer 除外：审查者 react 结束 ≠ 审查完成（还要过 schema 校验，
        // 校验失败还会重试跑工具）—— 绿色只能来自 review_submitted。
        // 仅 status=done（正常收尾）标绿 —— 工具轮次耗尽/LLM 错误
        // 的强制收尾（terminated/error）不标绿，标 ⚠️ 警示。
        if (e.status && e.status !== 'done') {
          win.done = false
          win.invalid = true
          break
        }
        if (!win.invalid && !win.agent.includes('Reviewer')) win.done = true
        break
      }
      case 'agent_typing': {
        // 面板窗口内不显示思考占位（窗口有自己的工作行/流式内容）
        break
      }
      default:
        break
    }
    // 窗口内滚动上限：丢最旧
    if (win.items.length > 40) win.items.splice(0, win.items.length - 40)
    syncStageItem()
  }

  /** 面板数据同步到对话流中的 stage item（响应式更新） */
  function syncStageItem() {
    if (!stagePanel) return
    const it = items.find((i) => i.id === stagePanel!.id)
    if (it?.stage) {
      it.stage.windows = [...stagePanel!.windows.values()]
      it.stage.done = stagePanel!.done
      it.stage.round = stagePanel!.round
      it.content = `${PHASE_LABELS_CN[stagePanel!.phase] ?? stagePanel!.phase} 协作面板${stagePanel!.done ? ' ✅ 完成' : ' …'}`
      it.ts = now()
    }
  }

  /** 关闭面板（done = 面板定格为完成态；false = 阶段切换中断） */
  function closeStage(done: boolean) {
    if (!stagePanel) return
    stagePanel.done = done
    syncStageItem()
    stagePanel = null
  }

  /** 打开协作面板（阶段开始 / integration_start 共用） */
  function openStagePanel(panelPhase: string) {
    const panel: StagePanel = { phase: panelPhase, windows: [], done: false }
    const item = push({
      type: 'stage', agent: 'DevForge',
      content: `${PHASE_LABELS_CN[panelPhase] ?? panelPhase} 协作面板 …`,
      ts: now(), stage: panel,
    })
    stagePanel = { id: item.id, phase: panelPhase, windows: new Map(), done: false }
  }

  /** "思考中…"占位：每个 agent 固定一条（i agree 后 CTO 最终 JSON 总结等
      非流式调用期间显示），该 agent 的内容事件到达即移除 */
  function upsertTyping(agent: string) {
    const existing = [...items].reverse()
      .find((i) => i.type === 'typing' && i.agent === agent)
    // CTO 的最终调用就是架构总结；其余 agent（质检 inspector 等）通用文案
    const text = agent.includes('chief_technology_officer')
      ? `${agentLabel(agent).name} 正在进行最后总结…`
      : `${agentLabel(agent).name} 思考中…`
    if (existing) {
      existing.content = text
      existing.ts = now()
    } else {
      push({ type: 'typing', agent, content: text, ts: now() })
    }
  }

  function clearTyping(agent: string) {
    const idx = items.findIndex((i) => i.type === 'typing' && i.agent === agent)
    if (idx >= 0) items.splice(idx, 1)
  }

  /** 审查者聚合卡：每个 reviewer 固定一张（审查中 = 状态刷新，完成 = 绿色 + 问题数） */
  function upsertReviewerCard(agent: string, done: boolean, issues?: number) {
    const label = done
      ? `✅ 审查完成 · ${issues ? `${issues} 个问题` : '无问题'}`
      : '🔍 审查中…'
    const existing = [...items].reverse()
      .find((i) => i.type === 'reviewer' && i.agent === agent)
    if (existing) {
      existing.reviewerDone = done
      if (issues !== undefined) existing.issues = issues
      existing.content = label
      existing.ts = now()
    } else {
      push({
        type: 'reviewer', agent,
        content: label, ts: now(), reviewerDone: done,
        issues: issues !== undefined ? issues : 0,
      })
    }
  }

  return {
    items,
    addEvent(e) {
      const evType = e.event as string
      // 阶段面板拦截：多子 agent 阶段内的事件进各自小窗口，不进主对话流
      if (stagePanel && !stagePanel.done) {
        if (evType === 'phase_start') {
          closeStage(false)          // 阶段切换：未完成的面板定格
        } else if (evType === 'phase_end') {
          closeStage(true)           // 阶段结束关闭任意活跃面板（含子面板）
        } else if (evType === 'phase_retry') {
          closeStage(false)
        } else if (evType === 'integration_start') {
          closeStage(true)           // 编码子 agent 面板定格，打开整合面板
        } else if (evType === 'review_round') {
          stageEvent(e)              // 无 agent 字段，但必须进面板（重置窗口）
          return
        } else if (stageActive(String(e.agent ?? ''))) {
          stageEvent(e)
          return
        }
      }
      switch (evType) {
        case 'conversation_turn': {
          const agent = String(e.agent ?? 'Agent')
          const raw = String(e.content ?? '')
          const content = readableContent(raw)
          clearTyping(agent)   // 内容到达 → 思考占位移除
          // 去重：流式对话（llm_delta 已实时展示全文）后又来同内容的
          // conversation_turn（converse 两条路径都发）→ 不再重复渲染。
          // 仅当上一条来自流式（fromStream）时才去重 —— 否则同 agent
          // 60s 内合法重复发言（如 PM 重复同一问题）会被吞掉
          const lastChat = [...items].reverse()
            .find((i) => i.type === 'chat' && i.agent === agent)
          if (lastChat && !lastChat.streaming && !lastChat.design
              && lastChat.fromStream === true
              && lastChat.content === content
              && now() - lastChat.ts < 60000) {
            lastChat.ts = now()
            break
          }
          if (agent.includes('Reviewer')) {
            // 审查者聚合卡：每个 reviewer 固定一张，审查中 = 刷新状态（不刷屏）
            upsertReviewerCard(agent, false)
            break
          }
          if (MILESTONE_AGENTS.has(agent)) {
            push({ type: 'milestone', agent, content, ts: now() })
          } else {
            // 结构化架构设计（CTO 最终输出）→ 附 design 数据，渲染为设计卡片
            const item: Omit<FeedItem, 'id' | 'ts' | 'phase'> & { ts?: number }
              = { type: 'chat', agent, content, ts: now() }
            try {
              const obj = JSON.parse(raw)
              if (obj && typeof obj === 'object' && (obj.modality || obj.modules)) {
                item.design = {
                  modality: String(obj.modality ?? ''),
                  language: String(obj.language ?? ''),
                  modules: Array.isArray(obj.modules) ? obj.modules.map((m: any) => ({
                    name: String(m?.name ?? ''),
                    purpose: String(m?.purpose ?? ''),
                    exports: Array.isArray(m?.exports) ? m.exports.map((ex: any) => ({
                      name: String(ex?.name ?? ''),
                      signature: String(ex?.signature ?? ''),
                      description: String(ex?.description ?? ''),
                    })) : [],
                  })) : [],
                }
              } else if (obj && typeof obj === 'object'
                         && Object.keys(obj).some((k) => k !== 'message')) {
                // message 之外的键（core_features/priorities…）被 readableContent
                // 剥掉了 —— 保留原始 JSON 供文档卡层次化渲染
                item.rawContent = raw
              }
            } catch { /* 非 JSON —— 保持普通聊天气泡 */ }
            push(item)
          }
          break
        }
        case 'discuss_choice': {
          // 兼容两种 wire 形状：真实后端 question 为字符串 + options/allow_multiple 顶层；
          // 旧测试/老 Dashboard 用 question 对象形状
          const rawQ = e.question
          const qObj = (typeof rawQ === 'object' && rawQ !== null) ? rawQ as Record<string, unknown> : {}
          const text = typeof rawQ === 'string' ? rawQ : String(qObj.text ?? '')
          const options = Array.isArray(e.options) ? e.options.map(String)
            : (Array.isArray(qObj.options) ? qObj.options.map(String) : [])
          const allowMultiple = Boolean(e.allow_multiple ?? qObj.allow_multiple)
          push({
            type: 'question', agent: 'PM', content: text, ts: now(),
            question: {
              text, options, allowMultiple,
              answered: false, selected: [], sending: false, undelivered: false,
            },
          })
          break
        }
        case 'tool_pre_use': {
          const agent = String(e.agent ?? 'Agent')
          const tool = String(e.tool ?? 'tool')
          const verb = TOOL_VERBS[tool] || tool
          // 写入内容摘要（write_file 等携带 content 参数）
          const args = (e.args && typeof e.args === 'object')
            ? e.args as Record<string, unknown> : {}
          const body = typeof args.content === 'string' ? args.content : ''
          const snippet = body ? contentSnippet(body) : undefined
          // 行数 = 换行符分段数（结尾换行不产生空行）
          const lines = body
            ? body.split('\n').length - (body.endsWith('\n') ? 1 : 0)
            : undefined
          const last = items[items.length - 1]
          // 合并：同阶段 + 同 agent + 同 tool + 同文件 + 距上一条 < 8s
          // （detail 相同才合并 —— 不同文件不能合并成一条"写代码 ×4"）
          const detail = toolDetail(e.args)
          if (last && last.type === 'work' && last.phase === phase
              && last.agent === agent && last.tool === tool
              && last.detail === detail
              && now() - last.ts < MERGE_WINDOW_MS) {
            last.count = (last.count ?? 1) + 1
            last.detail = toolDetail(e.args) || last.detail
            if (snippet) { last.snippet = snippet; last.lines = lines }
            // 失败重跑：清除上次失败的错误摘要，避免残留
            last.err = undefined
            last.status = 'running'
            last.ts = now()
          } else {
            push({
              type: 'work', agent, content: verb, ts: now(),
              tool, detail: toolDetail(e.args), count: 1, status: 'running',
              snippet, lines,
            })
          }
          break
        }
        case 'tool_post_use': {
          const agent = String(e.agent ?? 'Agent')
          const tool = String(e.tool ?? '')
          const preview = String(e.result_preview ?? '')
          for (let i = items.length - 1; i >= 0; i--) {
            const it = items[i]
            if (it.type === 'work' && it.agent === agent && it.tool === tool && it.status === 'running') {
              it.status = /Traceback|Error:/i.test(preview) ? 'fail' : 'ok'
              if (it.status === 'fail') it.err = errSnippet(preview)
              else it.err = undefined
              break
            }
          }
          break
        }
        case 'todo_update': {
          // 任务清单：按 agent 固定一条 —— 同 agent 的更新永远就地刷新
          // 同一张卡（任务数增减、完成打勾都不产生新条目，不割裂）
          const done = Number(e.done ?? 0)
          const total = Number(e.total ?? 0)
          const todos = Array.isArray(e.todos) ? e.todos : []
          const who = String(e.agent ?? 'Agent')
          const label = agentLabel(who).name
          const existing = [...items].reverse()
            .find((i) => i.type === 'todo' && i.agent === who)
          if (existing) {
            existing.content = `📋 ${label} 任务清单 ${done}/${total}`
            existing.todoList = todos
            existing.ts = now()
          } else {
            push({
              type: 'todo', agent: who,
              content: `📋 ${label} 任务清单 ${done}/${total}`, ts: now(),
              todoList: todos,
            })
          }
          break
        }
        case 'review_submitted': {
          // 审查完成：对应的 reviewer 聚合卡标绿（有问题的显示问题数）
          const agent = String(e.agent ?? '')
          if (agent.includes('Reviewer')) {
            upsertReviewerCard(agent, true,
                               Array.isArray(e.issues) ? e.issues.length : 0)
          }
          break
        }
        case 'llm_delta': {
          // 流式输出：追到该 agent 最后一条流式中的 chat 条目（无则新建）
          const agent = String(e.agent ?? 'Agent')
          const delta = String(e.delta ?? '')
          if (!delta) break
          clearTyping(agent)   // 流式内容到达 → 思考占位移除
          const last = [...items].reverse()
            .find((i) => i.type === 'chat' && i.agent === agent && i.streaming)
          if (last) {
            last.content += delta
            last.ts = now()
          } else {
            push({
              type: 'chat', agent, content: delta, ts: now(), streaming: true,
              fromStream: true,   // 全文已实时展示 → 后续同内容 conversation_turn 可去重
            })
          }
          break
        }
        case 'llm_stream_end': {
          // 流式结束：标记完成（之后 markdown 渲染）
          const agent = String(e.agent ?? 'Agent')
          clearTyping(agent)
          const last = [...items].reverse()
            .find((i) => i.type === 'chat' && i.agent === agent && i.streaming)
          if (last) { last.streaming = false; last.ts = now() }
          break
        }
        case 'phase_start': {
          phase = String(e.phase ?? '')
          // 标题在上、面板在下：阶段开始里程碑先出现，再看协作面板
          push({ type: 'milestone', agent: phase,
                 content: `▶ ${PHASE_LABELS_CN[phase] ?? phase} 阶段开始`, ts: now() })
          // 多子 agent 阶段（Coding/Verification/Documentation）→ 打开协作面板：
          // 各子 agent 的小窗口取代主对话流（阶段结束恢复）
          if (stagePhases[phase]) {
            openStagePanel(phase)
          }
          break
        }
        case 'integration_start': {
          // Coding 阶段内的整合联调子面板：标题 + 面板
          push({ type: 'milestone', agent: 'Coding',
                 content: '🧩 进入整合联调', ts: now() })
          if (stagePhases.Integration) openStagePanel('Integration')
          break
        }
        case 'phase_end': {
          phase = ''
          // 后端 ask_choice 有 300s 超时：阶段结束时仍未作答的问题按"超时未作答"
          // 收尾，卡片标记已答不可再交互（否则会永远停留在可回答状态）
          for (const it of items) {
            if (it.type === 'question' && it.question && !it.question.answered) {
              it.question.answered = true
              it.question.selected = ['(超时未作答)']
            }
          }
          break
        }
        case 'phase_retry': {
          phase = ''
          const p = String(e.phase ?? '')
          const loop = Number(e.loop ?? 1)
          const reason = String(e.reason ?? 'fail')   // 'fail'=质检回跳 'error'=出错重试 'feedback'=追加需求
          const content = reason === 'error'
            ? `阶段出错，正在重试（第 ${loop} 次）`
            : reason === 'feedback'
              ? `收到你的补充需求，回到 ${PHASE_LABELS_CN[p] ?? p} 重新规划`
              : `质检未通过，回到 ${PHASE_LABELS_CN[p] ?? p} 重新修复（第 ${loop} 轮）`
          push({ type: 'system', agent: 'DevForge', content, ts: now(), variant: reason === 'error' ? 'error' : 'warn' })
          break
        }
        case 'review_request': {
          // 人工审阅：fixer 修复 diff，等待用户通过/拒绝
          const files = Array.isArray(e.files) ? e.files.map(String) : []
          push({
            type: 'review', agent: 'DevForge',
            content: `需要你审阅 ${files.length} 个文件的修复`,
            ts: now(),
            review: { files, diff: String(e.diff ?? ''), approved: null },
          })
          break
        }
        case 'review_timed_out': {
          // 等待超时 → 后端自动通过：把待决策的审阅卡收尾，不再永久悬停
          const it = [...items].reverse()
            .find((i) => i.type === 'review' && i.review?.approved === null)
          if (it?.review) {
            it.review.approved = true
            it.review.timedOut = true
            it.ts = now()
          }
          break
        }
        case 'agent_typing': {
          // 非流式调用（i agree 后的 CTO 最终 JSON 总结、质检 inspector…）
          // 期间显示思考占位，内容到达即移除
          const agent = String(e.agent ?? '')
          if (agent) upsertTyping(agent)
          break
        }
        case 'agent_done': {
          clearTyping(String(e.agent ?? ''))
          break
        }
        case 'token_warning': {
          const used = Number(e.used ?? 0)
          const budget = Number(e.budget ?? 0)
          push({
            type: 'system', agent: 'DevForge',
            content: `已消耗 ${(used / 1000).toFixed(0)}k / ${(budget / 1000).toFixed(0)}k token 预算，注意控制成本`,
            ts: now(), variant: 'warn',
          })
          break
        }
        case 'phase_error': {
          // 阶段出错：红色错误卡片（区别于普通 warn）+ D2 中文提示
          phase = ''
          const p = String(e.phase ?? '')
          const err = String(e.error ?? e.message ?? '')
          const hint = zhErrorHint(err)
          const content = err
            ? `${PHASE_LABELS_CN[p] ?? p} 阶段出错：${errSnippet(err)}`
              + (hint ? `\n💡 ${hint}` : '')
            : `${PHASE_LABELS_CN[p] ?? p} 阶段出错`
          push({ type: 'system', agent: 'DevForge', content, ts: now(), variant: 'error' })
          break
        }
        case 'error': {
          // 运行级错误：红色错误卡片 + D2 中文提示
          phase = ''
          const msg = String(e.message ?? e.error ?? '运行出错')
          const hint = zhErrorHint(msg)
          push({ type: 'system', agent: 'DevForge',
                 content: `运行出错：${errSnippet(msg)}`
                          + (hint ? `\n💡 ${hint}` : ''),
                 ts: now(), variant: 'error' })
          break
        }
        case 'requirements_submitted': {
          push({ type: 'milestone', agent: 'PM', content: '需求确定 ✅', ts: now() })
          break
        }
        case 'design_submitted': {
          push({ type: 'milestone', agent: 'CTO', content: '设计完成 ✅', ts: now() })
          break
        }
        case 'quality_gate': {
          const d = (e.data ?? {}) as Record<string, unknown>
          const verdict = String(d.verdict ?? 'WARN')
          if (verdict === 'PASS') {
            push({ type: 'milestone', agent: 'QA', content: '质检通过 ✅', ts: now() })
          } else {
            // FAIL 与 WARN 都完整列出未达标项 —— 不能只给个"质检结论: WARN"
            const missing = (Array.isArray(d.features) ? d.features : [])
              .filter((f: any) => f && (f.status === 'NO' || f.status === 'PARTIAL'))
              .slice(0, 8)
            if (missing.length) {
              const head = `质检${verdict === 'FAIL' ? '未通过' : '有未达标项'} ❌ 共 ${missing.length} 项：`
              const lines = missing.map((m: any) =>
                `- [${m.status}] ${m.name ?? '?'}${m.notes ? `：${String(m.notes).slice(0, 60)}` : ''}`)
              push({ type: 'system', agent: 'QA', variant: 'error',
                     content: [head, ...lines].join('\n'), ts: now() })
            } else {
              push({ type: 'milestone', agent: 'QA', content: `质检结论: ${verdict}`, ts: now() })
            }
          }
          break
        }
        case 'pipeline_complete': {
          if (e.failed) {
            // 项目已交付（带缺陷标记），不是放弃 —— 历史页可查看/重跑
            const loops = Number(e.qg_loops ?? 0) || 3
            push({ type: 'system', agent: 'DevForge',
                   content: `⚠️ 项目已交付，但质检 ${loops} 次未通过（仍存在缺陷）— 详见历史页质量报告，可重跑修复`, ts: now() })
          } else {
            const verdict = String(e.verdict ?? '')
            if (verdict && verdict !== 'PASS') {
              // 质检非 PASS 完成：不能说"全部完成"
              push({ type: 'milestone', agent: 'DevForge',
                     content: `🎉 流程完成（质检结论: ${verdict} — 存在未达标项，见上方质检卡）`,
                     ts: now() })
            } else {
              push({ type: 'milestone', agent: 'DevForge', content: '🎉 全部完成', ts: now() })
            }
          }
          break
        }
        default:
          break
      }
    },
    addChat(content) {
      // 运行中追加需求：用户消息进对话流（与 answerQuestion 同形状渲染）
      push({ type: 'chat', agent: '你', content, ts: now() })
    },
    answerQuestion(questionId, selected, custom) {
      const it = items.find((i) => i.id === questionId && i.type === 'question')
      if (!it?.question || it.question.answered) return
      it.question.answered = true
      it.question.selected = [...selected]
      if (custom) it.question.custom = custom
      const parts = [...selected]
      if (custom) parts.push(`其他: ${custom}`)
      push({ type: 'answer', agent: '你', content: parts.join(', ') || '(无选择)', ts: now() })
    },
    setQuestionSending(questionId, sending) {
      const it = items.find((i) => i.id === questionId && i.type === 'question')
      if (it?.question) it.question.sending = sending
    },
    decideReview(reviewId: number, approved: boolean) {
      const it = items.find((i) => i.id === reviewId && i.type === 'review')
      if (!it?.review || it.review.approved !== null) return
      it.review.approved = approved
      push({ type: 'answer', agent: '你',
             content: approved ? `已通过修复（${(it.review.files ?? []).join(', ')}）`
                               : `拒绝修复（${(it.review.files ?? []).join(', ')}）— 请重新处理`,
             ts: now() })
    },
    setUndelivered(questionId, undelivered) {
      const it = items.find((i) => i.id === questionId && i.type === 'question')
      if (it?.question) it.question.undelivered = undelivered
    },
    clearSending() {
      for (const it of items) if (it.question) it.question.sending = false
    },
    setStagePhases(phases) {
      // 后端 /api/config 下发阶段面板路由（单一来源 phases.json）——
      // 新增 lens/角色后前端面板自动跟随。与默认路由合并：
      // Integration 等前端约定面板不被后端覆盖丢失
      if (phases && typeof phases === 'object') {
        stagePhases = { ...DEFAULT_STAGE_PHASES, ...phases }
      }
    },
    reset() {
      items.length = 0
      phase = ''
      stagePanel = null
    },
    resetStage() {
      // 重跑（startFrom）只裁剪条目不清空：旧 run 中断在面板进行中时，
      // 新 run 的首个无 agent 字段事件会被旧 stagePanel 闭包拦截，
      // 产生瞬态 'Agent' 幻影窗口 —— 重跑时显式清掉面板状态
      stagePanel = null
      phase = ''
    },
  }
}
