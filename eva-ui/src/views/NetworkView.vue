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
  <div class="network-view">
    <div class="network-grid">
      <!-- Main: Topology -->
      <div class="network-main glass">
        <h3 class="section-title">节点拓扑</h3>
        <div v-if="!networkEnabled" class="disabled-notice">
          分布式网络未启用
        </div>
        <NodeTopology v-else :nodes="nodes" @select="(n: any) => selectedNode = n" />
      </div>

      <!-- Right: Node detail -->
      <div class="network-sidebar" v-if="selectedNode">
        <div class="node-detail glass">
          <h3 class="section-title">节点详情</h3>
          <div class="detail-row">
            <span class="detail-label">ID</span>
            <span class="detail-value">{{ selectedNode.node_id }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">主机名</span>
            <span class="detail-value">{{ selectedNode.hostname }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">IP</span>
            <span class="detail-value">{{ selectedNode.ip }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">状态</span>
            <span class="detail-value" :class="selectedNode.status">{{ selectedNode.status }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">负载</span>
            <span class="detail-value">{{ (selectedNode.current_load * 100).toFixed(0) }}%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom: Channels -->
    <div class="channels-section glass">
      <h3 class="section-title">渠道状态</h3>
      <ChannelCards :channels="channels" />
    </div>
  </div>
</template>

<style scoped>
.network-view { height: 100%; display: flex; flex-direction: column; padding: 16px; gap: 16px; overflow-y: auto; }
.network-grid { display: grid; grid-template-columns: 1fr 300px; gap: 16px; flex: 1; min-height: 0; }
.network-main { padding: 16px; }
.network-sidebar { display: flex; flex-direction: column; gap: 16px; }
.section-title { font-size: 14px; font-weight: 500; color: var(--eva-ice); margin-bottom: 12px; letter-spacing: 1px; }
.disabled-notice { text-align: center; padding: 40px; color: var(--eva-text-dim); font-size: 14px; }
.node-detail { padding: 16px; }
.detail-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid hsla(200, 20%, 20%, 0.2); font-size: 13px; }
.detail-label { color: var(--eva-text-dim); }
.detail-value { color: var(--eva-text); }
.detail-value.alive { color: #44cc66; }
.detail-value.dead { color: #cc4444; }
.channels-section { padding: 16px; flex-shrink: 0; }
@media (max-width: 900px) { .network-grid { grid-template-columns: 1fr; } }
</style>
