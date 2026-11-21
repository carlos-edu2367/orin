/**
 * Typed client for GET/PUT /v1/settings/orchestration (WS-E0).
 */
import type { ApiClient } from './client'
import { bool, list, optStr, rec, str } from './parse'

export type OrchestrationSettingType = 'boolean' | 'number' | 'string' | 'choice' | 'string_list' | 'map'
export type OrchestrationSettingLayer = 'default' | 'user' | 'project' | 'local' | 'cli' | 'managed'
export type OrchestrationSettingScope = 'user_only' | 'project_off_only' | 'project_lower_only' | 'any' | 'agent_file'

export type OrchestrationSettingItem = {
  path: string
  type: OrchestrationSettingType
  group: string
  label: string
  description: string
  scope: OrchestrationSettingScope
  defaultValue: unknown
  value: unknown
  layer: OrchestrationSettingLayer
  userValue: unknown
  hasUserOverride: boolean
  minimum: number | null
  maximum: number | null
  choices: string[] | null
  itemType: string | null
  valueMinimum: number | null
  valueMaximum: number | null
  locked: boolean
}

export type OrchestrationSettingsReport = {
  projectRoot: string | null
  trusted: boolean
  items: OrchestrationSettingItem[]
}

export type OrchestrationSettingsQuery = {
  conversationId?: string | null
  projectRoot?: string | null
  group?: string
  prefix?: string
}

const TYPES: readonly OrchestrationSettingType[] = ['boolean', 'number', 'string', 'choice', 'string_list', 'map']
const LAYERS: readonly OrchestrationSettingLayer[] = ['default', 'user', 'project', 'local', 'cli', 'managed']
const SCOPES: readonly OrchestrationSettingScope[] = [
  'user_only',
  'project_off_only',
  'project_lower_only',
  'any',
  'agent_file',
]

function optNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function optChoices(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null
  return value.filter((item): item is string => typeof item === 'string')
}

function parseSettingItem(raw: unknown): OrchestrationSettingItem | null {
  const data = rec(raw)
  const path = str(data.path)
  if (!path) return null
  const type = TYPES.includes(data.type as OrchestrationSettingType)
    ? (data.type as OrchestrationSettingType)
    : 'string'
  const layer = LAYERS.includes(data.layer as OrchestrationSettingLayer)
    ? (data.layer as OrchestrationSettingLayer)
    : 'default'
  const scope = SCOPES.includes(data.scope as OrchestrationSettingScope)
    ? (data.scope as OrchestrationSettingScope)
    : 'user_only'
  const userValue = data.user_value ?? null
  const hasUserOverride =
    typeof data.has_user_override === 'boolean' ? data.has_user_override : userValue !== null

  return {
    path,
    type,
    group: str(data.group),
    label: str(data.label) || path,
    description: str(data.description),
    scope,
    defaultValue: data.default ?? null,
    value: data.value ?? null,
    layer,
    userValue,
    hasUserOverride,
    minimum: optNumber(data.minimum),
    maximum: optNumber(data.maximum),
    choices: optChoices(data.choices),
    itemType: optStr(data.item_type),
    valueMinimum: optNumber(data.value_minimum),
    valueMaximum: optNumber(data.value_maximum),
    locked: bool(data.locked) || layer === 'managed',
  }
}

export function parseOrchestrationSettingsReport(raw: unknown): OrchestrationSettingsReport {
  const data = rec(raw)
  return {
    projectRoot: optStr(data.project_root),
    trusted: bool(data.trusted),
    items: list(data.items, parseSettingItem),
  }
}

export function getOrchestrationSettings(
  client: ApiClient,
  options?: OrchestrationSettingsQuery,
): Promise<OrchestrationSettingsReport> {
  const query: Record<string, string> = {}
  if (options?.conversationId) query.conversation_id = options.conversationId
  if (options?.projectRoot) query.project_root = options.projectRoot
  if (options?.group) query.group = options.group
  if (options?.prefix) query.prefix = options.prefix
  return client.request({
    path: '/v1/settings/orchestration',
    query: Object.keys(query).length > 0 ? query : undefined,
    parse: parseOrchestrationSettingsReport,
  })
}

export function updateOrchestrationSettings(
  client: ApiClient,
  changes: Record<string, unknown>,
  options?: OrchestrationSettingsQuery,
): Promise<OrchestrationSettingsReport> {
  const body: Record<string, unknown> = { changes }
  if (options?.conversationId) body.conversation_id = options.conversationId
  if (options?.projectRoot) body.project_root = options.projectRoot
  if (options?.group) body.group = options.group
  if (options?.prefix) body.prefix = options.prefix
  return client.request({
    path: '/v1/settings/orchestration',
    method: 'PUT',
    body,
    parse: parseOrchestrationSettingsReport,
  })
}
