<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ source: string }>()

const tag = computed(() => {
  switch (props.source) {
    case 'self_thinking': return { label: '\u601D\u8003\u6D1E\u5BDF' }
    case 'system_alert': return { label: '\u7CFB\u7EDF\u544A\u8B66' }
    case 'late_night': return { label: '\u6DF1\u591C\u5173\u6000' }
    default: return { label: '\u4E3B\u52A8\u6D88\u606F' }
  }
})
</script>

<template>
  <div class="proactive-tag">
    <span class="tag-dot" />
    <span class="tag-label">{{ tag.label }}</span>
  </div>
</template>

<style scoped>
.proactive-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 10px;
  background: hsla(270, 40%, 30%, 0.3);
  font-size: 11px;
  color: var(--eva-purple);
  margin-bottom: 6px;
}

.tag-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--eva-purple);
  opacity: 0.7;
}
</style>
