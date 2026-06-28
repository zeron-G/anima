<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Stratum from '../components/Stratum.vue'
import { getStatus, getSystem, type SentinelStatus } from '../api/facets'

const status = ref<SentinelStatus | null>(null)
const sys = ref<{ version: string; uptime_s: number; python_version: string; memory_backend: string } | null>(null)
const err = ref(false)

const comps = computed(() => Object.entries(status.value?.components || {}))
function sev(h: string): string {
  if (h === 'ok') return 's-ok'
  if (['warn', 'warned', 'degraded', 'recovering'].includes(h)) return 's-warn'
  if (['down', 'crit', 'critical', 'defeated', 'escalated'].includes(h)) return 's-crit'
  return 's-dim'
}
function uptime(s?: number): string {
  if (!s) return '—'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m`
}
const pyShort = computed(() => (sys.value?.python_version || '').split(' ')[0])

onMounted(async () => {
  try {
    const [st, sy] = await Promise.all([getStatus(), getSystem()])
    status.value = st; sys.value = sy
  } catch { err.value = true }
})
</script>

<template>
  <Stratum line="她如何" line-bold="照顾自己">
    <p v-if="err" class="s-note">没接上守护系统。</p>
    <template v-else>
      <div class="s-head">
        Sentinel · 总体
        <span :class="sev(status?.overall || '')" style="margin-left:10px">{{ status?.overall || '…' }}</span>
        <span v-if="status?.active_repairs" class="s-warn" style="margin-left:10px">{{ status?.active_repairs }} repairing</span>
      </div>
      <div class="s-rows">
        <div v-for="[name, c] in comps" :key="name" class="s-row">
          <span class="s-lab">{{ name }}</span>
          <span class="s-val">{{ c.detail }}<template v-if="c.self_healed"> · <span class="s-note">self-healed ×{{ c.attempts }}</span></template></span>
          <span class="s-st" :class="sev(c.health)">{{ c.state || c.health }}</span>
        </div>
        <p v-if="!comps.length" class="s-note">守护系统没有上报组件。</p>
      </div>

      <div class="s-head">进程</div>
      <div class="s-rows">
        <div class="s-row"><span class="s-lab">version</span><span class="s-val"><b>{{ sys?.version || '—' }}</b></span><span class="s-st s-dim">build</span></div>
        <div class="s-row"><span class="s-lab">uptime</span><span class="s-val"><b>{{ uptime(sys?.uptime_s) }}</b></span><span class="s-st s-ok">alive</span></div>
        <div class="s-row"><span class="s-lab">memory</span><span class="s-val">{{ sys?.memory_backend || '—' }}</span><span class="s-st s-dim">backend</span></div>
        <div class="s-row"><span class="s-lab">python</span><span class="s-val">{{ pyShort }}</span><span class="s-st s-dim">runtime</span></div>
      </div>
    </template>
  </Stratum>
</template>
