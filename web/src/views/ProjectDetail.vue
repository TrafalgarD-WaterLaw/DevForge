<template>
  <div class="det">
    <router-link to="/history" class="back">&larr; History</router-link>
    <div class="hero" v-if="files"><h1>{{ id }}</h1><p>{{ Object.keys(files).length }} files</p></div>

    <div v-if="error" class="err-box">
      <p>加载失败：{{ error }}</p>
      <button class="retry" @click="load">重试</button>
    </div>

    <div v-if="pipelineEvents.length" class="card">
      <div class="card-hd">Pipeline</div>
      <PipelineFlow :phases="phases" />
    </div>

    <template v-if="files">
      <div v-for="(c, n) in files" :key="n" class="card">
        <div class="fn">{{ n }}</div>
        <pre><code>{{ c }}</code></pre>
      </div>
    </template>
    <div v-if="loading" class="dim">Loading...</div>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PipelineFlow from '../components/PipelineFlow.vue'
import { apiFetch } from '../api'

const route = useRoute()
const id = route.params.id as string
const files = ref<Record<string, string> | null>(null)
const pipelineEvents = ref<any[]>([])   // 事件形状与后端 wire 一致（含多种事件类型）
const loading = ref(true)
const error = ref('')

const phases = computed(() => {
  const m = new Map<string, { name: string; status: string }>()
  for (const e of pipelineEvents.value) {
    if (e.event === 'phase_start') m.set(e.phase, { name: e.phase, status: 'done' })
  }
  return [...m.values()]
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    // apiFetch 带鉴权头（裸 fetch 在启用 token 后 401 静默空白）
    const [r1, r2] = await Promise.all([apiFetch(`/api/projects/${id}`), apiFetch(`/api/projects/${id}/events`)])
    files.value = (await r1.json()).files
    const ed = await r2.json()
    pipelineEvents.value = ed.events || []
  } catch (e) {
    // 加载失败展示错误框 + 重试按钮，避免整页空白
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
<style scoped>
.det{max-width:880px;padding:32px 24px}.back{color:var(--dim);text-decoration:none;font-size:13px}.back:hover{color:var(--accent)}
.hero{margin:16px 0 24px}.hero h1{font-size:24px;font-weight:700;color:var(--text);letter-spacing:-0.4px;word-break:break-all}.hero p{color:var(--dim);margin-top:6px;font-size:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px 24px;margin-bottom:12px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card-hd{font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:16px}
.fn{font-weight:600;color:var(--accent);font-size:13px;margin-bottom:12px;font-family:monospace}
pre{background:var(--bg);padding:14px;border-radius:8px;overflow-x:auto;font-size:12px;line-height:1.6;border:1px solid var(--border)}code{color:var(--text)}.dim{color:var(--dim);font-size:13px}
.err-box{background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:16px 20px;margin-bottom:12px;color:#991b1b;font-size:13px}
.err-box p{margin-bottom:10px}.retry{padding:5px 16px;border:none;border-radius:6px;background:#dc2626;color:#fff;font:600 12px system-ui;cursor:pointer}
.retry:hover{background:#b91c1c}
</style>
