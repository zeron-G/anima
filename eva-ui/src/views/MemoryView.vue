<script setup lang="ts">
import { ref, onMounted } from 'vue'
import StarField from '@/components/memory/StarField.vue'
import MemoryDetail from '@/components/memory/MemoryDetail.vue'
import MemoryStats from '@/components/memory/MemoryStats.vue'
import * as api from '@/api/memory'

const memories = ref<any[]>([])
const selected = ref<any>(null)
const stats = ref({ total: 0, by_type: {} })
const searchQuery = ref('')
const loading = ref(true)
const typeFilter = ref('')

const types = ['', 'chat', 'self_thinking', 'system', 'task']

async function loadData() {
  loading.value = true
  try {
    const [recentRes, statsRes] = await Promise.all([
      api.getRecent(100),
      api.getStats(),
    ])
    memories.value = recentRes.data.results || []
    stats.value = statsRes.data
  } catch (e) {
    console.error('Memory load failed:', e)
  } finally {
    loading.value = false
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    await loadData()
    return
  }
  try {
    const res = await api.search(searchQuery.value, 50, typeFilter.value || undefined)
    memories.value = res.data.results || []
  } catch (e) {
    console.error('Search failed:', e)
  }
}

function selectMemory(mem: any) {
  selected.value = mem
}

onMounted(loadData)
</script>

<template>
  <div class="memory-view">
    <!-- Top bar: search + filters -->
    <div class="memory-toolbar glass">
      <input v-model="searchQuery" @keydown.enter="handleSearch"
             placeholder="搜索记忆..." class="search-input" />
      <select v-model="typeFilter" class="type-select" @change="handleSearch">
        <option value="">全部类型</option>
        <option v-for="t in types.filter(Boolean)" :key="t" :value="t">{{ t }}</option>
      </select>
      <button class="search-btn" @click="handleSearch">搜索</button>
    </div>

    <div class="memory-grid">
      <!-- Main: Star Field -->
      <div class="memory-main">
        <StarField :memories="memories" @select="selectMemory" />
      </div>

      <!-- Right sidebar -->
      <div class="memory-sidebar">
        <MemoryStats :stats="stats" />
        <MemoryDetail :memory="selected" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.memory-view { height: 100%; display: flex; flex-direction: column; padding: 16px; gap: 16px; }
.memory-toolbar { display: flex; gap: 8px; padding: 10px 16px; align-items: center; }
.search-input { flex: 1; background: hsla(220, 20%, 10%, 0.5); border: 1px solid hsla(200, 30%, 30%, 0.2); border-radius: 8px; padding: 8px 12px; color: var(--eva-text); font-size: 13px; outline: none; }
.search-input:focus { border-color: hsla(200, 60%, 50%, 0.4); }
.type-select { background: hsla(220, 20%, 12%, 0.5); border: 1px solid hsla(200, 30%, 30%, 0.2); border-radius: 8px; padding: 8px; color: var(--eva-text); font-size: 13px; outline: none; }
.search-btn { padding: 8px 16px; border-radius: 8px; border: 1px solid hsla(200, 50%, 50%, 0.2); background: hsla(200, 40%, 25%, 0.4); color: var(--eva-ice); font-size: 13px; cursor: pointer; }
.search-btn:hover { background: hsla(200, 40%, 30%, 0.5); }
.memory-grid { flex: 1; display: grid; grid-template-columns: 1fr 320px; gap: 16px; min-height: 0; }
.memory-main { min-height: 400px; }
.memory-sidebar { display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
@media (max-width: 900px) { .memory-grid { grid-template-columns: 1fr; } }
</style>
