import { ref } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import client from '@/api/client'

export function useStreaming() {
  const isStreaming = ref(false)

  async function sendStreaming(message: string) {
    const chat = useChatStore()

    chat.addUserMessage(message)
    isStreaming.value = true

    try {
      // Queue the message via REST API — response comes back through WebSocket
      // The WS 'stream' handler in App.vue routes chunks to chatStore.appendStream()
      const res = await client.post('/v1/chat/send', { message })
      const correlationId = res.data?.correlation_id || `msg_${Date.now()}`

      // Create a placeholder for the response that will be filled via WS
      // If no WS stream events arrive within 30s, show a fallback
      const timeout = setTimeout(() => {
        const lastMsg = chat.messages[chat.messages.length - 1]
        if (lastMsg?.role === 'user') {
          // No response received yet — add waiting indicator
          chat.appendStream(correlationId, '', false)
        }
        isStreaming.value = false
      }, 30000)

      // Watch for response completion via store
      const unwatch = setInterval(() => {
        const lastMsg = chat.messages[chat.messages.length - 1]
        if (lastMsg?.role === 'assistant' && !lastMsg.streaming) {
          clearTimeout(timeout)
          clearInterval(unwatch)
          isStreaming.value = false
        }
      }, 500)

    } catch (e) {
      console.error('Send error:', e)
      isStreaming.value = false
    }
  }

  return { isStreaming, sendStreaming }
}
