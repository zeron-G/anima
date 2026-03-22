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
    <!-- Header -->
    <div class="chat-header">
      <div class="header-content">
        <div class="header-left">
          <h2 class="header-title">Eva</h2>
          <div class="header-divider" />
          <div class="mood-indicator">
            <div class="mood-dot" />
            <span class="mood-label">{{ emotion.current.mood_label }}</span>
          </div>
        </div>
        <div class="header-right">
          <span class="engagement-label">engagement</span>
          <div class="engagement-bar">
            <div class="engagement-fill" :style="{ width: `${emotion.current.engagement * 100}%` }" />
          </div>
        </div>
      </div>
    </div>

    <!-- Messages -->
    <div ref="messagesRef" class="messages-container" @scroll="onScroll">
      <div class="messages-inner">
        <div v-if="chat.messages.length === 0" class="empty-state">
          <div class="empty-orb-wrap">
            <div class="empty-orb" />
            <div class="empty-orb-ring" />
            <div class="empty-orb-ring ring-2" />
          </div>
          <p class="empty-text">Start a conversation with Eva</p>
          <p class="empty-hint">Enter to send / Shift+Enter for new line</p>
        </div>

        <MessageBubble
          v-for="msg in chat.messages"
          :key="msg.id"
          :message="msg"
        />

        <!-- Scroll to bottom button -->
        <transition name="fade">
          <button v-if="userScrolled" class="scroll-btn" @click="userScrolled = false; scrollToBottom()">
            <!-- Down arrow SVG -->
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M12 5v14M5 12l7 7 7-7"/>
            </svg>
            <span>New messages</span>
          </button>
        </transition>
      </div>
    </div>

    <!-- Input area -->
    <div class="input-area">
      <div class="input-wrapper">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="chat-input"
          :disabled="isStreaming"
          rows="1"
          placeholder="Message Eva..."
          @keydown="handleKeydown"
          @input="autoResize"
        />
        <button
          class="send-btn"
          :class="{ active: inputText.trim() && !isStreaming }"
          :disabled="!inputText.trim() || isStreaming"
          @click="handleSend"
        >
          <!-- Send arrow SVG -->
          <svg v-if="!isStreaming" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7"/>
          </svg>
          <!-- Streaming dots -->
          <span v-else class="sending-dots">
            <span class="dot" /><span class="dot" /><span class="dot" />
          </span>
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

/* ── Header ── */
.chat-header {
  flex-shrink: 0;
  padding: 0 24px;
  border-bottom: 1px solid hsla(var(--eva-ice-hsl), 0.06);
  background: hsla(222, 30%, 6%, 0.4);
  backdrop-filter: blur(16px);
}

.header-content {
  max-width: 800px;
  margin: 0 auto;
  padding: 14px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  font-family: 'Sora', sans-serif;
  font-size: 16px;
  font-weight: 500;
  color: var(--eva-text);
  letter-spacing: 0.02em;
}

.header-divider {
  width: 1px;
  height: 16px;
  background: hsla(var(--eva-ice-hsl), 0.12);
}

.mood-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mood-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--eva-ice);
  box-shadow: 0 0 8px hsla(var(--eva-ice-hsl), 0.4);
  animation: moodBreathe var(--breath-duration) ease-in-out infinite;
}

@keyframes moodBreathe {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.mood-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 12px;
  color: var(--eva-text-dim);
  text-transform: capitalize;
  letter-spacing: 0.02em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.engagement-label {
  font-family: 'Sora', sans-serif;
  font-size: 10px;
  color: var(--eva-text-dim);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.engagement-bar {
  width: 48px;
  height: 3px;
  border-radius: 2px;
  background: hsla(var(--eva-ice-hsl), 0.1);
  overflow: hidden;
}

.engagement-fill {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--eva-ice), hsla(var(--eva-pink-hsl), 0.7));
  transition: width 1s ease;
}

/* ── Messages ── */
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

/* ── Empty State ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
}

.empty-orb-wrap {
  position: relative;
  width: 80px;
  height: 80px;
  margin-bottom: 24px;
}

.empty-orb {
  position: absolute;
  inset: 20px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, hsla(var(--eva-ice-hsl), 0.6), hsla(200, 50%, 20%, 0.8));
  animation: orbFloat 4s ease-in-out infinite;
}

.empty-orb-ring {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  border: 1px solid hsla(var(--eva-ice-hsl), 0.12);
  animation: ringRotate 8s linear infinite;
}

.empty-orb-ring.ring-2 {
  inset: 0;
  border-color: hsla(var(--eva-pink-hsl), 0.08);
  animation-duration: 12s;
  animation-direction: reverse;
}

@keyframes orbFloat {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}

@keyframes ringRotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.empty-text {
  font-family: 'Sora', sans-serif;
  font-size: 15px;
  font-weight: 400;
  color: var(--eva-text-secondary);
  margin-bottom: 6px;
}

.empty-hint {
  font-size: 12px;
  color: var(--eva-text-dim);
  letter-spacing: 0.02em;
}

/* ── Scroll Button ── */
.scroll-btn {
  position: sticky;
  bottom: 10px;
  align-self: center;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 20px;
  background: var(--eva-surface);
  backdrop-filter: blur(12px);
  border: 1px solid var(--eva-glass-border);
  color: var(--eva-ice);
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  cursor: pointer;
  z-index: 10;
  transition: all 0.25s var(--ease-out-expo);
}

.scroll-btn:hover {
  background: hsla(var(--eva-ice-hsl), 0.1);
  border-color: hsla(var(--eva-ice-hsl), 0.2);
}

/* ── Input Area ── */
.input-area {
  flex-shrink: 0;
  padding: 12px 24px 16px;
  background: hsla(222, 30%, 6%, 0.3);
  backdrop-filter: blur(16px);
  border-top: 1px solid hsla(var(--eva-ice-hsl), 0.06);
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  max-width: 800px;
  margin: 0 auto;
  background: hsla(222, 25%, 10%, 0.5);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.08);
  border-radius: var(--radius-lg);
  padding: 4px 4px 4px 16px;
  transition: border-color 0.35s ease, box-shadow 0.35s ease;
}

.input-wrapper:focus-within {
  border-color: hsla(var(--eva-ice-hsl), 0.2);
  box-shadow: 0 0 0 3px hsla(var(--eva-ice-hsl), 0.06), 0 0 20px hsla(var(--eva-ice-hsl), 0.05);
}

.chat-input {
  flex: 1;
  background: transparent;
  border: none;
  padding: 10px 0;
  color: var(--eva-text);
  font-family: 'DM Sans', sans-serif;
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  outline: none;
  min-height: 42px;
  max-height: 150px;
}

.chat-input::placeholder {
  color: var(--eva-text-dim);
  opacity: 0.7;
}

.send-btn {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: none;
  background: hsla(var(--eva-ice-hsl), 0.08);
  color: var(--eva-text-dim);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.25s var(--ease-out-expo);
}

.send-btn.active {
  background: var(--eva-ice);
  color: var(--eva-dark);
  box-shadow: 0 0 16px hsla(var(--eva-ice-hsl), 0.3);
}

.send-btn.active:hover {
  box-shadow: 0 0 24px hsla(var(--eva-ice-hsl), 0.4);
  transform: scale(1.05);
}

.send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* Streaming dots */
.sending-dots {
  display: flex;
  gap: 3px;
  align-items: center;
}

.sending-dots .dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--eva-ice);
  animation: dotPulse 1.2s ease-in-out infinite;
}

.sending-dots .dot:nth-child(2) { animation-delay: 0.15s; }
.sending-dots .dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes dotPulse {
  0%, 60%, 100% { opacity: 0.2; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1.1); }
}

/* ── Fade Transition ── */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
