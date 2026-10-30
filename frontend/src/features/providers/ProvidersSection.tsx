import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  inspectProvider,
  listProviderModels,
  PROVIDER_NAMES,
  type ProviderModel,
  type ProviderName,
} from '../../api/providers'
import { useSession } from '../../app/useSession'
import { SettingsSection } from '../settings/SettingsSection'
import { MediaModelsSetting } from './MediaModelsSetting'
import { ProviderGrid, type ProviderCardStates } from './ProviderGrid'
import { VisionModelSetting } from './VisionModelSetting'

type PrimaryProviderOption = {
  id: string
  label: string
}

type CuratedModelOption = {
  id: string
  label: string
}

const BASE_PRIMARY_PROVIDERS: ReadonlyArray<PrimaryProviderOption> = [
  { id: 'openai', label: 'OpenAI' },
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'google', label: 'Google Gemini' },
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'groq', label: 'Groq' },
]

const CURATED_MODELS_BY_PROVIDER: Record<string, ReadonlyArray<CuratedModelOption>> = {
  openai: [
    { id: 'gpt-5.4', label: 'GPT-5.4 · Recomendado' },
    { id: 'gpt-4.1', label: 'GPT-4.1' },
  ],
  anthropic: [
    { id: 'claude-sonnet-4-5', label: 'Claude Sonnet 4.5 · Recomendado' },
    { id: 'claude-3-7-sonnet', label: 'Claude 3.7 Sonnet' },
  ],
  google: [
    { id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro · Recomendado' },
    { id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  ],
  openrouter: [
    { id: 'openrouter/auto', label: 'OpenRouter Auto · Recomendado' },
  ],
  groq: [
    { id: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B · Recomendado' },
  ],
  omniroute: [
    { id: 'auto/coding', label: 'OmniRoute Auto · Recomendado' },
  ],
}

const FALLBACK_OPTIONS: ReadonlyArray<CuratedModelOption> = [
  { id: 'ollama:local', label: 'Ollama · Modelo local' },
  { id: 'openrouter:auto', label: 'OpenRouter · Modelo automático' },
  { id: 'anthropic:claude-sonnet-4-5', label: 'Anthropic · Claude Sonnet 4.5' },
  { id: 'openai:gpt-4.1-mini', label: 'OpenAI · GPT-4.1 Mini' },
  { id: 'none', label: 'Nenhum modelo reserva' },
]

const PROVIDER_DISPLAY_LABELS: Record<ProviderName, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  nvidia: 'NVIDIA NIM',
  opencode: 'OpenCode',
  omniroute: 'OmniRoute',
  ollama: 'Ollama (no computador)',
}

function safeGetStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeSetStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Ignore storage write errors (e.g. quota exceeded or private mode)
  }
}

function isCatalogProvider(provider: string): provider is ProviderName {
  return (PROVIDER_NAMES as readonly string[]).includes(provider)
}

export function ProvidersSection({ client: providedClient }: { client?: ApiClient }) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const navigate = useNavigate()
  const catalogRef = useRef<HTMLDivElement | null>(null)
  const { capabilities } = useSession()
  const omniroute = capabilities.omniroute

  const [states, setStates] = useState<ProviderCardStates>(
    () =>
      Object.fromEntries(
        PROVIDER_NAMES.map((provider) => [
          provider,
          { status: 'unconfigured', detail: 'Carregando estado…' },
        ]),
      ) as ProviderCardStates,
  )

  const primaryProviderOptions = useMemo<ReadonlyArray<PrimaryProviderOption>>(
    () =>
      omniroute
        ? [...BASE_PRIMARY_PROVIDERS, { id: 'omniroute', label: 'OmniRoute' }]
        : BASE_PRIMARY_PROVIDERS,
    [omniroute],
  )

  const [primaryProvider, setPrimaryProvider] = useState<string>(() => {
    const stored = safeGetStorage('agentos.provider')
    if (stored && (BASE_PRIMARY_PROVIDERS.some((p) => p.id === stored) || stored === 'omniroute')) {
      return stored
    }
    return 'openai'
  })

  const [primaryModel, setPrimaryModel] = useState<string>(() => {
    const storedModel = safeGetStorage('agentos.model')
    if (storedModel) return storedModel
    const storedProvider = safeGetStorage('agentos.provider') || 'openai'
    return CURATED_MODELS_BY_PROVIDER[storedProvider]?.[0]?.id ?? 'gpt-5.4'
  })

  const [dynamicModels, setDynamicModels] = useState<ProviderModel[]>([])

  const [fallbackModel, setFallbackModel] = useState<string>(() => {
    const stored = safeGetStorage('orin.providers.fallback')
    if (stored && FALLBACK_OPTIONS.some((opt) => opt.id === stored)) {
      return stored
    }
    return 'ollama:local'
  })

  const [fallbackAuto, setFallbackAuto] = useState<boolean>(() => {
    return safeGetStorage('orin.providers.fallbackAuto') !== 'false'
  })

  const [showCostEstimate, setShowCostEstimate] = useState<boolean>(() => {
    return safeGetStorage('orin.providers.showCostEstimate') !== 'false'
  })

  const [savedNotice, setSavedNotice] = useState<boolean>(false)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all(
      PROVIDER_NAMES.filter((provider) => provider !== 'omniroute' || omniroute).map(async (provider) => {
        try {
          const state = await inspectProvider(client, provider, controller.signal)
          if (state.enabled !== true) {
            return [
              provider,
              {
                status: 'unconfigured' as const,
                detail: state.enabled === false ? 'Desabilitado' : 'Não configurado',
              },
            ] as const
          }
          try {
            const models = await listProviderModels(client, provider, controller.signal)
            return [provider, { status: 'configured' as const, detail: `${models.length} modelos` }] as const
          } catch {
            return [provider, { status: 'configured' as const, detail: 'Configurado' }] as const
          }
        } catch (error) {
          if (error instanceof DOMException && error.name === 'AbortError') throw error
          return [provider, { status: 'unavailable' as const, detail: 'Indisponível' }] as const
        }
      }),
    )
      .then((entries) => {
        if (!controller.signal.aborted) {
          setStates(Object.fromEntries(entries) as ProviderCardStates)
        }
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [client, omniroute])

  useEffect(() => {
    if (!isCatalogProvider(primaryProvider)) {
      return
    }
    const controller = new AbortController()
    listProviderModels(client, primaryProvider, controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) {
          setDynamicModels(items)
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setDynamicModels([])
        }
      })
    return () => controller.abort()
  }, [client, primaryProvider])

  const modelOptions = useMemo<ReadonlyArray<CuratedModelOption>>(() => {
    const curated = CURATED_MODELS_BY_PROVIDER[primaryProvider] ?? []
    const seen = new Set<string>(curated.map((item) => item.id))
    const merged: CuratedModelOption[] = [...curated]
    const activeDynamicModels = isCatalogProvider(primaryProvider) ? dynamicModels : []
    for (const model of activeDynamicModels) {
      if (!seen.has(model.model_id)) {
        seen.add(model.model_id)
        merged.push({
          id: model.model_id,
          label: model.display_name || model.model_id,
        })
      }
    }
    if (primaryModel && !seen.has(primaryModel)) {
      merged.push({ id: primaryModel, label: primaryModel })
    }
    return merged
  }, [dynamicModels, primaryModel, primaryProvider])

  const connectedProviders = useMemo(() => {
    const items: Array<{ id: ProviderName; name: string; statusLabel: string }> = []
    for (const provider of PROVIDER_NAMES) {
      if (provider === 'omniroute' && !omniroute) continue
      const st = states[provider]
      if (st?.status === 'configured') {
        items.push({
          id: provider,
          name: PROVIDER_DISPLAY_LABELS[provider],
          statusLabel: st.detail ? `Conectado · ${st.detail}` : 'Conectado',
        })
      }
    }
    return items
  }, [omniroute, states])

  function handleChangePrimaryProvider(nextProvider: string) {
    setPrimaryProvider(nextProvider)
    setSavedNotice(false)
    const defaultModel = CURATED_MODELS_BY_PROVIDER[nextProvider]?.[0]?.id ?? ''
    setPrimaryModel(defaultModel)
  }

  function handleSave() {
    safeSetStorage('agentos.provider', primaryProvider)
    safeSetStorage('agentos.model', primaryModel)
    safeSetStorage('orin.providers.fallback', fallbackModel)
    safeSetStorage('orin.providers.fallbackAuto', String(fallbackAuto))
    safeSetStorage('orin.providers.showCostEstimate', String(showCostEstimate))
    setSavedNotice(true)
  }

  function handleConnectProvider() {
    catalogRef.current?.scrollIntoView?.({ behavior: 'smooth' })
    navigate('/settings/providers/openai')
  }

  return (
    <SettingsSection eyebrow="PROVIDERS / CONEXÕES">
      <div className="providers-ref14">
        <header className="providers-ref14__header">
          <h2 className="providers-ref14__title" data-testid="providers-ref14-heading">
            Modelos e provedores
          </h2>
          <p className="providers-ref14__subtitle">Escolha a IA que trabalha com você.</p>
        </header>

        <div className="providers-ref14__grid" data-testid="providers-ref14">
          <div className="providers-ref14__main">
            <section className="providers-ref14__card" data-testid="providers-primary-card">
              <h3>Modelo principal</h3>
              <div className="providers-ref14__fields">
                <div className="providers-ref14__field">
                  <label htmlFor="providers-primary-provider">Provedor</label>
                  <select
                    id="providers-primary-provider"
                    data-testid="providers-primary-provider-select"
                    value={primaryProvider}
                    onChange={(e) => handleChangePrimaryProvider(e.target.value)}
                  >
                    {primaryProviderOptions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="providers-ref14__field">
                  <label htmlFor="providers-primary-model">Modelo</label>
                  <select
                    id="providers-primary-model"
                    data-testid="providers-primary-model-select"
                    value={primaryModel}
                    onChange={(e) => {
                      setPrimaryModel(e.target.value)
                      setSavedNotice(false)
                    }}
                  >
                    {modelOptions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="providers-ref14__hint">Usado por padrão nas novas conversas.</p>
            </section>

            <section className="providers-ref14__card" data-testid="providers-fallback-card">
              <h3>Se houver uma falha</h3>
              <div className="providers-ref14__field">
                <label htmlFor="providers-fallback-select">Provedor e modelo reserva</label>
                <select
                  id="providers-fallback-select"
                  data-testid="providers-fallback-select"
                  value={fallbackModel}
                  onChange={(e) => {
                    setFallbackModel(e.target.value)
                    setSavedNotice(false)
                  }}
                >
                  {FALLBACK_OPTIONS.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <label className="providers-ref14__checkbox">
                <input
                  type="checkbox"
                  data-testid="providers-fallback-auto-checkbox"
                  checked={fallbackAuto}
                  onChange={(e) => {
                    setFallbackAuto(e.target.checked)
                    setSavedNotice(false)
                  }}
                />
                <span>Usar reserva automaticamente</span>
              </label>
            </section>

            <section className="providers-ref14__card" data-testid="providers-connected-card">
              <h3>Provedores conectados</h3>
              {connectedProviders.length === 0 ? (
                <p
                  className="providers-ref14__empty-connected"
                  data-testid="providers-connected-empty"
                >
                  Nenhum provedor conectado ainda. Conecte um provedor abaixo para começar.
                </p>
              ) : (
                <ul className="providers-ref14__connected-list">
                  {connectedProviders.map((item) => (
                    <li key={item.id} className="providers-ref14__connected-row">
                      <span className="providers-ref14__connected-name">{item.name}</span>
                      <span className="providers-ref14__connected-status">
                        <span className="providers-ref14__status-dot" aria-hidden="true" />
                        {item.statusLabel}
                      </span>
                      <button
                        type="button"
                        className="providers-ref14__manage-btn"
                        data-testid={`providers-manage-${item.id}`}
                        onClick={() => navigate(`/settings/providers/${item.id}`)}
                      >
                        Gerenciar <span aria-hidden="true">›</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <button
                type="button"
                className="providers-ref14__connect-btn"
                data-testid="providers-connect-btn"
                onClick={handleConnectProvider}
              >
                + Conectar provedor
              </button>
            </section>
          </div>

          <aside className="providers-ref14__aside">
            <section className="providers-ref14__card" data-testid="providers-cost-card">
              <h3>Uso e custos</h3>
              <label className="providers-ref14__checkbox">
                <input
                  type="checkbox"
                  data-testid="providers-cost-estimate-checkbox"
                  checked={showCostEstimate}
                  onChange={(e) => {
                    setShowCostEstimate(e.target.checked)
                    setSavedNotice(false)
                  }}
                />
                <span>Mostrar estimativa por conversa</span>
              </label>
              <p className="providers-ref14__hint">
                O envio a provedores externos depende da conexão escolhida.
              </p>
            </section>

            <div className="providers-ref14__actions">
              <button
                type="button"
                className="button button--primary providers-ref14__save-btn"
                data-testid="providers-save-btn"
                onClick={handleSave}
              >
                Salvar alterações
              </button>
              {savedNotice && (
                <p
                  role="status"
                  className="providers-ref14__saved-notice"
                  data-testid="providers-saved-notice"
                >
                  Alterações salvas.
                </p>
              )}
            </div>
          </aside>
        </div>
      </div>

      <div className="providers-ref14__catalog" ref={catalogRef}>
        <ProviderGrid states={states} />
        <VisionModelSetting client={client} />
        <MediaModelsSetting client={client} />
      </div>
    </SettingsSection>
  )
}

export type { ProviderName }
