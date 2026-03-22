<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { usePlatform } from '@/composables/usePlatform'
import { sceneManager } from '@/three/SceneManager'
import { createDNAHelixScene } from '@/three/DNAHelixScene'
import type { EvolutionNode } from '@/three/DNAHelixScene'
import EvolutionPanel from '@/components/evolution/EvolutionPanel.vue'
import GovernanceBar from '@/components/evolution/GovernanceBar.vue'
import * as api from '@/api/evolution'

const { enable3D } = usePlatform()
const helixCanvas = ref<HTMLCanvasElement>()
const status = ref<any>({})
const history = ref<any>({ successes: [], failures: [] })
const governance = ref<any>({})
const loading = ref(true)

const evolutionNodes = ref<EvolutionNode[]>([])

async function loadData() {
  loading.value = true
  try {
    const [statusRes, historyRes, govRes] = await Promise.all([
      api.getStatus(),
      api.getHistory(),
      api.getGovernance(),
    ])
    status.value = statusRes.data
    history.value = historyRes.data
    governance.value = govRes.data

    // Build nodes for DNA helix
    const nodes: EvolutionNode[] = []
    for (const s of (historyRes.data.successes || []).slice(-15)) {
      nodes.push({ title: s.title || '', status: 'success', files: s.files || [], timestamp: s.timestamp || 0 })
    }
    for (const f of (historyRes.data.failures || []).slice(-5)) {
      nodes.push({ title: f.title || '', status: 'failed', files: [], timestamp: f.timestamp || 0 })
    }
    nodes.sort((a, b) => a.timestamp - b.timestamp)
    evolutionNodes.value = nodes
  } catch (e) {
    console.error('Evolution load failed:', e)
  } finally {
    loading.value = false
  }
}

async function changeMode(mode: string) {
  try {
    await api.updateGovernanceMode(mode)
    governance.value.activity_level = mode
  } catch (e) {
    console.error('Mode change failed:', e)
  }
}

onMounted(async () => {
  await loadData()
  if (enable3D && helixCanvas.value) {
    sceneManager.register('dnaHelix', helixCanvas.value, (renderer) => {
      return createDNAHelixScene(renderer, () => evolutionNodes.value)
    })
    sceneManager.activate('dnaHelix')
  }
})

onUnmounted(() => {
  sceneManager.dispose('dnaHelix')
})
</script>

<template>
  <div class="evolution-view">
    <div class="evo-grid">
      <!-- Left: DNA Helix -->
      <div class="evo-left">
        <div class="helix-container glass">
          <h3 class="section-title">进化 DNA</h3>
          <canvas v-if="enable3D" ref="helixCanvas" width="350" height="500" />
          <div v-else class="helix-fallback">
            <div v-for="(node, i) in evolutionNodes.slice(-10)" :key="i" class="fallback-node">
              <span class="node-status" :class="node.status">●</span>
              <span class="node-title">{{ node.title }}</span>
            </div>
            <div v-if="evolutionNodes.length === 0" class="empty">无进化历史</div>
          </div>
        </div>
      </div>

      <!-- Center: Evolution Panel -->
      <div class="evo-center">
        <EvolutionPanel :status="status" />

        <!-- History -->
        <div class="history-section glass">
          <h3 class="section-title">历史记录</h3>
          <div class="history-list">
            <div v-for="s in (history.successes || []).slice(-8)" :key="s.title" class="history-item success">
              <span class="item-icon">✅</span>
              <span class="item-title">{{ s.title }}</span>
            </div>
            <div v-for="f in (history.failures || []).slice(-5)" :key="f.title" class="history-item failed">
              <span class="item-icon">❌</span>
              <span class="item-title">{{ f.title }}</span>
            </div>
          </div>
        </div>

        <!-- Goals -->
        <div v-if="history.goals?.length" class="goals-section glass">
          <h3 class="section-title">进化目标</h3>
          <div v-for="g in history.goals" :key="g.title" class="goal-item">
            <span class="goal-title">{{ g.title }}</span>
            <span class="goal-progress">{{ ((g.progress || 0) * 100).toFixed(0) }}%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom: Governance -->
    <GovernanceBar :governance="governance" @change-mode="changeMode" />
  </div>
</template>

<style scoped>
.evolution-view { height: 100%; display: flex; flex-direction: column; padding: 16px; gap: 16px; overflow-y: auto; }
.evo-grid { display: grid; grid-template-columns: 380px 1fr; gap: 16px; flex: 1; }
.evo-left { display: flex; flex-direction: column; }
.evo-center { display: flex; flex-direction: column; gap: 16px; }
.helix-container { padding: 16px; display: flex; flex-direction: column; align-items: center; }
.section-title { font-size: 14px; font-weight: 500; color: var(--eva-ice); margin-bottom: 12px; letter-spacing: 1px; }
.helix-fallback { width: 100%; }
.fallback-node { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 13px; }
.node-status { font-size: 10px; }
.node-status.success { color: #44cc66; }
.node-status.failed { color: #cc4444; }
.node-status.rolled_back { color: #cc8844; }
.node-title { color: var(--eva-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-section, .goals-section { padding: 16px; }
.history-list { display: flex; flex-direction: column; gap: 6px; max-height: 300px; overflow-y: auto; }
.history-item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px; font-size: 13px; }
.history-item.success { background: hsla(140, 30%, 15%, 0.2); }
.history-item.failed { background: hsla(0, 30%, 15%, 0.2); }
.item-icon { font-size: 12px; }
.item-title { color: var(--eva-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.goal-item { display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; }
.goal-title { color: var(--eva-text); }
.goal-progress { color: var(--eva-ice); }
.empty { color: var(--eva-text-dim); font-size: 13px; text-align: center; padding: 20px; }
@media (max-width: 900px) { .evo-grid { grid-template-columns: 1fr; } }
</style>
