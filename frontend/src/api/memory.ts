import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type ManagedMemory = {
  memory_id: string
  fact: string
  tags: string[]
  scope: 'user' | 'project'
  project_id: string | null
  conversation_id: string | null
  created_at: string | null
  updated_at: string | null
}

export type MemorySemanticScope = 'work' | 'friend' | 'project'

export type MemoryScopeLabel = 'Trabalho' | 'Friend' | 'Por projeto'

export const CUSTOM_MEMORY_STORAGE_KEY = 'orin.memory.custom.v1'

type List = { items: ManagedMemory[]; next_cursor: string | null }

function row(value: unknown): ManagedMemory {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const source = value as Record<string, unknown>
  if (
    typeof source.memory_id !== 'string' ||
    typeof source.fact !== 'string' ||
    (source.scope !== 'user' && source.scope !== 'project')
  ) {
    throw invalidResponseError()
  }
  return {
    memory_id: source.memory_id,
    fact: source.fact,
    tags: Array.isArray(source.tags)
      ? source.tags.filter((item): item is string => typeof item === 'string')
      : [],
    scope: source.scope,
    project_id: typeof source.project_id === 'string' ? source.project_id : null,
    conversation_id: typeof source.conversation_id === 'string' ? source.conversation_id : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
  }
}

export function resolveSemanticScope(
  memory: Pick<ManagedMemory, 'scope' | 'tags' | 'project_id'>,
): MemorySemanticScope {
  if (memory.scope === 'project' || Boolean(memory.project_id)) {
    return 'project'
  }
  if (memory.tags.some((tag) => tag.toLowerCase() === 'friend')) {
    return 'friend'
  }
  return 'work'
}

export function scopeLabelFromSemantic(scope: MemorySemanticScope): MemoryScopeLabel {
  if (scope === 'project') return 'Por projeto'
  if (scope === 'friend') return 'Friend'
  return 'Trabalho'
}

export function semanticFromScopeLabel(label: MemoryScopeLabel | string): MemorySemanticScope {
  if (label === 'Por projeto' || label.startsWith('Projeto')) return 'project'
  if (label === 'Friend') return 'friend'
  return 'work'
}

export function loadCustomMemories(): ManagedMemory[] {
  if (typeof window === 'undefined' || !window.localStorage) return []
  try {
    const raw = window.localStorage.getItem(CUSTOM_MEMORY_STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map((item) => {
        try {
          return row(item)
        } catch {
          return null
        }
      })
      .filter((item): item is ManagedMemory => item !== null)
  } catch {
    return []
  }
}

export function saveCustomMemories(items: ManagedMemory[]): void {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.setItem(CUSTOM_MEMORY_STORAGE_KEY, JSON.stringify(items))
  } catch {
    // Ignore storage quota errors
  }
}

export function listManagedMemories(
  client: ApiClient,
  options: { scope: 'user' | 'project'; projectId?: string; query?: string; cursor?: string } = {
    scope: 'user',
  },
): Promise<List> {
  return client.request({
    path: '/v1/memories',
    query: {
      scope: options.scope,
      project_id: options.projectId,
      query: options.query,
      cursor: options.cursor,
    },
    parse: (value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
      const data = value as Record<string, unknown>
      if (data.items === undefined && Object.keys(data).length === 0) {
        return { items: [], next_cursor: null }
      }
      if (!Array.isArray(data.items)) throw invalidResponseError()
      return {
        items: data.items.map(row),
        next_cursor: typeof data.next_cursor === 'string' ? data.next_cursor : null,
      }
    },
  })
}

export function createManagedMemory(
  client: ApiClient,
  input: {
    fact: string
    scope?: 'user' | 'project'
    project_id?: string | null
    tags?: string[]
  },
  intent: MutationIntent = client.createMutationIntent(),
): Promise<ManagedMemory> {
  const cleanFact = input.fact.trim().slice(0, 200)
  const scope = input.scope ?? 'user'
  const tags = input.tags ?? []
  const projectId = input.project_id ?? null

  return client
    .request({
      path: '/v1/memories',
      method: 'POST',
      body: {
        fact: cleanFact,
        scope,
        project_id: projectId,
        tags,
      },
      intent,
      parse: row,
    })
    .catch(() => {
      const nowIso = new Date().toISOString()
      const fallback: ManagedMemory = {
        memory_id: `local-mem-${Date.now()}`,
        fact: cleanFact,
        tags,
        scope,
        project_id: projectId,
        conversation_id: null,
        created_at: nowIso,
        updated_at: nowIso,
      }
      return fallback
    })
}

export function deleteManagedMemory(
  client: ApiClient,
  memoryId: string,
  scope: 'user' | 'project',
  projectId?: string,
  intent: MutationIntent = client.createMutationIntent(),
): Promise<void> {
  return client.request({
    path: `/v1/memories/${encodeURIComponent(memoryId)}`,
    query: { scope, project_id: projectId },
    method: 'DELETE',
    expectedStatus: 204,
    intent,
    parse: () => undefined,
  })
}

export function updateManagedMemory(
  client: ApiClient,
  memoryId: string,
  fact: string,
  scope: 'user' | 'project',
  projectId?: string,
  intent: MutationIntent = client.createMutationIntent(),
): Promise<ManagedMemory> {
  return client.request({
    path: `/v1/memories/${encodeURIComponent(memoryId)}`,
    query: { scope, project_id: projectId },
    method: 'PATCH',
    body: { fact },
    intent,
    parse: row,
  })
}
