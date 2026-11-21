import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  getVerifierMetrics,
  type VerifierMetricsResponse,
  type VerifierPairMetric,
} from '../../api/verifierMetrics'
import { describeError, ErrorNote } from '../codeSettings/shared'
import './orchestrationSettings.css'

const PERIOD_OPTIONS: ReadonlyArray<{ value: string; label: string; days?: number }> = [
  { value: 'all', label: 'Todo o período' },
  { value: '7', label: 'Últimos 7 dias', days: 7 },
  { value: '30', label: 'Últimos 30 dias', days: 30 },
  { value: '90', label: 'Últimos 90 dias', days: 90 },
]

function formatPercent(rate: number | null): string {
  if (rate === null || !Number.isFinite(rate)) return '—'
  return `${(rate * 100).toFixed(1).replace('.', ',')}%`
}

function formatRatePer100Lines(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return value.toFixed(2).replace('.', ',')
}

function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, '.')
}

function formatCostUsd(cost: number | null): string {
  if (cost === null || !Number.isFinite(cost)) return '—'
  return `$${cost.toFixed(4)}`
}

function formatEndpoint(provider: string, model: string): string {
  if (!provider) return model || '—'
  if (!model) return provider
  return `${model} (${provider})`
}

function pairRowKey(pair: VerifierPairMetric): string {
  return `${pair.author_provider}:${pair.author_model}->${pair.reviewer_provider}:${pair.reviewer_model}`
}

export function VerifierMetricsTable({
  client,
  initialPeriod = '30',
}: {
  client?: ApiClient
  initialPeriod?: 'all' | '7' | '30' | '90'
}) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [period, setPeriod] = useState<string>(initialPeriod)
  const [reloadTick, setReloadTick] = useState(0)
  const [data, setData] = useState<VerifierMetricsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)

  useEffect(() => {
    let cancelled = false
    const option = PERIOD_OPTIONS.find((item) => item.value === period)
    getVerifierMetrics(apiClient, option?.days ? { days: option.days } : undefined)
      .then((response) => {
        if (!cancelled) {
          setData(response)
          setError(null)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(describeError(caught, 'carregar as métricas do verificador'))
          setData(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [apiClient, period, reloadTick])

  const handlePeriodChange = useCallback((nextPeriod: string) => {
    setLoading(true)
    setError(null)
    setPeriod(nextPeriod)
  }, [])

  const handleRefresh = useCallback(() => {
    setLoading(true)
    setError(null)
    setReloadTick((tick) => tick + 1)
  }, [])

  const pairs = data?.pairs ?? []

  return (
    <div className="verifier-metrics" data-testid="verifier-metrics-table">
      <div className="verifier-metrics__toolbar">
        <label className="verifier-metrics__filter">
          <span>Período</span>
          <select
            aria-label="Período das métricas do verificador"
            value={period}
            disabled={loading}
            onChange={(event) => handlePeriodChange(event.target.value)}
          >
            {PERIOD_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button button--quiet"
          disabled={loading}
          onClick={handleRefresh}
        >
          Atualizar
        </button>
      </div>

      <ErrorNote error={error} />

      {loading && !data && (
        <p className="code-settings__muted">Carregando métricas do verificador…</p>
      )}

      {!loading && !error && pairs.length === 0 && (
        <div className="verifier-metrics__empty" data-testid="verifier-metrics-empty">
          <p>Nenhuma revisão registrada neste período.</p>
          <p className="code-settings__muted">
            Quando o verificador independente rodar em seus turnos, a taxa de confirmados, falsos positivos, achados por 100 linhas e tokens por par aparecerão aqui.
          </p>
        </div>
      )}

      {pairs.length > 0 && (
        <div className="orch-settings__table-wrap">
          <table className="verifier-metrics__table" aria-label="Métricas do verificador por par">
            <thead>
              <tr>
                <th>Autor → Revisor</th>
                <th>Revisões</th>
                <th>Confirmados</th>
                <th>Falsos positivos</th>
                <th>Achados / 100 linhas</th>
                <th>Média de tokens</th>
                <th>Custo médio</th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((pair) => (
                <tr key={pairRowKey(pair)}>
                  <td>
                    <div className="verifier-metrics__pair">
                      <span className="verifier-metrics__endpoint">
                        {formatEndpoint(pair.author_provider, pair.author_model)}
                      </span>
                      <span className="verifier-metrics__arrow" aria-hidden="true">
                        →
                      </span>
                      <span className="verifier-metrics__endpoint">
                        {formatEndpoint(pair.reviewer_provider, pair.reviewer_model)}
                      </span>
                    </div>
                  </td>
                  <td>
                    <span title={`Aprovadas: ${pair.pass_count} · Falhas: ${pair.fail_count} · Inconclusivas: ${pair.inconclusive_count}`}>
                      {pair.reviews_count}
                    </span>
                  </td>
                  <td>{formatPercent(pair.confirmed_rate ?? pair.accepted_rate)}</td>
                  <td>{formatPercent(pair.rejected_rate)}</td>
                  <td>{formatRatePer100Lines(pair.findings_per_100_lines)}</td>
                  <td>{formatTokens(pair.avg_tokens)}</td>
                  <td>{formatCostUsd(pair.avg_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
