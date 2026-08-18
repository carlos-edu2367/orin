import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export const PROVIDER_NAMES = ['openai', 'anthropic', 'openrouter', 'omniroute', 'ollama'] as const
export type ProviderName = (typeof PROVIDER_NAMES)[number]

/**
 * The gateway (`src/agentos/api/gateway.py` `_provider_public`) strips any field
 * whose name contains api_key/secret/token/password/credential (case-insensitive)
 * from every provider response, but the remaining shape is otherwise whatever the
 * concrete `ProviderConfigurationApplication` adapter returns. The current
 * Provider Settings UI uses the production-composed adapter, but only
 * `provider`/`enabled` are documented as safe to assume (BACKEND_CAPABILITY_MATRIX.md);
 * every other field is kept as opaque, unlabeled technical detail, never as a
 * trusted UI label — and is deliberately never rendered by `ProviderSettingsPage`.
 */
export type ProviderPublicState = {
  provider: ProviderName | null
  enabled: boolean | null
  extra: Record<string, unknown>
}

export type ConfigureProviderInput = {
  apiKey: string
  enabled?: boolean
  baseUrl?: string
}

export type ProviderApiKeyState = {
  id: number
  label: string | null
  position: number
  status: 'active' | 'cooldown'
  cooldownUntil: string | null
}

export type ProviderModel = {
  provider: ProviderName
  model_id: string
  display_name: string
  context_window: number | null
  capabilities: string[]
  input_modalities: string[]
  output_modalities: string[]
  pricing: { input_per_million: string | null; output_per_million: string | null } | null
  is_favorite: boolean
  refreshed_at: string | null
  route_kind: 'model' | 'auto'
}

function providerPath(provider: ProviderName): string {
  return `/v1/providers/${encodeURIComponent(provider)}`
}

function providerKeysPath(provider: ProviderName): string {
  return `${providerPath(provider)}/keys`
}

export function listProviderKeys(client: ApiClient, provider: ProviderName, signal?: AbortSignal): Promise<ProviderApiKeyState[]> {
  return client.request({
    path: providerKeysPath(provider), signal,
    parse: (value) => {
      if (!Array.isArray(value)) throw invalidResponseError()
      return value.map(parseProviderApiKeyState)
    },
  })
}

export function addProviderKey(client: ApiClient, provider: ProviderName, input: { apiKey: string; label?: string }, intent = client.createMutationIntent()): Promise<ProviderApiKeyState> {
  return client.request({
    path: providerKeysPath(provider), method: 'POST', intent,
    body: { api_key: input.apiKey, ...(input.label ? { label: input.label } : {}) },
    parse: parseProviderApiKeyState,
  })
}

export function renameProviderKey(client: ApiClient, provider: ProviderName, keyId: number, label: string | null, intent = client.createMutationIntent()): Promise<ProviderApiKeyState> {
  return client.request({
    path: `${providerKeysPath(provider)}/${keyId}`, method: 'PATCH', intent,
    body: { label },
    parse: parseProviderApiKeyState,
  })
}

export function removeProviderKey(client: ApiClient, provider: ProviderName, keyId: number, intent = client.createMutationIntent()): Promise<void> {
  return client.request({
    path: `${providerKeysPath(provider)}/${keyId}`, method: 'DELETE', intent,
    parse: () => undefined,
  })
}

export function reorderProviderKeys(client: ApiClient, provider: ProviderName, orderedIds: number[], intent = client.createMutationIntent()): Promise<ProviderApiKeyState[]> {
  return client.request({
    path: `${providerKeysPath(provider)}:reorder`, method: 'PUT', intent,
    body: { ordered_ids: orderedIds },
    parse: (value) => {
      if (!Array.isArray(value)) throw invalidResponseError()
      return value.map(parseProviderApiKeyState)
    },
  })
}

export function setProviderKeyCooldownSeconds(client: ApiClient, provider: ProviderName, seconds: number, intent = client.createMutationIntent()): Promise<ProviderPublicState> {
  return client.request({
    path: `${providerKeysPath(provider)}:cooldown`, method: 'PUT', intent,
    body: { seconds },
    parse: parseProviderPublicState,
  })
}

function parseProviderApiKeyState(value: unknown): ProviderApiKeyState {
  const data = record(value)
  if (!Number.isInteger(data.id)) throw invalidResponseError()
  if (data.status !== 'active' && data.status !== 'cooldown') throw invalidResponseError()
  if (!Number.isInteger(data.position) || (data.position as number) < 0) throw invalidResponseError()
  return {
    id: data.id as number,
    label: nullableString(data.label),
    position: data.position as number,
    status: data.status,
    cooldownUntil: nullableString(data.cooldown_until),
  }
}

export function configureProvider(
  client: ApiClient,
  provider: ProviderName,
  input: ConfigureProviderInput,
  intent = client.createMutationIntent(),
): Promise<ProviderPublicState> {
  return client.request({
    path: providerPath(provider),
    method: 'PUT',
    body: {
      api_key: input.apiKey,
      ...(input.enabled === undefined ? {} : { enabled: input.enabled }),
      ...(input.baseUrl === undefined ? {} : { base_url: input.baseUrl }),
    },
    intent,
    parse: parseProviderPublicState,
  })
}

export type OmniRouteConnection = { connected: true; models_available: number | null; base_url: string | null }
export type OmniRouteRuntime = { state: 'stopped' | 'starting' | 'ready' | 'failed' | 'external'; ownership: 'agentos' | 'external' | null; auto_start: boolean; failure: string | null }

function parseOmniRouteRuntime(value: unknown): OmniRouteRuntime {
  const data = record(value)
  if (!['stopped', 'starting', 'ready', 'failed', 'external'].includes(String(data.state))) throw invalidResponseError()
  if (data.ownership !== 'agentos' && data.ownership !== 'external' && data.ownership !== null) throw invalidResponseError()
  if (typeof data.auto_start !== 'boolean') throw invalidResponseError()
  return { state: data.state as OmniRouteRuntime['state'], ownership: data.ownership, auto_start: data.auto_start, failure: typeof data.failure === 'string' ? data.failure : null }
}

export function getOmniRouteRuntime(client: ApiClient, signal?: AbortSignal): Promise<OmniRouteRuntime> {
  return client.request({ path: `${providerPath('omniroute')}/runtime`, signal, parse: parseOmniRouteRuntime })
}

export function setOmniRouteAutoStart(client: ApiClient, autoStart: boolean, intent = client.createMutationIntent()): Promise<OmniRouteRuntime> {
  return client.request({ path: `${providerPath('omniroute')}/runtime`, method: 'PUT', body: { auto_start: autoStart }, intent, parse: parseOmniRouteRuntime })
}

export function controlOmniRoute(client: ApiClient, action: 'start' | 'stop' | 'restart', intent = client.createMutationIntent()): Promise<OmniRouteRuntime> {
  return client.request({ path: `${providerPath('omniroute')}/runtime/actions`, method: 'POST', body: { action }, intent, parse: parseOmniRouteRuntime })
}

export function installOmniRoute(client: ApiClient, intent = client.createMutationIntent()): Promise<{ installed: true; next_step: 'omniroute' }> {
  return client.request({
    path: `${providerPath('omniroute')}/install`, method: 'POST', intent,
    parse: (value) => {
      const data = record(value)
      if (data.installed !== true || data.next_step !== 'omniroute') throw invalidResponseError()
      return { installed: true, next_step: 'omniroute' }
    },
  })
}

export function getOmniRouteInstallationStatus(client: ApiClient, signal?: AbortSignal): Promise<{ installed: boolean }> {
  return client.request({
    path: `${providerPath('omniroute')}/install`, signal,
    parse: (value) => {
      const data = record(value)
      if (typeof data.installed !== 'boolean') throw invalidResponseError()
      return { installed: data.installed }
    },
  })
}

export function testOmniRouteConnection(
  client: ApiClient,
  input: { apiKey: string; baseUrl: string },
  intent = client.createMutationIntent(),
): Promise<OmniRouteConnection> {
  return client.request({
    path: `${providerPath('omniroute')}/test`, method: 'POST', intent,
    body: { api_key: input.apiKey, base_url: input.baseUrl },
    parse: (value) => {
      const data = record(value)
      if (data.connected !== true) throw invalidResponseError()
      return { connected: true, models_available: data.models_available === null ? null : nonNegativeInteger(data.models_available), base_url: nullableString(data.base_url) }
    },
  })
}

export function testOllamaConnection(
  client: ApiClient,
  input: { apiKey: string; baseUrl: string },
  intent = client.createMutationIntent(),
): Promise<OmniRouteConnection> {
  return client.request({
    path: `${providerPath('ollama')}/test`, method: 'POST', intent,
    body: { api_key: input.apiKey, base_url: input.baseUrl },
    parse: (value) => {
      const data = record(value)
      if (data.connected !== true) throw invalidResponseError()
      return { connected: true, models_available: data.models_available === null ? null : nonNegativeInteger(data.models_available), base_url: nullableString(data.base_url) }
    },
  })
}

export function inspectProvider(client: ApiClient, provider: ProviderName, signal?: AbortSignal): Promise<ProviderPublicState> {
  return client.request({
    path: providerPath(provider),
    signal,
    parse: parseProviderPublicState,
  })
}

export function revokeProvider(
  client: ApiClient,
  provider: ProviderName,
  intent = client.createMutationIntent(),
): Promise<ProviderPublicState> {
  return client.request({
    path: providerPath(provider),
    method: 'DELETE',
    intent,
    parse: parseProviderPublicState,
  })
}

export function listProviderModels(client: ApiClient, provider: ProviderName, signal?: AbortSignal): Promise<ProviderModel[]> {
  return client.request({
    path: `${providerPath(provider)}/models`,
    signal,
    parse: (value) => {
      const data = record(value)
      if (!Array.isArray(data.items)) throw invalidResponseError()
      return data.items.map(parseProviderModel)
    },
  })
}

export function refreshProviderModels(client: ApiClient, provider: ProviderName, intent = client.createMutationIntent()): Promise<{ count: number; refreshed_at: string | null }> {
  return client.request({
    path: `${providerPath(provider)}/models:refresh`, method: 'POST', intent,
    parse: (value) => {
      const data = record(value)
      if (!Number.isInteger(data.count) || (data.count as number) < 0) throw invalidResponseError()
      return { count: data.count as number, refreshed_at: nullableString(data.refreshed_at) }
    },
  })
}

export function setProviderModelFavorite(client: ApiClient, provider: ProviderName, modelId: string, favorite: boolean, intent = client.createMutationIntent()): Promise<ProviderModel> {
  return client.request({
    path: `${providerPath(provider)}/favorites/${encodeURIComponent(modelId)}`,
    method: favorite ? 'PUT' : 'DELETE', intent, parse: parseProviderModel,
  })
}

/**
 * The model chosen to perform visual reading (`src/agentos/reading/selection.py`)
 * when the turn's own model cannot see an attachment. `mode: 'automatic'` means
 * nothing is stored and the worker picks at turn time (own provider, then a
 * local Ollama, then anything else); `'manual'` means `provider`/`modelId` are
 * both present and override that choice.
 */
export type VisionModelSetting = { provider: ProviderName | null; modelId: string | null; mode: 'automatic' | 'manual' }

export function getVisionModelSetting(client: ApiClient, signal?: AbortSignal): Promise<VisionModelSetting> {
  return client.request({ path: '/v1/settings/vision-model', signal, parse: parseVisionModelSetting })
}

export function setVisionModelSetting(
  client: ApiClient,
  input: { provider: ProviderName; modelId: string } | { provider: null; modelId: null },
  intent = client.createMutationIntent(),
): Promise<VisionModelSetting> {
  return client.request({
    path: '/v1/settings/vision-model',
    method: 'PUT',
    body: { provider: input.provider, model_id: input.modelId },
    intent,
    parse: parseVisionModelSetting,
  })
}

function parseVisionModelSetting(value: unknown): VisionModelSetting {
  const data = record(value)
  if (data.mode !== 'automatic' && data.mode !== 'manual') throw invalidResponseError()
  const provider = PROVIDER_NAMES.includes(data.provider as ProviderName) ? data.provider as ProviderName : null
  return { provider, modelId: nullableString(data.model_id), mode: data.mode }
}

const SENSITIVE_FIELD_TERMS = ['api_key', 'secret', 'token', 'password', 'credential']

function isSensitiveFieldName(name: string): boolean {
  const lowered = name.toLowerCase()
  return SENSITIVE_FIELD_TERMS.some((term) => lowered.includes(term))
}

function parseProviderPublicState(value: unknown): ProviderPublicState {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const record = value as Record<string, unknown>
  const provider = PROVIDER_NAMES.includes(record.provider as ProviderName) ? record.provider as ProviderName : null
  const enabled = typeof record.enabled === 'boolean' ? record.enabled : null
  const extra: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(record)) {
    if (key === 'provider' || key === 'enabled' || isSensitiveFieldName(key)) continue
    extra[key] = item
  }
  return { provider, enabled, extra }
}

function parseProviderModel(value: unknown): ProviderModel {
  const data = record(value)
  const provider = data.provider
  if (!PROVIDER_NAMES.includes(provider as ProviderName)) throw invalidResponseError()
  return {
    provider: provider as ProviderName,
    model_id: string(data.model_id),
    display_name: string(data.display_name),
    context_window: data.context_window === null ? null : positiveInteger(data.context_window),
    capabilities: stringArray(data.capabilities),
    input_modalities: stringArray(data.input_modalities),
    output_modalities: stringArray(data.output_modalities),
    pricing: parsePricing(data.pricing), is_favorite: data.is_favorite === true,
    refreshed_at: nullableString(data.refreshed_at),
    route_kind: data.route_kind === 'auto' ? 'auto' : 'model',
  }
}

function parsePricing(value: unknown): ProviderModel['pricing'] {
  if (value === null) return null
  const data = record(value)
  return { input_per_million: nullableString(data.input_per_million), output_per_million: nullableString(data.output_per_million) }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function string(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) throw invalidResponseError()
  return value
}

function nullableString(value: unknown): string | null {
  return value === null || value === undefined ? null : string(value)
}

function positiveInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw invalidResponseError()
  return value as number
}

function nonNegativeInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 0) throw invalidResponseError()
  return value as number
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) throw invalidResponseError()
  return value as string[]
}
