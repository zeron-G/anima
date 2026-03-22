import client from './client'

export const getConfig = () => client.get('/v1/settings/config')
export const updateConfig = (key: string, value: any) => client.put('/v1/settings/config', { key, value })
export const getSkills = () => client.get('/v1/settings/skills')
export const installSkill = (source: string, name?: string) => client.post('/v1/settings/skills/install', { source, name })
export const uninstallSkill = (name: string) => client.delete(`/v1/settings/skills/${name}`)
export const getSystemInfo = () => client.get('/v1/settings/system')
export const getUsage = () => client.get('/v1/settings/usage')
export const restart = () => client.post('/v1/settings/restart')
export const shutdown = () => client.post('/v1/settings/shutdown')
export const getTraces = () => client.get('/v1/settings/traces')
