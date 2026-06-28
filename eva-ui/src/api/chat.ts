import client from './client'

export async function sendMessage(message: string, sessionId?: string) {
  return client.post('/v1/chat/send', { message, session_id: sessionId })
}

export interface ChatMessage { role: string; content: string; timestamp: number }
export async function getChatHistory(page = 1, limit = 50) {
  return client.get<{ messages: ChatMessage[]; total: number }>('/v1/chat/history', { params: { page, limit } })
}
export async function getSessions() {
  return client.get('/v1/chat/sessions')
}
export async function markGolden(scene: string, userText: string, evaReply: string, score = 0.85) {
  return client.post('/v1/chat/golden', { scene, user_text: userText, eva_reply: evaReply, score })
}

// ---- SSE token streaming ---------------------------------------------------
// /v1/chat/stream is a POST that returns text/event-stream. EventSource is
// GET-only, so we read the body stream by hand and parse SSE frames. The
// backend emits: activity (thinking/executing/tool_done/responding/error),
// stream_chunk (token text, after the hub fix), and a final message; the
// stream closes when the turn goes idle.
export interface StreamHandlers {
  onActivity?: (stage: string, detail: string) => void
  onChunk?: (chunk: string, eventType: string) => void
  onMessage?: (content: string) => void
  onDone?: () => void
  onError?: (err: unknown) => void
}

export async function streamChat(message: string, h: StreamHandlers): Promise<void> {
  const token = localStorage.getItem('eva_auth_token') || ''
  const base = import.meta.env.VITE_API_BASE || ''
  let res: Response
  try {
    res = await fetch(`${base}/v1/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ message }),
    })
  } catch (e) { h.onError?.(e); return }
  if (res.status === 401) { localStorage.removeItem('eva_auth_token'); h.onError?.(new Error('unauthorized')); return }
  if (!res.ok || !res.body) { h.onError?.(new Error(`HTTP ${res.status}`)); return }

  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf = (buf + dec.decode(value, { stream: true })).replace(/\r\n/g, '\n')
      let i: number
      while ((i = buf.indexOf('\n\n')) >= 0) {
        dispatchFrame(buf.slice(0, i), h)
        buf = buf.slice(i + 2)
      }
    }
    if (buf.trim()) dispatchFrame(buf, h)
  } catch (e) { h.onError?.(e); return }
  h.onDone?.()
}

function dispatchFrame(frame: string, h: StreamHandlers) {
  let ev = 'message', dataStr = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) ev = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
  }
  if (!dataStr) return
  let data: any
  try { data = JSON.parse(dataStr) } catch { return }
  if (ev === 'activity') h.onActivity?.(data.stage || '', data.detail || '')
  else if (ev === 'stream_chunk') h.onChunk?.(data.chunk || '', data.event_type || 'text')
  else if (ev === 'message') h.onMessage?.(data.content || '')
}
