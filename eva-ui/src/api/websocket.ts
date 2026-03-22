import { ref } from 'vue'

export type WSMessageType = 'heartbeat' | 'stream' | 'tool_call' | 'activity' |
  'thinking' | 'proactive' | 'evolution' | 'emotion_shift' | 'node_event'

export interface WSMessage {
  type: WSMessageType
  data: any
}

type MessageHandler = (msg: WSMessage) => void

class EvaWebSocket {
  private ws: WebSocket | null = null
  private reconnectDelay = 1000
  private handlers: Map<WSMessageType, MessageHandler[]> = new Map()
  public connected = ref(false)

  connect() {
    const base = import.meta.env.VITE_WS_BASE || window.location.host
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem('eva_auth_token') || ''

    this.ws = new WebSocket(`${protocol}//${base}/ws?token=${token}`)

    this.ws.onopen = () => {
      this.connected.value = true
      this.reconnectDelay = 1000
    }

    this.ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        this.dispatch(msg)
      } catch (e) {
        console.warn('WS parse error:', e)
      }
    }

    this.ws.onclose = () => {
      this.connected.value = false
      setTimeout(() => this.connect(), this.reconnectDelay)
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000)
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  on(type: WSMessageType, handler: MessageHandler) {
    if (!this.handlers.has(type)) this.handlers.set(type, [])
    this.handlers.get(type)!.push(handler)
  }

  off(type: WSMessageType, handler: MessageHandler) {
    const list = this.handlers.get(type)
    if (list) {
      const idx = list.indexOf(handler)
      if (idx >= 0) list.splice(idx, 1)
    }
  }

  send(data: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  private dispatch(msg: WSMessage) {
    const handlers = this.handlers.get(msg.type) || []
    for (const h of handlers) {
      try { h(msg) } catch (e) { console.error('WS handler error:', e) }
    }
  }

  disconnect() {
    this.ws?.close()
  }
}

export const ws = new EvaWebSocket()
