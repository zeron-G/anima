<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  log: string
  driftEntries: Array<{ timestamp: number; drift_score: number; flags: string[] }>
}>()

interface GrowthEvent {
  date: string
  change: string
  reason: string
}

const events = computed<GrowthEvent[]>(() => {
  if (!props.log) return []
  const entries: GrowthEvent[] = []
  const blocks = props.log.split('---').filter(b => b.trim())
  for (const block of blocks) {
    const dateMatch = block.match(/\*(\d{4}-\d{2}-\d{2}[^*]*)\*/)
    const changeMatch = block.match(/\*\*变化\*\*:\s*(.+)/)
    const reasonMatch = block.match(/\*\*原因\*\*:\s*(.+)/)
    if (dateMatch) {
      entries.push({
        date: dateMatch[1].trim(),
        change: changeMatch?.[1]?.trim() || '',
        reason: reasonMatch?.[1]?.trim() || '',
      })
    }
  }
  return entries
})
</script>

<template>
  <div class="timeline-container">
    <div class="timeline-scroll">
      <div class="timeline-track">
        <div v-for="(evt, i) in events" :key="i" class="timeline-node" :title="evt.reason">
          <div class="node-marker">
            <div class="node-dot" />
          </div>
          <div class="node-info">
            <span class="node-date">{{ evt.date }}</span>
            <span class="node-change">{{ evt.change }}</span>
          </div>
        </div>
        <div v-if="events.length === 0" class="timeline-empty">
          No growth events recorded
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.timeline-container {
  width: 100%;
  overflow: hidden;
}

.timeline-scroll {
  overflow-x: auto;
  padding: var(--space-md) 0;
}

.timeline-track {
  display: flex;
  align-items: flex-start;
  min-width: max-content;
  padding: 0 var(--space-lg);
  position: relative;
}

.timeline-track::before {
  content: '';
  position: absolute;
  top: 8px;
  left: var(--space-lg);
  right: var(--space-lg);
  height: 1px;
  background: linear-gradient(90deg, var(--accent), rgba(var(--accent-rgb), 0.05));
}

.timeline-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 130px;
  cursor: pointer;
  position: relative;
}

.node-marker {
  position: relative;
  z-index: 1;
}

.node-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--accent);
  background: var(--bg);
  transition: all var(--transition-fast);
}

.timeline-node:hover .node-dot {
  background: var(--accent);
  box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.4);
  transform: scale(1.3);
}

.node-info {
  margin-top: 10px;
  text-align: center;
  max-width: 110px;
}

.node-date {
  display: block;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-dim);
  margin-bottom: 2px;
}

.node-change {
  display: block;
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline-empty {
  color: var(--text-dim);
  font-size: 13px;
  padding: var(--space-lg);
}
</style>
