import { useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { inspectProvider, listProviderModels, PROVIDER_NAMES, type ProviderName } from '../../api/providers'
import { SettingsSection } from '../settings/SettingsSection'
import { VisionModelSetting } from './VisionModelSetting'
import { ProviderGrid, type ProviderCardStates } from './ProviderGrid'

export function ProvidersSection({ client: providedClient }: { client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const [states, setStates] = useState<ProviderCardStates>(() => Object.fromEntries(PROVIDER_NAMES.map((provider) => [provider, { status: 'unconfigured', detail: 'Carregando estado…' }])) as ProviderCardStates)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all(PROVIDER_NAMES.map(async (provider) => {
      try {
        const state = await inspectProvider(client, provider, controller.signal)
        if (state.enabled !== true) return [provider, { status: 'unconfigured' as const, detail: state.enabled === false ? 'Desabilitado' : 'Não configurado' }] as const
        try {
          const models = await listProviderModels(client, provider, controller.signal)
          return [provider, { status: 'configured' as const, detail: `${models.length} modelos` }] as const
        } catch { return [provider, { status: 'configured' as const, detail: 'Configurado' }] as const }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        return [provider, { status: 'unavailable' as const, detail: 'Indisponível' }] as const
      }
    })).then((entries) => { if (!controller.signal.aborted) setStates(Object.fromEntries(entries) as ProviderCardStates) }).catch(() => undefined)
    return () => controller.abort()
  }, [client])
  return <SettingsSection eyebrow="PROVIDERS / CONEXÕES"><ProviderGrid states={states} /><VisionModelSetting client={client} /></SettingsSection>
}

export type { ProviderName }
