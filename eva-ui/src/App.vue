<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useEmotionStore } from './stores/emotionStore'
import { useStatusStore } from './stores/statusStore'
import { useThinkingStore } from './stores/thinkingStore'
import { useChatStore } from './stores/chatStore'
import { ws } from './api/websocket'
import OrbitNav from './components/global/OrbitNav.vue'
import ConnectionBadge from './components/global/ConnectionBadge.vue'
import ThinkingStream from './components/global/ThinkingStream.vue'

const emotion = useEmotionStore()
const status = useStatusStore()
const thinking = useThinkingStore()
const chat = useChatStore()

// Apply emotion CSS variables to root
watch(() => emotion.cssVars, (vars) => {
  const root = document.documentElement
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value as string)
  }
}, { immediate: true, deep: true })

// Connect WebSocket on mount
onMounted(() => {
  ws.connect()

  // Route WS messages to stores
  ws.on('heartbeat', (msg) => {
    if (msg.data.emotion) emotion.update(msg.data.emotion)
    status.update(msg.data)
  })
  ws.on('stream', (msg) => {
    chat.appendStream(msg.data.correlation_id, msg.data.text || '', msg.data.done || false)
  })
  ws.on('tool_call', (msg) => chat.addToolCall(msg.data))
  ws.on('proactive', (msg) => chat.addProactive(msg.data))
  ws.on('thinking', (msg) => thinking.add(msg.data))
  ws.on('activity', (msg) => status.addActivity(msg.data))
  ws.on('evolution', (msg) => status.updateEvolution(msg.data))
  ws.on('emotion_shift', (msg) => emotion.shift(msg.data))
  ws.on('node_event', (msg) => status.updateNode(msg.data))
})
</script>

<template>
  <div class="app-shell">
    <OrbitNav />
    <main class="app-content">
      <router-view v-slot="{ Component }">
        <transition name="page" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
    <ThinkingStream />
    <ConnectionBadge />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  position: relative;
}

.app-content {
  flex: 1;
  overflow-y: auto;
  position: relative;
}

/* Page transitions */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.3s ease, transform 0.3s var(--ease);
}
.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
