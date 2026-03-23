<script setup lang="ts">
/* EvaPresence — removed VRM dependency for performance.
   The avatar is now a lightweight CSS-only mood indicator,
   visible only on non-chat pages. */
import { computed } from 'vue'
import { useEmotionStore } from '@/stores/emotionStore'
import { useRoute } from 'vue-router'

const emotion = useEmotionStore()
const route = useRoute()

const visible = computed(() => route.name !== 'chat' && route.name !== 'login')
</script>

<template>
  <Transition name="presence">
    <div v-if="visible" class="eva-presence">
      <div class="presence-orb">
        <div class="orb-core" />
        <div class="orb-ring" />
      </div>
      <div class="presence-mood">{{ emotion.current.mood_label }}</div>
    </div>
  </Transition>
</template>

<style scoped>
.eva-presence {
  position: fixed;
  bottom: 40px;
  right: 20px;
  z-index: 80;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.35s var(--ease);
}

.eva-presence:hover {
  transform: translateY(-2px);
}

.presence-orb {
  position: relative;
  width: 40px;
  height: 40px;
}

.orb-core {
  position: absolute;
  inset: 10px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, var(--accent), rgba(0, 140, 120, 0.7));
  box-shadow: 0 0 16px rgba(var(--accent-rgb), 0.3);
  animation: orbPulse var(--breath-duration) ease-in-out infinite;
}

.orb-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid rgba(var(--accent-rgb), 0.15);
  animation: ringGlow var(--breath-duration) ease-in-out infinite;
}

@keyframes orbPulse {
  0%, 100% { transform: scale(0.95); box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.2); }
  50% { transform: scale(1.05); box-shadow: 0 0 24px rgba(var(--accent-rgb), 0.4); }
}

@keyframes ringGlow {
  0%, 100% { box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.05); }
  50% { box-shadow: 0 0 16px rgba(var(--accent-rgb), 0.15); }
}

.presence-mood {
  font-family: var(--font-heading);
  font-size: 9px;
  font-weight: 400;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--text-dim);
  background: var(--surface);
  backdrop-filter: blur(12px);
  padding: 3px 10px;
  border-radius: 100px;
  border: 1px solid var(--border);
}

.presence-enter-active, .presence-leave-active {
  transition: all 0.4s var(--ease);
}
.presence-enter-from, .presence-leave-to {
  opacity: 0;
  transform: scale(0.8) translateY(20px);
}
</style>
