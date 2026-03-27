<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import NodeTopology from '@/components/network/NodeTopology.vue'
import ChannelCards from '@/components/network/ChannelCards.vue'
import {
  getChannels,
  getNodeConversation,
  getNodes,
  sendNodeMessage,
  type ChannelSnapshot,
  type NetworkNode,
  type RemoteConversationMessage,
} from '@/api/network'
import {
  getRobotNode,
  sendRobotCommand,
  type RobotNode,
} from '@/api/robotics'

interface RobotQuickAction {
  label: string
  mode: 'command' | 'scan'
  command?: string
  params?: Record<string, unknown>
  tone?: 'neutral' | 'accent'
}

const nodeMap = ref<NetworkNode[]>([])
const channels = ref<ChannelSnapshot[]>([])
const selectedNodeId = ref('')
const messages = ref<RemoteConversationMessage[]>([])
const robotDetail = ref<RobotNode | null>(null)
const networkEnabled = ref(false)
const aliveCount = ref(0)
const loading = ref(true)
const sending = ref(false)
const robotBusy = ref(false)
const composerText = ref('')
const errorText = ref('')
const messageListRef = ref<HTMLDivElement>()
let refreshHandle: number | undefined

const promptChips = ['站起来', '坐下', '看看周围', '描述你现在的状态']
const robotActions: RobotQuickAction[] = [
  { label: 'Stand', mode: 'command', command: 'stand', tone: 'accent' },
  { label: 'Sit', mode: 'command', command: 'sit' },
  { label: 'Look Left', mode: 'command', command: 'look_left' },
  { label: 'Look Right', mode: 'command', command: 'look_right' },
  { label: 'Center', mode: 'command', command: 'center_head' },
  { label: 'Scan Around', mode: 'scan', tone: 'accent' },
]

const selectedNode = computed<NetworkNode | null>(() => {
  if (!selectedNodeId.value) {
    return nodeMap.value[0] ?? null
  }
  return nodeMap.value.find((node) => node.node_id === selectedNodeId.value) ?? nodeMap.value[0] ?? null
})

const selectedRobotLink = computed(() => selectedNode.value?.robotics?.available ? selectedNode.value.robotics : null)
const selectedNodeName = computed(() =>
  selectedNode.value?.hostname || selectedNode.value?.agent_name || selectedNode.value?.node_id || 'Remote node',
)
const embodiedCount = computed(() => nodeMap.value.filter((node) => node.embodiment !== 'virtual').length)
const selectedStatusClass = computed(() => {
  switch (selectedNode.value?.status) {
    case 'alive':
      return 'online'
    case 'dead':
      return 'offline'
    default:
      return 'degraded'
  }
})
const robotDistance = computed(() => robotDetail.value?.perception.distance_cm ?? 0)
const robotBattery = computed(() => robotDetail.value?.perception.battery_v ?? 0)
const distancePercent = computed(() => Math.max(0, Math.min(100, (robotDistance.value / 120) * 100)))
const batteryPercent = computed(() => {
  if (robotBattery.value <= 0) return 0
  return Math.max(0, Math.min(100, ((robotBattery.value - 5.5) / 2) * 100))
})

function humanize(value: string | undefined) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (segment) => segment.toUpperCase())
}

function formatTime(timestamp: number) {
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatLoad(value: number) {
  return `${Math.round((value || 0) * 100)}%`
}

function formatUptime(totalSeconds: number) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  return `${minutes}m`
}

function statusLabel(node: NetworkNode) {
  if (node.is_self) return 'This node'
  if (node.status === 'alive') return 'Online'
  if (node.status === 'dead') return 'Offline'
  return 'Degraded'
}

function scrollMessagesToBottom() {
  if (!messageListRef.value) return
  messageListRef.value.scrollTop = messageListRef.value.scrollHeight
}

async function loadNetworkWorkspace() {
  const [nodesRes, channelsRes] = await Promise.all([getNodes(), getChannels()])
  networkEnabled.value = nodesRes.data.enabled
  aliveCount.value = nodesRes.data.alive_count || 0
  nodeMap.value = nodesRes.data.nodes || []
  channels.value = channelsRes.data.channels || []

  if (!selectedNodeId.value && nodeMap.value.length > 0) {
    selectedNodeId.value = nodeMap.value[0].node_id
  }

  if (selectedNodeId.value && !nodeMap.value.some((node) => node.node_id === selectedNodeId.value)) {
    selectedNodeId.value = nodeMap.value[0]?.node_id ?? ''
  }

  if (!selectedNodeId.value) {
    messages.value = []
    robotDetail.value = null
  }
}

async function loadConversation() {
  if (!selectedNode.value) {
    messages.value = []
    return
  }
  const response = await getNodeConversation(selectedNode.value.node_id, 80)
  messages.value = response.data.messages || []
}

async function loadRobotDetail(refresh = false) {
  const robotNodeId = selectedRobotLink.value?.node_id
  if (!robotNodeId) {
    robotDetail.value = null
    return
  }
  const response = await getRobotNode(robotNodeId, refresh)
  robotDetail.value = response.data
}

async function bootstrapWorkspace() {
  loading.value = true
  errorText.value = ''
  try {
    await loadNetworkWorkspace()
    await Promise.all([loadConversation(), loadRobotDetail(true)])
  } catch (error) {
    console.error('Network workspace load failed:', error)
    errorText.value = 'Unable to load network workspace.'
  } finally {
    loading.value = false
  }
}

async function submitMessage(prefill?: string) {
  if (!selectedNode.value || sending.value) return

  const text = (prefill ?? composerText.value).trim()
  if (!text) return

  errorText.value = ''
  sending.value = true
  composerText.value = ''
  messages.value = [
    ...messages.value,
    {
      role: 'user',
      content: text,
      timestamp: Date.now() / 1000,
      transport: 'gossip',
    },
  ]
  await nextTick()
  scrollMessagesToBottom()

  try {
    const response = await sendNodeMessage(selectedNode.value.node_id, text, 90)
    messages.value = response.data.conversation || []
    await loadRobotDetail(true)
  } catch (error: any) {
    console.error('Remote node message failed:', error)
    errorText.value = error?.response?.data?.error || error?.message || 'Remote node reply failed.'
    await loadConversation()
  } finally {
    sending.value = false
  }
}

async function runRobotAction(action: RobotQuickAction) {
  const robotNodeId = selectedRobotLink.value?.node_id
  if (!robotNodeId || robotBusy.value) return

  robotBusy.value = true
  errorText.value = ''
  try {
    if (action.mode === 'scan') {
      await sendRobotCommand(robotNodeId, 'look_left')
      await sendRobotCommand(robotNodeId, 'look_right')
      await sendRobotCommand(robotNodeId, 'center_head')
    } else {
      await sendRobotCommand(robotNodeId, action.command || '', action.params || {})
    }
    await Promise.all([loadRobotDetail(true), loadNetworkWorkspace()])
  } catch (error: any) {
    console.error('Robot quick action failed:', error)
    errorText.value = error?.response?.data?.error || error?.message || 'Robot action failed.'
  } finally {
    robotBusy.value = false
  }
}

watch(selectedNodeId, async () => {
  errorText.value = ''
  await Promise.all([loadConversation(), loadRobotDetail(true)])
})

watch(() => messages.value.length, async () => {
  await nextTick()
  scrollMessagesToBottom()
})

onMounted(async () => {
  await bootstrapWorkspace()
  refreshHandle = window.setInterval(() => {
    void (async () => {
      try {
        await loadNetworkWorkspace()
        await Promise.all([loadConversation(), loadRobotDetail(false)])
      } catch (error) {
        console.error('Network workspace refresh failed:', error)
      }
    })()
  }, 3500)
})

onBeforeUnmount(() => {
  if (refreshHandle !== undefined) {
    window.clearInterval(refreshHandle)
  }
})
</script>

<template>
  <div class="page-view network-view">
    <div class="page-header">
      <div class="section-label">Node Workbench</div>
      <h1 class="page-title">Distributed EVA</h1>
      <p class="page-subtitle">
        See edge nodes on Tailscale or the same LAN, talk to the EVA running on them,
        and directly drive embodied PiDog nodes from the desktop.
      </p>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
    </div>

    <div v-else-if="!networkEnabled" class="empty-state glass">
      <h3 class="card-title">Network Offline</h3>
      <p>Enable `network.enabled` to discover EVA nodes and route remote conversation across the mesh.</p>
    </div>

    <div v-else class="network-shell">
      <aside class="network-rail glass">
        <div class="rail-header">
          <div>
            <div class="section-label compact">Presence</div>
            <h3 class="card-title">Active Nodes</h3>
          </div>
          <div class="rail-stats">
            <article>
              <span>Alive</span>
              <strong>{{ aliveCount }}</strong>
            </article>
            <article>
              <span>Embodied</span>
              <strong>{{ embodiedCount }}</strong>
            </article>
          </div>
        </div>

        <div class="node-list">
          <button
            v-for="node in nodeMap"
            :key="node.node_id"
            class="node-card"
            :class="{ active: selectedNode?.node_id === node.node_id }"
            @click="selectedNodeId = node.node_id"
          >
            <div class="node-card-head">
              <strong>{{ node.hostname || node.agent_name || node.node_id }}</strong>
              <span class="status-pill" :class="node.status === 'alive' ? 'online' : node.status === 'dead' ? 'offline' : 'degraded'">
                {{ statusLabel(node) }}
              </span>
            </div>
            <p>{{ humanize(node.runtime_role) }} · {{ humanize(node.embodiment) }}</p>
            <p class="node-address">{{ node.reachability.label }} · {{ node.reachability.address || 'no address' }}</p>
            <p v-if="node.robotics.available" class="node-tag">PiDog direct control ready</p>
          </button>
        </div>
      </aside>

      <section class="conversation-stage">
        <div v-if="selectedNode" class="stage-stack">
          <div class="selected-node glass">
            <div class="selected-copy">
              <div class="section-label compact">Selected Node</div>
              <h2>{{ selectedNodeName }}</h2>
              <p>
                {{ humanize(selectedNode.runtime_profile) }} ·
                {{ selectedNode.reachability.label }} ·
                {{ selectedNode.reachability.address || selectedNode.node_id }}
              </p>
            </div>
            <div class="selected-metrics">
              <article>
                <span>Status</span>
                <strong :class="selectedStatusClass">{{ humanize(selectedNode.status) }}</strong>
              </article>
              <article>
                <span>Load</span>
                <strong>{{ formatLoad(selectedNode.current_load) }}</strong>
              </article>
              <article>
                <span>Uptime</span>
                <strong>{{ formatUptime(selectedNode.uptime_s) }}</strong>
              </article>
            </div>
          </div>

          <div class="prompt-strip">
            <button
              v-for="prompt in promptChips"
              :key="prompt"
              class="prompt-chip"
              :disabled="sending || !selectedNode.chat_available"
              @click="submitMessage(prompt)"
            >
              {{ prompt }}
            </button>
          </div>

          <div ref="messageListRef" class="message-surface glass">
            <div v-if="messages.length === 0" class="empty-conversation">
              <div class="empty-conversation-orb" />
              <h3>Talk to {{ selectedNodeName }}</h3>
              <p>Send a natural-language request like “站起来” or “看看周围”，the remote EVA will reason on its own node.</p>
            </div>

            <article
              v-for="(message, index) in messages"
              :key="`${message.timestamp}-${index}`"
              class="message-row"
              :class="message.role"
            >
              <div class="message-meta">
                <span>{{ message.role === 'assistant' ? selectedNodeName : message.role === 'system' ? 'System' : 'You' }}</span>
                <span>{{ formatTime(message.timestamp) }}</span>
              </div>
              <p>{{ message.content }}</p>
            </article>
          </div>

          <div class="composer glass">
            <textarea
              v-model="composerText"
              class="input-field composer-input"
              :disabled="sending || !selectedNode.chat_available"
              placeholder="对远端 EVA 说点什么，例如：站起来后看看周围，然后告诉我你现在的状态"
              @keydown.enter.exact.prevent="submitMessage()"
            />
            <div class="composer-actions">
              <p v-if="errorText" class="error-line">{{ errorText }}</p>
              <button
                class="btn-primary"
                :disabled="sending || !composerText.trim() || !selectedNode.chat_available"
                @click="submitMessage()"
              >
                {{ sending ? 'Sending...' : 'Message Node' }}
              </button>
            </div>
          </div>
        </div>
      </section>

      <aside class="network-context">
        <div class="topology-panel glass">
          <div class="context-head">
            <div>
              <div class="section-label compact">Mesh</div>
              <h3 class="card-title">Topology</h3>
            </div>
            <p>{{ aliveCount }} live node<span v-if="aliveCount !== 1">s</span></p>
          </div>
          <NodeTopology
            :nodes="nodeMap"
            :selected-node-id="selectedNode?.node_id"
            @select="(node) => selectedNodeId = node.node_id"
          />
        </div>

        <div v-if="selectedNode" class="detail-panel glass">
          <div class="context-head">
            <div>
              <div class="section-label compact">Routing</div>
              <h3 class="card-title">Reachability</h3>
            </div>
            <p>{{ selectedNode.reachability.label }}</p>
          </div>

          <div class="detail-list">
            <div class="detail-row">
              <span>Role</span>
              <strong>{{ humanize(selectedNode.runtime_role) }}</strong>
            </div>
            <div class="detail-row">
              <span>Embodiment</span>
              <strong>{{ humanize(selectedNode.embodiment) }}</strong>
            </div>
            <div class="detail-row">
              <span>Platform</span>
              <strong>{{ selectedNode.platform_class || 'n/a' }}</strong>
            </div>
            <div class="detail-row">
              <span>Sessions</span>
              <strong>{{ selectedNode.active_sessions.length }}</strong>
            </div>
          </div>

          <div v-if="selectedNode.robotics.available" class="address-block">
            <div class="address-head">
              <span class="section-label compact">Robot Direct Links</span>
              <strong>{{ selectedNode.robotics.current_transport_label || 'Connected' }}</strong>
            </div>
            <div class="address-list">
              <article
                v-for="address in selectedNode.robotics.addresses || []"
                :key="address.url"
                class="address-card"
                :class="{ active: address.active }"
              >
                <span>{{ address.label }}</span>
                <strong>{{ address.url }}</strong>
              </article>
            </div>
          </div>
        </div>

        <div v-if="selectedRobotLink" class="robot-panel glass">
          <div class="context-head">
            <div>
              <div class="section-label compact">Embodied Ops</div>
              <h3 class="card-title">PiDog Control</h3>
            </div>
            <p>{{ selectedRobotLink.connected ? 'Desktop direct link active' : 'Using cached node link' }}</p>
          </div>

          <div v-if="robotDetail" class="robot-grid">
            <div class="sensor-card">
              <span>State</span>
              <strong>{{ robotDetail.state }}</strong>
            </div>
            <div class="sensor-card">
              <span>Emotion</span>
              <strong>{{ robotDetail.emotion }}</strong>
            </div>
            <div class="sensor-card wide">
              <div class="meter-head">
                <span>Distance</span>
                <strong>{{ robotDistance.toFixed(1) }} cm</strong>
              </div>
              <div class="meter-track"><div class="meter-fill accent" :style="{ width: `${distancePercent}%` }" /></div>
            </div>
            <div class="sensor-card wide">
              <div class="meter-head">
                <span>Battery</span>
                <strong>{{ robotBattery > 0 ? `${robotBattery.toFixed(2)} V` : 'n/a' }}</strong>
              </div>
              <div class="meter-track"><div class="meter-fill gold" :style="{ width: `${batteryPercent}%` }" /></div>
            </div>
            <div class="sensor-card">
              <span>Touch</span>
              <strong>{{ robotDetail.perception.touch }}</strong>
            </div>
            <div class="sensor-card">
              <span>Lifted</span>
              <strong>{{ robotDetail.perception.is_lifted ? 'yes' : 'no' }}</strong>
            </div>
          </div>

          <div class="robot-actions">
            <button
              v-for="action in robotActions"
              :key="action.label"
              class="robot-action"
              :class="action.tone || 'neutral'"
              :disabled="robotBusy"
              @click="runRobotAction(action)"
            >
              {{ action.label }}
            </button>
          </div>
        </div>

        <div class="channels-panel glass">
          <div class="context-head">
            <div>
              <div class="section-label compact">External</div>
              <h3 class="card-title">Channels</h3>
            </div>
          </div>
          <ChannelCards :channels="channels" />
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.network-view {
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

.network-shell {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr) 360px;
  gap: var(--space-lg);
  min-height: 0;
}

.network-rail,
.selected-node,
.message-surface,
.composer,
.topology-panel,
.detail-panel,
.robot-panel,
.channels-panel {
  padding: var(--space-lg);
}

.network-rail {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.rail-header,
.context-head,
.selected-node,
.composer-actions,
.node-card-head,
.address-head,
.meter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
}

.rail-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  min-width: 150px;
}

.rail-stats article,
.selected-metrics article {
  padding: 10px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: rgba(10, 12, 20, 0.48);
}

.rail-stats span,
.selected-metrics span,
.detail-row span,
.sensor-card span,
.address-card span {
  display: block;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  margin-bottom: 6px;
}

.rail-stats strong,
.selected-metrics strong,
.detail-row strong,
.sensor-card strong,
.address-card strong {
  font-size: 16px;
  font-weight: 500;
}

.node-list,
.stage-stack,
.network-context,
.address-list {
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

.node-card p {
  color: var(--text-secondary);
  font-size: 12px;
}

.node-address,
.selected-copy p,
.address-card strong {
  font-family: var(--font-mono);
  font-size: 12px;
}

.node-tag {
  color: rgba(var(--gold-rgb), 0.95);
}

.status-pill {
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.3px;
}

.status-pill.online,
.online {
  color: var(--success);
}

.status-pill.online {
  background: rgba(52, 211, 153, 0.12);
}

.status-pill.offline,
.offline {
  color: var(--error);
}

.status-pill.offline {
  background: rgba(248, 113, 113, 0.12);
}

.status-pill.degraded,
.degraded {
  color: rgba(var(--gold-rgb), 0.95);
}

.status-pill.degraded {
  background: rgba(var(--gold-rgb), 0.12);
}

.conversation-stage {
  min-width: 0;
}

.selected-copy h2 {
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 300;
  margin-bottom: 8px;
}

.selected-copy p,
.context-head p {
  color: var(--text-secondary);
}

.selected-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  min-width: 320px;
}

.prompt-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.prompt-chip {
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  background: rgba(var(--accent-rgb), 0.06);
  color: var(--text);
  font-size: 12px;
}

.prompt-chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.message-surface {
  min-height: 420px;
  max-height: 560px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-conversation {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 10px;
  color: var(--text-secondary);
  padding: 32px;
}

.empty-conversation h3 {
  font-family: var(--font-heading);
  font-size: 16px;
  color: var(--text);
}

.empty-conversation-orb {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background:
    radial-gradient(circle at 35% 35%, rgba(var(--accent-rgb), 0.5), rgba(0, 120, 100, 0.55)),
    rgba(255, 255, 255, 0.04);
  box-shadow: 0 0 32px rgba(var(--accent-rgb), 0.16);
}

.message-row {
  max-width: 92%;
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid var(--border);
  background: rgba(10, 12, 20, 0.5);
  align-self: flex-start;
}

.message-row.user {
  align-self: flex-end;
  background: rgba(var(--accent-rgb), 0.08);
  border-color: rgba(var(--accent-rgb), 0.16);
}

.message-row.system {
  border-color: rgba(248, 113, 113, 0.2);
  background: rgba(248, 113, 113, 0.08);
}

.message-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  margin-bottom: 8px;
}

.message-row p {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--text);
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.composer-input {
  min-height: 108px;
  resize: vertical;
}

.error-line {
  color: var(--error);
  font-size: 12px;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.detail-row:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.address-block,
.robot-grid,
.robot-actions {
  margin-top: var(--space-md);
}

.address-list {
  gap: 10px;
}

.address-card {
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: rgba(10, 12, 20, 0.46);
}

.address-card.active {
  border-color: rgba(var(--accent-rgb), 0.22);
}

.robot-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.sensor-card {
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: rgba(10, 12, 20, 0.46);
}

.sensor-card.wide {
  grid-column: span 2;
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

.robot-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.robot-action {
  min-height: 46px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: rgba(8, 10, 18, 0.72);
  color: var(--text);
  font-family: var(--font-heading);
  font-size: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.robot-action.accent {
  border-color: rgba(var(--accent-rgb), 0.18);
  background: rgba(var(--accent-rgb), 0.08);
}

.robot-action:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

@media (max-width: 1320px) {
  .network-shell {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .network-context {
    grid-column: span 2;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-lg);
  }
}

@media (max-width: 980px) {
  .network-shell,
  .network-context,
  .selected-node,
  .selected-metrics,
  .robot-grid,
  .robot-actions {
    grid-template-columns: 1fr;
  }

  .network-shell {
    display: flex;
    flex-direction: column;
  }

  .selected-node,
  .composer-actions {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
