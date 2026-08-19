import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiClient, createBrowserApiClient, type MutationIntent } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  addProviderCustomModel, configureProvider, getOmniRouteInstallationStatus, inspectProvider, installOmniRoute, listProviderModels,
  refreshProviderModels, removeProviderCustomModel, revokeProvider, setProviderModelFavorite, testOllamaConnection, testOmniRouteConnection,
  type ProviderModel, type ProviderName, type ProviderPublicState,
} from '../../api/providers'

export type ProviderLoadState =
  | { status: 'loading' }
  | { status: 'loaded'; state: ProviderPublicState }
  | { status: 'unavailable'; error: ApiError }
export type ProviderAction = 'configure' | 'revoke' | 'refresh' | 'test' | 'install' | 'favorite' | 'custom-model' | null
export type ProviderActionState = { pending: boolean; error: ApiError | null; kind: ProviderAction }

export type ProviderState = {
  load: ProviderLoadState
  action: ProviderActionState
  apiKey: string
  setApiKey: (value: string) => void
  baseUrl: string
  setBaseUrl: (value: string) => void
  enabled: boolean
  setEnabled: (value: boolean) => void
  ollamaMode: 'local' | 'cloud'
  setOllamaMode: (value: 'local' | 'cloud') => void
  connection: { connected: boolean; models: number | null } | null
  installed: boolean
  models: ProviderModel[]
  catalog: { loaded: boolean; error: ApiError | null }
  catalogLoading: boolean
  canRevoke: boolean
  requiresApiKey: boolean
  configure: (event?: FormEvent<HTMLFormElement>) => Promise<void>
  revoke: () => Promise<void>
  refreshModels: () => Promise<void>
  loadModels: () => Promise<void>
  setFavorite: (model: ProviderModel) => Promise<void>
  addCustomModel: (modelId: string) => Promise<boolean>
  removeCustomModel: (modelId: string) => Promise<boolean>
  testConnection: () => Promise<void>
  install: () => Promise<void>
}

export function useProviderState(client: ApiClient = createBrowserApiClient(), provider: ProviderName, providedBootstrap?: BrowserSessionBootstrap): ProviderState {
  const bootstrap = providedBootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))
  const [load, setLoad] = useState<ProviderLoadState>({ status: 'loading' })
  const [action, setAction] = useState<ProviderActionState>({ pending: false, error: null, kind: null })
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(() => provider === 'ollama' ? 'http://localhost:11434' : 'http://localhost:20128/v1')
  const [enabled, setEnabled] = useState(true)
  const [ollamaMode, setOllamaMode] = useState<'local' | 'cloud'>('local')
  const [connection, setConnection] = useState<{ connected: boolean; models: number | null } | null>(null)
  const [installed, setInstalled] = useState(false)
  const [models, setModels] = useState<ProviderModel[]>([])
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalog, setCatalog] = useState<{ loaded: boolean; error: ApiError | null }>({ loaded: false, error: null })
  const intents = useRef(new Map<string, MutationIntent>())
  const mutationRevision = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    inspectProvider(client, provider, controller.signal).then((state) => {
      if (controller.signal.aborted || mutationRevision.current > 0) return
      setLoad({ status: 'loaded', state })
      if (state.enabled !== null) setEnabled(state.enabled)
      if (provider === 'omniroute' && typeof state.extra.base_url === 'string') setBaseUrl(state.extra.base_url)
      if (provider === 'ollama' && typeof state.extra.base_url === 'string') {
        setBaseUrl(state.extra.base_url)
        try { setOllamaMode(new URL(state.extra.base_url).hostname.toLowerCase().endsWith('ollama.com') ? 'cloud' : 'local') } catch { setOllamaMode('local') }
      }
    }).catch((error: unknown) => {
      if (controller.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return
      const apiError = toApiError(error)
      setLoad(apiError.status === 404 ? { status: 'loaded', state: { provider, enabled: null, extra: {} } } : { status: 'unavailable', error: apiError })
    })
    return () => controller.abort()
  }, [client, provider])

  useEffect(() => {
    if (provider !== 'omniroute') return
    const controller = new AbortController()
    getOmniRouteInstallationStatus(client, controller.signal).then((result) => { if (result.installed) setInstalled(true) }).catch(() => undefined)
    return () => controller.abort()
  }, [client, provider])

  const intentFor = (key: string) => {
    const existing = intents.current.get(key)
    if (existing) return existing
    const intent = client.createMutationIntent()
    intents.current.set(key, intent)
    return intent
  }
  const requiresApiKey = provider !== 'omniroute' && !(provider === 'ollama' && ollamaMode === 'local')
  const canRevoke = load.status === 'loaded' && load.state.enabled === true

  async function configure(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault()
    if (action.pending || (!apiKey && requiresApiKey) || bootstrap.status === 'missing_csrf') return
    mutationRevision.current += 1
    setAction({ pending: true, error: null, kind: 'configure' })
    try {
      const state = await configureProvider(client, provider, { apiKey, enabled, ...(provider === 'omniroute' || provider === 'ollama' ? { baseUrl } : {}) }, intentFor('configure'))
      intents.current.delete('configure')
      setLoad({ status: 'loaded', state })
      setApiKey('')
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'configure' })
    }
  }

  async function revoke() {
    if (action.pending || !canRevoke || bootstrap.status === 'missing_csrf') return
    mutationRevision.current += 1
    setAction({ pending: true, error: null, kind: 'revoke' })
    try {
      const state = await revokeProvider(client, provider, intentFor('revoke'))
      intents.current.delete('revoke')
      setLoad({ status: 'loaded', state }); setApiKey('')
      setAction({ pending: false, error: null, kind: null })
    } catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'revoke' }) }
  }

  async function loadModels() {
    setCatalogLoading(true)
    try { setModels(await listProviderModels(client, provider)); setCatalog({ loaded: true, error: null }) }
    catch (error) { setModels([]); setCatalog({ loaded: true, error: toApiError(error) }) }
    finally { setCatalogLoading(false) }
  }

  async function refreshModels() {
    if (action.pending || !canRevoke || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'refresh' })
    try { await refreshProviderModels(client, provider, intentFor('refresh')); intents.current.delete('refresh'); await loadModels(); setAction({ pending: false, error: null, kind: null }) }
    catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'refresh' }) }
  }

  async function setFavorite(model: ProviderModel) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'favorite' })
    try {
      const key = `favorite:${model.model_id}`
      const updated = await setProviderModelFavorite(client, provider, model.model_id, !model.is_favorite, intentFor(key))
      intents.current.delete(key); setModels((current) => current.map((item) => item.model_id === updated.model_id ? updated : item)); setAction({ pending: false, error: null, kind: null })
    } catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'favorite' }) }
  }

  async function addCustomModel(modelId: string): Promise<boolean> {
    if (action.pending || !canRevoke || bootstrap.status === 'missing_csrf') return false
    setAction({ pending: true, error: null, kind: 'custom-model' })
    try {
      const key = `custom-model:add:${modelId}`
      await addProviderCustomModel(client, provider, modelId, undefined, intentFor(key))
      intents.current.delete(key)
      await loadModels()
      setAction({ pending: false, error: null, kind: null })
      return true
    } catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'custom-model' }); return false }
  }

  async function removeCustomModel(modelId: string): Promise<boolean> {
    if (action.pending || !canRevoke || bootstrap.status === 'missing_csrf') return false
    setAction({ pending: true, error: null, kind: 'custom-model' })
    try {
      const key = `custom-model:remove:${modelId}`
      await removeProviderCustomModel(client, provider, modelId, intentFor(key))
      intents.current.delete(key)
      await loadModels()
      setAction({ pending: false, error: null, kind: null })
      return true
    } catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'custom-model' }); return false }
  }

  async function testConnection() {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'test' })
    try { const result = await (provider === 'ollama' ? testOllamaConnection(client, { apiKey, baseUrl }, intentFor('test')) : testOmniRouteConnection(client, { apiKey, baseUrl }, intentFor('test'))); intents.current.delete('test'); setConnection({ connected: true, models: result.models_available }); setAction({ pending: false, error: null, kind: null }) }
    catch (error) { setConnection(null); setAction({ pending: false, error: toApiError(error), kind: 'test' }) }
  }

  async function install() {
    if (action.pending || provider !== 'omniroute' || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'install' })
    try { await installOmniRoute(client, intentFor('install')); intents.current.delete('install'); setInstalled(true); setAction({ pending: false, error: null, kind: null }) }
    catch (error) { setAction({ pending: false, error: toApiError(error), kind: 'install' }) }
  }

  return { load, action, apiKey, setApiKey, baseUrl, setBaseUrl, enabled, setEnabled, ollamaMode, setOllamaMode, connection, installed, models, catalog, catalogLoading, canRevoke, requiresApiKey, configure, revoke, refreshModels, loadModels, setFavorite, addCustomModel, removeCustomModel, testConnection, install }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({ status: 0, code: 'request_failed', category: 'INTERNAL', messageKey: 'request_failed', retryable: false })
}
