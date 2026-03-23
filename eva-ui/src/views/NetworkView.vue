<script setup lang="ts">
import { ref, onMounted } from 'vue'
import NodeTopology from '@/components/network/NodeTopology.vue'
import ChannelCards from '@/components/network/ChannelCards.vue'
import * as api from '@/api/network'

const nodes = ref<any[]>([])
const channels = ref<any[]>([])
const selectedNode = ref<any>(null)
const networkEnabled = ref(false)
const loading = ref(true)

async function loadData() {
  loading.value = true
  try {
    const [nodesRes, channelsRes] = await Promise.all([api.getNodes(), api.getChannels()])
    networkEnabled.value = nodesRes.data.enabled
    nodes.value = nodesRes.data.nodes || []
    channels.value = channelsRes.data.channels || []
  } catch (e) {
    console.error('Network load failed:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="page-view network-view">
    <div class="page-header">
      <div class="section-label">Live topology</div>
      <h1 class="page-title">Network</h1>
      <p class="page-subtitle">Visualize distributed cognitive nodes exchanging signals across the neural mesh.</p>
    </div>

    <div class="network-grid">
      <!-- Main: Topology -->
      <div class="network-main glass">
        <h3 class="card-title">Node Topology</h3>
        <div v-if="!networkEnabled" class="disabled-notice">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="notice-icon">
            <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
          </svg>
          <p>Distributed network is not enabled</p>
          <p class="notice-hint">Enable in config: network.enabled = true</p>
        </div>
        <NodeTopology v-else :nodes="nodes" @select="(n: any) => selectedNode = n" />
      </div>

      <!-- Right: Node detail -->
      <div class="network-sidebar" v-if="selectedNode">
        <div class="node-detail glass">
          <h3 class="card-title">Node Details</h3>
          <div class="detail-list">
            <div class="detail-row">
              <span class="detail-label">ID</span>
              <span class="detail-value mono">{{ selectedNode.node_id }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Hostname</span>
              <span class="detail-value">{{ selectedNode.hostname }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">IP</span>
              <span class="detail-value mono">{{ selectedNode.ip }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value">
                <span class="status-dot" :class="selectedNode.status === 'alive' ? 'success' : selectedNode.status === 'dead' ? 'error' : 'warning'" />
                {{ selectedNode.status }}
              </span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Load</span>
              <span class="detail-value mono">{{ ((selectedNode.current_load || 0) * 100).toFixed(0) }}%</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom: Channels -->
    <div class="channels-section glass">
      <h3 class="card-title">Channels</h3>
      <ChannelCards :channels="channels" />
    </div>
  </div>
</template>

<style scoped>
.network-view {
  gap: var(--space-lg);
}

.network-grid {
  display: grid;
  grid-template-columns: 1fr 300px;
  gap: var(--space-lg);
  flex: 1;
  min-height: 0;
}

.network-main { padding: var(--space-lg); }

.disabled-notice {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-secondary);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-sm);
}

.notice-icon { color: var(--text-dim); margin-bottom: var(--space-sm); }
.notice-hint { font-size: 12px; color: var(--text-dim); font-family: var(--font-mono); }

.network-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

.node-detail { padding: var(--space-lg); }

.detail-list {
  display: flex;
  flex-direction: column;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.detail-row:last-child { border-bottom: none; }

.detail-label { color: var(--text-secondary); }

.detail-value {
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
}

.detail-value.mono { font-family: var(--font-mono); font-size: 12px; }

.channels-section { padding: var(--space-lg); flex-shrink: 0; }

@media (max-width: 900px) {
  .network-grid { grid-template-columns: 1fr; }
}
</style>
