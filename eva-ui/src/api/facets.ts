// Typed fetchers for the deeper strata — wired to the real /v1 REST shapes.
import client from './client'

// ---- soul -------------------------------------------------------------
export interface Emotion {
  engagement: number; confidence: number; curiosity: number; concern: number
  user_state: string; intensity: number; mood_label: string
  arousal: number; valence: number
}
export const getEmotion = () => client.get<Emotion>('/v1/soulscape/emotion').then(r => r.data)
export const getPersonality = () => client.get<{ content: string }>('/v1/soulscape/personality').then(r => r.data.content)
export const getRelationship = () => client.get<{ content: string }>('/v1/soulscape/relationship').then(r => r.data.content)
export const getGrowthLog = () => client.get<{ content: string }>('/v1/soulscape/growth-log').then(r => r.data.content)
export const getDrift = (limit = 12) =>
  client.get<{ entries: any[]; total: number }>('/v1/soulscape/drift', { params: { limit } }).then(r => r.data)

// ---- memory -----------------------------------------------------------
export interface MemoryRow { id: string; content: string; type: string; importance: number; created_at: number }
export const getMemoryStats = () =>
  client.get<{ total: number; by_type: Record<string, number> }>('/v1/memory/stats').then(r => r.data)
export const getRecentMemories = (limit = 8) =>
  client.get<{ results: MemoryRow[]; count: number }>('/v1/memory/recent', { params: { limit } }).then(r => r.data.results)
export const getDocuments = () =>
  client.get<{ documents: any[] }>('/v1/memory/documents').then(r => r.data.documents)

// ---- evolution --------------------------------------------------------
export interface EvolutionStatus {
  running: boolean; current: any; queue_size: number; evolutions_this_hour: number
  consecutive_failures: number; cooldown_remaining: number; last_failure: any
  memory: { successes: number; failures: number; goals: number; anti_patterns: number }
}
export const getEvolutionStatus = () => client.get<EvolutionStatus>('/v1/evolution/status').then(r => r.data)
export const getEvolutionHistory = () =>
  client.get<{ successes: any[]; failures: any[]; goals: any[] }>('/v1/evolution/history').then(r => r.data)
export const getGovernance = () =>
  client.get<{ activity_level: string; drift_scores: number[]; recent_self_thinking: any[]; quiet_ratio?: number }>('/v1/evolution/governance').then(r => r.data)

// ---- network ----------------------------------------------------------
export interface NodeReach { host: string; port: number; transport: string; label: string; address: string }
export interface MeshNode {
  node_id: string; hostname: string; agent_name: string; status: string
  is_self: boolean; chat_available: boolean; embodiment: string; uptime_s: number
  runtime_role: string; compute_tier: number; reachability: NodeReach
}
export const getNodes = () =>
  client.get<{ enabled: boolean; alive_count: number; nodes: MeshNode[] }>('/v1/network/nodes').then(r => r.data)
export const getChannels = () =>
  client.get<{ channels: { name: string; connected: boolean; type: string }[] }>('/v1/network/channels').then(r => r.data.channels)

// ---- health / system --------------------------------------------------
export interface Component { health: string; state: string; detail: string; self_healed: boolean; attempts: number }
export interface SentinelStatus {
  overall: string; detail?: string; node_id?: string; sentinel_tick?: number
  active_repairs?: number; components: Record<string, Component>
}
export const getStatus = () => client.get<SentinelStatus>('/v1/status', { validateStatus: () => true }).then(r => r.data)
export const getSystem = () =>
  client.get<{ version: string; uptime_s: number; agent_name: string; python_version: string; memory_backend: string }>('/v1/settings/system').then(r => r.data)
export const getUsage = () => client.get<Record<string, any>>('/v1/settings/usage').then(r => r.data)
