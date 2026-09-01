import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type ManagedMemory = { memory_id: string; fact: string; tags: string[]; scope: 'user' | 'project'; project_id: string | null; conversation_id: string | null; created_at: string | null; updated_at: string | null }
type List = { items: ManagedMemory[]; next_cursor: string | null }

function row(value: unknown): ManagedMemory {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const source = value as Record<string, unknown>
  if (typeof source.memory_id !== 'string' || typeof source.fact !== 'string' || (source.scope !== 'user' && source.scope !== 'project')) throw invalidResponseError()
  return { memory_id: source.memory_id, fact: source.fact, tags: Array.isArray(source.tags) ? source.tags.filter((item): item is string => typeof item === 'string') : [], scope: source.scope, project_id: typeof source.project_id === 'string' ? source.project_id : null, conversation_id: typeof source.conversation_id === 'string' ? source.conversation_id : null, created_at: typeof source.created_at === 'string' ? source.created_at : null, updated_at: typeof source.updated_at === 'string' ? source.updated_at : null }
}

export function listManagedMemories(client: ApiClient, options: { scope: 'user' | 'project'; projectId?: string; query?: string; cursor?: string } = {} as { scope: 'user' | 'project' }): Promise<List> {
  return client.request({ path: '/v1/memories', query: { scope: options.scope, project_id: options.projectId, query: options.query, cursor: options.cursor }, parse: (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
    const data = value as Record<string, unknown>
    if (!Array.isArray(data.items)) throw invalidResponseError()
    return { items: data.items.map(row), next_cursor: typeof data.next_cursor === 'string' ? data.next_cursor : null }
  } })
}

export function deleteManagedMemory(client: ApiClient, memoryId: string, scope: 'user' | 'project', projectId?: string, intent: MutationIntent = client.createMutationIntent()): Promise<void> {
  return client.request({ path: `/v1/memories/${encodeURIComponent(memoryId)}`, query: { scope, project_id: projectId }, method: 'DELETE', expectedStatus: 204, intent, parse: () => undefined })
}

export function updateManagedMemory(client: ApiClient, memoryId: string, fact: string, scope: 'user' | 'project', projectId?: string, intent: MutationIntent = client.createMutationIntent()): Promise<ManagedMemory> {
  return client.request({ path: `/v1/memories/${encodeURIComponent(memoryId)}`, query: { scope, project_id: projectId }, method: 'PATCH', body: { fact }, intent, parse: row })
}
