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
  private _lastChatHistory: any[] = []
  private _lastActivityTs: number = 0
  /** Track correlation IDs already seen via typed protocol to avoid legacy duplication */
  private _seenCorrelationIds: Set<string> = new Set()

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
        const raw = JSON.parse(event.data)

        if (raw.type) {
          // Typed event (new protocol) — track correlation IDs to avoid legacy duplication
          if (raw.type === 'stream' && raw.data?.correlation_id) {
            this._seenCorrelationIds.add(raw.data.correlation_id)
          }
          this.dispatch(raw as WSMessage)
        } else if (raw.emotion || raw.uptime_s !== undefined) {
          // Full snapshot (legacy protocol) — convert to typed events
          this.dispatch({ type: 'heartbeat', data: raw })

          // Extract chat history updates (skip messages already seen via typed protocol)
          if (raw.chat_history) {
            this._lastChatHistory = this._lastChatHistory || []
            const newMsgs = raw.chat_history.slice(this._lastChatHistory.length)
            for (const msg of newMsgs) {
              if (msg.role === 'agent' || msg.role === 'assistant') {
                const cid = `ws_${msg.timestamp || Date.now()}`
                // Skip if this message was already delivered via typed stream events
                if (this._seenCorrelationIds.size > 0) {
                  // Typed protocol is active — legacy chat_history is redundant
                  continue
                }
                this.dispatch({
                  type: 'stream',
                  data: { correlation_id: cid, text: msg.content, done: true }
                })
              }
            }
            this._lastChatHistory = raw.chat_history
          }

          // Extract activity events
          if (raw.activity) {
            const lastAct = raw.activity[raw.activity.length - 1]
            if (lastAct && lastAct.timestamp !== this._lastActivityTs) {
              this._lastActivityTs = lastAct.timestamp
              this.dispatch({ type: 'activity', data: lastAct })
            }
          }
        }
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
