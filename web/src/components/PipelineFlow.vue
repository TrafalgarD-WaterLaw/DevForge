<template>
  <div class="pf">
    <div v-for="(p, i) in phases" :key="p.name" class="step"
      :class="{ active: p.status==='active', done: p.status==='done', error: p.status==='error' }">
      <div class="step-dot">
        <span v-if="p.status==='active'" class="spinner"></span>
        <span v-else-if="p.status==='done'">✓</span>
        <span v-else-if="p.status==='error'">✗</span>
        <span v-else>{{ i+1 }}</span>
      </div>
      <div class="step-info">
        <div class="step-name">{{ label(p.name) }}</div>
        <div class="step-hint" v-if="p.status==='active' && hint">{{ hint }}</div>
        <div class="step-meta" v-if="p.elapsed">{{ p.elapsed }}s</div>
        <div class="step-error" v-if="p.error">{{ p.error.slice(0, 40) }}</div>
      </div>
      <div v-if="i < phases.length-1" class="step-line" :class="{ done: p.status==='done' }"></div>
    </div>

    <div v-if="totalElapsed || totalTokens" class="stats-bar">
      <span v-if="totalElapsed" class="stat">⏱ {{ totalElapsed }}s</span>
      <span v-if="totalTokens" class="stat">🔤 {{ totalTokens.toLocaleString() }} tokens</span>
      <span v-if="totalCalls" class="stat">📞 {{ totalCalls }} calls</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { PHASE_LABELS_CN } from "../phases";

const props = defineProps<{
  phases: { name: string; status: string; agents?: string[]; elapsed?: number; cost?: number; tokens?: number; calls?: number; error?: string }[];
  /** 活跃阶段实时动作提示（如"🧠 正在写 counter 模块…"） */
  hint?: string;
}>();

function label(name: string): string { return PHASE_LABELS_CN[name] || name; }

const totalElapsed = computed(() => {
  const total = props.phases.reduce((s, p) => s + (p.elapsed || 0), 0);
  return total ? total.toFixed(1) : "";
});
const totalTokens = computed(() =>
  props.phases.reduce((s, p) => s + (p.tokens || 0), 0));
const totalCalls = computed(() =>
  props.phases.reduce((s, p) => s + (p.calls || 0), 0));
</script>

<style scoped>
.pf {
  display: flex;
  align-items: flex-start;
  gap: 0;
  flex-wrap: wrap;
}
.step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  position: relative;
}
.step-dot {
  width: 28px; height: 28px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700;
  background: #f3f4f6; color: #9ca3af;
  border: 2px solid #e5e7eb;
  flex-shrink: 0;
  transition: all .3s;
}
.step.active .step-dot { background: #2563eb; border-color: #2563eb; color: #fff; }
.step.done .step-dot { background: #059669; border-color: #059669; color: #fff; }
.step.error .step-dot { background: #ef4444; border-color: #ef4444; color: #fff; }
.spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(255,255,255,.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.step-info { min-width: 0; }
.step-name { font-size: 12px; font-weight: 600; color: #9ca3af; }
.step.active .step-name { color: #2563eb; }
.step.done .step-name { color: #374151; }
.step-meta { font-size: 10px; color: #9ca3af; }
.step-hint { font-size: 10px; color: #2563eb; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-error { font-size: 10px; color: #ef4444; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-line {
  width: 36px; height: 2px;
  background: #e5e7eb;
  margin: 13px 4px 0 4px;
  flex-shrink: 0;
  transition: background .3s;
}
.step-line.done { background: #059669; }
.stats-bar {
  display: flex; gap: 16px; margin-top: 8px; width: 100%;
  font-size: 11px; color: #9ca3af;
}
</style>
