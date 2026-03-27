import client from './client'

export interface NetworkAddress {
  url: string
  host: string
  transport: string
  label: string
  active: boolean
}

export interface NetworkRobotLink {
  available: boolean
  node_id?: string
  name?: string
  role?: string
  connected?: boolean
  connected_url?: string
  current_transport?: string
  current_transport_label?: string
  addresses?: NetworkAddress[]
  tags?: string[]
  metadata?: Record<string, unknown>
}

export interface NetworkReachability {
  host: string
  port: number
  transport: string
  label: string
  address: string
}

export interface NetworkNode {
  node_id: string
  hostname: string
  agent_name: string
  ip: string
  port: number
  status: string
  current_load: number
  emotion: Record<string, unknown>
  compute_tier: number
  runtime_profile: string
  runtime_role: string
  platform_class: string
  embodiment: string
  labels: string[]
  uptime_s: number
  active_sessions: string[]
  is_self: boolean
  chat_available: boolean
  reachability: NetworkReachability
  robotics: NetworkRobotLink
}

export interface NetworkSnapshot {
  enabled: boolean
  alive_count: number
  nodes: NetworkNode[]
}

export interface ChannelSnapshot {
  name: string
  connected: boolean
  type: string
}

export interface RemoteConversationMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  transport?: string
  task_id?: string
  node_name?: string
  error?: boolean
}

export interface RemoteConversationSnapshot {
  node_id: string
  messages: RemoteConversationMessage[]
}

export const getNodes = () => client.get<NetworkSnapshot>('/v1/network/nodes')
export const getChannels = () => client.get<{ channels: ChannelSnapshot[] }>('/v1/network/channels')

export const getNodeConversation = (nodeId: string, limit = 50) =>
  client.get<RemoteConversationSnapshot>(`/v1/network/nodes/${nodeId}/conversation`, {
    params: { limit },
  })

export const sendNodeMessage = (nodeId: string, message: string, timeout = 90) =>
  client.post<{
    status: string
    node_id: string
    task_id: string
    reply: string
    message: RemoteConversationMessage
    conversation: RemoteConversationMessage[]
  }>(`/v1/network/nodes/${nodeId}/chat`, { message, timeout })
