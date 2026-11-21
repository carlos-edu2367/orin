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

export type MemoryLifecycleStatus =
  | 'active'
  | 'proposed'
  | 'quarantined'
  | 'superseded'
  | 'invalid'
  | 'deleted'

export type MemoryReviewKind = 'conflict' | 'quarantined' | 'proposed' | null

export type ConflictResolution = 'keep_new' | 'keep_old' | 'keep_both'

export type MemoryReviewItem = {
  memory_id: string
  fact: string
  tags: string[]
  kind: string
  scope_type: 'user' | 'project' | 'agent' | 'friend'
  scope_id: string
  project_id: string | null
  conversation_id: string | null
  agent_key: string | null
  status: MemoryLifecycleStatus
  source: string
  source_kind: string
  source_ref: string | null
  trust: number
  confidence: number
  superseded_by: string | null
  valid_from: string | null
  invalid_at: string | null
  hit_count: number
  last_used_at: string | null
  created_at: string | null
  updated_at: string | null
  review_kind: MemoryReviewKind
  can_restore: boolean
  conflicting_memory: MemoryReviewItem | null
}

export type MemoryReviewCounts = {
  active: number
  pending: number
  proposed: number
  quarantined: number
  conflicts: number
  history: number
}

export type MemoryReviewListResponse = {
  items: MemoryReviewItem[]
  counts: MemoryReviewCounts
  agent_keys: string[]
}

export type MemoryEventRecord = {
  id: number
  memory_id: string
  event: string
  old_text: string | null
  new_text: string | null
  related_id: string | null
  actor: string
  conversation_id: string | null
  turn_id: string | null
  created_at: string | null
}

export type MemoryHistoryResponse = {
  memory_id: string
  memory: MemoryReviewItem
  events: MemoryEventRecord[]
}

export type MemoryMutationResponse = {
  memory: MemoryReviewItem
  status: MemoryLifecycleStatus
  resolution?: string
  conflicting_memory?: MemoryReviewItem | null
  superseded_memory_id?: string | null
}

const VALID_REVIEW_STATUSES: readonly MemoryLifecycleStatus[] = [
  'active',
  'proposed',
  'quarantined',
  'superseded',
  'invalid',
  'deleted',
]

const VALID_REVIEW_SCOPES: readonly MemoryReviewItem['scope_type'][] = [
  'user',
  'project',
  'agent',
  'friend',
]

function parseReviewItem(value: unknown): MemoryReviewItem {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const source = value as Record<string, unknown>
  if (typeof source.memory_id !== 'string' || typeof source.fact !== 'string') {
    throw invalidResponseError()
  }
  const status = VALID_REVIEW_STATUSES.includes(source.status as MemoryLifecycleStatus)
    ? (source.status as MemoryLifecycleStatus)
    : 'active'
  const scopeType = VALID_REVIEW_SCOPES.includes(source.scope_type as MemoryReviewItem['scope_type'])
    ? (source.scope_type as MemoryReviewItem['scope_type'])
    : 'user'
  const reviewKind =
    source.review_kind === 'conflict' ||
    source.review_kind === 'quarantined' ||
    source.review_kind === 'proposed'
      ? source.review_kind
      : null

  return {
    memory_id: source.memory_id,
    fact: source.fact,
    tags: Array.isArray(source.tags)
      ? source.tags.filter((item): item is string => typeof item === 'string')
      : [],
    kind: typeof source.kind === 'string' ? source.kind : 'fact',
    scope_type: scopeType,
    scope_id: typeof source.scope_id === 'string' ? source.scope_id : '',
    project_id: typeof source.project_id === 'string' ? source.project_id : null,
    conversation_id: typeof source.conversation_id === 'string' ? source.conversation_id : null,
    agent_key: typeof source.agent_key === 'string' ? source.agent_key : null,
    status,
    source: typeof source.source === 'string' ? source.source : 'agent_learned',
    source_kind: typeof source.source_kind === 'string' ? source.source_kind : 'agent',
    source_ref: typeof source.source_ref === 'string' ? source.source_ref : null,
    trust: typeof source.trust === 'number' ? source.trust : 1,
    confidence: typeof source.confidence === 'number' ? source.confidence : 1,
    superseded_by: typeof source.superseded_by === 'string' ? source.superseded_by : null,
    valid_from: typeof source.valid_from === 'string' ? source.valid_from : null,
    invalid_at: typeof source.invalid_at === 'string' ? source.invalid_at : null,
    hit_count: typeof source.hit_count === 'number' ? source.hit_count : 0,
    last_used_at: typeof source.last_used_at === 'string' ? source.last_used_at : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
    review_kind: reviewKind,
    can_restore: Boolean(source.can_restore),
    conflicting_memory:
      source.conflicting_memory && typeof source.conflicting_memory === 'object'
        ? parseReviewItem(source.conflicting_memory)
        : null,
  }
}

function parseEventRecord(value: unknown): MemoryEventRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const source = value as Record<string, unknown>
  if (typeof source.memory_id !== 'string' || typeof source.event !== 'string') {
    throw invalidResponseError()
  }
  const isForgotten = source.event === 'forgotten'
  return {
    id: typeof source.id === 'number' ? source.id : 0,
    memory_id: source.memory_id,
    event: source.event,
    old_text: isForgotten ? null : typeof source.old_text === 'string' ? source.old_text : null,
    new_text: isForgotten ? null : typeof source.new_text === 'string' ? source.new_text : null,
    related_id: typeof source.related_id === 'string' ? source.related_id : null,
    actor: typeof source.actor === 'string' ? source.actor : 'system',
    conversation_id: typeof source.conversation_id === 'string' ? source.conversation_id : null,
    turn_id: typeof source.turn_id === 'string' ? source.turn_id : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
  }
}

function parseMutationResponse(value: unknown): MemoryMutationResponse {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const memory = parseReviewItem(data.memory)
  return {
    memory,
    status: VALID_REVIEW_STATUSES.includes(data.status as MemoryLifecycleStatus)
      ? (data.status as MemoryLifecycleStatus)
      : memory.status,
    resolution: typeof data.resolution === 'string' ? data.resolution : undefined,
    conflicting_memory:
      data.conflicting_memory && typeof data.conflicting_memory === 'object'
        ? parseReviewItem(data.conflicting_memory)
        : null,
    superseded_memory_id:
      typeof data.superseded_memory_id === 'string' ? data.superseded_memory_id : null,
  }
}

export function listMemoryReview(
  client: ApiClient,
  options: {
    projectId?: string
    agentKey?: string
    status?: string
    scopeType?: string
    query?: string
    limit?: number
  } = {},
): Promise<MemoryReviewListResponse> {
  return client.request({
    path: '/v1/memory/review',
    query: {
      project_id: options.projectId,
      agent_key: options.agentKey,
      status: options.status,
      scope_type: options.scopeType,
      query: options.query,
      limit: options.limit,
    },
    parse: (value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
      const data = value as Record<string, unknown>
      const rawCounts =
        data.counts && typeof data.counts === 'object' && !Array.isArray(data.counts)
          ? (data.counts as Record<string, unknown>)
          : {}
      return {
        items: Array.isArray(data.items) ? data.items.map(parseReviewItem) : [],
        counts: {
          active: typeof rawCounts.active === 'number' ? rawCounts.active : 0,
          pending: typeof rawCounts.pending === 'number' ? rawCounts.pending : 0,
          proposed: typeof rawCounts.proposed === 'number' ? rawCounts.proposed : 0,
          quarantined: typeof rawCounts.quarantined === 'number' ? rawCounts.quarantined : 0,
          conflicts: typeof rawCounts.conflicts === 'number' ? rawCounts.conflicts : 0,
          history: typeof rawCounts.history === 'number' ? rawCounts.history : 0,
        },
        agent_keys: Array.isArray(data.agent_keys)
          ? data.agent_keys.filter((item): item is string => typeof item === 'string')
          : [],
      }
    },
  })
}

export function approveMemory(
  client: ApiClient,
  memoryId: string,
  options: {
    resolution?: ConflictResolution
    conflictingMemoryId?: string
  } = {},
  intent: MutationIntent = client.createMutationIntent(),
): Promise<MemoryMutationResponse> {
  const body: Record<string, unknown> = {}
  if (options.resolution) body.resolution = options.resolution
  if (options.conflictingMemoryId) body.conflicting_memory_id = options.conflictingMemoryId
  return client.request({
    path: `/v1/memory/${encodeURIComponent(memoryId)}/approve`,
    method: 'POST',
    body: Object.keys(body).length > 0 ? body : undefined,
    intent,
    parse: parseMutationResponse,
  })
}

export function resolveMemoryConflict(
  client: ApiClient,
  memoryId: string,
  resolution: ConflictResolution,
  conflictingMemoryId?: string,
  intent: MutationIntent = client.createMutationIntent(),
): Promise<MemoryMutationResponse> {
  return client.request({
    path: `/v1/memory/${encodeURIComponent(memoryId)}/resolve-conflict`,
    method: 'POST',
    body: {
      resolution,
      ...(conflictingMemoryId ? { conflicting_memory_id: conflictingMemoryId } : {}),
    },
    intent,
    parse: parseMutationResponse,
  })
}

export function rejectMemory(
  client: ApiClient,
  memoryId: string,
  intent: MutationIntent = client.createMutationIntent(),
): Promise<MemoryMutationResponse> {
  return client.request({
    path: `/v1/memory/${encodeURIComponent(memoryId)}/reject`,
    method: 'POST',
    intent,
    parse: parseMutationResponse,
  })
}

export function restoreMemory(
  client: ApiClient,
  memoryId: string,
  intent: MutationIntent = client.createMutationIntent(),
): Promise<MemoryMutationResponse> {
  return client.request({
    path: `/v1/memory/${encodeURIComponent(memoryId)}/restore`,
    method: 'POST',
    intent,
    parse: parseMutationResponse,
  })
}

export function getMemoryHistory(
  client: ApiClient,
  memoryId: string,
): Promise<MemoryHistoryResponse> {
  return client.request({
    path: `/v1/memory/${encodeURIComponent(memoryId)}/history`,
    parse: (value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
      const data = value as Record<string, unknown>
      if (typeof data.memory_id !== 'string') throw invalidResponseError()
      return {
        memory_id: data.memory_id,
        memory: parseReviewItem(data.memory),
        events: Array.isArray(data.events) ? data.events.map(parseEventRecord) : [],
      }
    },
  })
}
