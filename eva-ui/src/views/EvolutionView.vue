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
  <div class="page-view evolution-view">
    <div class="page-header">
      <div class="section-label">Self-modification</div>
      <h1 class="page-title">Evolution</h1>
      <p class="page-subtitle">Track directed mutations, fitness navigation, and governance across generation cycles.</p>
    </div>

    <div class="evo-grid">
      <!-- Left: DNA Helix -->
      <div class="evo-left">
        <div class="helix-container glass">
          <h3 class="card-title">DNA Helix</h3>
          <canvas v-if="enable3D" ref="helixCanvas" width="350" height="500" />
          <div v-else class="helix-fallback">
            <div v-for="(node, i) in evolutionNodes.slice(-10)" :key="i" class="fallback-node">
              <span class="node-dot" :class="node.status" />
              <span class="node-title">{{ node.title }}</span>
            </div>
            <div v-if="evolutionNodes.length === 0" class="empty-msg">No evolution history</div>
          </div>
        </div>
      </div>

      <!-- Center: Panel + History -->
      <div class="evo-center">
        <EvolutionPanel :status="status" />

        <!-- History -->
        <div class="history-card glass">
          <h3 class="card-title">History</h3>
          <div class="history-list">
            <div v-for="s in (history.successes || []).slice(-8)" :key="s.title" class="history-item">
              <span class="status-dot success" />
              <span class="item-title">{{ s.title }}</span>
            </div>
            <div v-for="f in (history.failures || []).slice(-5)" :key="f.title" class="history-item">
              <span class="status-dot error" />
              <span class="item-title">{{ f.title }}</span>
            </div>
            <div v-if="!(history.successes?.length || history.failures?.length)" class="empty-msg">
              No entries yet
            </div>
          </div>
        </div>

        <!-- Goals -->
        <div v-if="history.goals?.length" class="goals-card glass">
          <h3 class="card-title">Goals</h3>
          <div v-for="g in history.goals" :key="g.title" class="goal-item">
            <div class="goal-info">
              <span class="goal-title">{{ g.title }}</span>
              <div class="goal-bar">
                <div class="goal-fill" :style="{ width: `${(g.progress || 0) * 100}%` }" />
              </div>
            </div>
            <span class="goal-pct">{{ ((g.progress || 0) * 100).toFixed(0) }}%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Governance -->
    <GovernanceBar :governance="governance" @change-mode="changeMode" />
  </div>
</template>

<style scoped>
.evolution-view {
  gap: var(--space-lg);
}

.evo-grid {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: var(--space-lg);
  flex: 1;
}

.evo-left {
  display: flex;
  flex-direction: column;
}

.evo-center {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

.helix-container {
  padding: var(--space-lg);
  display: flex;
  flex-direction: column;
  align-items: center;
}

.helix-fallback {
  width: 100%;
}

.fallback-node {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--border);
}

.fallback-node:last-child { border-bottom: none; }

.node-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.node-dot.success { background: var(--success); box-shadow: 0 0 6px rgba(52, 211, 153, 0.3); }
.node-dot.failed { background: var(--error); box-shadow: 0 0 6px rgba(248, 113, 113, 0.3); }
.node-dot.rolled_back { background: var(--warning); box-shadow: 0 0 6px rgba(251, 191, 36, 0.3); }

.node-title {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-card, .goals-card { padding: var(--space-lg); }

.history-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  transition: background var(--transition-fast);
}

.history-item:hover {
  background: rgba(var(--accent-rgb), 0.03);
}

.item-title {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.goal-item {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.goal-item:last-child { border-bottom: none; }

.goal-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.goal-title { font-size: 13px; color: var(--text); }

.goal-bar {
  height: 3px;
  border-radius: 2px;
  background: rgba(var(--accent-rgb), 0.1);
  overflow: hidden;
}

.goal-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width 0.5s ease;
}

.goal-pct {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent);
  min-width: 36px;
  text-align: right;
}

.empty-msg {
  color: var(--text-dim);
  font-size: 13px;
  text-align: center;
  padding: var(--space-lg);
}

@media (max-width: 900px) {
  .evo-grid { grid-template-columns: 1fr; }
}
</style>
