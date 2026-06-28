<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Stratum from '../components/Stratum.vue'
import { getMemoryStats, getRecentMemories, getDocuments, type MemoryRow } from '../api/facets'

const total = ref<number | null>(null)
const byType = ref<Record<string, number>>({})
const recent = ref<MemoryRow[]>([])
const docCount = ref<number | null>(null)
const err = ref(false)

const types = computed(() => Object.entries(byType.value).sort((a, b) => b[1] - a[1]))
const maxType = computed(() => Math.max(1, ...Object.values(byType.value)))

onMounted(async () => {
  try {
    const [s, r, d] = await Promise.all([getMemoryStats(), getRecentMemories(8), getDocuments()])
    total.value = s.total; byType.value = s.by_type || {}
    recent.value = r; docCount.value = d.length
  } catch { err.value = true }
})
</script>

<template>
  <Stratum line="她" line-bold="记得">
    <p v-if="err" class="s-note">没接上记忆库（pgvector）。</p>
    <template v-else>
      <div class="s-head">记忆体量 · {{ total ?? '…' }} 条 episodic</div>
      <div class="s-rows">
        <div v-for="[t, n] in types" :key="t" class="s-row">
          <span class="s-lab">{{ t }}</span>
          <span class="s-val"><span class="s-bar"><i :style="{ width: (n / maxType * 100) + '%' }"></i></span></span>
          <span class="s-st s-dim">{{ n }}</span>
        </div>
        <div class="s-row"><span class="s-lab">documents</span><span class="s-val">RAG chunks</span><span class="s-st s-dim">{{ docCount ?? '—' }}</span></div>
      </div>

      <div class="s-head">最近沉淀</div>
      <div class="s-rows">
        <div v-for="m in recent" :key="m.id" class="s-row">
          <span class="s-lab">{{ m.type }}</span>
          <span class="s-val">{{ (m.content || '').replace(/\s+/g, ' ').slice(0, 88) }}</span>
          <span class="s-st" :class="m.importance >= 0.8 ? 's-ok' : 's-dim'">{{ m.importance != null ? m.importance.toFixed(2) : '' }}</span>
        </div>
        <p v-if="!recent.length" class="s-note">还没有沉淀的记忆。</p>
      </div>
    </template>
  </Stratum>
</template>
