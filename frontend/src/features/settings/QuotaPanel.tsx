import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { getQuota, QUOTA_PROBE_FOLLOW_UP_MS, type ProviderQuota } from '../../api/quota'
import { SubscriptionQuotaCards } from '../conversations/QuotaPopover'
import { useQuotaNow } from '../conversations/useQuotaNow'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'
import './orchestrationSettings.css'

const ORDERED_QUOTA_PATHS = [
  'quota.display.enabled',
  'quota.activeProbe.enabled',
  'quota.activeProbe.intervalMinutes',
  'quota.policy.enabled',
  'quota.policy.warnPercent',
  'quota.policy.blockBackgroundPercent',
  'quota.fallback.enabled',
  'quota.fallback.chain',
] as const

export type QuotaPanelProps = {
  client?: ApiClient
  items: OrchestrationSettingItem[]
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
  busy?: boolean
  initialSubscriptions?: ProviderQuota[]
  nowMs?: number
}

export function QuotaPanel({
  client,
  items,
  onSave,
  busy = false,
  initialSubscriptions,
  nowMs,
}: QuotaPanelProps) {
  const [subscriptions, setSubscriptions] = useState<ProviderQuota[]>(initialSubscriptions ?? [])
  const [loadingQuota, setLoadingQuota] = useState(Boolean(client && !initialSubscriptions))
  const { nowMs: clockMs } = useQuotaNow(nowMs)
  const followUpTimer = useRef<number | null>(null)

  const loadQuota = useCallback(
    async (refresh = false, signal?: AbortSignal) => {
      if (!client) return
      setLoadingQuota(true)
      try {
        const report = await getQuota(client, { refresh, signal })
        if (!signal?.aborted) {
          setSubscriptions(report.subscriptions)
        }
      } catch {
        // Keep current cards gracefully when quota endpoint is unavailable.
      } finally {
        if (!signal?.aborted) setLoadingQuota(false)
      }
    },
    [client],
  )

  useEffect(() => {
    if (!client) return
    const controller = new AbortController()
    getQuota(client, { refresh: false, signal: controller.signal })
      .then((report) => {
        if (!controller.signal.aborted) {
          setSubscriptions(report.subscriptions)
        }
      })
      .catch(() => {
        // Keep current cards gracefully when quota endpoint is unavailable.
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingQuota(false)
      })
    return () => controller.abort()
  }, [client])

  useEffect(
    () => () => {
      if (followUpTimer.current !== null) window.clearTimeout(followUpTimer.current)
    },
    [],
  )

  // The refresh answers at once with what is stored; the CLI probes finish a few seconds later.
  const refreshQuota = useCallback(() => {
    void loadQuota(true)
    if (followUpTimer.current !== null) window.clearTimeout(followUpTimer.current)
    followUpTimer.current = window.setTimeout(() => {
      followUpTimer.current = null
      void loadQuota(false)
    }, QUOTA_PROBE_FOLLOW_UP_MS)
  }, [loadQuota])

  const orderedItems = useMemo(() => {
    const byPath = new Map(items.map((item) => [item.path, item]))
    const result: OrchestrationSettingItem[] = []
    const seen = new Set<string>()
    for (const path of ORDERED_QUOTA_PATHS) {
      const found = byPath.get(path)
      if (found) {
        result.push(found)
        seen.add(path)
      }
    }
    for (const item of items) {
      if (!seen.has(item.path)) result.push(item)
    }
    return result
  }, [items])

  return (
    <div className="quota-panel" data-testid="quota-settings-panel">
      <div className="quota-panel__live">
        <div className="quota-panel__live-head">
          <div>
            <h3 className="quota-panel__subhead">Assinaturas monitoradas</h3>
            <p className="code-settings__muted">
              Janelas de 5 h e semanal lidas dos CLIs, idade do dado e consumo acumulado pelo Orin.
            </p>
          </div>
          {client && (
            <button
              type="button"
              className="button button--quiet"
              disabled={loadingQuota}
              onClick={refreshQuota}
            >
              {loadingQuota ? 'Atualizando…' : 'Atualizar'}
            </button>
          )}
        </div>
        <SubscriptionQuotaCards
          subscriptions={subscriptions}
          nowMs={clockMs}
          layout="settings"
        />
      </div>

      {orderedItems.length > 0 && (
        <div className="quota-panel__settings">
          <OrchestrationSettingsForm
            items={orderedItems}
            busy={busy}
            hideGroupHeaders
            onSave={onSave}
          />
        </div>
      )}
    </div>
  )
}
