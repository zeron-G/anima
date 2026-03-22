import client from './client'

export const getNodes = () => client.get('/v1/network/nodes')
export const getChannels = () => client.get('/v1/network/channels')
