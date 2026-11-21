/**
 * Typed client for subscription quota visibility (`GET /v1/quota` and `GET /v1/quota/stream`).
 */
import type { ApiClient } from './client'
import { list, num, optStr, rec, str } from './parse'

export type SubscriptionProviderId = 'anthropic-cli' | 'openai-codex' | 'google-antigravity'

export const SUBSCRIPTION_PROVIDERS: readonly SubscriptionProviderId[] = [
  'anthropic-cli',
  'openai-codex',
  'google-antigravity',
] as const

export type QuotaConfidence = 'measured' | 'partial' | 'estimated' | 'unknown'
export type QuotaWindowStatus = 'allowed' | 'allowed_warning' | 'rejected' | 'unknown'

export type QuotaWindow = {
  kind: string
  used_percent: number | null
  resets_at: string | null
  status: QuotaWindowStatus
  scope_model: string | null
}

export type OrinUsageWindow = {
  tokens: number
  calls: number
}

export type OrinUsageSummary = {
  five_hour: OrinUsageWindow
  seven_day: OrinUsageWindow
}

export type ProviderQuota = {
  provider: string
  windows: QuotaWindow[]
  confidence: QuotaConfidence
  observed_at: string | null
  age_seconds: number | null
  source: string | null
  orin_usage: OrinUsageSummary
}

export type QuotaReport = {
  subscriptions: ProviderQuota[]
}

export type QuotaUpdatedEvent = {
  event: 'quota.updated'
  provider: string
  user_id: string
}

const CONFIDENCES: readonly QuotaConfidence[] = ['measured', 'partial', 'estimated', 'unknown']
const STATUSES: readonly QuotaWindowStatus[] = ['allowed', 'allowed_warning', 'rejected', 'unknown']

function optFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseQuotaWindow(raw: unknown): QuotaWindow | null {
  const data = rec(raw)
  const kind = str(data.kind)
  if (!kind) return null
  const rawStatus = str(data.status)
  const status: QuotaWindowStatus = STATUSES.includes(rawStatus as QuotaWindowStatus)
    ? (rawStatus as QuotaWindowStatus)
    : 'unknown'
  const rawPercent = optFiniteNumber(data.used_percent)
  const used_percent = rawPercent === null ? null : Math.max(0, Math.min(100, rawPercent))
  return {
    kind,
    used_percent,
    resets_at: optStr(data.resets_at),
    status,
    scope_model: optStr(data.scope_model),
  }
}

function parseOrinUsageWindow(raw: unknown): OrinUsageWindow {
  const data = rec(raw)
  return {
    tokens: Math.max(0, Math.round(num(data.tokens, 0))),
    calls: Math.max(0, Math.round(num(data.calls, 0))),
  }
}

function parseOrinUsageSummary(raw: unknown): OrinUsageSummary {
  const data = rec(raw)
  return {
    five_hour: parseOrinUsageWindow(data.five_hour),
    seven_day: parseOrinUsageWindow(data.seven_day),
  }
}

function parseProviderQuota(raw: unknown): ProviderQuota | null {
  const data = rec(raw)
  const provider = str(data.provider)
  if (!provider) return null
  const rawConf = str(data.confidence)
  const confidence: QuotaConfidence = CONFIDENCES.includes(rawConf as QuotaConfidence)
    ? (rawConf as QuotaConfidence)
    : 'unknown'
  const ageRaw = optFiniteNumber(data.age_seconds)
  return {
    provider,
    windows: list(data.windows, parseQuotaWindow),
    confidence,
    observed_at: optStr(data.observed_at),
    age_seconds: ageRaw === null ? null : Math.max(0, Math.round(ageRaw)),
    source: optStr(data.source),
    orin_usage: parseOrinUsageSummary(data.orin_usage),
  }
}

export function parseQuotaReport(raw: unknown): QuotaReport {
  const data = rec(raw)
  return {
    subscriptions: list(data.subscriptions, parseProviderQuota),
  }
}

export function getQuota(
  client: ApiClient,
  options?: { refresh?: boolean; signal?: AbortSignal },
): Promise<QuotaReport> {
  return client.request({
    path: '/v1/quota',
    query: options?.refresh ? { refresh: true } : undefined,
    signal: options?.signal,
    parse: parseQuotaReport,
  })
}

export function normalizeSubscriptionProvider(
  provider: string | null | undefined,
): SubscriptionProviderId | null {
  const clean = (provider ?? '').trim().toLowerCase()
  if (!clean) return null
  if (clean === 'anthropic' || clean === 'anthropic-cli' || clean === 'claude-cli' || clean === 'claude_cli') {
    return 'anthropic-cli'
  }
  if (clean === 'openai-codex' || clean === 'codex' || clean === 'codex-cli') {
    return 'openai-codex'
  }
  if (clean === 'google-antigravity' || clean === 'antigravity' || clean === 'agy') {
    return 'google-antigravity'
  }
  return null
}

export function subscriptionDisplayName(provider: string): string {
  const normalized = normalizeSubscriptionProvider(provider)
  if (normalized === 'anthropic-cli') return 'Claude CLI'
  if (normalized === 'openai-codex') return 'Codex CLI'
  if (normalized === 'google-antigravity') return 'Antigravity'
  return provider
}

export function quotaSourceLabel(source: string | null): string {
  switch (source) {
    case 'rate_limit_event':
      return 'Stream do CLI'
    case 'codex_rollout':
      return 'Rollout do Codex'
    case 'claude_usage_cache':
      return 'Cache local do Claude'
    case 'error':
      return 'Erro recente'
    case 'orin_usage':
      return 'Uso do Orin'
    default:
      return 'Sem fonte ativa'
  }
}

export function quotaConfidenceLabel(confidence: QuotaConfidence): string {
  switch (confidence) {
    case 'measured':
      return 'Medido'
    case 'partial':
      return 'Parcial'
    case 'estimated':
      return 'Estimado'
    case 'unknown':
    default:
      return 'Indisponível'
  }
}

export function quotaStatusLabel(status: QuotaWindowStatus): string {
  switch (status) {
    case 'allowed':
      return 'Normal'
    case 'allowed_warning':
      return 'Atenção'
    case 'rejected':
      return 'Esgotada'
    case 'unknown':
    default:
      return 'Desconhecido'
  }
}

export function quotaWindowLabel(win: QuotaWindow): string {
  if (win.kind === 'five_hour') return '5 h'
  if (win.kind === 'seven_day' || win.kind === 'weekly') return 'Semanal'
  if (win.kind === 'weekly_scoped') {
    return win.scope_model ? `Semanal (${win.scope_model})` : 'Semanal'
  }
  if (win.scope_model) return `${win.kind} (${win.scope_model})`
  return win.kind === 'unknown' ? 'Assinatura' : win.kind
}

export function highestKnownPercent(quota: ProviderQuota | null): number | null {
  if (!quota || quota.confidence === 'unknown') return null
  let maxPercent: number | null = null
  for (const win of quota.windows) {
    if (typeof win.used_percent === 'number' && Number.isFinite(win.used_percent)) {
      maxPercent = maxPercent === null ? win.used_percent : Math.max(maxPercent, win.used_percent)
    }
  }
  return maxPercent
}

export function isQuotaRejected(quota: ProviderQuota | null): boolean {
  if (!quota) return false
  return quota.windows.some((win) => win.status === 'rejected')
}

export function formatQuotaAge(
  observedAt: string | null,
  ageSeconds?: number | null,
  nowMs?: number,
): string {
  let seconds: number | null = null
  if (observedAt) {
    const parsed = Date.parse(observedAt)
    if (Number.isFinite(parsed)) {
      const reference = nowMs ?? Date.now()
      seconds = Math.max(0, Math.floor((reference - parsed) / 1000))
    }
  }
  if (seconds === null && typeof ageSeconds === 'number' && Number.isFinite(ageSeconds)) {
    seconds = Math.max(0, Math.floor(ageSeconds))
  }
  if (seconds === null) return 'sem leitura recente'
  if (seconds < 60) return 'agora'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `visto há ${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `visto há ${hours} h`
  const days = Math.floor(hours / 24)
  return `visto há ${days} d`
}

export function formatResetTime(resetsAt: string | null, nowMs?: number): string | null {
  if (!resetsAt) return null
  const parsed = new Date(resetsAt)
  if (!Number.isFinite(parsed.getTime())) return null
  const now = new Date(nowMs ?? Date.now())
  const sameDay =
    parsed.getFullYear() === now.getFullYear() &&
    parsed.getMonth() === now.getMonth() &&
    parsed.getDate() === now.getDate()
  if (sameDay) {
    return parsed.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  }
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function emptyProviderQuota(provider: SubscriptionProviderId): ProviderQuota {
  return {
    provider,
    windows: [],
    confidence: 'unknown',
    observed_at: null,
    age_seconds: null,
    source: null,
    orin_usage: {
      five_hour: { tokens: 0, calls: 0 },
      seven_day: { tokens: 0, calls: 0 },
    },
  }
}

export function ensureAllSubscriptions(subscriptions: readonly ProviderQuota[]): ProviderQuota[] {
  const byProvider = new Map<SubscriptionProviderId, ProviderQuota>()
  for (const item of subscriptions) {
    const normalized = normalizeSubscriptionProvider(item.provider)
    if (normalized) {
      byProvider.set(normalized, { ...item, provider: normalized })
    }
  }
  return SUBSCRIPTION_PROVIDERS.map(
    (provider) => byProvider.get(provider) ?? emptyProviderQuota(provider),
  )
}

/**
 * Subscribe to `GET /v1/quota/stream` (SSE `quota.updated`).
 * Returns `true` if a live SSE stream was opened and read until EOF, or `false`
 * when the endpoint is unavailable or returned a non-SSE response (e.g. unit test stubs).
 */
export async function streamQuotaUpdates(
  client: ApiClient,
  onUpdated: (event: QuotaUpdatedEvent) => void,
  signal?: AbortSignal,
): Promise<boolean> {
  const response = await client.stream({ path: '/v1/quota/stream', signal })
  if (!response.ok || !response.body) return false
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.toLowerCase().includes('text/event-stream')) {
    try {
      await response.body.cancel()
    } catch {
      // Ignore cancel errors on stub responses
    }
    return false
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let dataLines: string[] = []

  const flushRecord = () => {
    const eventName = currentEvent
    const payloadRaw = dataLines.join('\n')
    currentEvent = ''
    dataLines = []
    if (!payloadRaw) return
    if (eventName && eventName !== 'quota.updated') return
    try {
      const parsed = rec(JSON.parse(payloadRaw))
      const provider = str(parsed.provider)
      const userId = str(parsed.user_id)
      const kind = str(parsed.event) || eventName
      if (kind === 'quota.updated') {
        onUpdated({ event: 'quota.updated', provider, user_id: userId })
      }
    } catch {
      // Ignore malformed SSE records gracefully
    }
  }

  const consumeLine = (line: string) => {
    if (line === '') {
      flushRecord()
      return
    }
    if (line.startsWith('event:')) {
      currentEvent = line.slice(6).trimStart()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  try {
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) consumeLine(line)
      if (chunk.done) {
        if (buffer) consumeLine(buffer)
        flushRecord()
        break
      }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // Ignore releaseLock errors
    }
  }
  return true
}
