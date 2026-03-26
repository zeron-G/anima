<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  getRobotNodes,
  sendRobotCommand,
  sendRobotNlp,
  speakFromRobot,
  startRobotExploration,
  stopRobotExploration,
  type RobotNode,
} from '@/api/robotics'

interface ActionButton {
  label: string
  command: string
  params?: Record<string, unknown>
  tone?: 'neutral' | 'accent' | 'warning'
}

const nodeMap = ref<RobotNode[]>([])
const selectedNodeId = ref('')
const loading = ref(true)
const busy = ref(false)
const roboticsEnabled = ref(false)
const freeformText = ref('')
const spokenText = ref('')
const explorationGoal = ref('wander')
let refreshHandle: number | undefined

const actionButtons: ActionButton[] = [
  { label: 'Stand', command: 'stand', tone: 'accent' },
  { label: 'Sit', command: 'sit' },
  { label: 'Lie', command: 'lie' },
  { label: 'Stop', command: 'stop', tone: 'warning' },
  { label: 'Forward', command: 'walk_forward', params: { speed: 45 }, tone: 'accent' },
  { label: 'Backward', command: 'walk_backward', params: { speed: 35 } },
  { label: 'Turn Left', command: 'turn_left', params: { speed: 55 } },
  { label: 'Turn Right', command: 'turn_right', params: { speed: 55 } },
  { label: 'Trot', command: 'trot', params: { speed: 60 }, tone: 'accent' },
  { label: 'Look Left', command: 'look_left' },
  { label: 'Look Right', command: 'look_right' },
  { label: 'Center Head', command: 'center_head' },
  { label: 'Wag Tail', command: 'wag_tail' },
  { label: 'Bark', command: 'bark' },
  { label: 'Sleep', command: 'sleep_mode' },
  { label: 'Wake', command: 'wake_mode', tone: 'accent' },
  { label: 'Emergency', command: 'emergency_stop', tone: 'warning' },
]

const selectedNode = computed<RobotNode | null>(() => {
  if (!selectedNodeId.value) {
    return nodeMap.value[0] ?? null
  }
  return nodeMap.value.find((node) => node.node_id === selectedNodeId.value) ?? nodeMap.value[0] ?? null
})

const explorationHistory = computed(() =>
  [...(selectedNode.value?.exploration.history ?? [])].reverse().slice(0, 8),
)

const connectionLabel = computed(() => {
  if (!selectedNode.value) return 'No node selected'
  if (selectedNode.value.connected) return selectedNode.value.connected_url || 'Connected'
  return selectedNode.value.last_error || 'Offline'
})

const distancePercent = computed(() => {
  const distance = selectedNode.value?.perception.distance_cm ?? 0
  return Math.max(0, Math.min(100, (distance / 120) * 100))
})

const batteryPercent = computed(() => {
  const battery = selectedNode.value?.perception.battery_v ?? 0
  if (battery <= 0) return 0
  return Math.max(0, Math.min(100, ((battery - 5.5) / 2) * 100))
})

async function loadData(refresh = true) {
  try {
    const response = await getRobotNodes(refresh)
    roboticsEnabled.value = response.data.enabled
    nodeMap.value = response.data.nodes ?? []
    if (!selectedNodeId.value && nodeMap.value.length > 0) {
      selectedNodeId.value = nodeMap.value[0].node_id
    }
    if (selectedNodeId.value && !nodeMap.value.some((node) => node.node_id === selectedNodeId.value)) {
      selectedNodeId.value = nodeMap.value[0]?.node_id ?? ''
    }
  } catch (error) {
    console.error('Robotics load failed:', error)
  } finally {
    loading.value = false
  }
}

async function invokeCommand(button: ActionButton) {
  if (!selectedNode.value) return
  busy.value = true
  try {
    await sendRobotCommand(selectedNode.value.node_id, button.command, button.params ?? {})
    await loadData(true)
  } finally {
    busy.value = false
  }
}

async function submitNlp() {
  if (!selectedNode.value || !freeformText.value.trim()) return
  busy.value = true
  try {
    await sendRobotNlp(selectedNode.value.node_id, freeformText.value.trim())
    freeformText.value = ''
    await loadData(true)
  } finally {
    busy.value = false
  }
}

async function submitSpeech() {
  if (!selectedNode.value || !spokenText.value.trim()) return
  busy.value = true
  try {
    await speakFromRobot(selectedNode.value.node_id, spokenText.value.trim())
    spokenText.value = ''
    await loadData(true)
  } finally {
    busy.value = false
  }
}

async function startExploration() {
  if (!selectedNode.value) return
  busy.value = true
  try {
    await startRobotExploration(selectedNode.value.node_id, explorationGoal.value, {
      walk_speed: 45,
      turn_speed: 55,
      avoid_distance_cm: 32,
    })
    await loadData(true)
  } finally {
    busy.value = false
  }
}

async function stopExploration() {
  if (!selectedNode.value) return
  busy.value = true
  try {
    await stopRobotExploration(selectedNode.value.node_id)
    await loadData(true)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await loadData(true)
  refreshHandle = window.setInterval(() => {
    void loadData(false)
  }, 2500)
})

onBeforeUnmount(() => {
  if (refreshHandle !== undefined) {
    window.clearInterval(refreshHandle)
  }
})
</script>

<template>
  <div class="page-view robotics-view">
    <div class="page-header">
      <div class="section-label">Embodied Control</div>
      <h1 class="page-title">Robotics</h1>
      <p class="page-subtitle">
        Directly drive PiDog nodes from the desktop, monitor live sensors, and hand off
        local frontier exploration to the dog’s onboard ANIMA runtime.
      </p>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
    </div>

    <div v-else-if="!roboticsEnabled" class="empty-state glass">
      <h3 class="card-title">Robotics Offline</h3>
      <p>Enable `robotics.enabled` and configure at least one PiDog node in `local/env.yaml`.</p>
    </div>

    <div v-else class="robotics-grid">
      <aside class="robot-sidebar glass">
        <h3 class="card-title">Nodes</h3>
        <button
          v-for="node in nodeMap"
          :key="node.node_id"
          class="node-card"
          :class="{ active: selectedNode?.node_id === node.node_id }"
          @click="selectedNodeId = node.node_id"
        >
          <div class="node-card-head">
            <strong>{{ node.name }}</strong>
            <span class="status-pill" :class="node.connected ? 'online' : 'offline'">
              {{ node.connected ? 'online' : 'offline' }}
            </span>
          </div>
          <p>{{ node.state }} · {{ node.emotion }}</p>
          <p class="node-address">{{ node.connected_url || node.base_urls[0] }}</p>
        </button>
      </aside>

      <section class="robot-main">
        <div v-if="selectedNode" class="main-stack">
          <div class="robot-summary glass">
            <div class="summary-copy">
              <div class="section-label compact">Selected Node</div>
              <h2>{{ selectedNode.name }}</h2>
              <p>{{ connectionLabel }}</p>
            </div>
            <div class="summary-metrics">
              <article>
                <span>State</span>
                <strong>{{ selectedNode.state }}</strong>
              </article>
              <article>
                <span>Emotion</span>
                <strong>{{ selectedNode.emotion }}</strong>
              </article>
              <article>
                <span>Queue</span>
                <strong>{{ selectedNode.queue_size }}</strong>
              </article>
            </div>
          </div>

          <div class="sensor-grid">
            <div class="sensor-card glass">
              <h3 class="card-title">Perception</h3>
              <div class="sensor-meter">
                <span>Distance</span>
                <strong>{{ selectedNode.perception.distance_cm.toFixed(1) }} cm</strong>
                <div class="meter-track"><div class="meter-fill accent" :style="{ width: `${distancePercent}%` }" /></div>
              </div>
              <div class="sensor-meter">
                <span>Battery</span>
                <strong>{{ selectedNode.perception.battery_v > 0 ? `${selectedNode.perception.battery_v.toFixed(2)} V` : 'n/a' }}</strong>
                <div class="meter-track"><div class="meter-fill gold" :style="{ width: `${batteryPercent}%` }" /></div>
              </div>
              <div class="sensor-readout">
                <div><span>Touch</span><strong>{{ selectedNode.perception.touch }}</strong></div>
                <div><span>Pitch</span><strong>{{ selectedNode.perception.pitch_deg.toFixed(1) }}°</strong></div>
                <div><span>Roll</span><strong>{{ selectedNode.perception.roll_deg.toFixed(1) }}°</strong></div>
                <div><span>Lifted</span><strong>{{ selectedNode.perception.is_lifted ? 'yes' : 'no' }}</strong></div>
              </div>
            </div>

            <div class="sensor-card glass">
              <h3 class="card-title">Exploration</h3>
              <div class="explore-status">
                <div>
                  <span>Status</span>
                  <strong>{{ selectedNode.exploration.running ? selectedNode.exploration.mode : 'idle' }}</strong>
                </div>
                <div>
                  <span>Goal</span>
                  <strong>{{ selectedNode.exploration.goal }}</strong>
                </div>
                <div>
                  <span>Ticks</span>
                  <strong>{{ selectedNode.exploration.tick_count }}</strong>
                </div>
              </div>
              <p class="decision-line">
                {{ selectedNode.exploration.last_decision || 'Awaiting next planning cycle.' }}
              </p>
              <div class="explore-actions">
                <input v-model="explorationGoal" class="input-field" placeholder="wander / patrol / scout" />
                <button class="btn-primary" :disabled="busy" @click="startExploration">
                  Start Explore
                </button>
                <button class="btn-secondary" :disabled="busy" @click="stopExploration">
                  Stop Explore
                </button>
              </div>
            </div>
          </div>

          <div class="command-panel glass">
            <h3 class="card-title">Direct Actions</h3>
            <div class="command-grid">
              <button
                v-for="button in actionButtons"
                :key="button.label"
                class="command-button"
                :class="button.tone || 'neutral'"
                :disabled="busy"
                @click="invokeCommand(button)"
              >
                {{ button.label }}
              </button>
            </div>
          </div>

          <div class="interaction-grid">
            <div class="interaction-card glass">
              <h3 class="card-title">Natural Language</h3>
              <textarea
                v-model="freeformText"
                class="input-field multiline"
                placeholder="例如：站起来往前走，然后看左边"
              />
              <button class="btn-primary submit-button" :disabled="busy" @click="submitNlp">
                Send To Robot
              </button>
            </div>

            <div class="interaction-card glass">
              <h3 class="card-title">Robot Speech</h3>
              <textarea
                v-model="spokenText"
                class="input-field multiline"
                placeholder="让机器狗通过机载 TTS 说一句话"
              />
              <button class="btn-secondary submit-button" :disabled="busy" @click="submitSpeech">
                Speak
              </button>
            </div>
          </div>

          <div class="history-card glass">
            <h3 class="card-title">Autonomy Trace</h3>
            <div v-if="explorationHistory.length" class="history-list">
              <article v-for="entry in explorationHistory" :key="`${entry.timestamp}-${entry.detail}`">
                <span>{{ new Date(entry.timestamp * 1000).toLocaleTimeString() }}</span>
                <strong>{{ entry.command || entry.kind }}</strong>
                <p>{{ entry.detail }}</p>
              </article>
            </div>
            <p v-else class="history-empty">Exploration history will appear here once the node starts planning actions.</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.robotics-view {
  gap: var(--space-lg);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(var(--accent-rgb), 0.12);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-state {
  padding: var(--space-xl);
  max-width: 760px;
}

.empty-state p {
  color: var(--text-secondary);
}

.robotics-grid {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--space-lg);
  min-height: 0;
}

.robot-sidebar,
.robot-summary,
.sensor-card,
.command-panel,
.interaction-card,
.history-card {
  padding: var(--space-lg);
}

.robot-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.node-card {
  width: 100%;
  padding: 14px;
  text-align: left;
  background: rgba(8, 10, 18, 0.72);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.node-card.active {
  border-color: rgba(var(--accent-rgb), 0.28);
  box-shadow: 0 0 0 1px rgba(var(--accent-rgb), 0.12);
}

.node-card-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-sm);
  align-items: center;
}

.node-card p {
  color: var(--text-secondary);
  font-size: 12px;
}

.node-address {
  font-family: var(--font-mono);
  color: var(--text-dim);
}

.status-pill {
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.4px;
}

.status-pill.online {
  background: rgba(52, 211, 153, 0.12);
  color: var(--success);
}

.status-pill.offline {
  background: rgba(248, 113, 113, 0.12);
  color: var(--error);
}

.main-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

.robot-summary {
  display: flex;
  justify-content: space-between;
  gap: var(--space-xl);
  align-items: end;
}

.summary-copy h2 {
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 300;
  margin-bottom: 8px;
}

.summary-copy p {
  color: var(--text-secondary);
  max-width: 520px;
  font-family: var(--font-mono);
  font-size: 12px;
}

.section-label.compact {
  margin-bottom: 10px;
}

.summary-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-md);
  min-width: 300px;
}

.summary-metrics article {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: rgba(10, 12, 20, 0.48);
}

.summary-metrics span,
.sensor-meter span,
.sensor-readout span,
.explore-status span {
  display: block;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.3px;
  margin-bottom: 6px;
}

.summary-metrics strong,
.sensor-meter strong,
.sensor-readout strong,
.explore-status strong {
  font-size: 18px;
  font-weight: 500;
}

.sensor-grid,
.interaction-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-lg);
}

.sensor-card,
.interaction-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.sensor-meter {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.meter-track {
  width: 100%;
  height: 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  overflow: hidden;
}

.meter-fill {
  height: 100%;
  border-radius: inherit;
}

.meter-fill.accent {
  background: linear-gradient(90deg, rgba(var(--accent-rgb), 0.35), var(--accent));
}

.meter-fill.gold {
  background: linear-gradient(90deg, rgba(var(--gold-rgb), 0.35), rgba(var(--gold-rgb), 0.95));
}

.sensor-readout,
.explore-status {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.decision-line {
  color: var(--text-secondary);
  min-height: 44px;
  line-height: 1.7;
}

.explore-actions {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 12px;
  align-items: center;
}

.command-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.command-button {
  min-height: 52px;
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: rgba(8, 10, 18, 0.72);
  color: var(--text);
  font-family: var(--font-heading);
  font-size: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.command-button.accent {
  border-color: rgba(var(--accent-rgb), 0.18);
  background: rgba(var(--accent-rgb), 0.08);
}

.command-button.warning {
  border-color: rgba(248, 113, 113, 0.2);
  background: rgba(248, 113, 113, 0.08);
}

.command-button:disabled,
.submit-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.multiline {
  min-height: 96px;
  resize: vertical;
}

.submit-button {
  align-self: flex-start;
}

.history-list {
  display: grid;
  gap: 10px;
}

.history-list article {
  display: grid;
  grid-template-columns: 96px 110px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}

.history-list article:last-child {
  border-bottom: none;
}

.history-list span {
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
}

.history-list strong {
  text-transform: uppercase;
  letter-spacing: 1px;
  font-size: 12px;
}

.history-list p,
.history-empty {
  color: var(--text-secondary);
}

@media (max-width: 1200px) {
  .robotics-grid {
    grid-template-columns: 1fr;
  }

  .robot-sidebar {
    order: 2;
  }
}

@media (max-width: 900px) {
  .robot-summary,
  .sensor-grid,
  .interaction-grid,
  .explore-actions,
  .command-grid,
  .history-list article {
    grid-template-columns: 1fr;
  }

  .summary-metrics,
  .sensor-readout,
  .explore-status {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
