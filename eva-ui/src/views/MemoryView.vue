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
  <div class="page-view memory-view">
    <div class="page-header">
      <div class="section-label">Knowledge base</div>
      <h1 class="page-title">Memory</h1>
      <p class="page-subtitle">Search, explore, and visualize stored memories across all knowledge domains.</p>
    </div>

    <!-- Search toolbar -->
    <div class="memory-toolbar glass">
      <div class="search-wrap">
        <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input
          v-model="searchQuery"
          @keydown.enter="handleSearch"
          placeholder="Search memories..."
          class="search-input"
        />
      </div>
      <select v-model="typeFilter" class="type-select input-field" @change="handleSearch">
        <option value="">All types</option>
        <option v-for="t in types.filter(Boolean)" :key="t" :value="t">{{ t }}</option>
      </select>
      <button class="btn-primary search-btn" @click="handleSearch">Search</button>
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
.memory-view {
  gap: var(--space-lg);
}

.memory-toolbar {
  display: flex;
  gap: 10px;
  padding: 10px var(--space-md);
  align-items: center;
}

.search-wrap {
  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 12px;
  color: var(--text-dim);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 10px 14px 10px 38px;
  background: rgba(10, 10, 20, 0.5);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: border-color var(--transition-fast);
}

.search-input:focus {
  border-color: rgba(var(--accent-rgb), 0.25);
}

.search-input::placeholder {
  color: var(--text-dim);
}

.type-select {
  width: 140px;
  padding: 10px 12px;
  appearance: none;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23666' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 28px;
}

.search-btn {
  padding: 10px 20px;
  font-size: 12px;
  flex-shrink: 0;
}

.memory-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: var(--space-lg);
  min-height: 0;
}

.memory-main {
  min-height: 400px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  overflow: hidden;
}

.memory-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
  overflow-y: auto;
}

@media (max-width: 900px) {
  .memory-grid { grid-template-columns: 1fr; }
}
</style>
