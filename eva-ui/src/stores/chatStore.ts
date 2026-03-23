import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  streaming?: boolean
  toolCalls?: any[]
  proactive?: { source: string }
  emotionSnapshot?: any
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)

  function addUserMessage(content: string) {
    messages.value.push({
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now() / 1000,
    })
  }

  function appendStream(correlationId: string, text: string, done: boolean) {
    // Find existing message by correlationId (streaming or finished)
    let msg = messages.value.find(m => m.id === correlationId)
    if (msg) {
      // Already finished — skip duplicate "done" dispatches
      if (!msg.streaming) return
      msg.content += text
      if (done) {
        msg.streaming = false
        isStreaming.value = false
      }
      return
    }
    // Create new streaming message
    msg = {
      id: correlationId,
      role: 'assistant',
      content: text,
      timestamp: Date.now() / 1000,
      streaming: !done,
    }
    messages.value.push(msg)
    isStreaming.value = !done
  }

  function addToolCall(data: any) {
    const msg = [...messages.value].reverse().find((m: ChatMessage) => m.role === 'assistant')
    if (msg) {
      if (!msg.toolCalls) msg.toolCalls = []
      msg.toolCalls.push(data)
    }
  }

  function addProactive(data: any) {
    // Deduplicate: skip if same content was added within the last 5 seconds
    const ts = data.timestamp || Date.now() / 1000
    const isDupe = messages.value.some(
      m => m.proactive && m.content === data.text && Math.abs(m.timestamp - ts) < 5
    )
    if (isDupe) return
    messages.value.push({
      id: `proactive_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      role: 'assistant',
      content: data.text,
      timestamp: ts,
      proactive: { source: data.source },
    })
  }

  return { messages, isStreaming, addUserMessage, appendStream, addToolCall, addProactive }
})
