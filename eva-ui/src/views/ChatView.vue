<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useEmotionStore } from '@/stores/emotionStore'
import { useStatusStore } from '@/stores/statusStore'
import { useStreaming } from '@/composables/useStreaming'
import MessageBubble from '@/components/chat/MessageBubble.vue'

const chat = useChatStore()
const emotion = useEmotionStore()
const status = useStatusStore()
const { isStreaming, sendStreaming } = useStreaming()

const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const messagesRef = ref<HTMLDivElement>()
const userScrolled = ref(false)

// Auto-scroll to bottom on new messages
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

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 150) + 'px'
}
</script>

<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-identity">
            <div class="identity-dot" />
            <h2 class="header-name">Eva</h2>
          </div>
          <div class="header-sep" />
          <div class="mood-chip">
            <span class="mood-text">{{ emotion.current.mood_label }}</span>
          </div>
        </div>
        <div class="header-right">
          <div class="header-metric">
            <span class="metric-label">Model</span>
            <span class="metric-value">{{ status.activeTier || 'default' }}</span>
          </div>
          <div class="header-sep" />
          <div class="header-metric">
            <span class="metric-label">Engagement</span>
            <div class="metric-bar">
              <div class="metric-fill" :style="{ width: `${emotion.current.engagement * 100}%` }" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Messages -->
    <div ref="messagesRef" class="messages-container" @scroll="onScroll">
      <div class="messages-inner">
        <!-- Empty state -->
        <div v-if="chat.messages.length === 0" class="empty-state">
          <div class="empty-visual">
            <div class="empty-ring ring-1" />
            <div class="empty-ring ring-2" />
            <div class="empty-core" />
          </div>
          <p class="empty-title">Start a conversation</p>
          <p class="empty-hint">Enter to send / Shift+Enter for new line</p>
        </div>

        <MessageBubble
          v-for="msg in chat.messages"
          :key="msg.id"
          :message="msg"
        />

        <!-- Scroll to bottom -->
        <transition name="fade">
          <button v-if="userScrolled" class="scroll-btn" @click="userScrolled = false; scrollToBottom()">
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
      <div class="input-wrapper" :class="{ focused: false }">
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
          <svg v-if="!isStreaming" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7"/>
          </svg>
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
  padding: 0 32px;
  border-bottom: 1px solid var(--border);
  background: rgba(5, 5, 8, 0.5);
  backdrop-filter: blur(20px);
}

.header-content {
  max-width: 840px;
  margin: 0 auto;
  padding: 16px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.identity-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 10px rgba(var(--accent-rgb), 0.4);
  animation: dotPulse 3s ease-in-out infinite;
}

@keyframes dotPulse {
  0%, 100% { opacity: 0.6; box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.3); }
  50% { opacity: 1; box-shadow: 0 0 16px rgba(var(--accent-rgb), 0.5); }
}

.header-name {
  font-family: var(--font-heading);
  font-size: 16px;
  font-weight: 500;
  color: var(--text);
  letter-spacing: 0.5px;
}

.header-sep {
  width: 1px;
  height: 16px;
  background: var(--border-hover);
}

.mood-chip {
  padding: 3px 12px;
  border-radius: 100px;
  border: 1px solid var(--border);
  background: rgba(var(--accent-rgb), 0.04);
}

.mood-text {
  font-family: var(--font-heading);
  font-size: 11px;
  font-weight: 400;
  color: var(--text-secondary);
  text-transform: capitalize;
  letter-spacing: 0.5px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-metric {
  display: flex;
  align-items: center;
  gap: 8px;
}

.metric-label {
  font-family: var(--font-heading);
  font-size: 10px;
  font-weight: 400;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 1.5px;
}

.metric-value {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}

.metric-bar {
  width: 48px;
  height: 3px;
  border-radius: 2px;
  background: rgba(var(--accent-rgb), 0.1);
  overflow: hidden;
}

.metric-fill {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent), rgba(var(--magenta-rgb), 0.7));
  transition: width 1s ease;
}

/* ── Messages ── */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
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
  padding: 100px 0;
}

.empty-visual {
  position: relative;
  width: 80px;
  height: 80px;
  margin-bottom: 28px;
}

.empty-core {
  position: absolute;
  inset: 28px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, rgba(var(--accent-rgb), 0.5), rgba(0, 120, 100, 0.6));
  box-shadow: 0 0 20px rgba(var(--accent-rgb), 0.2);
  animation: coreFloat 4s ease-in-out infinite;
}

.empty-ring {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(var(--accent-rgb), 0.1);
}

.empty-ring.ring-1 {
  inset: 8px;
  animation: ringRotate 10s linear infinite;
}

.empty-ring.ring-2 {
  inset: 0;
  border-color: rgba(var(--magenta-rgb), 0.06);
  animation: ringRotate 14s linear infinite reverse;
}

@keyframes coreFloat {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}

@keyframes ringRotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.empty-title {
  font-family: var(--font-heading);
  font-size: 15px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.empty-hint {
  font-size: 12px;
  color: var(--text-dim);
  letter-spacing: 0.5px;
}

/* ── Scroll Button ── */
.scroll-btn {
  position: sticky;
  bottom: 12px;
  align-self: center;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border-radius: 100px;
  background: var(--surface);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  color: var(--accent);
  font-family: var(--font-body);
  font-size: 11px;
  cursor: pointer;
  z-index: 10;
  transition: all var(--transition-fast);
}

.scroll-btn:hover {
  background: rgba(var(--accent-rgb), 0.08);
  border-color: rgba(var(--accent-rgb), 0.2);
}

/* ── Input Area ── */
.input-area {
  flex-shrink: 0;
  padding: 16px 32px 20px;
  background: rgba(5, 5, 8, 0.4);
  backdrop-filter: blur(20px);
  border-top: 1px solid var(--border);
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  max-width: 840px;
  margin: 0 auto;
  background: rgba(10, 10, 20, 0.5);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 4px 6px 4px 20px;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.input-wrapper:focus-within {
  border-color: rgba(var(--accent-rgb), 0.2);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.05), 0 0 24px rgba(var(--accent-rgb), 0.04);
}

.chat-input {
  flex: 1;
  background: transparent;
  border: none;
  padding: 12px 0;
  color: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  outline: none;
  min-height: 44px;
  max-height: 150px;
}

.chat-input::placeholder {
  color: var(--text-dim);
}

.send-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: none;
  background: rgba(var(--accent-rgb), 0.06);
  color: var(--text-dim);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition-fast);
}

.send-btn.active {
  background: var(--accent);
  color: var(--bg);
  box-shadow: 0 0 20px rgba(var(--accent-rgb), 0.3);
}

.send-btn.active:hover {
  box-shadow: 0 0 28px rgba(var(--accent-rgb), 0.4);
  transform: scale(1.05);
}

.send-btn:disabled {
  opacity: 0.25;
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
  background: var(--accent);
  animation: streamDot 1.2s ease-in-out infinite;
}

.sending-dots .dot:nth-child(2) { animation-delay: 0.15s; }
.sending-dots .dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes streamDot {
  0%, 60%, 100% { opacity: 0.2; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1.1); }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
