<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '@/stores/chatStore'
import StreamingText from './StreamingText.vue'
import ToolCallChain from './ToolCallChain.vue'
import ProactiveTag from './ProactiveTag.vue'

const props = defineProps<{
  message: ChatMessage
}>()

const isUser = computed(() => props.message.role === 'user')
const isProactive = computed(() => !!props.message.proactive)
const timeStr = computed(() => {
  const d = new Date(props.message.timestamp * 1000)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
})

function renderMarkdown(text: string): string {
  if (!text) return ''
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="code-block"><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}
</script>

<template>
  <div class="bubble-row" :class="{ 'user-row': isUser, 'eva-row': !isUser }">
    <!-- Eva avatar indicator (small dot, not emoji) -->
    <div v-if="!isUser" class="eva-indicator">
      <div class="eva-dot" />
    </div>

    <div class="bubble" :class="{ 'user-bubble': isUser, 'eva-bubble': !isUser }">
      <ProactiveTag v-if="isProactive" :source="message.proactive!.source" />

      <div class="bubble-content">
        <StreamingText v-if="message.streaming" :text="message.content" />
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-else class="message-text" v-html="renderMarkdown(message.content)" />
      </div>

      <ToolCallChain v-if="message.toolCalls?.length" :calls="message.toolCalls" />

      <span class="bubble-time">{{ timeStr }}</span>
    </div>
  </div>
</template>

<style scoped>
.bubble-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: 18px;
  padding: 0 24px;
  animation: bubbleEnter 0.3s var(--ease-out-expo) both;
}

@keyframes bubbleEnter {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.user-row {
  justify-content: flex-end;
}

.eva-row {
  justify-content: flex-start;
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}

.user-row {
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}

/* Eva message indicator */
.eva-indicator {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
}

.eva-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, var(--eva-ice), hsla(200, 50%, 35%, 1));
  box-shadow: 0 0 8px hsla(var(--eva-ice-hsl), 0.3);
}

/* ── Bubble Base ── */
.bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 16px;
  position: relative;
  transition: box-shadow 0.5s ease;
}

.user-bubble {
  background: hsla(var(--eva-ice-hsl), 0.1);
  backdrop-filter: blur(12px);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.1);
  border-radius: 16px 16px 4px 16px;
}

.eva-bubble {
  background: var(--eva-glass);
  backdrop-filter: blur(20px);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.08);
  border-radius: 16px 16px 16px 4px;
  box-shadow: 0 2px 16px hsla(220, 50%, 5%, 0.3);
}

/* ── Content ── */
.bubble-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--eva-text);
}

.bubble-time {
  display: block;
  text-align: right;
  font-family: 'DM Sans', sans-serif;
  font-size: 10px;
  color: var(--eva-text-dim);
  margin-top: 6px;
  opacity: 0;
  transition: opacity 0.25s ease;
}

.bubble:hover .bubble-time {
  opacity: 0.6;
}

/* ── Code Blocks ── */
:deep(.code-block) {
  background: hsla(222, 25%, 8%, 0.7);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  margin: 10px 0;
  overflow-x: auto;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  border: 1px solid hsla(var(--eva-ice-hsl), 0.06);
  color: hsla(var(--eva-ice-hsl), 0.85);
}

:deep(.inline-code) {
  background: hsla(222, 20%, 18%, 0.6);
  padding: 2px 7px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  color: var(--eva-ice);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.06);
}

:deep(strong) {
  font-weight: 600;
  color: var(--eva-text);
}
</style>
