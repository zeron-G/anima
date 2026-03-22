<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <div class="streaming-text">
    <span class="materialized">{{ text }}</span>
    <span class="cursor" />
  </div>
</template>

<style scoped>
.streaming-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--eva-ice);
  margin-left: 1px;
  animation: blink 1s step-end infinite;
  vertical-align: text-bottom;
}

@keyframes blink {
  50% { opacity: 0; }
}
</style>
