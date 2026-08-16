<template>
  <div class="app">
    <!-- Input area (before pipeline starts) -->
    <div v-if="!pipelineStarted" class="hero">
      <!-- 后端未连接提示 -->
      <div v-if="backendDown" class="backend-down">
        ⚠️ 后端未连接 — 请先启动 <code>python -m devforge.server.app</code>（端口 8000），然后刷新页面
      </div>
      <!-- Resume prompt -->
      <div v-if="resumeCheckpoint" class="resume-banner">
        上次运行到 <strong>{{ resumeCheckpoint.phase }}</strong>（{{ resumeCheckpoint.task?.slice(0, 60) }}...）
        <button class="btn sm ok" @click="resumeRun">继续运行</button>
        <button class="btn sm outline" @click="resumeCheckpoint = null">重新开始</button>
      </div>
      <textarea v-model="taskPrompt"
        placeholder="描述你的想法，比如：一个记账命令行工具，支持收支记录和月度统计..."
        rows="3" class="hero-input"
        @keydown.ctrl.enter="runPipeline()" />
      <!-- D3 新手引导：示例任务一键填入 -->
      <div class="hero-examples">
        <span class="hero-examples-label">试试：</span>
        <button
          v-for="ex in EXAMPLE_TASKS" :key="ex.name"
          class="btn sm outline" @click="taskPrompt = ex.prompt"
        >{{ ex.icon }} {{ ex.name }}</button>
      </div>
      <!-- 任务模板（框架型）：选类型 → 填关键字段 → 组装任务描述 -->
      <div class="hero-templates">
        <button
          v-for="t in TASK_TEMPLATES" :key="t.id"
          class="btn sm outline" :class="{ active: activeTemplate?.id === t.id }"
          @click="toggleTemplate(t)"
        >{{ t.icon }} {{ t.label }}</button>
      </div>
      <div v-if="activeTemplate" class="template-form">
        <div class="tf-intro">{{ activeTemplate.intro }} — 只填关键项即可</div>
        <div class="tf-fields">
          <label v-for="f in activeTemplate.fields" :key="f.key" class="tf-field">
            <span class="tf-label">{{ f.label }}</span>
            <!-- 默认值作占位：聚焦输入即消失；留空时预览用默认兜底 -->
            <input v-model="templateValues[f.key]" class="tf-input" :placeholder="f.default || f.placeholder" />
          </label>
        </div>
        <div class="tf-preview">
          <div class="tf-preview-title">任务描述预览</div>
          <div class="tf-preview-text">{{ templatePreview }}</div>
        </div>
        <div class="tf-actions">
          <button class="btn primary sm" @click="applyTemplate">✓ 使用此模板</button>
          <button class="btn sm outline" @click="activeTemplate = null">取消</button>
        </div>
      </div>
      <div class="hero-bar">
        <button class="btn primary" @click="runPipeline()"
          :disabled="running || !taskPrompt.trim()">
          {{ running ? "运行中..." : "开始生成" }}
        </button>
        <button class="btn outline" @click="previewHall()">🎮 预览大厅</button>
        <span class="hint">Ctrl+Enter 快速发送</span>
      </div>
    </div>

    <!-- Pipeline area (after start) -->
    <div v-if="pipelineStarted" class="workspace">
      <!-- Top bar: task + status -->
      <div class="topbar">
        <div class="task-title">{{ taskPrompt.slice(0, 100) }}{{ taskPrompt.length > 100 ? '...' : '' }}</div>
        <div class="topbar-right">
          <button class="btn sm outline" @click="historyOpen = true">📁 历史</button>
          <span v-if="runStatus" class="status-tag" :class="statusClass">{{ runStatus }}</span>
          <div v-if="!running" class="rerun-group">
            <button class="btn sm" @click="runPipeline()">全部重跑</button>
            <select v-if="completedPhases.length" v-model="rerunPhase" class="rerun-select">
              <option value="">重跑某个阶段...</option>
              <option v-for="p in rerunOptions" :key="p" :value="p">从 {{ labelPhase(p) }} 开始</option>
            </select>
            <button v-if="rerunPhase" class="btn sm outline" @click="runPipeline(rerunPhase)">重跑</button>
          </div>
        </div>
      </div>

      <!-- Pipeline stepper -->
      <div class="pipe-area">
        <PipelineFlow :phases="phaseList" :hint="activeHint" />
      </div>

      <!-- Main 2-column -->
      <div class="main-area">
        <!-- Chat -->
        <div class="chat-panel">
          <div class="panel-label">Agent 对话</div>
          <div class="chat-scroll">
            <ConversationPanel
              :items="feed.items"
              :placeholder="waitingHint"
              :interactive="running"
              @confirm="onConfirmAnswer"
              @user-message="onUserMessage"
              @review-decision="onReviewDecision"
            />
          </div>
        </div>

        <!-- Activity -->
        <div class="act-panel">
          <div class="panel-label">活动</div>
          <div class="act-scroll">
            <PixelOffice ref="officeRef" />
          </div>
        </div>
      </div>
    </div>

    <!-- 历史记录抽屉 -->
    <HistoryPanel :open="historyOpen" @close="historyOpen = false" @iterate="onIterate" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onUnmounted, nextTick } from "vue";
import { apiFetch, wsUrl } from "../api";
import PipelineFlow from "../components/PipelineFlow.vue";
import ConversationPanel from "../components/ConversationPanel.vue";
import HistoryPanel from "../components/HistoryPanel.vue";
import { createFeed, type FeedItem } from "../components/feed";
import PixelOffice, { type PixelOfficeApi } from "../components/PixelOffice.vue";
import { agentLabel, TOOL_VERBS } from "../components/spriteDefs";
import { PHASES, PHASE_LABELS_CN } from "../phases";
import { TASK_TEMPLATES, type TaskTemplate } from "../templates";

const pipelineStarted = ref(false),
  taskPrompt = ref(""),
  runStatus = ref(""),
  running = ref(false);

// D3 新手引导示例任务（只提供 Python 可交付的类型）
const EXAMPLE_TASKS = [
  { name: '记账 CLI', icon: '💰', prompt: '设计一个记账命令行工具记账本：支持添加收入/支出记录（日期、类别、金额、备注）、查看记录列表、按类别统计汇总。' },
  { name: '字数统计', icon: '🔤', prompt: '设计一个命令行字数统计工具：输入一个或多个文本文件，统计每个文件的行数、单词数、字符数（忽略空白字符），支持 -l/-w/-c 选项与帮助信息。' },
  { name: '数据处理', icon: '📊', prompt: '开发一个 CSV 数据分析脚本：读取学生成绩 CSV 文件，计算每科平均分、最高分、最低分、不及格人数，并生成汇总报告。' },
];

// 中文状态 → CSS class 映射（避免中文类名转义问题）
const statusClass = computed(() => {
  const m: Record<string, string> = {
    "运行中": "running", "运行中（重跑）": "running",
    "完成": "done", "完成(有缺陷)": "defect",
    "失败": "failed", "启动失败": "startfail", "错误": "error",
  };
  return m[runStatus.value] || "";
});
const phases = ref<{ name: string; status: string; agents?: string[]; elapsed?: number; tokens?: number; calls?: number }[]>([]);
// 注入 Vue 响应式宿主数组：feed 原地 push/修改会触发 ConversationPanel 重渲染
const feed = createFeed({ items: reactive<FeedItem[]>([]) });
// 断线时未送达的回答，重连后自动重发
let pendingAnswer: { id: number; selected: string[]; custom: string } | null = null;
const officeRef = ref<PixelOfficeApi | null>(null);
const rerunPhase = ref("");
const completedPhases = ref<string[]>([]);
const historyOpen = ref(false);          // 历史记录抽屉
// 任务模板（框架型）：选类型 → 填关键字段 → 组装任务描述
const activeTemplate = ref<TaskTemplate | null>(null)
const templateValues = reactive<Record<string, string>>({})
const templatePreview = computed(() => {
  if (!activeTemplate.value) return ''
  return activeTemplate.value.build({ ...templateValues })
})
function toggleTemplate(t: TaskTemplate) {
  if (activeTemplate.value?.id === t.id) { activeTemplate.value = null; return }
  activeTemplate.value = t
  // 输入框初始为空（默认值只作 placeholder 提示，不预填实际值）
  for (const key of Object.keys(templateValues)) delete templateValues[key]
  for (const f of t.fields) templateValues[f.key] = ''
}
function applyTemplate() {
  if (!activeTemplate.value) return
  taskPrompt.value = templatePreview.value
  activeTemplate.value = null
}
// 活跃阶段实时动作提示（阶段条）；空对话时同时用作"思考中"占位
const activeHint = ref("");
const waitingHint = computed(() =>
  running.value && !feed.items.length && !activeHint.value
    ? "产品经理正在梳理需求…" : "");
// 断线恢复检查点（/api/checkpoint/latest 返回的最小形状）
interface CheckpointInfo {
  phase: string
  task?: string
}
const resumeCheckpoint = ref<CheckpointInfo | null>(null);
// 后端不可达（未启动/端口占用）→ 顶部提示
const backendDown = ref(false);
let ws: WebSocket | null = null,
  reconnectTimer: number | null = null;
let disposed = false;          // 组件已卸载：不再重连、不再处理 onclose
let lastSeq = -1;              // 重放去重：已消费的最大事件 seq（-1 = 未启动）
let questionSeq: number | undefined;   // 当前 PM 问题的 qseq（回显给后端防迟到回答）

// Check for checkpoint on load
(async () => {
  try {
    const r = await apiFetch("/api/checkpoint/latest");
    const d = await r.json();
    if (d.checkpoint?.phase) resumeCheckpoint.value = d.checkpoint;
  } catch {
    backendDown.value = true;   // 后端未连接：显示提示而非静默
  }
})();

// 阶段面板路由表（后端 phases.json 单一来源）：新增审查 lens 后前端自动跟随
(async () => {
  try {
    const r = await apiFetch("/api/config");
    const d = await r.json();
    if (d?.stage_phases) feed.setStagePhases(d.stage_phases);
  } catch { /* 后端未起/接口缺失 → 用 feed 内置默认路由 */ }
})();

const phaseList = computed(() => phases.value);
const rerunOptions = computed(() => {
  const lastDone = [...completedPhases.value].pop();
  if (!lastDone) return [];
  const idx = ALL_PHASES.indexOf(lastDone);
  return idx >= 0 ? ALL_PHASES.slice(1, idx + 2) : [];
});

function labelPhase(name: string) { return PHASE_LABELS_CN[name] || name; }

// 后端事件的最小形状（onEvent 按需读取的字段；其余字段原样透传给 feed/办公室）
interface PipelineEvent {
  event?: string
  seq?: number
  phase?: string
  phases?: string[]
  agents?: string[]
  elapsed?: number
  error?: string
  failed?: boolean
  tokens?: { prompt_tokens?: number; calls?: number }
}

function onEvent(e: Record<string, unknown>) {
  const ev = e as PipelineEvent;
  // 重放去重：seq 单调递增，<= 已消费的旧事件直接丢弃（替代 feed.reset）
  // （discuss_choice 等 live 事件无 seq → 永不丢弃，用户回答不受影响）
  if (ev.seq !== undefined && ev.seq <= lastSeq) return;
  if (ev.seq !== undefined) lastSeq = ev.seq;
  switch (ev.event) {
    case "pipeline_start":
      // Replace pre-populated phases with the actual list from backend
      // (important if the config uses a custom pipeline order)
      if (ev.phases?.length) {
        phases.value = ev.phases.map((name: string) => {
          const existing = phases.value.find((p) => p.name === name);
          return existing || { name, status: "pending", agents: [] };
        });
        // 仅真实运行中标记首阶段 active：重放已完成 run 的历史事件时
        // 不得让第一阶段闪烁为"进行中"
        if (running.value && !phases.value.some((p) => p.status === "active")) {
          phases.value[0].status = "active";
        }
      }
      break;
    case "phase_start": {
      const exist = phases.value.find((x) => x.name === ev.phase);
      if (exist) { exist.status = "active"; }
      else { phases.value = [...phases.value, { name: ev.phase, status: "active", agents: [] }]; }
      activeHint.value = `${PHASE_LABELS_CN[ev.phase] ?? ev.phase} 阶段开始`;
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
    }
    case "phase_end": {
      const p = phases.value.find((x) => x.name === ev.phase);
      if (p) {
        p.status = "done"; p.agents = ev.agents || []; p.elapsed = ev.elapsed;
        p.tokens = ev.tokens?.prompt_tokens ?? 0;
        p.calls = ev.tokens?.calls ?? 0;
      }
      if (!completedPhases.value.includes(ev.phase)) completedPhases.value.push(ev.phase);
      activeHint.value = "";   // 阶段结束 → 阶段条不再残留"🧠 X 正在…"
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
    }
    case "phase_error":
      phases.value = phases.value.map((p) => p.name === ev.phase
        ? { ...p, status: "error", error: ev.error, elapsed: ev.elapsed } : p);
      activeHint.value = "";
      feed.addEvent(e);            // 错误卡片进对话流（此前只改状态徽章，看不到原因）
      officeRef.value?.onEvent(e);
      break;
    case "phase_retry": {
      // QualityGate FAIL → jump back to Verification for rework；
      // 运行中追加反馈回退 Iterate —— Iterate 不在标准列表（indexOf=-1），
      // 硬编码 ALL_PHASES 重建会把阶段条全部重置、目标阶段消失
      const idx = ALL_PHASES.indexOf(ev.phase);
      if (idx === -1) {
        // 自定义阶段（Iterate 等）：以现有列表为基底，目标标 active、
        // 其后阶段置 pending；不在列表则追加
        const pi = phases.value.findIndex((p) => p.name === ev.phase);
        phases.value = phases.value.map((p, i) => ({
          ...p,   // 保留 elapsed/tokens/calls/error 统计（重建会丢）
          status: pi === i ? "active"
            : (pi >= 0 && i > pi) ? "pending"
            : (p.status === "done" ? "done" : "pending"),
        }));
        if (pi === -1) {
          phases.value = [...phases.value, { name: ev.phase, status: "active", agents: [] }];
        }
      } else {
        phases.value = ALL_PHASES.map((name, i) => {
          const existing = phases.value.find((p) => p.name === name);
          return {
            ...existing,   // 保留已得阶段的统计（elapsed/tokens/calls/error）
            name,
            status: i > idx ? "pending" : (existing?.status === "done" ? "done" : (i === idx ? "active" : "pending")),
            agents: existing?.agents || [],
          };
        });
      }
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
    }
    case "conversation_turn": {
      const who = agentLabel(String(e.agent ?? "")).name;
      activeHint.value = `💬 ${who} 发言中`;
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
    }
    case "discuss_choice":
      pendingAnswer = null;   // 后端已进入新问题：上一题的未送达回答作废，不得回答新问题
      questionSeq = e.qseq as number | undefined;   // 回显给后端校验（防迟到旧回答）
      feed.addEvent(e);
      feed.clearSending();   // 新问题到达 → 上一题的"发送中…"结束
      break;
    case "pipeline_complete":
      running.value = false;
      runStatus.value = ev.failed ? "完成(有缺陷)" : "完成";
      activeHint.value = "";
      feed.clearSending();   // 流程结束 → 上一题的"发送中…"结束
      feed.addEvent(e);
      officeRef.value?.onEvent(e);   // 竣工典礼
      break;
    case "error": {
      feed.clearSending();   // 运行出错 → 上一题的"发送中…"结束，避免永久卡住
      pendingAnswer = null;
      activeHint.value = "";
      running.value = false; runStatus.value = "失败";
      feed.addEvent(e);      // 错误卡片进对话流
      break;
    }
    case "tool_pre_use": {
      const who = agentLabel(String(e.agent ?? "")).name;
      const verb = TOOL_VERBS[String(e.tool ?? "")] || String(e.tool ?? "工作中");
      activeHint.value = `🧠 ${who} 正在${verb}`;
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
    }
    case "review_request": {
      // 人工审阅：pipeline 正阻塞等待用户决策 — 状态条明确提示（此前
      // 事件没接线，审阅卡从不出现，用户只看到"卡住"）
      activeHint.value = "⏸ 等待你审阅修复 — 对话区点「通过 / 拒绝」";
      feed.addEvent(e);
      break;
    }
    case "review_timed_out":
      activeHint.value = "";
      feed.addEvent(e);
      break;
    case "agent_typing":
    case "integration_start":
      feed.addEvent(e); break;   // 思考占位 / 整合子面板：只进对话区
    case "tool_post_use":
    case "todo_update": case "design_submitted": case "requirements_submitted":
    case "review_submitted": case "quality_gate": case "coding_progress":
    case "agent_done": case "review_discarded": case "review_round":
      feed.addEvent(e);
      officeRef.value?.onEvent(e); break;
    case "llm_delta": case "llm_stream_end":
      feed.addEvent(e); break;   // 流式输出：只进对话区
    case "token_warning":
      feed.addEvent(e); break;   // 预算黄卡（feed 渲染，后端 token_budget 触发）
    default:
      // 未接线事件兜底转发：feed/办公室对未知事件各自 no-op ——
      // 新增后端事件不会因 switch 漏分支被静默丢弃
      feed.addEvent(e);
      officeRef.value?.onEvent(e);
      break;
  }
}

let reconnectTries = 0;
function connectWS(rid: string) {
  if (disposed) return;
  const p = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(wsUrl(`/ws/${rid}`));
  ws.onmessage = (m: MessageEvent) => { try { onEvent(JSON.parse(m.data)); } catch {} };
  ws.onopen = () => {
    reconnectTries = 0;
    if (pendingAnswer && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "discuss_choice", qseq: questionSeq, selected: pendingAnswer.selected, custom: pendingAnswer.custom }));
      feed.setQuestionSending(pendingAnswer.id, false);
      feed.setUndelivered(pendingAnswer.id, false);
      pendingAnswer = null;
    }
    // 断线期间积压的追加需求按序补发
    while (pendingMessages.length && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "user_message", content: pendingMessages.shift() }));
    }
  };
  ws.onclose = () => {
    // 卸载后 ws.close() 触发的 onclose 是异步的：disposed 标记阻止僵尸重连
    if (disposed || !running.value) return;
    reconnectTries++;
    const delay = Math.min(1000 * Math.pow(2, reconnectTries), 8000);
    reconnectTimer = window.setTimeout(() => { if (running.value) connectWS(rid); }, delay);
  };
}
onUnmounted(() => {
  disposed = true;
  running.value = false;
  ws?.close();
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (queueTimer) clearTimeout(queueTimer);
});

function onConfirmAnswer(p: { id: number; selected: string[]; custom: string }) {
  feed.answerQuestion(p.id, p.selected, p.custom);
  if (ws && ws.readyState === WebSocket.OPEN) {
    feed.setQuestionSending(p.id, true);
    ws.send(JSON.stringify({ type: "discuss_choice", qseq: questionSeq, selected: p.selected, custom: p.custom }));
  } else {
    // 断线：标记未送达，重连后自动重发
    feed.setQuestionSending(p.id, true);
    feed.setUndelivered(p.id, true);
    pendingAnswer = { id: p.id, selected: p.selected, custom: p.custom };
  }
}

// 运行中追加需求：进对话流 + 发后端（阶段边界生效，回退 Design 重跑）。
// 断线期间的消息入待发队列，重连后按序补发 —— 与 pendingAnswer 对称
//（此前断线时用户反馈被静默丢弃）
const pendingMessages: string[] = [];
function onUserMessage(text: string) {
  feed.addChat(text);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "user_message", content: text }));
  } else {
    pendingMessages.push(text);
  }
}

// 人工审阅决策：标记卡片 + 发后端（Verification 阻塞等待中）
function onReviewDecision(p: { id: number; approved: boolean }) {
  feed.decideReview(p.id, p.approved);
  // 已决策 → 明确告知下一步（新验证轮次在面板里进行）
  activeHint.value = p.approved
    ? "✅ 已通过 — 继续下一轮修复验证…"
    : "❌ 已拒绝 — 带着你的反馈重新修复…";
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "review_decision", approved: p.approved }));
  }
}

function resumeRun() {
  const cp = resumeCheckpoint.value;
  if (cp) {
    taskPrompt.value = cp.task || taskPrompt.value;
    resumeCheckpoint.value = null;
    const idx = ALL_PHASES.indexOf(cp.phase);
    const startFrom = idx >= 0 && idx < ALL_PHASES.length - 1 ? ALL_PHASES[idx + 1] : "";
    runPipeline(startFrom);
  }
}

// 阶段列表来自 phases.ts 单一来源
const ALL_PHASES = PHASES;

// 预览模式：不进真实流程，直接让大厅角色演示动画
async function previewHall() {
  pipelineStarted.value = true;
  running.value = false;
  runStatus.value = "预览";
  feed.reset();
  phases.value = ALL_PHASES.map((name, i) => ({
    name, status: i === 0 ? "active" : "pending", agents: [] as string[],
  }));
  completedPhases.value = [];
  // PixelOffice 在 v-if="pipelineStarted" 下，同步调用时尚未挂载
  //（officeRef 为 null，demo() 空操作 → 演示剧本从未播放）→ 等一帧再启动
  await nextTick();
  officeRef.value?.reset();
  officeRef.value?.demo();
}

// A2 增量迭代：历史项目上发起修改请求（pipeline=iterate，复用项目目录）
async function onIterate(p: { projectId: string; feedback: string }) {
  historyOpen.value = false;
  taskPrompt.value = `[迭代] ${p.feedback}`;
  pipelineStarted.value = true; running.value = true;
  runStatus.value = "运行中（迭代）";
  lastSeq = -1;
  feed.reset();
  officeRef.value?.reset();
  pendingMessages.length = 0;
  phases.value = [{ name: "Iterate", status: "active", agents: [] }];
  completedPhases.value = [];
  try {
    const body = new URLSearchParams();
    body.append("task", p.feedback);
    body.append("pipeline", "iterate");
    body.append("project", p.projectId);
    const r = await apiFetch("/api/run", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    const d = await r.json();
    if (d.run_id) connectWS(d.run_id);
    else { running.value = false; runStatus.value = "启动失败"; }
  } catch { running.value = false; runStatus.value = "错误"; backendDown.value = true; }
}

// B4 任务队列：排队中轮询，就绪后自动切工作区并连接 ws
let queueTimer: number | null = null;
function startQueuePolling(rid: string) {
  runStatus.value = "排队中…";
  const poll = () => {
    if (disposed) return;   // 已卸载：in-flight 响应不再续期轮询（防永久泄漏）
    apiFetch(`/api/queue/${rid}`)
      .then((r) => r.json())
      .then((d) => {
        if (disposed) return;
        if (d.started) {
          runStatus.value = "运行中";
          pipelineStarted.value = true;
          connectWS(rid);
        } else {
          runStatus.value = `排队中（第 ${d.position ?? "?"} 位）`;
          queueTimer = window.setTimeout(poll, 3000);
        }
      })
      .catch(() => { if (!disposed) queueTimer = window.setTimeout(poll, 3000); });
  };
  poll();
}

async function runPipeline(startFrom: string = "") {
  if (!taskPrompt.value.trim() || running.value) return;
  running.value = true; runStatus.value = startFrom ? "运行中（重跑）" : "运行中";
  // seq 基线必须重置：emit 的 seq 是 per-run 从 0 计数的，不重置会把
  // 新 run 全部事件当成旧重放丢弃（lastSeq 保留旧 run 的较大值），界面假死。
  lastSeq = -1;
  if (startFrom) {
    // 重跑保留重跑点之前的历史（需求讨论等）：清掉重跑点及之后的条目，
    // 否则新 run 重放的事件与旧历史重复显示。
    // resetStage 清掉旧 run 残留的面板内部状态（否则新 run 首个无 agent
    // 字段的事件被旧面板闭包拦截 → 瞬态 'Agent' 幻影窗口）
    feed.resetStage();
    const dropIdx = ALL_PHASES.indexOf(startFrom);
    const dropPhases = new Set(dropIdx >= 0 ? ALL_PHASES.slice(dropIdx) : []);
    for (let i = feed.items.length - 1; i >= 0; i--) {
      if (dropPhases.has(feed.items[i].phase)) feed.items.splice(i, 1);
    }
  } else {
    feed.reset();
  }
  officeRef.value?.reset();
  rerunPhase.value = "";
  pendingAnswer = null;   // 新运行清除跨运行残留的未送达回答
  pendingMessages.length = 0;   // 同理清除断线积压的追加需求
  if (!startFrom) {
    // Pre-populate all phases immediately so the user sees the pipeline
    // before the first backend event arrives (venv creation takes 3-10s).
    phases.value = ALL_PHASES.map((name, i) => ({
      name,
      status: i === 0 ? "active" : "pending",
      agents: [] as string[],
    }));
    completedPhases.value = [];
  } else {
    // Pre-mark phases before startFrom as completed
    const idx = ALL_PHASES.indexOf(startFrom);
    phases.value = ALL_PHASES.map((name, i) => ({
      name, status: i < idx ? "done" : (i === idx ? "active" : "pending"),
      agents: [] as string[],
    }));
    completedPhases.value = ALL_PHASES.slice(0, idx);
    if (idx < 0) completedPhases.value = [];
  }
  if (reconnectTimer) clearTimeout(reconnectTimer);
  // 先摘掉 onclose 再 close：否则旧 ws 的 onclose 异步触发时 running 仍为 true，
  // 触发指数退避无限重连旧 run（旧 run 内存保留 1h），每次无效重连白等 8s
  if (ws) { ws.onclose = null; ws.close(); }
  try {
    const p = new URLSearchParams(); p.append("task", taskPrompt.value);
    if (startFrom) p.append("start_from", startFrom);
    const r = await apiFetch("/api/run", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: p });
    const d = await r.json();
    if (d.run_id) {
      if (d.queued) startQueuePolling(d.run_id);          // B4 排队
      else { pipelineStarted.value = true; connectWS(d.run_id); }
    }
    else { running.value = false; runStatus.value = "启动失败"; }
  } catch { running.value = false; runStatus.value = "错误"; backendDown.value = true; }
}
</script>

<style scoped>
.app { width: 100%; min-width: 0; margin: 0 auto; padding: 16px; box-sizing: border-box; color: #1e293b; font-family: system-ui, -apple-system, sans-serif; }
/* Hero input */
.hero { margin: 40px auto; max-width: 640px; }
.resume-banner { background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; color: #065f46; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.backend-down { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 10px 16px; margin-bottom: 16px; font-size: 13px; color: #b91c1c; }
.backend-down code { background: #fee2e2; border-radius: 4px; padding: 1px 6px; font-size: 12px; }
.resume-banner strong { color: #047857; }

.hero-input { width: 100%; padding: 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; font: 15px/1.6 system-ui; resize: vertical; outline: none; box-sizing: border-box; color: #1e293b; }
.hero-input:focus { border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.12); }
.hero-templates { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
/* D3 示例任务 */
.hero-examples { display: flex; gap: 8px; margin-top: 8px; align-items: center; flex-wrap: wrap; }
.hero-examples-label { font-size: 12px; color: #94a3b8; }
.hero-examples .btn { padding: 4px 10px; font-size: 12px; }
.hero-templates .btn { padding: 5px 12px; font-size: 12px; }
.hero-templates .btn.active { background: #6366f1; color: #fff; border-color: #6366f1; }

/* 模板表单：选类型 → 填字段 → 预览 → 使用 */
.template-form {
  margin-top: 12px; padding: 14px 16px;
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
}
.tf-intro { font-size: 12px; color: #64748b; margin-bottom: 10px; }
.tf-fields { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px 14px; }
.tf-field { display: flex; flex-direction: column; gap: 3px; }
.tf-label { font-size: 11px; font-weight: 700; color: #475569; }
.tf-input {
  padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 6px;
  font: 12px system-ui; outline: none; color: #1e293b;
}
.tf-input:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.1); }
.tf-preview {
  margin-top: 10px; padding: 8px 12px;
  background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px;
}
.tf-preview-title { font-size: 10px; font-weight: 700; color: #94a3b8; letter-spacing: 0.06em; }
.tf-preview-text { font-size: 13px; color: #334155; line-height: 1.6; margin-top: 3px; }
.tf-actions { display: flex; gap: 8px; margin-top: 12px; }
.hero-bar { display: flex; align-items: center; gap: 14px; margin-top: 14px; }
.hint { font-size: 12px; color: #94a3b8; }

/* Buttons */
.btn { padding: 8px 18px; border: none; border-radius: 7px; font: 600 13px system-ui; cursor: pointer; background: #6366f1; color: #fff; }
.btn:hover:not(:disabled) { background: #4f46e5; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
.btn.primary { padding: 12px 28px; font-size: 14px; border-radius: 8px; }
.btn.sm { padding: 6px 14px; font-size: 12px; }
.btn.ok { background: #10b981; }
.btn.ok:hover:not(:disabled) { background: #059669; }
.btn.outline { background: #fff; color: #64748b; border: 1px solid #e2e8f0; }
.btn.outline:hover:not(:disabled) { background: #f8fafc; }

/* Workspace */
.workspace {
  display: flex;
  flex-direction: column;
  gap: 14px;
  /* 锁定一屏高度：视口 - topbar(52) - app padding(32) */
  height: calc(100vh - 84px);
  min-height: 0;
}
.topbar { display: flex; justify-content: space-between; align-items: center; }
.task-title { font-size: 14px; font-weight: 600; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; margin-right: 16px; }
.topbar-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.status-tag { font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px; }
.status-tag.running { color: #6366f1; background: rgba(99,102,241,.08); }
.status-tag.done { color: #10b981; background: rgba(16,185,129,.08); }
.status-tag.failed { color: #ef4444; background: rgba(239,68,68,.08); }
.status-tag.defect { color: #ef4444; background: rgba(239,68,68,.14); }
.status-tag.startfail { color: #f59e0b; background: rgba(245,158,11,.08); }
.status-tag.error { color: #f59e0b; background: rgba(245,158,11,.08); }

/* Pipeline area */
.pipe-area { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 20px; }

/* Main 2-column */
.main-area {
  display: grid;
  grid-template-columns: minmax(300px, 44%) minmax(0, 56%);
  /* 关键：行高锁定为可用空间，否则面板 height:100% 与内容形成循环撑开 */
  grid-template-rows: minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
  flex: 1;
  min-height: 0;
}
.chat-panel, .act-panel {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  overflow: hidden; display: flex; flex-direction: column;
  height: 100%; min-height: 0;
}
.panel-label { font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: .08em; padding: 12px 16px 8px; }
/* position:relative 供对话区绝对定位撑满；滚动交给对话区内部 */
.chat-scroll { position: relative; flex: 1; overflow: hidden; min-height: 0; }
.act-scroll { flex: 1; overflow-y: auto; min-height: 0; padding: 0 16px 12px; }

.rerun-group { display: flex; gap: 8px; align-items: center; }
.rerun-select { padding: 5px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font: 12px system-ui; color: #475569; background: #fff; outline: none; }
.rerun-select:focus { border-color: #6366f1; }

@media (max-width: 800px) {
  .main-area { grid-template-columns: 1fr; }
  .hero { margin: 20px 0; }
}
</style>
