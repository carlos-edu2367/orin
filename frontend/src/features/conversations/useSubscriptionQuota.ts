import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { getOrchestrationSettings } from '../../api/orchestrationSettings'
import {
  getQuota,
  normalizeSubscriptionProvider,
  streamQuotaUpdates,
  type ProviderQuota,
} from '../../api/quota'

export type UseSubscriptionQuotaArgs = {
  client?: ApiClient | null
  provider?: string | null
  subscribeStream?: boolean
}

export type UseSubscriptionQuotaResult = {
  subscriptions: ProviderQuota[]
  displayEnabled: boolean
  loading: boolean
  refresh: () => void
  provider: string | null
}

/**
 * Fetches `quota.display.enabled` and `/v1/quota`, and listens to SSE `quota.updated`
 * when a subscription provider is active and display is enabled.
 * Falls back silently when endpoints are absent (such as in legacy unit tests).
 */
export function useSubscriptionQuota({
  client,
  provider = null,
  subscribeStream = true,
}: UseSubscriptionQuotaArgs): UseSubscriptionQuotaResult {
  const [displayEnabled, setDisplayEnabled] = useState(true)
  const [subscriptions, setSubscriptions] = useState<ProviderQuota[]>([])
  const [loading, setLoading] = useState(false)
  const normalizedProvider = normalizeSubscriptionProvider(provider)

  const loadQuota = useCallback(
    async (refresh = false, signal?: AbortSignal) => {
      if (!client) return
      setLoading(true)
      try {
        const report = await getQuota(client, { refresh, signal })
        if (!signal?.aborted) {
          setSubscriptions(report.subscriptions)
        }
      } catch {
        // Best-effort: never fail the composer when quota telemetry is unavailable.
      } finally {
        if (!signal?.aborted) setLoading(false)
      }
    },
    [client],
  )

  useEffect(() => {
    if (!client) return
    let cancelled = false
    getOrchestrationSettings(client, { prefix: 'quota.display.enabled' })
      .then((report) => {
        if (cancelled) return
        const item = report.items.find((entry) => entry.path === 'quota.display.enabled')
        if (item && typeof item.value === 'boolean') {
          setDisplayEnabled(item.value)
        }
      })
      .catch(() => {
        // Default remains true if orchestration settings endpoint is not mocked.
      })
    return () => {
      cancelled = true
    }
  }, [client])

  useEffect(() => {
    if (!client || !displayEnabled || !normalizedProvider) return
    const controller = new AbortController()
    getQuota(client, { refresh: false, signal: controller.signal })
      .then((report) => {
        if (!controller.signal.aborted) {
          setSubscriptions(report.subscriptions)
        }
      })
      .catch(() => {
        // Best-effort: never fail the composer when quota telemetry is unavailable.
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [client, displayEnabled, normalizedProvider])

  useEffect(() => {
    if (!client || !displayEnabled || !subscribeStream || !normalizedProvider) return
    const controller = new AbortController()
    let retryTimer: number | null = null

    const run = async () => {
      try {
        const openedSse = await streamQuotaUpdates(
          client,
          () => {
            if (!controller.signal.aborted) {
              void loadQuota(false, controller.signal)
            }
          },
          controller.signal,
        )
        if (!openedSse || controller.signal.aborted) return
        retryTimer = window.setTimeout(() => {
          if (!controller.signal.aborted) void run()
        }, 5000)
      } catch {
        // Do not loop aggressively if the server does not support SSE or test fetch rejects.
      }
    }

    void run()
    return () => {
      controller.abort()
      if (retryTimer !== null) window.clearTimeout(retryTimer)
    }
  }, [client, displayEnabled, loadQuota, normalizedProvider, subscribeStream])

  const refresh = useCallback(() => {
    void loadQuota(true)
  }, [loadQuota])

  return {
    subscriptions,
    displayEnabled,
    loading,
    refresh,
    provider: normalizedProvider,
  }
}
