import type { ApiClient, MutationIntent } from './client'
import { ApiError, invalidResponseError } from './errors'

export type DiscordGatewayStatus =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'error'

export type DiscordRuntimeSnapshot = {
  status: DiscordGatewayStatus
  last_connected_at: string | null
  last_error: string | null
  bot_user_id: string | null
  bot_username: string | null
  latency_ms: number | null
}

export type DiscordIntegrationState = {
  enabled: boolean
  has_bot_token: boolean
  masked_token_hint: string | null
  allowed_discord_user_ids: string[]
  allowed_channel_ids: string[]
  default_notify_channel_id: string | null
  bot_application_id: string | null
  bot_user_id: string | null
  bot_username: string | null
  runtime: DiscordRuntimeSnapshot
}

export type UpdateDiscordIntegrationInput = {
  enabled: boolean
  bot_token?: string | null
  allowed_discord_user_ids: string[]
  allowed_channel_ids?: string[]
  default_notify_channel_id?: string | null
}

export type TestDiscordIntegrationInput = {
  bot_token?: string | null
  discord_user_id?: string | null
  send_dm?: boolean
}

export type TestDiscordIntegrationResult = DiscordIntegrationState & {
  ok: boolean
  connected: boolean
  dm_sent: boolean
  dm_channel_id: string | null
  recipient_discord_user_id: string | null
  message: string
  warning: string | null
}

export type DeleteDiscordIntegrationResult = DiscordIntegrationState & {
  deleted: boolean
}

const SNOWFLAKE_REGEX = /^[0-9]{17,20}$/

export const DEFAULT_DISCORD_INTEGRATION_STATE: DiscordIntegrationState = {
  enabled: false,
  has_bot_token: false,
  masked_token_hint: null,
  allowed_discord_user_ids: [],
  allowed_channel_ids: [],
  default_notify_channel_id: null,
  bot_application_id: null,
  bot_user_id: null,
  bot_username: null,
  runtime: {
    status: 'disconnected',
    last_connected_at: null,
    last_error: null,
    bot_user_id: null,
    bot_username: null,
    latency_ms: null,
  },
}

export function parseSnowflakeInputList(raw: string): string[] {
  const trimmed = raw.trim()
  if (!trimmed) return []
  const parts = trimmed.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean)
  const seen = new Set<string>()
  const result: string[] = []
  for (const part of parts) {
    if (!seen.has(part)) {
      seen.add(part)
      result.push(part)
    }
  }
  return result
}

export function isValidSnowflakeId(candidate: string): boolean {
  const cleaned = candidate.trim()
  if (!SNOWFLAKE_REGEX.test(cleaned)) return false
  try {
    return BigInt(cleaned) > 0n
  } catch {
    return false
  }
}

export function findInvalidSnowflakes(ids: readonly string[]): string[] {
  return ids.filter((id) => !isValidSnowflakeId(id))
}

export function extractDiscordErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.problems.length > 0 && error.problems[0]) {
      return error.problems[0]
    }
    if (error.messageKey && error.messageKey !== 'request_failed' && error.messageKey !== 'invalid_request') {
      return error.messageKey
    }
  }
  if (error instanceof Error && error.message && !error.message.startsWith('Orin request failed:')) {
    return error.message
  }
  return fallback
}

export function getDiscordIntegration(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<DiscordIntegrationState> {
  return client.request({
    path: '/v1/integrations/discord',
    signal,
    parse: parseDiscordIntegrationState,
  })
}

export function updateDiscordIntegration(
  client: ApiClient,
  input: UpdateDiscordIntegrationInput,
  intent = client.createMutationIntent(),
): Promise<DiscordIntegrationState> {
  return client.request({
    path: '/v1/integrations/discord',
    method: 'PUT',
    body: input,
    intent,
    parse: parseDiscordIntegrationState,
  })
}

export function testDiscordIntegration(
  client: ApiClient,
  input: TestDiscordIntegrationInput = {},
  intent = client.createMutationIntent(),
): Promise<TestDiscordIntegrationResult> {
  return client.request({
    path: '/v1/integrations/discord/test',
    method: 'POST',
    body: input,
    intent,
    parse: parseTestDiscordResult,
  })
}

export function deleteDiscordIntegration(
  client: ApiClient,
  intent = client.createMutationIntent(),
): Promise<DeleteDiscordIntegrationResult> {
  return client.request({
    path: '/v1/integrations/discord',
    method: 'DELETE',
    intent,
    parse: parseDeleteDiscordResult,
  })
}

function parseDiscordIntegrationState(value: unknown): DiscordIntegrationState {
  const data = record(value)
  const runtimeRaw = isRecord(data.runtime) ? data.runtime : {}
  return {
    enabled: data.enabled === true,
    has_bot_token: data.has_bot_token === true,
    masked_token_hint: nullableText(data.masked_token_hint),
    allowed_discord_user_ids: textArray(data.allowed_discord_user_ids ?? []),
    allowed_channel_ids: textArray(data.allowed_channel_ids ?? []),
    default_notify_channel_id: nullableText(data.default_notify_channel_id),
    bot_application_id: nullableText(data.bot_application_id),
    bot_user_id: nullableText(data.bot_user_id),
    bot_username: nullableText(data.bot_username),
    runtime: {
      status: parseGatewayStatus(runtimeRaw.status),
      last_connected_at: nullableText(runtimeRaw.last_connected_at),
      last_error: nullableText(runtimeRaw.last_error),
      bot_user_id: nullableText(runtimeRaw.bot_user_id),
      bot_username: nullableText(runtimeRaw.bot_username),
      latency_ms: nullableNumber(runtimeRaw.latency_ms),
    },
  }
}

function parseTestDiscordResult(value: unknown): TestDiscordIntegrationResult {
  const data = record(value)
  const state = parseDiscordIntegrationState(data)
  return {
    ...state,
    ok: data.ok === true,
    connected: data.connected === true,
    dm_sent: data.dm_sent === true,
    dm_channel_id: nullableText(data.dm_channel_id),
    recipient_discord_user_id: nullableText(data.recipient_discord_user_id),
    message: typeof data.message === 'string' ? data.message : '',
    warning: nullableText(data.warning),
  }
}

function parseDeleteDiscordResult(value: unknown): DeleteDiscordIntegrationResult {
  const data = record(value)
  const state = parseDiscordIntegrationState(data)
  return {
    ...state,
    deleted: data.deleted === true,
  }
}

function parseGatewayStatus(value: unknown): DiscordGatewayStatus {
  if (
    value === 'disconnected' ||
    value === 'connecting' ||
    value === 'connected' ||
    value === 'reconnecting' ||
    value === 'error'
  ) {
    return value
  }
  return 'disconnected'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw invalidResponseError()
  return value
}

function nullableText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string') throw invalidResponseError()
  return value
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'number' || Number.isNaN(value)) throw invalidResponseError()
  return value
}

function textArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw invalidResponseError()
  }
  return value
}

export type { MutationIntent }
