import client from './client'

export async function sendMessage(message: string, sessionId?: string) {
  return client.post('/v1/chat/send', { message, session_id: sessionId })
}

export async function streamMessage(message: string): Promise<Response> {
  const token = localStorage.getItem('eva_auth_token') || ''
  const base = import.meta.env.VITE_API_BASE || ''

  // Use fetch for SSE POST (EventSource only supports GET)
  const response = await fetch(`${base}/v1/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({ message }),
  })

  return response
}

export async function getChatHistory(page = 1, limit = 50) {
  return client.get('/v1/chat/history', { params: { page, limit } })
}

export async function getSessions() {
  return client.get('/v1/chat/sessions')
}

export async function markGolden(scene: string, userText: string, evaReply: string, score = 0.85) {
  return client.post('/v1/chat/golden', { scene, user_text: userText, eva_reply: evaReply, score })
}
