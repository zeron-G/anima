<script setup lang="ts">
import { computed } from 'vue'
import { useEmotionStore } from '@/stores/emotionStore'
import { useRoute } from 'vue-router'

const emotion = useEmotionStore()
const route = useRoute()

// Hide on chat page (Eva is in the chat itself there)
const visible = computed(() => route.name !== 'chat')

const expression = computed(() => {
  const e = emotion.current
  if (e.concern > 0.6) return '\uD83D\uDE1F'
  if (e.engagement > 0.75 && e.curiosity > 0.7) return '\u2728'
  if (e.engagement > 0.7) return '\uD83D\uDE0A'
  if (e.confidence > 0.8) return '\uD83D\uDE24'
  if (e.curiosity > 0.7) return '\uD83E\uDD14'
  return '\uD83D\uDE0C'
})
</script>

<template>
  <Transition name="presence">
    <div v-if="visible" class="eva-presence breathing">
      <div class="presence-avatar">
        <span class="avatar-face">{{ expression }}</span>
      </div>
      <div class="presence-mood">{{ emotion.current.mood_label }}</div>
    </div>
  </Transition>
</template>

<style scoped>
.eva-presence {
  position: fixed;
  bottom: 40px;
  right: 16px;
  z-index: 80;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  transition: all 0.3s;
}

.eva-presence:hover {
  transform: scale(1.1);
}

.presence-avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--eva-glass);
  backdrop-filter: blur(12px);
  border: 1px solid var(--eva-glass-border);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 20px hsla(200, 60%, 50%, var(--glow-opacity));
}

.avatar-face {
  font-size: 28px;
}

.presence-mood {
  font-size: 10px;
  color: var(--eva-text-dim);
  text-transform: capitalize;
  background: var(--eva-surface);
  padding: 2px 8px;
  border-radius: 8px;
}

.presence-enter-active, .presence-leave-active {
  transition: all 0.3s ease;
}
.presence-enter-from, .presence-leave-to {
  opacity: 0;
  transform: scale(0.8) translateY(20px);
}
</style>
