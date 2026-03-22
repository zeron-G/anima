<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useEmotionStore } from '@/stores/emotionStore'
import { useStreaming } from '@/composables/useStreaming'
import { ws } from '@/api/websocket'
import MessageBubble from '@/components/chat/MessageBubble.vue'

const chat = useChatStore()
const emotion = useEmotionStore()
const { isStreaming, sendStreaming } = useStreaming()

const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const messagesRef = ref<HTMLDivElement>()
const userScrolled = ref(false)

// Auto-scroll to bottom on new messages (unless user scrolled up)
watch(() => chat.messages.length, async () => {
  if (!userScrolled.value) {
    await nextTick()
    scrollToBottom()
  }
})

function scrollToBottom() {
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

function onScroll() {
  if (!messagesRef.value) return
  const el = messagesRef.value
  userScrolled.value = el.scrollTop + el.clientHeight < el.scrollHeight - 60
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return
  inputText.value = ''
  // Reset textarea height after clearing
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }
  await sendStreaming(text)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// Auto-resize textarea
function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 150) + 'px'
}

// Wire WS events to chat store
onMounted(() => {
  ws.on('stream', (msg) => {
    chat.appendStream(msg.data.correlation_id, msg.data.text || '', msg.data.done || false)
  })
  ws.on('tool_call', (msg) => {
    chat.addToolCall(msg.data)
  })
  ws.on('proactive', (msg) => {
    chat.addProactive(msg.data)
  })
})
</script>

<template>
  <div class="chat-view">
    <!-- Status bar -->
    <div class="chat-header glass">
      <div class="header-info">
        <div class="mood-indicator">
          <div class="mood-dot" />
          <span class="mood-label">{{ emotion.current.mood_label }}</span>
        </div>
      </div>
    </div>

    <!-- Messages -->
    <div ref="messagesRef" class="messages-container" @scroll="onScroll">
      <div class="messages-inner">
        <div v-if="chat.messages.length === 0" class="empty-state">
          <div class="empty-icon-circle">
            <div class="empty-pulse" />
          </div>
          <p class="empty-text">和 Eva 说点什么吧</p>
        </div>

        <MessageBubble
          v-for="msg in chat.messages"
          :key="msg.id"
          :message="msg"
        />

        <!-- Scroll to bottom button -->
        <transition name="fade">
          <button v-if="userScrolled" class="scroll-btn" @click="userScrolled = false; scrollToBottom()">
            &#x2193; 新消息
          </button>
        </transition>
      </div>
    </div>

    <!-- Input area -->
    <div class="input-area glass">
      <div class="input-wrapper">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="chat-input"
          :disabled="isStreaming"
          rows="1"
          placeholder="和 Eva 说点什么... (Enter 发送, Shift+Enter 换行)"
          @keydown="handleKeydown"
          @input="autoResize"
        />
        <button
          class="send-btn"
          :class="{ active: inputText.trim() && !isStreaming }"
          :disabled="!inputText.trim() || isStreaming"
          @click="handleSend"
        >
          <span v-if="isStreaming" class="sending-indicator">&#x25CF;&#x25CF;&#x25CF;</span>
          <span v-else>&uarr;</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
}

.chat-header {
  flex-shrink: 0;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: 0;
  border-top: none;
  border-left: none;
  border-right: none;
}

.header-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mood-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mood-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--eva-ice);
  animation: moodBreathe var(--breath-duration) ease-in-out infinite;
}

@keyframes moodBreathe {
  0%, 100% { opacity: 0.6; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.3); }
}

.mood-label {
  font-size: 13px;
  color: var(--eva-text-dim);
  text-transform: capitalize;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px 0;
  position: relative;
}

.messages-inner {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  opacity: 0.4;
}

.empty-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  border: 1px solid var(--eva-glass-border);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  position: relative;
}

.empty-pulse {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--eva-ice);
  opacity: 0.3;
  animation: emptyPulse 3s ease-in-out infinite;
}

@keyframes emptyPulse {
  0%, 100% { transform: scale(0.8); opacity: 0.2; }
  50% { transform: scale(1.1); opacity: 0.4; }
}

.empty-text {
  font-size: 16px;
  color: var(--eva-text-dim);
}

.scroll-btn {
  position: sticky;
  bottom: 10px;
  align-self: center;
  padding: 6px 16px;
  border-radius: 16px;
  background: var(--eva-surface);
  backdrop-filter: blur(12px);
  border: 1px solid var(--eva-glass-border);
  color: var(--eva-ice);
  font-size: 12px;
  cursor: pointer;
  z-index: 10;
  transition: all 0.2s;
}

.scroll-btn:hover {
  background: hsla(200, 40%, 25%, 0.6);
}

.input-area {
  flex-shrink: 0;
  padding: 12px 20px;
  border-radius: 0;
  border-bottom: none;
  border-left: none;
  border-right: none;
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  max-width: 800px;
  margin: 0 auto;
}

.chat-input {
  flex: 1;
  background: hsla(220, 20%, 12%, 0.5);
  border: 1px solid hsla(200, 30%, 30%, 0.2);
  border-radius: 12px;
  padding: 10px 14px;
  color: var(--eva-text);
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  outline: none;
  transition: border-color 0.3s;
  font-family: inherit;
  min-height: 42px;
  max-height: 150px;
}

.chat-input:focus {
  border-color: hsla(200, 60%, 50%, 0.4);
  box-shadow: 0 0 0 2px hsla(200, 60%, 50%, 0.1);
}

.chat-input::placeholder {
  color: var(--eva-text-dim);
  opacity: 0.5;
}

.send-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: none;
  background: hsla(200, 40%, 25%, 0.4);
  color: var(--eva-text-dim);
  font-size: 18px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.send-btn.active {
  background: var(--eva-ice);
  color: var(--eva-dark);
  box-shadow: 0 0 15px hsla(200, 70%, 50%, 0.3);
}

.send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.sending-indicator {
  font-size: 10px;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
