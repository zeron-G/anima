import client from './client'

export const getStatus = () => client.get('/v1/evolution/status')
export const getHistory = () => client.get('/v1/evolution/history')
export const getGovernance = () => client.get('/v1/evolution/governance')
export const updateGovernanceMode = (mode: string) => client.put('/v1/evolution/governance/mode', { mode })
