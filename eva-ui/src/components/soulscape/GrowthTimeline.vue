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
          <div class="node-dot" />
          <div class="node-info">
            <span class="node-date">{{ evt.date }}</span>
            <span class="node-change">{{ evt.change }}</span>
          </div>
        </div>
        <div v-if="events.length === 0" class="timeline-empty">
          尚无成长记录
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
  padding: 16px 0;
}

.timeline-track {
  display: flex;
  align-items: flex-start;
  gap: 0;
  min-width: max-content;
  padding: 0 20px;
  position: relative;
}

.timeline-track::before {
  content: '';
  position: absolute;
  top: 8px;
  left: 20px;
  right: 20px;
  height: 2px;
  background: hsla(200, 30%, 30%, 0.2);
}

.timeline-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 120px;
  cursor: pointer;
  position: relative;
}

.node-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--eva-ice);
  border: 2px solid var(--eva-dark);
  z-index: 1;
  transition: transform 0.2s;
}

.timeline-node:hover .node-dot {
  transform: scale(1.4);
  box-shadow: 0 0 10px var(--eva-ice);
}

.node-info {
  margin-top: 8px;
  text-align: center;
  max-width: 100px;
}

.node-date {
  display: block;
  font-size: 10px;
  color: var(--eva-text-dim);
}

.node-change {
  display: block;
  font-size: 11px;
  color: var(--eva-text);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline-empty {
  color: var(--eva-text-dim);
  font-size: 13px;
  padding: 20px;
}
</style>
