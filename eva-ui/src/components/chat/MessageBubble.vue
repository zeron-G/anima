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
    <!-- Eva indicator -->
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
  gap: 10px;
  margin-bottom: 20px;
  padding: 0 32px;
  animation: bubbleIn 0.35s var(--ease) both;
}

@keyframes bubbleIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.user-row {
  justify-content: flex-end;
  max-width: 880px;
  margin-left: auto;
  margin-right: auto;
}

.eva-row {
  justify-content: flex-start;
  max-width: 880px;
  margin-left: auto;
  margin-right: auto;
}

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
  background: radial-gradient(circle at 35% 35%, var(--accent), rgba(0, 140, 120, 1));
  box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.3);
}

.bubble {
  max-width: 68%;
  padding: 14px 18px;
  border-radius: var(--radius-lg);
  position: relative;
  transition: box-shadow 0.5s ease;
}

.user-bubble {
  background: rgba(var(--accent-rgb), 0.08);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(var(--accent-rgb), 0.08);
  border-radius: 16px 16px 4px 16px;
}

.eva-bubble {
  background: var(--glass);
  backdrop-filter: blur(20px);
  border: 1px solid var(--border);
  border-radius: 16px 16px 16px 4px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
}

.bubble-content {
  font-size: 14px;
  line-height: 1.75;
  color: var(--text);
}

.bubble-time {
  display: block;
  text-align: right;
  font-family: var(--font-body);
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 8px;
  opacity: 0;
  transition: opacity 0.25s ease;
}

.bubble:hover .bubble-time {
  opacity: 0.6;
}

/* Code blocks */
:deep(.code-block) {
  background: rgba(5, 5, 8, 0.7);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  margin: 10px 0;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.6;
  border: 1px solid var(--border);
  color: rgba(var(--accent-rgb), 0.85);
}

:deep(.inline-code) {
  background: rgba(20, 20, 30, 0.6);
  padding: 2px 7px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--accent);
  border: 1px solid var(--border);
}

:deep(strong) {
  font-weight: 600;
  color: var(--text);
}
</style>
