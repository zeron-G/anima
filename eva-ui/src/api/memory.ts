import client from './client'

export const search = (q: string, limit = 10, type?: string) =>
  client.get('/v1/memory/search', { params: { q, limit, type } })
export const getRecent = (limit = 20, type?: string) =>
  client.get('/v1/memory/recent', { params: { limit, type } })
export const getStats = () => client.get('/v1/memory/stats')
export const getDocuments = () => client.get('/v1/memory/documents')
export const searchDocuments = (q: string, limit = 5) =>
  client.get('/v1/memory/documents/search', { params: { q, limit } })
export const importDocument = (filePath: string, description = '') =>
  client.post('/v1/memory/documents/import', { file_path: filePath, description })
export const deleteDocument = (id: string) => client.delete(`/v1/memory/documents/${id}`)
