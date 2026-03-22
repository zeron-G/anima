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
    // Find or create streaming message
    let msg = messages.value.find(m => m.id === correlationId && m.streaming)
    if (!msg) {
      msg = {
        id: correlationId,
        role: 'assistant',
        content: '',
        timestamp: Date.now() / 1000,
        streaming: true,
      }
      messages.value.push(msg)
      isStreaming.value = true
    }
    msg.content += text
    if (done) {
      msg.streaming = false
      isStreaming.value = false
    }
  }

  function addToolCall(data: any) {
    const msg = [...messages.value].reverse().find((m: ChatMessage) => m.role === 'assistant')
    if (msg) {
      if (!msg.toolCalls) msg.toolCalls = []
      msg.toolCalls.push(data)
    }
  }

  function addProactive(data: any) {
    messages.value.push({
      id: `proactive_${Date.now()}`,
      role: 'assistant',
      content: data.text,
      timestamp: data.timestamp || Date.now() / 1000,
      proactive: { source: data.source },
    })
  }

  return { messages, isStreaming, addUserMessage, appendStream, addToolCall, addProactive }
})
