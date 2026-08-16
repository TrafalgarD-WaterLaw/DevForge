<!-- PixelOffice.vue — pixel hall with agent sprites + bubbles + moods -->
<template>
  <div class="po-wrap" ref="wrapRef">
    <div class="po-scene" :class="{ blackout }" :style="sceneStyle" @click="selectedAgent = null">
      <img src="/sprites/office-bg.png" class="po-bg" alt="" draggable="false" />

      <!-- 区域聚光（当前阶段 zone 微光） -->
      <div v-if="stage.glowZone" class="po-glow" :style="glowStyle(stage.glowZone)"></div>

      <!-- 阶段横幅 -->
      <div v-if="stage.banner" class="po-banner">{{ stage.banner }}</div>

      <!-- Agent sprites -->
      <div
        v-for="a in agents" :key="a.id"
        class="po-agent"
        :class="[`agent-${a.id}`, `mood-${a.mood}`, a.activity,
                 { 'break-walk': a.waterBreak || a.teaSpot >= 0 },
                 { solid: ceremonySolid }]"
        :data-name="a.displayName"
        :style="agentStyle(a)"
        @click.stop="selectedAgent = a"
      >
        <img
          :src="spriteUrl(a)"
          class="agent-img"
          :class="{ loaded: spriteLoaded[a.id] }"
          @error="onSpriteError(a)"
          @load="onSpriteLoad(a.id)"
        />
        <div v-if="!spriteLoaded[a.id]" class="agent-placeholder">
          <span>{{ a.displayName }}</span>
        </div>
        <!-- 状态徽章 -->
        <span class="agent-icon" :class="a.activity">{{ stateIcon(a.activity) }}</span>
      </div>

      <!-- 氛围气泡独立层（不随 agent 层叠上下文，始终显示在最上层） -->
      <div
        v-for="a in bubbleAgents" :key="'bubble-' + a.id"
        class="po-bubble"
        :style="bubbleStyle(a)"
      >{{ a.bubble.text }}</div>

      <div v-if="zoneLabel" class="po-zone-label">{{ zoneLabel }}</div>

      <!-- 🎈 庆祝气球 -->
      <div
        v-for="b in balloons" :key="b.id"
        class="po-balloon"
        :style="{ left: b.x + 'px', animationDelay: b.delay + 'ms' }"
      >🎈</div>

      <!-- 🐱 猫咪巡逻（素材精灵：walk/walk_left/idle 雪碧图 steps 动画） -->
      <div
        v-if="cat"
        class="po-cat"
        :class="[cat.dir === -1 ? 'left' : 'right', { lick: cat.phase === 'lick' }]"
        :style="{
          left: cat.left + 'px',
          top: cat.y + 'px',
        }"
      ></div>

      <!-- 饮水机水泡 -->
      <div
        v-for="b in waterBubbles" :key="b.id"
        class="water-bubble"
      >💧</div>

      <!-- 点击小人卡片 -->
      <div
        v-if="selectedAgent"
        class="po-agent-card"
        :style="agentStyle(selectedAgent)"
      >
        <div class="pac-name">{{ selectedAgent.displayName }}</div>
        <div class="pac-status">{{ statusLabel(selectedAgent.activity) }}</div>
        <div v-if="selectedAgent.bubble" class="pac-act">{{ selectedAgent.bubble.text }}</div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  AGENT_MAP, STATE_ICONS, ZONE_LABELS, spriteFor, zoneBBox, ACTIVITY_LABELS_CN,
  type Activity, type AgentMeta, type ZoneName,
} from './spriteDefs'
import { init, dispatch, tick, getAgentStates, getStage, isCeremonyHeld, blackoutBubble, type AgentState, WATER_DISPENSER, setEggsEnabled } from './stateMachine'

const agents = ref<AgentState[]>([])
const stage = ref({ glowZone: '' as ZoneName, banner: null as string | null, bannerUntil: 0 })
// 竣工典礼后：全员保持实体（不再虚影）
const ceremonySolid = ref(false)
const spriteLoaded = ref<Record<string, boolean>>({})

const SCENE_W = 1216
const SCENE_H = 878
const wrapRef = ref<HTMLElement | null>(null)
const sceneStyle = ref({})
let _resizeObs: ResizeObserver | null = null

const zoneLabel = computed(() => ZONE_LABELS[stage.value.glowZone] || '')

function updateLayout() {
  const wrap = wrapRef.value
  if (!wrap) return
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  if (!w || !h) return
  const scale = Math.min(w / SCENE_W, h / SCENE_H)
  sceneStyle.value = {
    transform: `scale(${scale})`,
    width: `${SCENE_W}px`,
    height: `${SCENE_H}px`,
    left: `${(w - SCENE_W * scale) / 2}px`,
    top: `${(h - SCENE_H * scale) / 2}px`,
  }
}

let _interval = 0

onMounted(() => {
  init()
  agents.value = getAgentStates()
  updateLayout()
  requestAnimationFrame(updateLayout)
  _resizeObs = new ResizeObserver(updateLayout)
  if (wrapRef.value) _resizeObs.observe(wrapRef.value)
  window.addEventListener('resize', updateLayout)
  // 8 FPS 模拟器 tick
  _interval = window.setInterval(() => {
    tick(125)
    agents.value = getAgentStates()
    stage.value = getStage()
    ceremonySolid.value = isCeremonyHeld()
  }, 125)

  // 彩蛋
  setEggsEnabled(true)
  scheduleCat()
  armBlackout()
  _bubbleTimer = window.setInterval(() => {
    if (Math.random() < 0.8) spawnWaterBubble()
  }, 3000)
})

onUnmounted(() => {
  if (_interval) clearInterval(_interval)
  _demoTimer.forEach((t) => clearTimeout(t))
  _demoTimer = []
  _oneShotTimers.forEach((t) => clearTimeout(t))
  _oneShotTimers = []
  _resizeObs?.disconnect()
  window.removeEventListener('resize', updateLayout)
  clearTimeout(_catTimer)
  clearInterval(_catInterval)
  clearInterval(_bubbleTimer)
  clearTimeout(_blackoutTimer)
  setEggsEnabled(false)
})

// ── helpers ──

// 原始精灵 URL（走路帧 / 站立帧），与 :src 解耦：错误回退与 :src 计算各自独立
function spriteSrc(a: AgentState): string {
  const meta = AGENT_MAP[a.id]
  if (!meta) return ''
  const frame = a.activity === 'walk' ? a.frame : 0
  return `/sprites/${spriteFor(meta, a.facing, frame)}`
}

// 精灵 URL：走路帧加载失败时回退到同朝向 f0 站立帧；
// f0 也失败则保持原 URL（占位符由 spriteLoaded 控制）
const spriteFailed = ref<Record<string, boolean>>({})
function spriteUrl(a: AgentState): string {
  const url = spriteSrc(a)
  if (!spriteFailed.value[url]) return url
  const meta = AGENT_MAP[a.id]
  if (!meta) return ''
  const fallback = `/sprites/${spriteFor(meta, a.facing, 0)}`
  return spriteFailed.value[fallback] ? url : fallback
}

function agentStyle(a: AgentState) {
  return {
    left: `${(a.pos.x / SCENE_W) * 100}%`,
    top: `${(a.pos.y / SCENE_H) * 100}%`,
  }
}

const bubbleAgents = computed(() => agents.value.filter((a) => a.bubble))

function bubbleStyle(a: AgentState) {
  return {
    left: `${(a.pos.x / SCENE_W) * 100}%`,
    top: `${(a.pos.y / SCENE_H) * 100}%`,
  }
}

function glowStyle(zone: ZoneName) {
  const b = zoneBBox(zone)
  return {
    left: `${(b.x / SCENE_W) * 100}%`,
    top: `${(b.y / SCENE_H) * 100}%`,
    width: `${(b.w / SCENE_W) * 100}%`,
    height: `${(b.h / SCENE_H) * 100}%`,
  }
}

function stateIcon(activity: string): string {
  return STATE_ICONS[activity] || ''
}

function onSpriteLoad(id: string) { spriteLoaded.value = { ...spriteLoaded.value, [id]: true } }
function onSpriteError(a: AgentState) {
  const url = spriteSrc(a)
  if (url) spriteFailed.value = { ...spriteFailed.value, [url]: true }
  spriteLoaded.value = { ...spriteLoaded.value, [a.id]: false }
}

// ── public API（Dashboard 不变）──

function onEvent(e: Record<string, unknown>) {
  if (e.event) {
    if (e.event === 'phase_end') spawnBalloons(3)
    dispatch(e)
    agents.value = getAgentStates()
    stage.value = getStage()
  }
}

function reset() {
  // 清除残留 demo 定时器：预览大厅后开始真实运行，demo 事件不得继续注入状态机
  _demoTimer.forEach((t) => clearTimeout(t))
  _demoTimer = []
  init()
  agents.value = getAgentStates()
  stage.value = { glowZone: '', banner: null, bannerUntil: 0 }
}

// ── demo：完整叙事剧本 — 每个条目是 [延迟ms, 事件] ──
const DEMO_EVENTS: [number, Record<string, unknown>][] = [
  // ── 需求讨论 ──
  [0, { event: 'phase_start', phase: 'RequirementsDiscussion' }],
  [1600, { event: 'conversation_turn', agent: 'product_manager', content: '{}' }],
  [1600, { event: 'tool_pre_use', agent: 'product_manager', tool: 'write_file' }],
  [1600, { event: 'requirements_submitted', agent: 'product_manager' }],
  [2000, { event: 'phase_end', phase: 'RequirementsDiscussion' }],
  // ── 设计 ──
  [1200, { event: 'phase_start', phase: 'Design' }],
  [1600, { event: 'conversation_turn', agent: 'chief_technology_officer', content: '{}' }],
  [1600, { event: 'conversation_turn', agent: 'chief_product_officer', content: '{}' }],
  [1500, { event: 'phase_end', phase: 'Design' }],
  // ── 编码 ──
  [1200, { event: 'phase_start', phase: 'Coding' }],
  [1600, { event: 'tool_pre_use', agent: 'coder_0', tool: 'write_file' }],
  [1600, { event: 'tool_pre_use', agent: 'coder_1', tool: 'write_file' }],
  [1600, { event: 'tool_pre_use', agent: 'coder_2', tool: 'write_file' }],
  [1600, { event: 'tool_post_use', agent: 'coder_0' }],
  [1600, { event: 'tool_post_use', agent: 'coder_1' }],
  [1600, { event: 'tool_post_use', agent: 'coder_2' }],
  [1200, { event: 'coding_progress' }],
  [1200, { event: 'conversation_turn', agent: 'integrator', content: '{}' }], // ← 触发串门
  [10000, { event: 'conversation_turn', agent: 'tester', content: '{}' }],    // 留足 串门+交付+回座位
  [1500, { event: 'phase_end', phase: 'Coding' }],
  // ── 验证 ──
  [1200, { event: 'phase_start', phase: 'Verification' }],
  [1600, { event: 'conversation_turn', agent: 'SecurityReviewer', content: '{}' }],
  [1200, { event: 'review_submitted', issues: [{}] }],        // → reviewer_0 发现问题
  [1600, { event: 'conversation_turn', agent: 'LogicReviewer', content: '{}' }],
  [1200, { event: 'review_submitted', issues: [{}] }],        // → reviewer_1 发现问题
  [1200, { event: 'conversation_turn', agent: 'fixer', content: '{}' }],      // ← 触发串门
  [13000, { event: 'phase_end', phase: 'Verification' }],                     // 留足 reviewer 递交+回座位（最远 4.6s×2+2s）
  // ── 质检 FAIL 回跳 ──
  [1200, { event: 'phase_start', phase: 'QualityGate' }],
  [1600, { event: 'conversation_turn', agent: 'inspector', content: '{}' }],
  [1500, { event: 'phase_retry', phase: 'Verification', loop: 1 }],          // ← FAIL 回跳
  [1500, { event: 'phase_start', phase: 'Verification' }],
  [1600, { event: 'conversation_turn', agent: 'fixer', content: '{}' }],
  [4000, { event: 'phase_end', phase: 'Verification' }],
  // ── 文档 ──
  [1200, { event: 'phase_start', phase: 'Documentation' }],
  [1600, { event: 'conversation_turn', agent: 'technical_writer', content: '{}' }],
  [1600, { event: 'conversation_turn', agent: 'dependency_analyst', content: '{}' }],
  [1500, { event: 'phase_end', phase: 'Documentation' }],
  // ── 质检通过 ──
  [1200, { event: 'phase_start', phase: 'QualityGate' }],
  [1600, { event: 'conversation_turn', agent: 'inspector', content: '{}' }],
  [1500, { event: 'phase_end', phase: 'QualityGate' }],
]

let _demoTimer: number[] = []
function demo() {
  reset()
  _demoTimer.forEach((t) => clearTimeout(t))
  _demoTimer = []
  let acc = 0
  DEMO_EVENTS.forEach(([delay, e]) => {
    acc += delay
    _demoTimer.push(window.setTimeout(() => onEvent(e), acc))
  })
}

// ── 彩蛋状态 ──
const cat = ref<{ left: number; y: number; dir: 1 | -1; phase: 'walk' | 'lick' } | null>(null)
const waterBubbles = ref<{ id: number }[]>([])
const selectedAgent = ref<AgentState | null>(null)
let _catTimer = 0, _catInterval = 0, _bubbleTimer = 0, _bubbleSeq = 0

// ── 彩蛋二批：气球 / 断电 ──
const balloons = ref<{ id: number; x: number; delay: number }[]>([])
const blackout = ref(false)
let _balloonSeq = 0
let _blackoutTimer = 0

// 一次性 setTimeout 跟踪：组件卸载时全部清除，避免卸载后触碰响应式状态
let _oneShotTimers: number[] = []
function oneShot(fn: () => void, ms: number) {
  _oneShotTimers.push(window.setTimeout(fn, ms))
}

function spawnBalloons(n: number) {
  for (let i = 0; i < n; i++) {
    const id = _balloonSeq++
    balloons.value.push({ id, x: 60 + Math.random() * (SCENE_W - 120), delay: i * 300 })
    oneShot(() => {
      balloons.value = balloons.value.filter((b) => b.id !== id)
    }, 4200 + i * 300)
  }
}

function armBlackout() {
  clearTimeout(_blackoutTimer)
  _blackoutTimer = window.setTimeout(() => {
    if (Math.random() < 0.5) {
      blackout.value = true
      blackoutBubble()
      oneShot(() => { blackout.value = false }, 300)
    }
    armBlackout()
  }, 120000 + Math.random() * 120000)
}

function statusLabel(activity: string): string {
  return ACTIVITY_LABELS_CN[activity as Activity] || activity
}

function spawnCat() {
  const dir: 1 | -1 = Math.random() < 0.5 ? 1 : -1
  cat.value = { left: dir === 1 ? -60 : SCENE_W + 60, y: WATER_DISPENSER.y, dir, phase: 'walk' }
  let licking = false
  let lickLeft = 0
  _catInterval = window.setInterval(() => {
    const c = cat.value
    if (!c) { clearInterval(_catInterval); return }
    if (c.phase === 'lick') {
      lickLeft -= 50
      if (lickLeft <= 0) c.phase = 'walk'
      return
    }
    c.left += c.dir * 5   // 100px/s ≈ 12s 穿场（规格节奏）
    if (!licking && Math.abs(c.left - WATER_DISPENSER.x) < 30) {
      licking = true
      c.phase = 'lick'
      lickLeft = 1500
    }
    if ((c.dir === 1 && c.left > SCENE_W + 80) || (c.dir === -1 && c.left < -80)) {
      clearInterval(_catInterval)
      cat.value = null
      scheduleCat()
    }
  }, 50)
}

function scheduleCat() {
  clearTimeout(_catTimer)
  _catTimer = window.setTimeout(spawnCat, 30000 + Math.random() * 60000)
}

function spawnWaterBubble() {
  const id = _bubbleSeq++
  waterBubbles.value.push({ id })
  oneShot(() => {
    waterBubbles.value = waterBubbles.value.filter((b) => b.id !== id)
  }, 1800)
}

// Dashboard 通过 ref 调用的公开 API（type-only 导出，运行时被擦除）
export interface PixelOfficeApi {
  onEvent(e: Record<string, unknown>): void
  reset(): void
  demo(): void
}

defineExpose({ onEvent, reset, demo })
</script>

<style scoped>
.po-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.po-scene {
  position: absolute;
  transform-origin: top left;
  overflow: hidden;
  border-radius: 8px;
  background: #1d2b53;
}
.po-bg {
  position: absolute;
  top: 0; left: 0;
  width: 1216px; height: 878px;
  user-select: none;
  pointer-events: none;
}

/* ── 区域聚光 ── */
.po-glow {
  position: absolute;
  border-radius: 24px;
  background: radial-gradient(ellipse at center,
    rgba(255, 240, 150, 0.12) 0%, rgba(255, 240, 150, 0.04) 55%, transparent 75%);
  pointer-events: none;
  z-index: 1;
  animation: glow-in 0.8s ease;
}
@keyframes glow-in { from { opacity: 0; } to { opacity: 1; } }

/* ── 阶段横幅 ── */
.po-banner {
  position: absolute;
  top: 34px; left: 50%;
  transform: translateX(-50%);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.3em;
  color: #ffe95c;
  background: rgba(10, 15, 30, 0.82);
  border: 2px solid #ffd700;
  border-radius: 6px;
  padding: 3px 16px;
  z-index: 12;
  animation: banner-pop 0.4s ease;
  pointer-events: none;
}
@keyframes banner-pop {
  from { transform: translateX(-50%) scale(0.7); opacity: 0; }
  to   { transform: translateX(-50%) scale(1); opacity: 1; }
}

/* ── agent ── */
.po-agent {
  position: absolute;
  width: 52px; height: 70px;
  z-index: 2;
  transition: left 0.4s ease, top 0.4s ease,
              opacity 0.3s ease, transform 0.3s ease, filter 0.3s ease;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.po-agent.idle {
  opacity: 0.62;
  filter: saturate(0.75) brightness(0.92);
}
.po-agent:not(.idle) {
  opacity: 1;
  transform: scale(1.15);
  z-index: 3;
  filter: saturate(1.15) brightness(1.05)
          drop-shadow(0 0 6px rgba(255, 255, 255, 0.35));
}
/* 走路者：不放大、置顶（必须在 :not(.idle) 之后，优先级相同后者胜） */
.po-agent.walk {
  transform: scale(1);
  z-index: 4;
}
/* 摸鱼彩蛋（接水/喝茶）保持 idle 暗色，不和工作混淆（必须在 .walk 之后） */
.po-agent.break-walk {
  opacity: 0.62;
  filter: saturate(0.75) brightness(0.92);
  transform: scale(1);
}
/* 竣工典礼后：全员实体（覆盖 idle 虚影） */
.po-agent.solid.idle {
  opacity: 1;
  filter: saturate(1) brightness(1);
  transform: scale(1);
}
.po-agent.break-walk::before { display: none; }
.po-agent.break-walk::after { color: #fff1e8; }

.agent-img {
  width: 52px;
  height: 70px;
  image-rendering: pixelated;
  opacity: 0;
  transition: opacity 0.2s;
  object-fit: contain;
}
.agent-img.loaded { opacity: 1; }

.agent-placeholder {
  width: 32px; height: 40px;
  background: #5f574f;
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; color: #fff1e8;
  border-radius: 2px;
  font-family: monospace;
}

/* Agent 名字 */
.po-agent::after {
  content: attr(data-name);
  position: absolute;
  bottom: -4px;
  font-size: 11px;
  font-weight: 600;
  color: #fff1e8;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
  white-space: nowrap;
  transition: color 0.2s;
}

/* ── 气泡（独立层：left/top 由 bubbleStyle 定位，z-index 高于一切角色）── */
.po-bubble {
  position: absolute;
  transform: translate(-50%, -60px);
  max-width: 140px;
  padding: 4px 9px;
  background: #fff8e8;
  border: 2px solid #3b2d1f;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  color: #3b2d1f;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  box-shadow: 0 2px 0 rgba(0, 0, 0, 0.25);
  z-index: 30;
  animation: bubble-pop 0.25s ease;
}
.po-bubble::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: #fff8e8;
}
@keyframes bubble-pop {
  from { transform: translate(-50%, -60px) scale(0.6); opacity: 0; }
  to   { transform: translate(-50%, -60px) scale(1); opacity: 1; }
}

/* ── mood 情绪（与活动正交叠加）── */
.po-agent.mood-happy:not(.idle) { animation: mood-bounce 0.6s ease infinite; }
@keyframes mood-bounce {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-6px); }
}
.po-agent.mood-worried:not(.idle) { animation: mood-shake 0.5s ease infinite; }
@keyframes mood-shake {
  0%, 100% { transform: translateX(0); }
  25%      { transform: translateX(-2px); }
  75%      { transform: translateX(2px); }
}
.po-agent.mood-happy::after { color: #7ef06e; }
.po-agent.mood-worried::after { color: #9fb8d9; }

/* ── 状态光环 ── */
.po-agent:not(.idle)::before {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 44px; height: 14px;
  border-radius: 50%;
  background: radial-gradient(ellipse at center,
    rgba(255, 255, 255, 0.55) 0%,
    rgba(255, 255, 255, 0.15) 55%,
    transparent 75%);
  animation: ring-pulse 1.1s ease-in-out infinite;
  pointer-events: none;
}
@keyframes ring-pulse {
  0%, 100% { opacity: 0.55; transform: translateX(-50%) scale(1); }
  50%      { opacity: 1;    transform: translateX(-50%) scale(1.12); }
}
.po-agent.think::before { background: radial-gradient(ellipse at center, rgba(255, 163, 0, 0.55), rgba(255, 163, 0, 0.12) 55%, transparent 75%); }
.po-agent.work::before  { background: radial-gradient(ellipse at center, rgba(0, 228, 54, 0.6),  rgba(0, 228, 54, 0.15) 55%, transparent 75%); }
.po-agent.talk::before  { background: radial-gradient(ellipse at center, rgba(255, 236, 39, 0.6), rgba(255, 236, 39, 0.15) 55%, transparent 75%); }

.po-agent.think::after { color: #ffa300; }
.po-agent.work::after  { color: #7ef06e; }
.po-agent.talk::after  { color: #ffe95c; }

/* ── 状态徽章 ── */
.agent-icon {
  position: absolute;
  top: -28px;
  font-size: 20px;
  line-height: 1;
  filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.6));
  animation: icon-pop 0.3s ease;
  z-index: 6;
}
@keyframes icon-pop {
  from { transform: scale(0.5); opacity: 0; }
  to   { transform: scale(1); opacity: 1; }
}
.agent-icon.work { animation: icon-pop 0.3s ease, icon-pulse 1s ease infinite; }
@keyframes icon-pulse {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.15); }
}
.agent-icon.idle { animation: icon-pop 0.3s ease, icon-sip 2.4s ease infinite; }
@keyframes icon-sip {
  0%, 85%, 100% { transform: rotate(0deg); }
  90%           { transform: rotate(-15deg); }
}
.po-agent.walk .agent-icon { display: none; }

/* ── zone 标签 ── */
.po-zone-label {
  position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
  font-size: 10px; font-weight: 700; letter-spacing: 0.12em;
  color: #fff1e8; background: rgba(0, 0, 0, 0.55);
  padding: 2px 10px; border-radius: 4px;
  pointer-events: none; text-transform: uppercase; z-index: 10;
}

/* 🐱 猫咪巡逻（32×32 帧雪碧图，1.5x 显示 = 48×48） */
.po-cat {
  position: absolute;
  width: 48px;
  height: 48px;
  image-rendering: pixelated;
  background-repeat: no-repeat;
  z-index: 5;
  pointer-events: none;
  filter: drop-shadow(0 2px 2px rgba(0, 0, 0, 0.3));
}
.po-cat.right { background-image: url('/sprites/cat/walk.png'); background-size: 336px 48px; animation: cat-walk 0.8s steps(7) infinite; }
.po-cat.left  { background-image: url('/sprites/cat/walk_left.png'); background-size: 336px 48px; animation: cat-walk 0.8s steps(7) infinite; }
.po-cat.lick  { background-image: url('/sprites/cat/idle.png'); background-size: 384px 48px; animation: cat-idle 1.2s steps(8) infinite; }
@keyframes cat-walk { from { background-position: 0 0; } to { background-position: -336px 0; } }
@keyframes cat-idle { from { background-position: 0 0; } to { background-position: -384px 0; } }

/* 饮水机水泡 */
.water-bubble {
  position: absolute;
  left: 24px;
  top: 110px;
  font-size: 14px;
  z-index: 5;
  pointer-events: none;
  animation: bubble-rise 1.8s ease-out forwards;
}
@keyframes bubble-rise {
  from { transform: translate(0, 0); opacity: 0; }
  20%  { opacity: 1; }
  to   { transform: translate(6px, -34px); opacity: 0; }
}

/* 🎈 庆祝气球 */
.po-balloon {
  position: absolute;
  bottom: -50px;
  font-size: 24px;
  z-index: 6;
  pointer-events: none;
  animation: balloon-rise 4s ease-in forwards;
}
@keyframes balloon-rise {
  from { transform: translateY(0) translateX(0); opacity: 0; }
  15%  { opacity: 1; }
  to   { transform: translateY(-960px) translateX(24px); opacity: 0; }
}

/* ⚡ 断电闪烁 */
.po-scene.blackout {
  filter: brightness(0.25) saturate(0.4);
  transition: filter 0.1s;
}

/* 点击小人卡片 */
.po-agent-card {
  position: absolute;
  transform: translate(-50%, -130px);
  background: #0f172a;
  color: #f1f5f9;
  border: 2px solid #6366f1;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  z-index: 40;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
  pointer-events: none;
  min-width: 90px;
  text-align: center;
}
.po-agent-card::after {
  content: '';
  position: absolute;
  bottom: -7px;
  left: 50%;
  transform: translateX(-50%);
  border: 7px solid transparent;
  border-top-color: #6366f1;
}
.pac-name { font-weight: 800; font-size: 13px; }
.pac-status { color: #a5b4fc; font-size: 11px; margin-top: 2px; }
.pac-act { color: #e2e8f0; font-size: 11px; margin-top: 3px; font-style: italic; }
</style>
