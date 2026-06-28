<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Stratum from '../components/Stratum.vue'
import { getNodes, getChannels, type MeshNode } from '../api/facets'

const enabled = ref(true)
const aliveCount = ref(0)
const nodes = ref<MeshNode[]>([])
const channels = ref<{ name: string; connected: boolean; type: string }[]>([])
const err = ref(false)

const nodeSev = (s: string) => s === 'alive' ? 's-ok' : s === 'dead' ? 's-crit' : 's-warn'
function uptime(s: number): string {
  if (!s) return ''
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h${m}m` : `${m}m`
}

onMounted(async () => {
  try {
    const [n, c] = await Promise.all([getNodes(), getChannels()])
    enabled.value = n.enabled; aliveCount.value = n.alive_count; nodes.value = n.nodes || []
    channels.value = c || []
  } catch { err.value = true }
})
</script>

<template>
  <Stratum line="分布式的" line-bold="她">
    <p v-if="err" class="s-note">没接上网格。</p>
    <template v-else>
      <div class="s-head">节点 · {{ aliveCount }} alive{{ enabled ? '' : ' · mesh off' }}</div>
      <div class="s-rows">
        <div v-for="n in nodes" :key="n.node_id" class="s-row">
          <span class="s-lab">{{ n.hostname || n.node_id.slice(0, 10) }}<template v-if="n.is_self"> ·me</template></span>
          <span class="s-val">{{ n.runtime_role || n.embodiment }} · {{ n.reachability?.label }} <span class="s-note">{{ n.reachability?.address }}</span><template v-if="uptime(n.uptime_s)"> · {{ uptime(n.uptime_s) }}</template></span>
          <span class="s-st" :class="nodeSev(n.status)">{{ n.status }}</span>
        </div>
        <p v-if="!nodes.length" class="s-note">网格里只有她自己。</p>
      </div>

      <div class="s-head">渠道连接状态</div>
      <div class="s-rows">
        <div v-for="c in channels" :key="c.name" class="s-row">
          <span class="s-lab">{{ c.name }}</span>
          <span class="s-val s-note">{{ c.type }}</span>
          <span class="s-st" :class="c.connected ? 's-ok' : 's-dim'">{{ c.connected ? 'connected' : 'offline' }}</span>
        </div>
        <p v-if="!channels.length" class="s-note">没有外部渠道接入。</p>
      </div>
    </template>
  </Stratum>
</template>
