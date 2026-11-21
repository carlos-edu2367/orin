/**
 * Typed client for GET /v1/orchestration/verifier/metrics (WS-A6 / WS-A7).
 */
import type { ApiClient } from './client'
import { list, num, optStr, rec, str } from './parse'

export type VerifierPairMetric = {
  author_provider: string
  author_model: string
  reviewer_provider: string
  reviewer_model: string
  reviews_count: number
  pass_count: number
  fail_count: number
  inconclusive_count: number
  total_findings: number
  proven_findings: number
  unproven_findings: number
  confirmed_count: number
  fixed_count: number
  rejected_count: number
  open_count: number
  confirmed_rate: number | null
  accepted_rate: number | null
  rejected_rate: number | null
  findings_per_100_lines: number | null
  total_tokens: number
  avg_tokens: number
  total_cost_usd: number | null
  avg_cost_usd: number | null
}

export type VerifierMetricsResponse = {
  pairs: VerifierPairMetric[]
  since: string | null
}

export type VerifierMetricsQuery = {
  since?: string
  days?: number
}

function optNum(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parsePairMetric(raw: unknown): VerifierPairMetric | null {
  const data = rec(raw)
  const reviewerProvider = str(data.reviewer_provider)
  const reviewerModel = str(data.reviewer_model)
  if (!reviewerProvider || !reviewerModel) return null

  return {
    author_provider: str(data.author_provider),
    author_model: str(data.author_model),
    reviewer_provider: reviewerProvider,
    reviewer_model: reviewerModel,
    reviews_count: num(data.reviews_count, 0),
    pass_count: num(data.pass_count, 0),
    fail_count: num(data.fail_count, 0),
    inconclusive_count: num(data.inconclusive_count, 0),
    total_findings: num(data.total_findings, 0),
    proven_findings: num(data.proven_findings, 0),
    unproven_findings: num(data.unproven_findings, 0),
    confirmed_count: num(data.confirmed_count, 0),
    fixed_count: num(data.fixed_count, 0),
    rejected_count: num(data.rejected_count, 0),
    open_count: num(data.open_count, 0),
    confirmed_rate: optNum(data.confirmed_rate),
    accepted_rate: optNum(data.accepted_rate),
    rejected_rate: optNum(data.rejected_rate),
    findings_per_100_lines: optNum(data.findings_per_100_lines),
    total_tokens: num(data.total_tokens, 0),
    avg_tokens: num(data.avg_tokens, 0),
    total_cost_usd: optNum(data.total_cost_usd),
    avg_cost_usd: optNum(data.avg_cost_usd),
  }
}

export function parseVerifierMetricsResponse(raw: unknown): VerifierMetricsResponse {
  const data = rec(raw)
  return {
    pairs: list(data.pairs, parsePairMetric),
    since: optStr(data.since),
  }
}

export function getVerifierMetrics(
  client: ApiClient,
  options?: VerifierMetricsQuery,
): Promise<VerifierMetricsResponse> {
  const query: Record<string, string> = {}
  if (options?.since && options.since.trim().length > 0) {
    query.since = options.since.trim()
  } else if (typeof options?.days === 'number' && Number.isFinite(options.days) && options.days > 0) {
    query.days = String(Math.floor(options.days))
  }
  return client.request({
    path: '/v1/orchestration/verifier/metrics',
    query: Object.keys(query).length > 0 ? query : undefined,
    parse: parseVerifierMetricsResponse,
  })
}
