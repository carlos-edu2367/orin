/**
 * Typed client for effective model context windows (`GET /v1/context/windows`, `DELETE /v1/context/windows/{provider}/{model_id}`).
 */
import type { ApiClient } from './client'
import { bool, list, optStr, rec, str } from './parse'

export const MIN_CONTEXT_OVERRIDE = 50_000
export const MAX_CONTEXT_OVERRIDE = 1_000_000

export type LearnedContextWindow = {
  tokens: number
  source: string
  observed_at: string
}

export type ContextWindowEntry = {
  provider: string
  model_id: string
  display_name: string
  catalog_tokens: number | null
  learned: LearnedContextWindow | null
  override_tokens: number | null
  effective_tokens: number
  effective_source: string
  effective_detail: string
}

export type ContextWindowsReport = {
  windows: ContextWindowEntry[]
}

export type ForgetContextWindowResult = {
  forgotten: boolean
  provider: string
  model_id: string
}

function optPositiveInt(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  return Math.round(value)
}

function parseLearnedWindow(raw: unknown): LearnedContextWindow | null {
  if (raw === null || raw === undefined) return null
  const data = rec(raw)
  const tokens = optPositiveInt(data.tokens)
  if (tokens === null) return null
  return {
    tokens,
    source: str(data.source, 'error_raw'),
    observed_at: str(data.observed_at),
  }
}

function parseContextWindowEntry(raw: unknown): ContextWindowEntry | null {
  const data = rec(raw)
  const provider = str(data.provider)
  const model_id = str(data.model_id)
  if (!provider || !model_id) return null
  const effective_tokens = optPositiveInt(data.effective_tokens) ?? 200_000
  return {
    provider,
    model_id,
    display_name: optStr(data.display_name) ?? model_id,
    catalog_tokens: optPositiveInt(data.catalog_tokens),
    learned: parseLearnedWindow(data.learned),
    override_tokens: optPositiveInt(data.override_tokens),
    effective_tokens,
    effective_source: str(data.effective_source, 'fallback'),
    effective_detail: str(data.effective_detail),
  }
}

export function parseContextWindowsReport(raw: unknown): ContextWindowsReport {
  const data = rec(raw)
  return {
    windows: list(data.windows, parseContextWindowEntry),
  }
}

export function getContextWindows(
  client: ApiClient,
  options?: { projectRoot?: string | null; signal?: AbortSignal },
): Promise<ContextWindowsReport> {
  return client.request({
    path: '/v1/context/windows',
    query: options?.projectRoot ? { project_root: options.projectRoot } : undefined,
    signal: options?.signal,
    parse: parseContextWindowsReport,
  })
}

export function forgetLearnedContextWindow(
  client: ApiClient,
  provider: string,
  modelId: string,
  signal?: AbortSignal,
): Promise<ForgetContextWindowResult> {
  const encodedModelPath = modelId
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
  return client.request({
    path: `/v1/context/windows/${encodeURIComponent(provider)}/${encodedModelPath}`,
    method: 'DELETE',
    signal,
    parse: (raw: unknown) => {
      const data = rec(raw)
      return {
        forgotten: bool(data.forgotten),
        provider: str(data.provider, provider),
        model_id: str(data.model_id, modelId),
      }
    },
  })
}

export function validateContextOverride(rawValue: string): { value: number | null; error: string | null } {
  const cleaned = rawValue.trim().replace(/\./g, '').replace(/\s+/g, '')
  if (!cleaned) {
    return { value: null, error: 'Informe um valor entre 50.000 e 1.000.000 tokens.' }
  }
  if (!/^\d+$/.test(cleaned)) {
    return { value: null, error: 'Use apenas números inteiros entre 50.000 e 1.000.000 tokens.' }
  }
  const parsed = Number.parseInt(cleaned, 10)
  if (!Number.isFinite(parsed) || parsed < MIN_CONTEXT_OVERRIDE || parsed > MAX_CONTEXT_OVERRIDE) {
    return { value: null, error: 'O limite manual deve ficar entre 50.000 e 1.000.000 tokens.' }
  }
  return { value: parsed, error: null }
}

export function formatTokensCompact(tokens: number | null | undefined): string {
  if (typeof tokens !== 'number' || !Number.isFinite(tokens) || tokens <= 0) return '—'
  if (tokens >= 1_000_000 && tokens % 1_000_000 === 0) {
    return `${tokens / 1_000_000} M`
  }
  if (tokens >= 1_000 && tokens % 1_000 === 0) {
    return `${tokens / 1_000} mil`
  }
  return tokens.toLocaleString('pt-BR')
}

export function describeWindowSource(
  source?: string | null,
  detail?: string | null,
  limitTokens?: number | null,
): string | null {
  const cleanDetail = (detail ?? '').trim()
  if (cleanDetail) return cleanDetail
  const prefix = typeof limitTokens === 'number' && limitTokens > 0 ? `${formatTokensCompact(limitTokens)}, ` : ''
  switch ((source ?? '').trim()) {
    case 'override':
      return `${prefix}definido manualmente`
    case 'learned':
      return `${prefix}aprendido após erro do modelo`
    case 'account_credits':
      return `${prefix}limite da conta sem créditos`
    case 'credits_1m':
      return `${prefix}créditos de uso ativos (1M)`
    case 'cli_catalog':
      return `${prefix}catálogo do CLI`
    case 'fixed_catalog':
      return `${prefix}catálogo do Orin`
    case 'fallback':
      return `${prefix}padrão`
    default:
      return null
  }
}
