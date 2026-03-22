import { ref } from 'vue'
import { useChatStore } from '@/stores/chatStore'

export function useStreaming() {
  const isStreaming = ref(false)

  async function sendStreaming(message: string) {
    const chat = useChatStore()
    const token = localStorage.getItem('eva_auth_token') || ''
    const base = import.meta.env.VITE_API_BASE || ''

    chat.addUserMessage(message)
    isStreaming.value = true

    try {
      const response = await fetch(`${base}/v1/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ message }),
      })

      if (!response.ok || !response.body) {
        // Fallback to non-streaming
        const { sendMessage } = await import('@/api/chat')
        await sendMessage(message)
        isStreaming.value = false
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      const correlationId = `stream_${Date.now()}`
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              const cid = data.correlation_id || correlationId

              if (data.type === 'stream_chunk' || data.chunk) {
                chat.appendStream(cid, data.chunk || data.text || '', false)
              } else if (data.type === 'tool_call') {
                chat.addToolCall(data)
              } else if (data.type === 'done' || data.done) {
                chat.appendStream(cid, '', true)
              }
            } catch {
              // Ignore malformed SSE lines
            }
          }
          if (line.startsWith('event: done')) {
            chat.appendStream(correlationId, '', true)
          }
        }
      }
    } catch (e) {
      console.error('Streaming error:', e)
    } finally {
      isStreaming.value = false
    }
  }

  return { isStreaming, sendStreaming }
}
