<!-- ConversationPanel.vue — 统一对话时间线：问答卡内嵌 + 工作消息 + 里程碑 -->
<template>
  <div class="conv-wrap">
  <div class="conv">
    <div class="conv-scroll" ref="panel" @scroll="onScroll">
    <!-- 等待期占位：任务刚启动、事件还没到达时的"思考中"反馈 -->
    <div v-if="!items.length" class="empty">
      <template v-if="placeholder">
        <span class="thinking-dots"><i /><i /><i /></span>
        <span class="thinking-text">{{ placeholder }}</span>
      </template>
      <template v-else>
        <span class="dot" />
        等待 Agent 对话...
      </template>
    </div>

    <div v-for="it in items" :key="it.id" class="msg" :class="[it.type, it.agent.toLowerCase()]"
      :style="{ '--accent': agentLabel(it.agent).color }">
      <!-- work：紧凑单行（agent + 动词 + 文件 + 写入摘要 + 次数 + 错误摘要） -->
      <div v-if="it.type === 'work'" class="work-row" :class="{ fail: it.status === 'fail' }">
        <span class="work-ic">{{ workIcon(it.status) }}</span>
        <span class="work-who">{{ agentLabel(it.agent).name }}</span>
        <span class="work-v">{{ it.content }}</span>
        <span v-if="it.detail" class="work-d" :title="it.detail">{{ it.detail }}</span>
        <span v-if="it.lines" class="work-lines">+{{ it.lines }} 行</span>
        <span v-if="it.snippet" class="work-snip" :title="it.snippet">“{{ it.snippet }}”</span>
        <span v-if="(it.count ?? 1) > 1" class="work-n">×{{ it.count }}</span>
        <span v-if="it.err" class="work-err" :title="it.err">{{ it.err }}</span>
        <span class="ts">{{ fmtTs(it.ts) }}</span>
      </div>

      <!-- design：结构化设计卡片 -->
      <div v-else-if="it.type === 'chat' && it.design" class="design-card">
        <div class="head">
          <span class="avatar">{{ agentLabel(it.agent).emoji }}</span>
          <span class="who">{{ agentLabel(it.agent).name }}</span>
          <span class="ts">{{ fmtTs(it.ts) }}</span>
        </div>
        <div class="design-head">架构设计</div>
        <div class="design-chips">
          <span class="chip">{{ it.design.modality }}</span>
          <span class="chip">{{ it.design.language }}</span>
        </div>
        <div class="design-modules">
          <details v-for="m in it.design.modules" :key="m.name" class="dm">
            <summary>
              <span class="dm-name">{{ m.name }}</span>
              <span class="dm-purpose">{{ m.purpose }}</span>
            </summary>
            <ul class="dm-exports" v-if="m.exports?.length">
              <li v-for="ex in m.exports" :key="ex.name">
                <code>{{ ex.name }}{{ ex.signature }}</code>
                <span v-if="ex.description"> — {{ ex.description }}</span>
              </li>
            </ul>
          </details>
        </div>
      </div>

      <!-- question：内嵌问答卡（已回答 → 默认折叠成单行摘要） -->
      <div v-else-if="it.type === 'question'">
        <div
          v-if="it.question?.answered && !qExpanded.has(it.id)"
          class="q-collapsed"
          title="点击展开回顾"
          @click="qExpanded.add(it.id)"
        >
          <span class="avatar">{{ agentLabel(it.agent).emoji }}</span>
          <span class="q-collapsed-sum">✅ 已选择：{{ answerSummary(it) }}</span>
          <span class="q-collapsed-toggle">展开 ▾</span>
          <span class="ts">{{ fmtTs(it.ts) }}</span>
        </div>
        <div v-else>
          <div class="head">
            <span class="avatar">{{ agentLabel(it.agent).emoji }}</span>
            <span class="who">{{ agentLabel(it.agent).name }}</span>
            <span class="ts">{{ fmtTs(it.ts) }}</span>
          </div>
        <div class="q-card" :class="{ answered: it.question?.answered }">
          <div class="q-text">
            {{ it.content }}
            <span class="q-mode">{{ it.question?.allowMultiple ? '（可多选）' : '（单选）' }}</span>
          </div>
          <div class="q-opts">
            <button
              v-for="o in it.question?.options ?? []" :key="o"
              class="q-btn"
              :class="{ picked: isPicked(it, o) }"
              :disabled="it.question?.answered || !interactive"
              @click="toggle(it, o)"
            >{{ o }}</button>
            <!-- 每个问题常驻"其他"：允许用户自行输入（PM 选项未含时不重复） -->
            <button
              v-if="!hasOtherOption(it)"
              class="q-btn"
              :class="{ picked: customOpen.has(it.id) }"
              :disabled="it.question?.answered || !interactive"
              @click="toggleOther(it)"
            >其他</button>
          </div>
          <div v-if="customOpen.has(it.id)" class="q-custom">
            <input
              v-model="customTexts[it.id]"
              placeholder="其他，请描述..."
              class="q-input"
              :disabled="it.question?.answered || !interactive"
              @keydown.enter="confirm(it)"
            />
          </div>
          <div class="q-bar">
            <button class="q-confirm" :disabled="!canConfirm(it)" @click="confirm(it)">
              {{ it.question?.sending ? (it.question?.undelivered ? '发送中…（断线，重连自动重发）' : '发送中…') : it.question?.answered ? '已选择' : '确认选择' }}
            </button>
            <span v-if="it.question?.answered" class="q-done">
              {{ it.question?.selected.join(', ') }}{{ it.question?.custom ? ' + 其他: ' + it.question.custom : '' }}
            </span>
            <button
              v-if="it.question?.answered"
              class="q-collapse-btn"
              @click="qExpanded.delete(it.id)"
            >收起</button>
          </div>
        </div>
        </div>
      </div>

      <!-- structured：JSON 结构内容 → 文档卡（主标题 + 分节要点，CTO/CPO 层次化） -->
      <div v-else-if="it.type === 'chat' && docSections(it)" class="doc-card">
        <div class="head">
          <span class="avatar">{{ agentLabel(it.agent).emoji }}</span>
          <span class="who">{{ agentLabel(it.agent).name }}</span>
          <span class="ts">{{ fmtTs(it.ts) }}</span>
        </div>
        <div v-if="docHead(it)" class="doc-head">{{ docHead(it) }}</div>
        <div v-for="sec in docSections(it)!" :key="sec.title" class="doc-sec">
          <div class="doc-sec-title">{{ sec.title }}</div>
          <ul class="doc-sec-lines">
            <li v-for="(l, li) in sec.lines" :key="li">{{ l }}</li>
          </ul>
        </div>
      </div>

      <!-- todo：任务清单卡片（agent 每步更新的计划） -->
      <div v-else-if="it.type === 'todo'" class="todo-card">
        <div class="todo-head">{{ it.content }}</div>
        <ul v-if="it.todoList?.length" class="todo-list">
          <li v-for="t in it.todoList.slice(0, 6)" :key="t.content" :class="t.status">
            <span class="todo-mark">{{ t.status === 'completed' ? '✓' : (t.status === 'in_progress' ? '●' : '○') }}</span>
            <span class="todo-text">{{ t.content }}</span>
          </li>
          <li v-if="it.todoList.length > 6" class="todo-more">+{{ it.todoList.length - 6 }} 项</li>
        </ul>
      </div>

      <!-- stage：多子 agent 阶段协作面板（每 agent 一个小窗口，各自滚动） -->
      <div v-else-if="it.type === 'stage'" class="stage-panel" :class="{ done: it.stage?.done }">
        <div class="stage-head">
          {{ stageLabel(it) }}
        </div>
        <div class="stage-grid">
          <div
            v-for="w in it.stage?.windows ?? []" :key="w.agent"
            class="stage-win" :class="{ done: w.done, invalid: w.invalid }"
          >
            <div class="stage-win-head">
              <span class="stage-win-name">{{ agentLabel(w.agent).name }}</span>
              <span class="stage-win-mark" :class="{ invalid: w.invalid }"
                :title="w.invalid ? '审查输出无效（结果存疑）'
                  : w.done ? (w.issues ? `发现 ${w.issues} 个问题` : '审查完成，无问题')
                  : '工作中…'">
                {{ w.invalid ? '⚠️' : w.done ? (w.issues ? `✅ ${w.issues}` : '✅') : '…' }}
              </span>
            </div>
            <div class="stage-win-body" :ref="(el) => setWinBody(w.agent, el)">
              <div v-for="(li, i) in w.items" :key="i" class="stage-line" :class="[li.kind, li.status ?? '']">
                <template v-if="li.kind === 'sep'">
                  <span class="stage-sep">{{ li.text }}</span>
                </template>
                <template v-else>
                  <span v-if="li.kind === 'work'" class="stage-ic">
                    {{ li.status === 'fail' ? '✗' : li.status === 'ok' ? '✓' : '⏳' }}
                  </span>
                  <span v-else-if="li.kind === 'todo'" class="stage-ic">📋</span>
                  <span class="stage-text stage-text-pre">{{ stageLineText(li) }}<span v-if="(li.count ?? 1) > 1"> ×{{ li.count }}</span></span>
                  <span v-if="li.streaming" class="stream-caret" />
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- reviewer：审查者聚合卡（固定区域，完成标绿） -->
      <div v-else-if="it.type === 'reviewer'" class="reviewer-card" :class="{ done: it.reviewerDone }">
        <span class="reviewer-ic">{{ it.reviewerDone ? '✅' : '🔍' }}</span>
        <span class="reviewer-name">{{ agentLabel(it.agent).name }}</span>
        <span class="reviewer-status">{{ it.reviewerDone ? '审查完成' : '审查中…' }}</span>
        <span class="reviewer-spin" v-if="!it.reviewerDone" />
        <span class="ts">{{ fmtTs(it.ts) }}</span>
      </div>

      <!-- review：人工审阅卡（fixer 修复 diff，等待用户通过/拒绝） -->
      <div v-else-if="it.type === 'review'" class="review-card"
        :class="{ pending: it.review?.approved === null, 'timed-out': it.review?.timedOut }">
        <div class="head">
          <span class="avatar">🔍</span>
          <span class="who">人工审阅</span>
          <span class="ts">{{ fmtTs(it.ts) }}</span>
        </div>
        <div class="review-title">修复待审阅：{{ (it.review?.files ?? []).join(', ') }}</div>
        <details v-if="it.review?.diff" class="review-diff">
          <summary>查看 diff（{{ it.review?.files?.length ?? 0 }} 个文件）
            <span class="diff-legend"><i class="legend-add" />新增 <i class="legend-del" />删除</span>
          </summary>
          <pre class="diff-pre"><template v-for="(line, li) in diffLines(it.review.diff)" :key="li">
<span :class="diffClass(line)">{{ line }}</span>
</template></pre>
        </details>
        <div v-if="it.review?.approved === null" class="review-waiting">
          ⏳ 正在等待<strong>你</strong>审阅 — 点「通过 / 拒绝」；无人操作会自动通过
        </div>
        <div v-if="it.review?.approved === null" class="review-actions">
          <button class="rv-btn rv-approve" @click="decide(it, true)">✓ 通过</button>
          <button class="rv-btn rv-reject" @click="decide(it, false)">✗ 拒绝</button>
        </div>
        <div v-else class="review-done"
          :class="{ rejected: it.review?.approved === false, timed: it.review?.timedOut }">
          {{ it.review?.timedOut
            ? '⏰ 等待超时，自动通过（你未在时限内审阅）'
            : it.review?.approved ? '✅ 已通过' : '❌ 已拒绝 — 将带着你的反馈重新修复' }}
        </div>
      </div>

      <!-- typing：非流式调用期间的思考占位（CTO 最终 JSON 总结等） -->
      <div v-else-if="it.type === 'typing'" class="typing-line">
        <span class="thinking-dots"><i /><i /><i /></span>
        <span>{{ it.content }}</span>
      </div>

      <!-- milestone / system：居中卡片 -->
      <div v-else-if="it.type === 'milestone'" class="milestone">{{ it.content }}</div>
      <div v-else-if="it.type === 'system'" class="system" :class="{ 'system-error': it.variant === 'error' }">
        {{ it.variant === 'error' ? '⛔ ' : '⚠️ ' }}{{ it.content }}
      </div>

      <!-- chat / answer：气泡（长文 → 文档样式 + 折叠；流式中 → 纯文本 + 光标） -->
      <template v-else>
        <div class="head" :class="{ me: it.type === 'answer' }">
          <span class="avatar">{{ agentLabel(it.agent).emoji }}</span>
          <span class="who">{{ agentLabel(it.agent).name }}</span>
          <span class="ts">{{ fmtTs(it.ts) }}</span>
        </div>
        <div v-if="it.streaming" class="bbl streaming">
          {{ it.content }}<span class="stream-caret" />
        </div>
        <div
          v-else
          class="bbl"
          :class="[{ me: it.type === 'answer' }, { doc: isLong(it) && !expanded.has(it.id) }]"
          v-html="mdCached(it)"
        ></div>
        <button v-if="isLong(it) && !it.streaming" class="toggle-btn" @click="toggleExpand(it.id)">
          {{ expanded.has(it.id) ? '收起 ▲' : '展开全文 ▼' }}
        </button>
      </template>
    </div>
    </div><!-- /conv-scroll -->

    <!-- 追加需求输入框：conv 的最后一个子元素，永远固定在 conv 底部 -->
    <div class="composer">
      <input
        v-model="draft"
        class="composer-input"
        :placeholder="interactive
          ? '补充需求 / 修改意见 — 阶段边界生效，会回到设计重新规划…'
          : '任务运行中可补充需求 / 修改意见…'"
        :disabled="!interactive"
        @keydown.enter="sendDraft()"
      />
      <button
        class="composer-send"
        :disabled="!interactive || !draft.trim()"
        @click="sendDraft()"
      >发送</button>
    </div>
  </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { agentLabel } from './spriteDefs'
import { PHASE_LABELS_CN } from '../phases'
import type { FeedItem } from './feed'

const props = defineProps<{
  items: FeedItem[]
  /** 空对话时的等待占位（如"产品经理正在梳理需求…"）；空 = 默认文案 */
  placeholder?: string
  /** 运行中：显示底部"追加需求"输入框 */
  interactive?: boolean
}>()
const emit = defineEmits<{
  confirm: [payload: { id: number; selected: string[]; custom: string }]
  'user-message': [text: string]
  'review-decision': [payload: { id: number; approved: boolean }]
}>()

// 追加需求输入框（常驻底部；任务未运行时禁用）
const draft = ref('')
function sendDraft() {
  if (!props.interactive) return
  const text = draft.value.trim()
  if (!text) return
  emit('user-message', text)
  draft.value = ''
}
function decide(it: FeedItem, approved: boolean) {
  if (it.review?.approved !== null) return
  emit('review-decision', { id: it.id, approved })
}

const panel = ref<HTMLElement | null>(null)
// 阶段小窗口滚动体（agent → 元素）：内容/流式追加时自动滚底
const winBodies = new Map<string, HTMLElement>()
function setWinBody(agent: string, el: unknown) {
  if (el instanceof HTMLElement) winBodies.set(agent, el)
  else winBodies.delete(agent)
}
const sel = reactive(new Map<number, string[]>())
// 注意：customTexts 用普通对象（Record），Vue 响应式 Map 不支持 v-model 下标赋值
const customTexts = reactive<Record<number, string>>({})
// customOpen 只调用 has/add/delete 方法（reactive Set 支持），无下标访问
const customOpen = reactive(new Set<number>())
// 长文折叠状态（chat 内容 > 300 字符）
const expanded = reactive(new Set<number>())
// 已回答问题卡的展开状态（默认折叠为单行摘要）
const qExpanded = reactive(new Set<number>())
const LONG_THRESHOLD = 300
let nearBottom = true

// 结构化 JSON → 文档卡分节（主标题 message + 其余键值/数组作为小节）。
// 仅处理 chat 内容；design 已走设计卡片；纯文本/单 message 走 markdown 气泡。
interface DocSection { title: string; lines: string[] }

function fmtVal(v: unknown): string {
  if (typeof v === 'string') return v.length > 120 ? v.slice(0, 120) + '…' : v
  const s = JSON.stringify(v)
  return (s ?? String(v)).length > 120 ? s.slice(0, 120) + '…' : s
}

function docSections(it: FeedItem): DocSection[] | null {
  if (it.design) return null
  let obj: unknown
  // 优先原始 JSON 载荷（feed 把 message 之外的键剥掉了），回退 content 本身
  try { obj = JSON.parse(it.rawContent ?? it.content) } catch { return null }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null
  const sections: DocSection[] = []
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (k === 'message') continue                       // message 作主标题
    if (v === null || v === undefined || v === '') continue
    const lines = Array.isArray(v)
      ? v.map(fmtVal)
      : typeof v === 'object'
        ? Object.entries(v as Record<string, unknown>)
          .map(([ik, iv]) => `${ik}: ${fmtVal(iv)}`)
        : [fmtVal(v)]
    sections.push({ title: k, lines: lines.slice(0, 8) })
  }
  return sections.length ? sections : null
}

function docHead(it: FeedItem): string {
  try {
    const obj = JSON.parse(it.rawContent ?? it.content) as { message?: unknown }
    if (obj && typeof obj.message === 'string') return obj.message
  } catch { /* 非 JSON */ }
  return ''
}

// markdown → HTML 后经 DOMPurify 消毒再进 v-html（内容来自 LLM，视为不可信）
function md(t: string): string { return DOMPurify.sanitize(marked.parse(t, { breaks: true }) as string) }

// markdown 按条目缓存：同一内容不重复解析（展开/收起、滚动重渲染只命中缓存）；
// 缓存以条目对象为键，displayContent 输出变化（如折叠切换）时自动失效
const mdCache = new WeakMap<FeedItem, { t: string; h: string }>()
function mdCached(it: FeedItem): string {
  const text = displayContent(it)
  const cached = mdCache.get(it)
  if (cached && cached.t === text) return cached.h
  const html = md(text)
  mdCache.set(it, { t: text, h: html })
  return html
}

function fmtTs(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  const today = new Date()
  const sameDay = d.getFullYear() === today.getFullYear() && d.getMonth() === today.getMonth() && d.getDate() === today.getDate()
  return sameDay ? time : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`
}

function workIcon(status?: string): string {
  if (status === 'fail') return '✗'
  if (status === 'ok') return '✓'
  return '⏳'
}

// D1: 阶段窗口聊天行解析渲染 —— coder 流式输出是 JSON 原文
//（{"files": [...]}），完成后渲染成紧凑的文件清单而非转义 JSON
function stageLineText(li: { kind: string; text: string; streaming?: boolean }): string {
  if (li.kind !== 'chat' || li.streaming) return li.text
  const t = li.text.trim()
  if (!t.startsWith('{')) return li.text
  try {
    const obj = JSON.parse(t)
    if (Array.isArray(obj.files) && obj.files.length) {
      return obj.files.slice(0, 4).map((f: any) => {
        const name = String(f?.filename ?? f?.path ?? '?')
        const lines = typeof f?.content === 'string'
          ? f.content.split('\n').length : 0
        return `📄 ${name}${lines ? ` +${lines} 行` : ''}`
      }).join('\n') + (obj.files.length > 4 ? `\n… 共 ${obj.files.length} 个文件` : '')
    }
    if (Array.isArray(obj.modules)) {
      return '模块: ' + obj.modules.map((m: any) => m?.name ?? '?').join(', ')
    }
    if (typeof obj.message === 'string') return obj.message
  } catch { /* 非 JSON */ }
  return li.text
}

// ── diff 行级着色（Claude Code 风格）：+ 绿色 / - 红色 / @@ 头部 / 文件头 ──
function diffLines(diff: string): string[] {
  return diff.split('\n')
}
function diffClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git'))
    return 'diff-file'
  if (line.startsWith('@@')) return 'diff-hunk'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  return ''
}

function isPicked(it: FeedItem, o: string): boolean {
  return (sel.get(it.id) ?? []).includes(o) || (it.question?.answered && it.question.selected.includes(o))
}

function stageLabel(it: FeedItem): string {
  const phase = PHASE_LABELS_CN[it.stage?.phase ?? ''] ?? it.stage?.phase ?? ''
  const n = it.stage?.windows.length ?? 0
  return it.stage?.done
    ? `${phase} 协作完成 ✅（${n} 个子 agent）`
    : `${phase} 协作面板 …（${n} 个子 agent 并行中）`
}

/** 已答问题的单行摘要（折叠卡文案） */
function answerSummary(it: FeedItem): string {
  const parts = [...(it.question?.selected ?? [])]
  if (it.question?.custom) parts.push(`其他: ${it.question.custom}`)
  return parts.join(', ') || '(无选择)'
}

/** 选项里是否已含"其他/Other"（避免常驻按钮重复） */
function hasOtherOption(it: FeedItem): boolean {
  return (it.question?.options ?? []).some((o) => o.includes('Other') || o.includes('其他'))
}

/** 常驻"其他"按钮：切换自定义输入框 */
function toggleOther(it: FeedItem) {
  if (it.question?.answered || !props.interactive) return
  if (customOpen.has(it.id)) customOpen.delete(it.id)
  else customOpen.add(it.id)
}

function isLong(it: FeedItem): boolean {
  return it.content.length > LONG_THRESHOLD
}

function displayContent(it: FeedItem): string {
  if (!isLong(it) || expanded.has(it.id)) return it.content
  return it.content.slice(0, LONG_THRESHOLD) + '\n\n…'
}

function toggleExpand(id: number) {
  if (expanded.has(id)) expanded.delete(id)
  else expanded.add(id)
}

function toggle(it: FeedItem, o: string) {
  // 运行结束/出错后（interactive=false）问题不可再交互 ——
  // 否则确认后 pendingAnswer 永无重发机会，卡片永久"发送中…"
  if (it.question?.answered || !props.interactive) return
  if (o.includes('Other') || o.includes('其他')) {
    if (customOpen.has(it.id)) customOpen.delete(it.id)
    else customOpen.add(it.id)
    return
  }
  const cur = sel.get(it.id) ?? []
  if (it.question?.allowMultiple) {
    const idx = cur.indexOf(o)
    if (idx >= 0) cur.splice(idx, 1)
    else cur.push(o)
    sel.set(it.id, cur)
  } else {
    sel.set(it.id, [o])
  }
}

function canConfirm(it: FeedItem): boolean {
  if (it.question?.answered || it.question?.sending) return false
  if (!props.interactive) return false   // 流程已结束：不再接受新回答
  const picked = (sel.get(it.id) ?? []).length > 0
  const custom = (customTexts[it.id] ?? '').trim().length > 0
  return picked || custom
}

function confirm(it: FeedItem) {
  if (!canConfirm(it)) return
  emit('confirm', {
    id: it.id,
    selected: sel.get(it.id) ?? [],
    custom: (customTexts[it.id] ?? '').trim(),
  })
}

// ── 滚动：新条目自动滚底；用户上翻暂停 ──
function onScroll() {
  const el = panel.value
  if (!el) return
  nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

// 滚底监听：条目数变化（新增）或内容变化（流式追加）都触发。
// 用内容签名避免 deep watch 全数组；items ≤ 200 开销可接受
watch(
  () => props.items.map((i) => i.content.length + (i.streaming ? 1 : 0)).join(','),
  async () => {
    if (!nearBottom) return
    await nextTick()
    const el = panel.value
    if (el) el.scrollTop = el.scrollHeight
  }
)

// 阶段小窗口自动滚底：窗口内容（含流式追加）变化时把该窗口滚到底，
// 用户才能看到"新内容正在写入"—— 之前新行在 96px 窗口折叠区外不可见
watch(
  () => props.items
    .filter((i) => i.type === 'stage')
    .map((i) => (i.stage?.windows ?? []).map(
      (w) => `${w.agent}:${w.items.length}:${w.items.reduce((s, li) => s + li.text.length, 0)}`
    ).join('|')).join(';'),
  async () => {
    await nextTick()
    for (const el of winBodies.values()) el.scrollTop = el.scrollHeight
  }
)

// 人工审阅卡到达 → 滚动到视野中央（等待用户决策，不能被埋在后面）。
// 必须定位"最后一张 pending 卡" —— 第二轮审阅时 querySelector 会命中
// 第一轮的旧卡（已决策但还在 DOM 里），把滚动位置带回去
watch(
  () => props.items.filter((i) => i.type === 'review' && i.review?.approved === null).length,
  async (n, old) => {
    if (n > (old ?? 0)) {
      await nextTick()
      // 定位最后一张 pending 卡（querySelector 会命中第一轮的旧卡）
      const cards = [...(panel.value?.querySelectorAll('.review-card.pending') ?? [])]
      const last = cards[cards.length - 1]
      last?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }
)

/** 最后一张面板元素 —— querySelector 会命中第一张（Coding 阶段面板），
 *  审阅后滚动必须定位当前正在工作的那张 */
function lastStagePanel(): Element | null {
  const els = [...(panel.value?.querySelectorAll('.stage-panel') ?? [])]
  return els[els.length - 1] ?? null
}

// 第 2 轮审查开始（review_round loop>1）→ 把协作面板滚回视野中央，
// 否则新一轮内容在旧窗口里输出，用户看不到。
// 注意：取"最后一张面板"的轮次 —— 多张面板 join 成字符串后 Number() 是
// NaN，永远不触发（此前人工确认后不滚动的真凶）
watch(
  () => {
    const stages = props.items.filter((i) => i.type === 'stage')
    return stages[stages.length - 1]?.stage?.round ?? 0
  },
  async (next, prev) => {
    if (next > 1 && next > (prev ?? 0)) {
      await nextTick()
      lastStagePanel()?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }
)

// 人工审阅决策后 → 面板滚回视野（新验证轮次在面板里进行）
watch(
  () => props.items.filter((i) => i.type === 'review' && i.review?.approved !== null).length,
  async (n, old) => {
    if (n > (old ?? 0)) {
      await nextTick()
      lastStagePanel()?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }
)
</script>

<style scoped>
/* 固定高度布局：绝对定位占满父容器（不依赖 height:100% 链）。
   conv = flex column：消息滚动区 flex:1 + composer 作为最后子元素固定在底部 */
.conv-wrap { position: absolute; inset: 0; display: flex; flex-direction: column; min-height: 0; }
.conv { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.conv-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px; box-sizing: border-box; font-size: 13px; }

/* 追加需求输入框 */
.composer {
  display: flex; gap: 8px; padding: 8px 12px;
  border-top: 1px solid #e2e8f0; background: #f8fafc;
  flex-shrink: 0;
}
.composer-input {
  flex: 1; padding: 7px 12px; border: 1px solid #e2e8f0; border-radius: 8px;
  font: 12px system-ui; outline: none; color: #1e293b; background: #fff;
}
.composer-input:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.1); }
.composer-send {
  padding: 7px 16px; border: none; border-radius: 8px;
  background: #6366f1; color: #fff; font: 600 12px system-ui; cursor: pointer;
}
.composer-send:disabled { opacity: 0.4; cursor: not-allowed; }

/* 阶段协作面板：多子 agent 各自小窗口，消息在窗口内滚动 */
.stage-panel {
  margin: 6px 0 12px; padding: 8px 10px;
  border: 1px solid #c7d2fe; border-left: 3px solid #6366f1;
  border-radius: 8px; background: #f8fafc;
}
.stage-panel.done { border-left-color: #10b981; }
.stage-head { font-size: 11px; font-weight: 700; color: #4338ca; margin-bottom: 6px; letter-spacing: 0.04em; }
.stage-panel.done .stage-head { color: #047857; }
.stage-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
.stage-win { border: 1px solid #e2e8f0; border-radius: 6px; background: #fff; min-width: 0; }
/* 完成的子 agent：绿框（边框 + 头部底色，完成一眼可辨） */
.stage-win.done { border-color: #34d399; box-shadow: 0 0 0 1px rgba(52, 211, 153, 0.25); }
.stage-win.invalid { border-color: #fca5a5; box-shadow: 0 0 0 1px rgba(252, 165, 165, 0.25); }
.stage-win-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 3px 8px; background: #eef2ff; border-radius: 5px 5px 0 0;
}
.stage-win.done .stage-win-head { background: #d1fae5; }
.stage-win.invalid .stage-win-head { background: #fee2e2; }
.stage-win-name { font-size: 11px; font-weight: 700; color: #4338ca; }
.stage-win.done .stage-win-name { color: #047857; }
.stage-win.invalid .stage-win-name { color: #b91c1c; }
.stage-win-mark { font-size: 10px; }
.stage-win-mark.invalid { font-size: 11px; }
.stage-win-body {
  height: 96px; overflow-y: auto; padding: 4px 8px;
}
.stage-line {
  display: flex; gap: 4px; align-items: baseline;
  font-size: 11px; color: #475569; line-height: 1.5;
  word-break: break-all;
}
.stage-line.chat { color: #334155; }
.stage-line.fail { color: #dc2626; }
.stage-ic { flex-shrink: 0; font-size: 10px; color: #94a3b8; }
.stage-line.fail .stage-ic { color: #dc2626; }
.stage-text { min-width: 0; }
.stage-text-pre { white-space: pre-wrap; }
/* 审查轮次分隔线（第 2 轮起） */
.stage-sep {
  width: 100%; margin: 2px 0; font-size: 10px; font-weight: 700;
  color: #6366f1; text-align: center; letter-spacing: 0.05em;
  border-top: 1px dashed #c7d2fe; padding-top: 2px;
}

/* 审查者聚合卡：固定区域，完成绿色 */
.reviewer-card {
  display: flex; align-items: center; gap: 6px;
  margin: 4px 0; padding: 5px 12px; max-width: 88%;
  background: #fffbeb; border: 1px solid #fde68a; border-left: 3px solid #f59e0b;
  border-radius: 6px; font-size: 12px; color: #92400e;
}
.reviewer-card.done {
  background: #ecfdf5; border-color: #a7f3d0; border-left-color: #10b981;
  color: #047857;
}
.reviewer-ic { flex-shrink: 0; }
.reviewer-name { font-weight: 700; }
.reviewer-status { color: #b45309; font-size: 11px; }
.reviewer-card.done .reviewer-status { color: #059669; }
.reviewer-spin {
  width: 10px; height: 10px; margin-left: auto;
  border: 2px solid rgba(245, 158, 11, 0.3); border-top-color: #f59e0b;
  border-radius: 50%; animation: rv-spin 0.8s linear infinite;
}
.reviewer-card.done .reviewer-spin { display: none; }
@keyframes rv-spin { to { transform: rotate(360deg); } }

/* 人工审阅卡：待决策 = 琥珀色脉冲（一眼看出流程在等你） */
.review-card { border: 2px solid #e2e8f0; border-left: 3px solid #f59e0b; border-radius: 8px; padding: 10px 12px; max-width: 92%; background: #fffbeb; }
.review-card.pending { animation: rv-pulse 1.6s ease-in-out infinite; }
.review-card.timed-out { border-left-color: #94a3b8; background: #f8fafc; }
@keyframes rv-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.35); }
  50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
}
.review-title { font-weight: 700; color: #92400e; font-size: 12px; margin: 2px 0 6px; }
.review-diff summary { font-size: 11px; color: #b45309; cursor: pointer; }
/* diff 图例（summary 右侧） */
.diff-legend { float: right; font-weight: 600; color: #64748b; }
.diff-legend i {
  display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin: 0 3px 0 8px; vertical-align: -1px;
}
.legend-add { background: #22c55e; }
.legend-del { background: #ef4444; }
/* diff 行级着色：绿色 = 新增，红色 = 删除（Claude Code 风格） */
.review-diff pre.diff-pre {
  margin: 6px 0 0; padding: 6px 0; max-height: 260px; overflow: auto;
  background: #0f172a; color: #94a3b8; border-radius: 6px;
  font: 11px/1.6 ui-monospace, monospace;
}
.review-diff pre.diff-pre > span {
  display: block; padding: 0 10px; white-space: pre-wrap; word-break: break-all;
}
.review-diff pre.diff-pre > span.diff-add { background: rgba(34, 197, 94, 0.18); color: #86efac; }
.review-diff pre.diff-pre > span.diff-del { background: rgba(239, 68, 68, 0.18); color: #fca5a5; }
.review-diff pre.diff-pre > span.diff-hunk { color: #a78bfa; font-weight: 700; }
.review-diff pre.diff-pre > span.diff-file { color: #e2e8f0; font-weight: 700; background: rgba(99, 102, 241, 0.12); }
.review-waiting { font-size: 11px; color: #92400e; margin-top: 4px; }
.review-waiting strong { color: #b45309; }
.review-actions { display: flex; gap: 8px; margin-top: 8px; }
.rv-btn {
  padding: 5px 16px; border: none; border-radius: 6px;
  font: 600 12px system-ui; cursor: pointer;
}
.rv-approve { background: #10b981; color: #fff; }
.rv-approve:hover { background: #059669; }
.rv-reject { background: #fff; color: #dc2626; border: 1px solid #fecaca; }
.rv-reject:hover { background: #fef2f2; }
.review-done { font-size: 12px; font-weight: 700; color: #059669; margin-top: 6px; }
.review-done.rejected { color: #dc2626; }
.review-done.timed { color: #64748b; }
.empty { color: #94a3b8; display: flex; align-items: center; gap: 8px; font-size: 12px; }
/* 思考占位（非流式调用期间） */
.typing-line {
  display: flex; align-items: center; gap: 8px;
  padding: 2px 12px; font-size: 12px; color: #64748b; font-style: italic;
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: #cbd5e1; display: inline-block; }

/* 等待占位：跳动三点 + 文案 */
.thinking-text { font-size: 12px; color: #64748b; }
.thinking-dots { display: inline-flex; gap: 3px; }
.thinking-dots i {
  width: 5px; height: 5px; border-radius: 50%;
  background: #6366f1; display: inline-block;
  animation: thinking-bounce 1.2s ease-in-out infinite;
}
.thinking-dots i:nth-child(2) { animation-delay: 0.15s; }
.thinking-dots i:nth-child(3) { animation-delay: 0.3s; }
@keyframes thinking-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

.msg { margin-bottom: 12px; }
.head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.head.me { flex-direction: row-reverse; }
.avatar { font-size: 15px; }
/* 名字按角色着色（--accent 由 agentLabel(it.agent).color 注入） */
.who { font-weight: 700; color: var(--accent, #334155); font-size: 12px; }
.ts { margin-left: auto; font-size: 10px; color: #94a3b8; }
.head.me .ts { margin-left: 0; margin-right: auto; }

/* 气泡（角色色左边条） */
.bbl { background: #f1f5f9; border: 2px solid #e2e8f0; border-left: 3px solid var(--accent, #cbd5e1); border-radius: 8px; padding: 8px 12px; line-height: 1.55; color: #1e293b; max-width: 88%; }
.bbl.me { background: #eef2ff; border-color: #c7d2fe; margin-left: auto; text-align: right; }
/* 用户消息气泡与文字等宽（收缩到内容宽度，右对齐） */
.msg.answer { text-align: right; }
.msg.answer .bbl.me { display: inline-block; text-align: left; margin-left: auto; }
.bbl :deep(p) { margin: 0 0 6px; }
.bbl :deep(p:last-child) { margin-bottom: 0; }
.bbl :deep(code) { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0 4px; font-size: 12px; }
.bbl.doc { max-height: 200px; overflow: hidden; }
/* 流式输出：纯文本 + 闪烁光标 */
.bbl.streaming { white-space: pre-wrap; word-break: break-word; }
.stream-caret {
  display: inline-block; width: 7px; height: 14px; margin-left: 2px;
  background: #6366f1; vertical-align: -2px;
  animation: caret-blink 0.8s step-end infinite;
}
@keyframes caret-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
/* markdown 排版：LLM 输出的 ### 标题/列表/引用要有主次层次（原样输出没有样式） */
.bbl :deep(h1), .bbl :deep(h2), .bbl :deep(h3) { margin: 10px 0 6px; font-weight: 800; line-height: 1.3; }
.bbl :deep(h1) { font-size: 15px; color: #1e293b; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
.bbl :deep(h2) { font-size: 14px; color: #334155; }
.bbl :deep(h3) { font-size: 13px; color: #475569; }
.bbl :deep(h1:first-child), .bbl :deep(h2:first-child), .bbl :deep(h3:first-child) { margin-top: 0; }
.bbl :deep(ul), .bbl :deep(ol) { margin: 4px 0 6px; padding-left: 20px; }
.bbl :deep(li) { margin-bottom: 2px; }
.bbl :deep(li:last-child) { margin-bottom: 0; }
.bbl :deep(strong) { color: #0f172a; }
.bbl :deep(blockquote) { margin: 6px 0; padding: 2px 10px; border-left: 3px solid #c7d2fe; color: #64748b; background: #f8fafc; border-radius: 0 4px 4px 0; }
.bbl :deep(hr) { border: none; border-top: 1px dashed #cbd5e1; margin: 8px 0; }

/* 结构化文档卡（JSON 内容：主标题 + 分节要点） */
.doc-card { border: 2px solid #e2e8f0; border-left: 3px solid var(--accent, #cbd5e1); border-radius: 8px; padding: 10px 12px; max-width: 92%; background: #fcfdff; }
.design-card { border-left-color: var(--accent, #cbd5e1); }
.doc-head { font-weight: 800; color: #0f172a; font-size: 13px; line-height: 1.5; margin: 4px 0 8px; }
.doc-head::before { content: '◈ '; color: #6366f1; }
.doc-sec { margin-bottom: 8px; }
.doc-sec:last-child { margin-bottom: 0; }
.doc-sec-title { font-weight: 700; color: #4338ca; font-size: 11px; letter-spacing: 0.04em; margin-bottom: 3px; }
.doc-sec-title::before { content: '▸ '; color: #94a3b8; }
.doc-sec-lines { margin: 0; padding-left: 18px; color: #475569; font-size: 12px; line-height: 1.6; }
.doc-sec-lines li { margin-bottom: 2px; word-break: break-all; }

/* work 紧凑行（角色色左边条） */
.work-row { display: flex; align-items: center; gap: 6px; padding: 4px 8px; background: rgba(241, 245, 249, 0.7); border-left: 3px solid var(--accent, #6366f1); border-radius: 4px; font-family: ui-monospace, monospace; font-size: 12px; color: #475569; }
.work-ic { width: 14px; text-align: center; font-weight: 700; }
.work-v { font-weight: 600; color: #334155; }
.work-d { color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.work-who { color: #64748b; font-weight: 600; font-size: 11px; }
.work-lines { background: #ecfdf5; color: #059669; font-weight: 700; border-radius: 4px; padding: 0 5px; font-size: 11px; }
.work-snip { color: #64748b; font-style: italic; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 160px; }
.work-n { background: #eef2ff; color: #6366f1; font-weight: 700; border-radius: 4px; padding: 0 5px; font-size: 11px; }
.work-err { color: #dc2626; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
.work-row.fail { border-left-color: #ef4444; }
.work-row.fail .work-ic { color: #dc2626; }
.work-row .ts { margin-left: auto; }

/* 长文折叠按钮 */
.toggle-btn { display: block; margin-top: 4px; border: none; background: none; color: #6366f1; font: 600 11px system-ui; cursor: pointer; padding: 0; }
.toggle-btn:hover { text-decoration: underline; }

/* 设计卡片 */
.design-card { border: 2px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; max-width: 92%; background: #fcfdff; }
.design-head { font-weight: 800; color: #0f172a; font-size: 13px; letter-spacing: 0.04em; margin: 6px 0 8px; }
.design-head::before { content: '◇ '; color: #6366f1; }
.design-chips { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.chip { padding: 3px 10px; border-radius: 20px; background: #eef2ff; color: #4338ca; font: 600 11px system-ui; border: 1px solid #c7d2fe; }
.design-modules { display: flex; flex-direction: column; gap: 6px; }
.dm { border: 1px solid #e2e8f0; border-radius: 6px; background: #fff; }
.dm summary { display: flex; gap: 8px; align-items: baseline; padding: 6px 10px; cursor: pointer; list-style: none; }
.dm summary::-webkit-details-marker { display: none; }
.dm summary::before { content: '▸'; color: #94a3b8; font-size: 10px; transition: transform 0.15s; }
.dm[open] summary::before { transform: rotate(90deg); }
.dm-name { font-family: ui-monospace, monospace; font-weight: 700; color: #1e293b; font-size: 12px; }
.dm-purpose { color: #64748b; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dm-exports { margin: 0; padding: 4px 10px 8px 26px; }
.dm-exports li { font-size: 11px; color: #475569; margin-bottom: 3px; }
.dm-exports code { background: #f1f5f9; border-radius: 4px; padding: 0 4px; font-size: 11px; color: #334155; }

/* question 卡 */
.q-card { border: 2px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; max-width: 88%; transition: opacity 0.2s; }
.q-card.answered { opacity: 0.55; }
.q-text { font-weight: 600; color: #1e293b; margin-bottom: 8px; line-height: 1.5; }
.q-mode { font-size: 10px; font-weight: 600; color: #4338ca; border: 1px solid #c7d2fe; background: #eef2ff; border-radius: 4px; padding: 0 5px; margin-left: 6px; vertical-align: 1px; }
.q-opts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.q-btn { padding: 5px 12px; background: #fff; border: 2px solid #e2e8f0; border-radius: 20px; font: 12px system-ui; color: #475569; cursor: pointer; transition: all 0.15s; }
.q-btn:hover:not(:disabled) { border-color: #6366f1; color: #6366f1; }
.q-btn.picked { background: #6366f1; border-color: #6366f1; color: #fff; box-shadow: 0 1px 0 rgba(30, 41, 59, 0.25); }
/* 已选中项 hover 保持白字：hover 的蓝色字体不能覆盖选中蓝底（否则看不见字） */
.q-btn.picked:hover:not(:disabled) { color: #fff; border-color: #818cf8; }
.q-btn:disabled { cursor: default; }
.q-custom { margin-bottom: 8px; }
.q-input { width: 100%; box-sizing: border-box; padding: 6px 10px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; font: 12px system-ui; outline: none; color: #1e293b; }
.q-bar { display: flex; align-items: center; gap: 8px; }
.q-confirm { padding: 5px 14px; border: none; border-radius: 6px; background: #10b981; color: #fff; font: 600 12px system-ui; cursor: pointer; }
.q-confirm:hover:not(:disabled) { background: #059669; }
.q-confirm:disabled { opacity: 0.4; cursor: not-allowed; }
.q-done { font-size: 11px; color: #64748b; }

/* milestone / system 居中卡片 */
.milestone { text-align: center; margin: 6px 0; padding: 4px 0; color: #6366f1; font-weight: 700; font-size: 12px; letter-spacing: 0.05em; }
.system { text-align: center; margin: 6px 0; padding: 5px 10px; background: #fef3c7; border: 1px solid #fde68a; border-radius: 6px; color: #92400e; font-size: 12px; font-weight: 600; }
/* 错误卡片：红色警示（运行出错/阶段出错），与普通 warn 区分 */
.system.system-error { background: #fef2f2; border-color: #fecaca; color: #b91c1c; text-align: left; word-break: break-all; }

/* todo 任务清单卡片 */
.todo-card {
  margin: 6px auto; max-width: 70%;
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #94a3b8;
  border-radius: 6px; padding: 6px 12px;
}
.todo-head { font-size: 11px; font-weight: 700; color: #64748b; letter-spacing: 0.04em; }
.todo-list { margin: 4px 0 0; padding: 0; list-style: none; }
.todo-list li { display: flex; gap: 6px; font-size: 12px; color: #475569; padding: 1px 0; }
.todo-mark { width: 12px; text-align: center; flex-shrink: 0; color: #94a3b8; }
.todo-list li.in_progress .todo-mark { color: #f59e0b; }
.todo-list li.completed .todo-mark { color: #10b981; }
.todo-list li.completed .todo-text { color: #94a3b8; text-decoration: line-through; }
.todo-more { font-size: 11px; color: #94a3b8; padding-left: 18px; }

/* 已答问题折叠单行 */
.q-collapsed {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; max-width: 88%;
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid var(--accent, #cbd5e1);
  border-radius: 6px; cursor: pointer; transition: background 0.15s;
}
.q-collapsed:hover { background: #eef2ff; }
.q-collapsed-sum { font-size: 12px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-collapsed-toggle { font-size: 11px; color: #6366f1; font-weight: 600; flex-shrink: 0; }
.q-collapse-btn { border: none; background: none; color: #94a3b8; font: 600 11px system-ui; cursor: pointer; padding: 0; }
.q-collapse-btn:hover { color: #6366f1; }
</style>
