import client from './client'

export interface RobotPerception {
  distance_cm: number
  touch: string
  pitch_deg: number
  roll_deg: number
  battery_v: number
  is_lifted: boolean
  is_obstacle_near: boolean
  is_obstacle_warn: boolean
  timestamp: number
}

export interface RobotExplorationEntry {
  kind: string
  detail: string
  timestamp: number
  command?: string
}

export interface RobotExplorationState {
  running: boolean
  mode: string
  goal: string
  tick_count: number
  last_decision: string
  last_command: string
  last_command_ts: number
  last_error: string
  history: RobotExplorationEntry[]
}

export interface RobotNode {
  node_id: string
  name: string
  kind: string
  role: string
  base_urls: string[]
  connected: boolean
  connected_url: string
  last_ok_ts: number
  last_refresh_ts: number
  last_error: string
  state: string
  emotion: string
  queue_size: number
  raw_status: Record<string, unknown>
  perception: RobotPerception
  exploration: RobotExplorationState
  capabilities: string[]
  tags: string[]
  metadata: Record<string, unknown>
}

export interface RoboticsSnapshot {
  enabled: boolean
  node_count: number
  nodes: RobotNode[]
}

export const getRobotNodes = (refresh = true) =>
  client.get<RoboticsSnapshot>('/v1/robotics/nodes', {
    params: { refresh: refresh ? 1 : 0 },
  })

export const getRobotNode = (nodeId: string, refresh = true) =>
  client.get<RobotNode>(`/v1/robotics/nodes/${nodeId}`, {
    params: { refresh: refresh ? 1 : 0 },
  })

export const sendRobotCommand = (
  nodeId: string,
  command: string,
  params: Record<string, unknown> = {},
) => client.post(`/v1/robotics/nodes/${nodeId}/command`, { command, params })

export const sendRobotNlp = (nodeId: string, text: string) =>
  client.post(`/v1/robotics/nodes/${nodeId}/nlp`, { text })

export const speakFromRobot = (nodeId: string, text: string, blocking = false) =>
  client.post(`/v1/robotics/nodes/${nodeId}/speak`, { text, blocking })

export const startRobotExploration = (
  nodeId: string,
  goal: string,
  policy: Record<string, unknown> = {},
) => client.post(`/v1/robotics/nodes/${nodeId}/exploration/start`, { goal, policy })

export const stopRobotExploration = (nodeId: string, reason = 'desktop_stop') =>
  client.post(`/v1/robotics/nodes/${nodeId}/exploration/stop`, { reason })
