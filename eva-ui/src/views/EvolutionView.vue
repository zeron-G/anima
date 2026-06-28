<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Stratum from '../components/Stratum.vue'
import { getEvolutionStatus, getEvolutionHistory, getGovernance, type EvolutionStatus } from '../api/facets'

const st = ref<EvolutionStatus | null>(null)
const successes = ref<any[]>([])
const failures = ref<any[]>([])
const gov = ref<{ activity_level: string; drift_scores: number[]; quiet_ratio?: number } | null>(null)
const err = ref(false)

function title(x: any): string {
  return (x?.title || x?.goal || x?.description || x?.proposal_id || x?.id || JSON.stringify(x)).toString().slice(0, 80)
}
const levelSev = (l?: string) => l === 'active' ? 's-ok' : l === 'minimal' ? 's-warn' : 's-dim'

onMounted(async () => {
  try {
    const [s, h, g] = await Promise.all([getEvolutionStatus(), getEvolutionHistory(), getGovernance()])
    st.value = s; successes.value = h.successes || []; failures.value = h.failures || []; gov.value = g
  } catch { err.value = true }
})
</script>

<template>
  <Stratum line="她如何" line-bold="重写自己">
    <p v-if="err" class="s-note">没接上进化引擎。</p>
    <template v-else>
      <div class="s-head">引擎状态</div>
      <div class="s-rows">
        <div class="s-row"><span class="s-lab">state</span><span class="s-val">proposal → consensus → test → review → reload</span><span class="s-st" :class="st?.running ? 's-ok' : 's-dim'">{{ st?.running ? 'evolving' : 'idle' }}</span></div>
        <div class="s-row"><span class="s-lab">queue</span><span class="s-val"><b>{{ st?.queue_size ?? 0 }}</b> 待处理 · 本小时 {{ st?.evolutions_this_hour ?? 0 }}/3</span><span class="s-st s-dim">rate</span></div>
        <div class="s-row"><span class="s-lab">failures</span><span class="s-val"><b>{{ st?.consecutive_failures ?? 0 }}</b> 连续</span><span class="s-st" :class="(st?.cooldown_remaining ?? 0) > 0 ? 's-warn' : 's-dim'">{{ (st?.cooldown_remaining ?? 0) > 0 ? `cooldown ${st?.cooldown_remaining}s` : 'clear' }}</span></div>
        <div class="s-row"><span class="s-lab">lifetime</span><span class="s-val"><b>{{ st?.memory.successes ?? 0 }}</b> 成功 · {{ st?.memory.failures ?? 0 }} 失败 · {{ st?.memory.goals ?? 0 }} 目标</span><span class="s-st s-dim">memory</span></div>
        <div class="s-row"><span class="s-lab">governance</span><span class="s-val">自主活跃度<template v-if="gov?.quiet_ratio != null"> · 静默比 <b>{{ Math.round(gov.quiet_ratio * 100) }}%</b></template></span><span class="s-st" :class="levelSev(gov?.activity_level)">{{ gov?.activity_level || '—' }}</span></div>
      </div>

      <div class="s-head">最近合并</div>
      <div class="s-rows">
        <div v-for="(x, i) in successes.slice(-6).reverse()" :key="'s' + i" class="s-row">
          <span class="s-lab">merged</span><span class="s-val">{{ title(x) }}</span><span class="s-st s-ok">✓</span>
        </div>
        <div v-for="(x, i) in failures.slice(-3).reverse()" :key="'f' + i" class="s-row">
          <span class="s-lab">rejected</span><span class="s-val">{{ title(x) }}</span><span class="s-st s-warn">✗</span>
        </div>
        <p v-if="!successes.length && !failures.length" class="s-note">还没有进化记录。</p>
      </div>
    </template>
  </Stratum>
</template>
