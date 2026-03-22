<script setup lang="ts">
import { computed } from 'vue'
import { useEmotionStore } from '@/stores/emotionStore'
import { useRoute } from 'vue-router'
import EvaAvatar from './EvaAvatar.vue'

const emotion = useEmotionStore()
const route = useRoute()

// Hide on chat page (Eva is in the chat itself there)
const visible = computed(() => route.name !== 'chat')
</script>

<template>
  <Transition name="presence">
    <div v-if="visible" class="eva-presence">
      <div class="presence-avatar-wrap">
        <EvaAvatar :size="56" />
        <div class="presence-ring" />
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
  gap: 6px;
  cursor: pointer;
  transition: all 0.35s var(--ease-out-expo);
}

.eva-presence:hover {
  transform: translateY(-2px);
}

.eva-presence:hover .presence-ring {
  border-color: hsla(var(--eva-ice-hsl), 0.3);
  box-shadow: 0 0 24px hsla(var(--eva-ice-hsl), 0.15);
}

.presence-avatar-wrap {
  position: relative;
  width: 56px;
  height: 56px;
}

.presence-ring {
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 1px solid hsla(var(--eva-ice-hsl), 0.15);
  box-shadow: 0 0 16px hsla(var(--eva-ice-hsl), var(--glow-opacity));
  animation: ringBreathe var(--breath-duration) ease-in-out infinite;
  pointer-events: none;
}

@keyframes ringBreathe {
  0%, 100% { box-shadow: 0 0 12px hsla(var(--eva-ice-hsl), 0.08); }
  50% { box-shadow: 0 0 24px hsla(var(--eva-ice-hsl), 0.18); }
}

.presence-mood {
  font-family: 'Sora', sans-serif;
  font-size: 10px;
  font-weight: 400;
  letter-spacing: 0.05em;
  color: var(--eva-text-dim);
  text-transform: capitalize;
  background: var(--eva-surface);
  backdrop-filter: blur(12px);
  padding: 3px 10px;
  border-radius: 10px;
  border: 1px solid hsla(var(--eva-ice-hsl), 0.06);
}

.presence-enter-active, .presence-leave-active {
  transition: all 0.4s var(--ease-out-expo);
}
.presence-enter-from, .presence-leave-to {
  opacity: 0;
  transform: scale(0.8) translateY(20px);
}
</style>
