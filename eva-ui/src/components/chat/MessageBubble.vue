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
    <div class="bubble" :class="{ 'user-bubble': isUser, 'eva-bubble': !isUser, 'breathing': !isUser && !message.streaming }">
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
  margin-bottom: 16px;
  padding: 0 20px;
}

.user-row {
  justify-content: flex-end;
}

.eva-row {
  justify-content: flex-start;
}

.bubble {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 16px;
  position: relative;
  transition: box-shadow 0.5s ease;
}

.user-bubble {
  background: hsla(200, 50%, 25%, 0.5);
  backdrop-filter: blur(12px);
  border: 1px solid hsla(200, 50%, 40%, 0.2);
  border-radius: 16px 16px 4px 16px;
}

.eva-bubble {
  background: var(--eva-glass);
  backdrop-filter: blur(16px);
  border: 1px solid var(--eva-glass-border);
  border-radius: 16px 16px 16px 4px;
  box-shadow: 0 0 20px hsla(200, 70%, 50%, var(--glow-opacity));
}

.eva-bubble.breathing {
  animation: bubbleBreathe var(--breath-duration) ease-in-out infinite;
}

@keyframes bubbleBreathe {
  0%, 100% { box-shadow: 0 0 15px hsla(200, 70%, 50%, 0.08); }
  50% { box-shadow: 0 0 30px hsla(200, 70%, 50%, 0.15); }
}

.bubble-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--eva-text);
}

.bubble-time {
  display: block;
  text-align: right;
  font-size: 10px;
  color: var(--eva-text-dim);
  margin-top: 6px;
  opacity: 0.5;
}

:deep(.code-block) {
  background: hsla(220, 20%, 10%, 0.6);
  border-radius: 8px;
  padding: 12px;
  margin: 8px 0;
  overflow-x: auto;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 13px;
  line-height: 1.5;
  border: 1px solid hsla(200, 30%, 30%, 0.2);
}

:deep(.inline-code) {
  background: hsla(220, 20%, 20%, 0.5);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
}
</style>
