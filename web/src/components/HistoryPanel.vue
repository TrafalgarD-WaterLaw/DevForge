<!-- HistoryPanel.vue — 历史记录抽屉：项目列表 → 质检报告 + 阶段耗时 + 文件 -->
<template>
  <Transition name="drawer">
    <div v-if="open" class="hp-mask" @click.self="$emit('close')">
      <div class="hp-drawer">
        <div class="hp-head">
          <span>📁 运行历史</span>
          <button class="hp-close" @click="$emit('close')">✕</button>
        </div>

        <!-- 列表 -->
        <div v-if="!loading && !error && !projects.length" class="hp-empty">
          还没有运行记录 — 开始一个任务试试
        </div>
        <div v-if="error" class="hp-error">{{ error }}</div>
        <div v-if="loading" class="hp-loading">加载中…</div>

        <!-- 记忆库概览（E1）：条数 + 最近条目 + 清空 -->
        <div class="hp-memory">
          <div class="hp-memory-head" @click="memOpen = !memOpen">
            <span>🧠 记忆库
              <em v-if="mem">阶段 {{ mem?.phases?.count ?? 0 }} · 函数 {{ mem?.functions?.count ?? 0 }}</em>
            </span>
            <span class="hp-memory-toggle">{{ memOpen ? '收起 ▴' : '展开 ▾' }}</span>
          </div>
          <div v-if="memOpen" class="hp-memory-body">
            <div v-if="!mem" class="hp-loading">加载记忆…</div>
            <template v-else>
              <div v-if="!(mem?.phases?.count) && !(mem?.functions?.count)" class="hp-empty">
                记忆库为空 — 跑过任务后这里会有跨项目经验
              </div>
              <template v-else>
                <div class="hp-sec-title">最近记忆</div>
                <div
                  v-for="e in [...(mem?.phases?.recent ?? []), ...(mem?.functions?.recent ?? [])].slice(0, 8)"
                  :key="e.id" class="hp-mem-item" :title="e.id"
                >
                  <span class="hp-mem-phase" :class="e.phase">{{ e.phase }}</span>
                  <span class="hp-mem-sum">{{ e.summary }}</span>
                </div>
                <button class="hp-mem-clear" @click="clearMemory()">
                  {{ memClearing ? '清空中…' : '🗑 清空记忆库（不可恢复）' }}
                </button>
              </template>
            </template>
          </div>
        </div>

        <div class="hp-list">
          <div
            v-for="p in projects" :key="p.id"
            class="hp-item" :class="{ open: selected?.id === p.id }"
            @click="toggle(p)"
          >
            <div class="hp-item-row">
              <span class="hp-status" :class="p.status">{{ statusLabel(p.status) }}</span>
              <span class="hp-task">{{ p.task || p.id }}</span>
              <span v-if="p.commit" class="hp-commit" :title="`git commit ${p.commit}`">⎇ {{ p.commit }}</span>
              <span class="hp-time">{{ fmtTime(p.updated) }}</span>
            </div>

            <!-- 详情：质检报告 + 阶段 + 文件 -->
            <div v-if="selected?.id === p.id" class="hp-detail">
              <div v-if="detailLoading" class="hp-loading">加载详情…</div>
              <template v-else-if="detail">
                <!-- 质检报告 -->
                <div class="hp-sec">
                  <div class="hp-sec-title">质检报告</div>
                  <div v-if="detail.quality" class="hp-qg" :class="detail.quality.verdict">
                    <div class="hp-qg-verdict">
                      {{ detail.quality.verdict === 'PASS' ? '✅ 质检通过' : '❌ 质检未通过' }}
                      <span v-if="detail.quality.verdict && detail.quality.verdict !== 'PASS'">
                        （{{ detail.quality.verdict }}）
                      </span>
                    </div>
                    <ul v-if="detail.quality.missing?.length" class="hp-qg-missing">
                      <li v-for="m in detail.quality.missing" :key="m.name">
                        [{{ m.status }}] {{ m.name }} — {{ m.notes || '未达标' }}
                      </li>
                    </ul>
                    <div v-else-if="detail.quality.verdict !== 'PASS'" class="hp-qg-none">
                      质检结论已生成（详见事件流）
                    </div>
                  </div>
                  <div v-else class="hp-muted">无质检记录</div>
                </div>

                <!-- 阶段耗时 + B3 成本明细（token/调用数，来自 phase_end 事件） -->
                <div class="hp-sec">
                  <div class="hp-sec-title">阶段耗时 / 成本</div>
                  <div class="hp-phases">
                    <span
                      v-for="ph in detail.phases" :key="ph.name"
                      class="hp-phase" :class="ph.status"
                    >
                      {{ labelPhase(ph.name) }}
                      <em v-if="ph.elapsed">{{ ph.elapsed }}s</em>
                      <em v-if="ph.tokens" class="hp-tokens" :title="`${ph.calls ?? 0} 次 LLM 调用`">
                        {{ (ph.tokens / 1000).toFixed(1) }}k tok
                      </em>
                    </span>
                  </div>
                </div>

                <!-- 文件（点击查看内容） -->
                <div class="hp-sec">
                  <div class="hp-sec-title">生成文件（{{ detail.files.length }}）</div>
                  <div class="hp-files">
                    <span
                      v-for="f in detail.files" :key="f"
                      class="hp-file" :class="{ active: openedFile === f }"
                      @click.stop="openFile(p.id, f)"
                    >{{ f }}</span>
                  </div>
                  <pre v-if="fileContent !== null" class="hp-file-view">{{ fileContent }}</pre>
                </div>

                <!-- A2 增量迭代：对已交付项目提出修改意见 -->
                <div class="hp-sec">
                  <div class="hp-sec-title">迭代这个项目</div>
                  <div class="hp-iterate">
                    <input
                      v-model="iterateText[p.id]"
                      class="hp-iterate-input"
                      placeholder="修改意见，如：给报表加一个 CSV 导出功能"
                      @keydown.enter="startIterate(p)"
                    />
                    <button class="hp-iterate-btn" @click="startIterate(p)">🔄 迭代</button>
                  </div>
                  <div v-if="iterateStatus[p.id]" class="hp-iterate-status">{{ iterateStatus[p.id] }}</div>
                </div>

                <!-- D4 运行此项目 -->
                <div class="hp-sec">
                  <div class="hp-sec-title">运行</div>
                  <button class="hp-run-btn" :disabled="!!runningProject" @click="runProject(p)">
                    {{ runningProject === p.id ? '运行中…' : '▶ 运行此项目' }}
                  </button>
                  <pre v-if="runOutput !== null" class="hp-file-view">{{ runOutput }}</pre>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { apiFetch } from '../api'
import { PHASE_LABELS_CN } from '../phases'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  close: []
  /** A2 增量迭代：历史项目上发起修改请求（Dashboard 接住并启动 run） */
  iterate: [payload: { projectId: string; feedback: string }]
}>()

interface Project {
  id: string
  name: string
  task: string
  status: string        // done | defect | interrupted
  files: string[]
  commit?: string       // 生成物 git commit（每轮运行一个版本）
  updated: number
}
interface QualityInfo { verdict: string; missing: { name: string; status: string; notes?: string }[] }
interface ProjectDetail {
  quality: QualityInfo | null
  phases: { name: string; status: string; elapsed?: number; tokens?: number; calls?: number }[]
  files: string[]
  filesMap?: Record<string, string>   // 文件名 → 内容（产物查看器）
}

const projects = ref<Project[]>([])
const selected = ref<Project | null>(null)
const detail = ref<ProjectDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const error = ref('')
// 产物查看器：当前打开的文件（null = 关闭）
const openedFile = ref<string | null>(null)
const fileContent = ref<string | null>(null)

function openFile(projectId: string, file: string) {
  if (openedFile.value === file) { openedFile.value = null; fileContent.value = null; return }
  openedFile.value = file
  const map = detail.value?.filesMap
  fileContent.value = map?.[file] ?? '（文件内容不可用）'
}

watch(() => props.open, (v) => {
  if (!v) return
  loading.value = true; error.value = ''
  apiFetch('/api/projects')
    .then((r) => r.json())
    .then((d) => { projects.value = d.projects ?? [] })
    .catch(() => { error.value = '加载失败 — 后端未连接？' })
    .finally(() => { loading.value = false })
  loadMemory()   // E1: 打开抽屉时同步加载记忆概览
}, { immediate: true })

/** 点开/收起项目详情；详情从 run_events.json 提取质检与阶段耗时 */
// 请求竞态防护：快速切换项目时旧请求后到会覆盖新项目详情（显示错位）
let detailRequestSeq = 0
function toggle(p: Project) {
  if (selected.value?.id === p.id) { selected.value = null; return }
  selected.value = p
  detail.value = null
  detailLoading.value = true
  const mySeq = ++detailRequestSeq
  const loadJson = (url: string): Promise<any> =>
    apiFetch(url).then((r) => r.json()).catch((): null => null)
  Promise.all([
    loadJson(`/api/projects/${p.id}/events`),
    loadJson(`/api/projects/${p.id}`),
  ]).then(([evData, projData]) => {
    if (mySeq !== detailRequestSeq) return   // 已切换到别的项目 — 丢弃过期响应
    const events: any[] = Array.isArray(evData) ? evData
      : (evData?.events ?? [])
    // 质检：最后一条 quality_gate 事件
    const qg = [...events].reverse().find((e) => e?.event === 'quality_gate')
    const missing: QualityInfo['missing'] = []
    if (qg?.data) {
      for (const f of qg.data.features ?? []) {
        if (f?.status === 'NO' || f?.status === 'PARTIAL') {
          missing.push({ name: String(f.name ?? '?'), status: String(f.status), notes: String(f.notes ?? '') })
        }
      }
    }
    // 阶段耗时：phase_start/phase_end 配对（同阶段多次取最后一次）
    // B3: phase_end 同时带 tokens/calls —— 成本明细直接展示
    const elapsedMap = new Map<string, number>()
    const tokenMap = new Map<string, { tokens: number; calls: number }>()
    const starts = new Map<string, number>()
    for (const e of events) {
      if (e?.event === 'phase_start' && e.phase) starts.set(e.phase, e.timestamp ?? 0)
      else if (e?.event === 'phase_end' && e.phase) {
        const s = starts.get(e.phase)
        if (s) elapsedMap.set(e.phase, Math.max(elapsedMap.get(e.phase) ?? 0, Math.round((e.timestamp ?? s) - s)))
        if (e.tokens?.prompt_tokens) {
          const prev = tokenMap.get(e.phase) ?? { tokens: 0, calls: 0 }
          tokenMap.set(e.phase, {
            tokens: Math.max(prev.tokens, e.tokens.prompt_tokens),
            calls: Math.max(prev.calls, e.tokens.calls ?? 0),
          })
        }
      }
    }
    // /api/projects/{id} 的 files 是 dict（文件名 → 内容）；列表接口是数组
    const filesMap = (projData?.files && typeof projData.files === 'object'
      && !Array.isArray(projData.files)) ? projData.files : {}
    detail.value = {
      quality: qg?.data ? { verdict: String(qg.data.verdict ?? 'WARN'), missing } : null,
      phases: Array.from(elapsedMap, ([name, elapsed]) => {
        const t = tokenMap.get(name)
        return { name, status: 'done', elapsed, tokens: t?.tokens, calls: t?.calls }
      }),
      files: Object.keys(filesMap).length ? Object.keys(filesMap) : (p.files ?? []),
      filesMap,
    }
    openedFile.value = null
    fileContent.value = null
    detailLoading.value = false
  }).catch(() => { if (mySeq === detailRequestSeq) detailLoading.value = false })
}

// ── A2 增量迭代 / D4 运行项目 ──────────────────────
const iterateText = reactive<Record<string, string>>({})
const iterateStatus = reactive<Record<string, string>>({})
const runningProject = ref('')
const runOutput = ref<string | null>(null)

function startIterate(p: Project) {
  const text = (iterateText[p.id] ?? '').trim()
  if (!text) { iterateStatus[p.id] = '请输入修改意见'; return }
  iterateStatus[p.id] = '已发起迭代 — 主界面查看进度'
  emit('iterate', { projectId: p.id, feedback: text })
  iterateText[p.id] = ''
}

async function runProject(p: Project) {
  if (runningProject.value) return
  runningProject.value = p.id
  runOutput.value = '运行中…'
  try {
    const r = await apiFetch(`/api/projects/${p.id}/run`, { method: 'POST' })
    const d = await r.json()
    runOutput.value = d.output ?? d.error ?? '（无输出）'
  } catch {
    runOutput.value = '运行失败 — 后端未连接？'
  } finally {
    runningProject.value = ''
  }
}

// ── 记忆库概览（E1）──────────────────────────────
interface MemoryOverview {
  location?: string
  phases: { count: number; recent: any[] }
  functions: { count: number; recent: any[] }
}
const mem = ref<MemoryOverview | null>(null)
const memOpen = ref(false)
const memClearing = ref(false)

function loadMemory() {
  apiFetch('/api/memory').then((r) => r.json())
    .then((d) => { mem.value = d })
    .catch(() => { mem.value = null })
}
async function clearMemory() {
  if (!window.confirm('确定清空记忆库？不可恢复，会删除所有已积累的跨项目经验。')) return
  memClearing.value = true
  try {
    await apiFetch('/api/memory/clear', { method: 'POST' })
    loadMemory()
  } finally { memClearing.value = false }
}

function statusLabel(s: string): string {
  return { done: '✅ 完成', defect: '⚠️ 有缺陷', interrupted: '⏸ 未完成' }[s] ?? s
}
function labelPhase(n: string): string { return PHASE_LABELS_CN[n] || n }
function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<style scoped>
.hp-mask {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(15, 23, 42, 0.35);
  display: flex; justify-content: flex-end;
}
.hp-drawer {
  width: min(480px, 92vw); height: 100%;
  background: #fff; box-shadow: -8px 0 24px rgba(15, 23, 42, 0.18);
  display: flex; flex-direction: column;
}
.hp-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid #e2e8f0;
  font-weight: 800; font-size: 14px; color: #1e293b;
}
.hp-close { border: none; background: none; font-size: 14px; color: #94a3b8; cursor: pointer; }
.hp-close:hover { color: #334155; }
.hp-empty, .hp-error { padding: 24px 18px; font-size: 13px; color: #94a3b8; }
.hp-error { color: #b91c1c; }
.hp-loading { padding: 12px 18px; font-size: 12px; color: #94a3b8; }
.hp-list { flex: 1; overflow-y: auto; padding: 8px 0; }
/* 记忆库概览 */
.hp-memory { border-top: 1px solid #e2e8f0; background: #fafbff; }
.hp-memory-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 18px; cursor: pointer; font-weight: 700; font-size: 13px; color: #4338ca;
}
.hp-memory-head em { font-style: normal; font-size: 11px; color: #94a3b8; font-weight: 600; margin-left: 6px; }
.hp-memory-toggle { font-size: 11px; color: #94a3b8; font-weight: 600; }
.hp-memory-body { padding: 0 18px 14px; }
.hp-mem-item {
  display: flex; align-items: baseline; gap: 8px;
  padding: 4px 0; font-size: 12px;
}
.hp-mem-phase {
  flex-shrink: 0; font-size: 10px; font-weight: 700;
  padding: 1px 6px; border-radius: 4px; color: #6366f1; background: #eef2ff;
}
.hp-mem-phase.Function { color: #059669; background: #ecfdf5; }
.hp-mem-sum {
  color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.hp-mem-clear {
  margin-top: 10px; width: 100%; padding: 7px 0;
  border: 1px solid #fecaca; border-radius: 6px;
  background: #fff; color: #dc2626; font: 600 12px system-ui; cursor: pointer;
}
.hp-mem-clear:hover { background: #fef2f2; }
/* A2 迭代 / D4 运行 */
.hp-iterate { display: flex; gap: 6px; }
.hp-iterate-input {
  flex: 1; padding: 6px 10px; border: 1px solid #e2e8f0; border-radius: 6px;
  font: 12px system-ui; outline: none; color: #1e293b;
}
.hp-iterate-input:focus { border-color: #6366f1; }
.hp-iterate-btn {
  padding: 6px 12px; border: none; border-radius: 6px;
  background: #6366f1; color: #fff; font: 600 12px system-ui; cursor: pointer;
}
.hp-iterate-btn:hover { background: #4f46e5; }
.hp-iterate-status { font-size: 11px; color: #059669; margin-top: 4px; }
.hp-run-btn {
  padding: 6px 14px; border: 1px solid #c7d2fe; border-radius: 6px;
  background: #eef2ff; color: #4338ca; font: 600 12px system-ui; cursor: pointer;
}
.hp-run-btn:hover:not(:disabled) { background: #e0e7ff; }
.hp-run-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.hp-item { border-bottom: 1px solid #f1f5f9; cursor: pointer; }
.hp-item:hover { background: #f8fafc; }
.hp-item-row { display: flex; align-items: center; gap: 8px; padding: 10px 18px; }
.hp-status { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 20px; flex-shrink: 0; }
.hp-status.done { color: #059669; background: #ecfdf5; }
.hp-status.defect { color: #d97706; background: #fffbeb; }
.hp-status.interrupted { color: #64748b; background: #f1f5f9; }
.hp-task { font-size: 13px; color: #334155; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hp-time { font-size: 11px; color: #94a3b8; flex-shrink: 0; }
/* B3 成本明细 */
.hp-tokens { color: #d97706; font-weight: 700; }
.hp-commit {
  font-family: ui-monospace, monospace; font-size: 10px;
  color: #6d28d9; background: #f5f3ff; border: 1px solid #ddd6fe;
  border-radius: 4px; padding: 1px 5px; flex-shrink: 0;
}
.hp-detail { padding: 0 18px 14px; }
.hp-sec { margin-bottom: 12px; }
.hp-sec-title { font-size: 11px; font-weight: 700; color: #64748b; letter-spacing: 0.05em; margin-bottom: 6px; }
.hp-qg { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 12px; }
.hp-qg.PASS { background: #ecfdf5; border-color: #a7f3d0; }
.hp-qg-verdict { font-size: 13px; font-weight: 800; color: #b91c1c; }
.hp-qg.PASS .hp-qg-verdict { color: #059669; }
.hp-qg-missing { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: #92400e; line-height: 1.6; }
.hp-qg-none { font-size: 12px; color: #92400e; margin-top: 4px; }
.hp-muted { font-size: 12px; color: #94a3b8; }
.hp-phases { display: flex; flex-wrap: wrap; gap: 6px; }
.hp-phase {
  font-size: 11px; padding: 3px 10px; border-radius: 20px;
  background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0;
}
.hp-phase.done { background: #ecfdf5; border-color: #a7f3d0; color: #047857; }
.hp-phase em { font-style: normal; font-weight: 700; margin-left: 4px; }
.hp-files { display: flex; flex-wrap: wrap; gap: 4px; }
.hp-file {
  font-family: ui-monospace, monospace; font-size: 11px;
  background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px;
  padding: 2px 6px; color: #475569; cursor: pointer;
}
.hp-file:hover { border-color: #6366f1; color: #4338ca; }
.hp-file.active { background: #eef2ff; border-color: #6366f1; color: #4338ca; }
.hp-file-view {
  margin: 8px 0 0; padding: 10px; max-height: 260px; overflow: auto;
  background: #0f172a; color: #e2e8f0; border-radius: 6px;
  font: 11px/1.5 ui-monospace, monospace; white-space: pre-wrap;
}

.drawer-enter-active, .drawer-leave-active { transition: opacity 0.2s; }
.drawer-enter-active .hp-drawer, .drawer-leave-active .hp-drawer { transition: transform 0.25s ease; }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-from .hp-drawer { transform: translateX(100%); }
.drawer-leave-to .hp-drawer { transform: translateX(100%); }
</style>
