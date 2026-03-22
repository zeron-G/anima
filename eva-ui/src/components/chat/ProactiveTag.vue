<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ source: string }>()

const tag = computed(() => {
  switch (props.source) {
    case 'self_thinking': return { label: 'Insight' }
    case 'system_alert': return { label: 'Alert' }
    case 'late_night': return { label: 'Check-in' }
    default: return { label: 'Proactive' }
  }
})
</script>

<template>
  <div class="proactive-tag">
    <svg class="tag-icon" width="10" height="10" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="3" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="0.8" opacity="0.3"/>
    </svg>
    <span class="tag-label">{{ tag.label }}</span>
  </div>
</template>

<style scoped>
.proactive-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 10px;
  border-radius: 12px;
  background: hsla(270, 40%, 25%, 0.2);
  border: 1px solid hsla(270, 40%, 40%, 0.12);
  font-size: 10px;
  color: var(--eva-purple);
  margin-bottom: 8px;
}

.tag-icon {
  opacity: 0.7;
}

.tag-label {
  font-family: 'Sora', sans-serif;
  font-weight: 400;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
</style>
