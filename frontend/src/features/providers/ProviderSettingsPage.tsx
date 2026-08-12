import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiClient, createBrowserApiClient, type MutationIntent } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError, isAuthenticationError, isCsrfAuthorizationError } from '../../api/errors'
import { configureProvider, inspectProvider, revokeProvider, refreshProviderModels, listProviderModels, setProviderModelFavorite, testOmniRouteConnection, installOmniRoute, getOmniRouteInstallationStatus, getOmniRouteRuntime, setOmniRouteAutoStart, controlOmniRoute, PROVIDER_NAMES, type OmniRouteRuntime, type ProviderModel, type ProviderName, type ProviderPublicState } from '../../api/providers'
import { OMNIROUTE_FREE_PROVIDER_GUIDES } from './omnirouteFreeProviders'
import { Brand } from '../../components/Brand'

type ProviderSettingsPageProps = {
  client?: ApiClient
  bootstrap?: BrowserSessionBootstrap
}

/**
 * A form, not a dashboard (UX_UI_SPEC.md "Decisões visuais"): one panel per
 * documented provider, each showing only the public state the gateway actually
 * returns. The key field is write-only — it is never populated from a response and
 * is cleared only after an accepted configuration or revocation.
 */
export function ProviderSettingsPage({ client = createBrowserApiClient(), bootstrap }: ProviderSettingsPageProps) {
  const session = bootstrap ?? (typeof document === 'undefined'
    ? { status: 'missing_csrf' as const }
    : readBrowserSessionBootstrap(document))

  return (
    <main className="app-shell provider-settings-shell">
      <header className="topbar">
        <Brand href="/" />
        <span className="topbar__context">Configurações de provider</span>
        <a className="topbar__back" href="/">Voltar ao início</a>
      </header>

      <section className="provider-settings" aria-labelledby="provider-settings-title">
        <p className="eyebrow">PROVIDERS / CONEXÃO SEGURA</p>
        <h1 id="provider-settings-title">Provedores de modelo</h1>
        <p className="provider-settings__lede">
          Configure ou revogue o acesso de cada provider autorizado. A chave de API nunca é relida nem reexibida depois do envio.
        </p>
        <div className="provider-settings__list">
          {PROVIDER_NAMES.map((provider) => (
            <ProviderPanel key={provider} provider={provider} client={client} bootstrap={session} />
          ))}
        </div>
      </section>
    </main>
  )
}

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded'; state: ProviderPublicState }
  | { status: 'unavailable'; error: ApiError }

type ProviderAction = 'configure' | 'revoke' | 'refresh' | 'test' | 'install' | 'favorite' | null
type ActionState = { pending: boolean; error: ApiError | null; kind: ProviderAction }

function ProviderPanel({ provider, client, bootstrap }: { provider: ProviderName; client: ApiClient; bootstrap: BrowserSessionBootstrap }) {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [action, setAction] = useState<ActionState>({ pending: false, error: null, kind: null })
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('http://localhost:20128/v1')
  const [enabled, setEnabled] = useState(true)
  const [omniOpen, setOmniOpen] = useState(provider !== 'omniroute')
  const [connection, setConnection] = useState<{ connected: boolean; models: number | null } | null>(null)
  const [installed, setInstalled] = useState(false)
  const [models, setModels] = useState<ProviderModel[]>([])
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalog, setCatalog] = useState<{ loaded: boolean; error: ApiError | null }>({ loaded: false, error: null })
  // One Idempotency-Key per intention (save/revoke), reused while that intention
  // has not yet received an accepted response — same pattern as ExecutionRoute.
  const intents = useRef(new Map<string, MutationIntent>())

  useEffect(() => {
    const controller = new AbortController()
    inspectProvider(client, provider, controller.signal)
      .then((state) => {
        setLoad({ status: 'loaded', state })
        if (state.enabled !== null) setEnabled(state.enabled)
        if (provider === 'omniroute' && state.enabled === true) setOmniOpen(true)
        if (provider === 'omniroute' && typeof state.extra.base_url === 'string') setBaseUrl(state.extra.base_url)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        const apiError = toApiError(error)
        // A provider has no row until its first key is saved. That is a normal
        // starting state, not an infrastructure failure.
        if (apiError.status === 404) {
          setLoad({ status: 'loaded', state: { provider, enabled: null, extra: {} } })
          return
        }
        setLoad({ status: 'unavailable', error: apiError })
      })
    return () => controller.abort()
  }, [client, provider])

  useEffect(() => {
    if (provider !== 'omniroute') return
    let active = true
    const controller = new AbortController()
    const refreshStatus = () => getOmniRouteInstallationStatus(client, controller.signal)
      .then(({ installed: status }) => {
        if (!active || !status) return
        setInstalled(true)
        setAction((current) => current.kind === 'install' ? { pending: false, error: null, kind: null } : current)
      })
      .catch(() => undefined)
    void refreshStatus()
    if (!(action.pending && action.kind === 'install')) return () => { active = false; controller.abort() }
    const interval = globalThis.setInterval(() => { void refreshStatus() }, 2000)
    return () => { active = false; controller.abort(); globalThis.clearInterval(interval) }
  }, [action.kind, action.pending, client, provider])

  // A silent `catch` here is what makes the button look inert: the request
  // fails, the list stays empty, and the panel renders exactly nothing. Both
  // outcomes — a failure and a genuinely empty catalog — have to say so.
  function loadModels() {
    setCatalogLoading(true)
    return listProviderModels(client, provider)
      .then((items) => { setModels(items); setCatalog({ loaded: true, error: null }) })
      .catch((error: unknown) => { setModels([]); setCatalog({ loaded: true, error: toApiError(error) }) })
      .finally(() => setCatalogLoading(false))
  }

  function intentFor(action: string): MutationIntent {
    const existing = intents.current.get(action)
    if (existing) return existing
    const created = client.createMutationIntent()
    intents.current.set(action, created)
    return created
  }

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (action.pending || (!apiKey && provider !== 'omniroute') || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'configure' })
    const intent = intentFor('configure')
    try {
      const state = await configureProvider(client, provider, { apiKey, enabled, ...(provider === 'omniroute' ? { baseUrl } : {}) }, intent)
      intents.current.delete('configure')
      setLoad({ status: 'loaded', state })
      setApiKey('')
      // "Salvar e ativar" is the last step of the guided OmniRoute flow, but a
      // saved credential alone leaves the catalog empty — which is why the
      // composer kept offering "Configurar provider" right after a successful
      // setup. Fetching the catalog here is what actually finishes the flow.
      // A refresh failure is reported on the catalog, never as a failed save:
      // the credential is already stored at this point.
      if (provider === 'omniroute' && state.enabled === true) {
        try {
          await refreshProviderModels(client, provider, intentFor('refresh'))
          intents.current.delete('refresh')
          await loadModels()
        } catch (refreshError) {
          setCatalog({ loaded: true, error: toApiError(refreshError) })
        }
      }
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'configure' })
    }
  }

  async function onRevoke() {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'revoke' })
    const intent = intentFor('revoke')
    try {
      const state = await revokeProvider(client, provider, intent)
      intents.current.delete('revoke')
      setLoad({ status: 'loaded', state })
      setAction({ pending: false, error: null, kind: null })
      setApiKey('')
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'revoke' })
    }
  }

  async function onRefresh() {
    if (action.pending || !canRevoke || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'refresh' })
    try {
      await refreshProviderModels(client, provider, intentFor('refresh'))
      intents.current.delete('refresh')
      await loadModels()
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'refresh' })
    }
  }

  async function onTestConnection() {
    if (provider !== 'omniroute' || action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'test' })
    try {
      const result = await testOmniRouteConnection(client, { apiKey, baseUrl }, intentFor('test'))
      intents.current.delete('test')
      setConnection({ connected: true, models: result.models_available })
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setConnection(null)
      setAction({ pending: false, error: toApiError(error), kind: 'test' })
    }
  }

  async function onInstall() {
    if (provider !== 'omniroute' || action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'install' })
    try {
      await installOmniRoute(client, intentFor('install'))
      intents.current.delete('install')
      setInstalled(true)
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'install' })
    }
  }

  async function toggleFavorite(model: ProviderModel) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'favorite' })
    try {
      const updated = await setProviderModelFavorite(client, provider, model.model_id, !model.is_favorite, intentFor(`favorite:${model.model_id}`))
      intents.current.delete(`favorite:${model.model_id}`)
      setModels((items) => items.map((item) => item.model_id === updated.model_id ? updated : item))
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'favorite' })
    }
  }

  const canRevoke = load.status === 'loaded' && load.state.enabled === true
  const isOmniRoute = provider === 'omniroute'

  return (
    <article className="provider-panel" aria-labelledby={`provider-${provider}-title`}>
      <div className="provider-panel__header">
        <p className="eyebrow">{provider.toUpperCase()}</p>
        <h2 id={`provider-${provider}-title`}>{providerLabel(provider)}</h2>
        <span className="provider-panel__status" role="status">{connection?.connected ? 'Conectado' : isOmniRoute && load.status === 'loaded' && load.state.enabled === null ? 'Off' : describeState(load)}</span>
      </div>

      {isOmniRoute && <OmniRouteSetup
        open={omniOpen}
        installed={installed}
        enabled={enabled}
        apiKey={apiKey}
        baseUrl={baseUrl}
        action={action}
        canRevoke={canRevoke}
        bootstrap={bootstrap}
        connection={connection}
        onOpen={() => setOmniOpen(true)}
        onInstall={onInstall}
        onTest={onTestConnection}
        onSave={onSave}
        onRevoke={onRevoke}
        onApiKeyChange={setApiKey}
        onBaseUrlChange={setBaseUrl}
        onEnabledChange={setEnabled}
      />}
      {isOmniRoute && <OmniRouteRuntimePanel client={client} bootstrap={bootstrap} />}

      {!isOmniRoute && <form className="provider-panel__form" onSubmit={onSave}>
        <label htmlFor={`${provider}-api-key`}>Chave de API</label>
        <input
          id={`${provider}-api-key`}
          name="api-key"
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder="Inserir uma nova chave"
        />
        <label className="provider-panel__toggle" htmlFor={`${provider}-enabled`}>
          <input
            id={`${provider}-enabled`}
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Habilitado
        </label>
        <div className="provider-panel__actions">
          <button type="submit" className="button button--primary" disabled={action.pending || (!apiKey && !isOmniRoute) || bootstrap.status === 'missing_csrf'}>
            {action.pending ? 'Salvando' : 'Salvar'}
          </button>
          <button
            type="button"
            className="button button--secondary button--danger"
            disabled={action.pending || !canRevoke || bootstrap.status === 'missing_csrf'}
            onClick={onRevoke}
          >
            Revogar acesso
          </button>
          <button type="button" className="button button--secondary" disabled={action.pending || !canRevoke || bootstrap.status === 'missing_csrf'} onClick={onRefresh}>
            Atualizar catálogo
          </button>
        </div>
        {bootstrap.status === 'missing_csrf' && <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de salvar.</p>}
        {action.error && <ProviderErrorNotice error={action.error} action={action.kind} />}
      </form>}
      {isOmniRoute && omniOpen && <OmniRouteFreeSetup />}
      {canRevoke && <section className="provider-panel__catalog" aria-label={`Modelos de ${providerLabel(provider)}`}>
        {isOmniRoute && <button type="button" className="button button--secondary" disabled={action.pending || bootstrap.status === 'missing_csrf'} onClick={onRefresh}>
          {action.pending && action.kind === 'refresh' ? 'Atualizando…' : 'Atualizar catálogo'}
        </button>}
        <button type="button" className="button button--secondary" disabled={catalogLoading} onClick={loadModels}>
          {catalogLoading ? 'Carregando modelos' : 'Ver modelos autorizados'}
        </button>
        {models.length > 0 && <ul>
          {models.map((model) => <li key={model.model_id}>
            <span>{model.display_name} <code>{model.model_id}</code></span>
            <button type="button" className="button button--secondary" onClick={() => toggleFavorite(model)} disabled={action.pending || bootstrap.status === 'missing_csrf'} aria-pressed={model.is_favorite}>
              {model.is_favorite ? 'Remover favorito' : 'Favoritar'}
            </button>
          </li>)}
        </ul>}
        {catalog.error && <ProviderErrorNotice error={catalog.error} action="refresh" />}
        {catalog.loaded && !catalog.error && models.length === 0 && <p role="status">
          Nenhum modelo no catálogo. Use "Atualizar catálogo" para buscá-los do provider.
        </p>}
      </section>}
    </article>
  )
}

function OmniRouteRuntimePanel({ client, bootstrap }: { client: ApiClient; bootstrap: BrowserSessionBootstrap }) {
  const [runtime, setRuntime] = useState<OmniRouteRuntime | null>(null)
  const [pending, setPending] = useState(false)
  useEffect(() => { void getOmniRouteRuntime(client).then(setRuntime).catch(() => setRuntime(null)) }, [client])
  async function configure(next: boolean) { if (pending || bootstrap.status === 'missing_csrf') return; setPending(true); try { setRuntime(await setOmniRouteAutoStart(client, next)) } finally { setPending(false) } }
  async function control(action: 'start' | 'stop' | 'restart') { if (pending || bootstrap.status === 'missing_csrf') return; setPending(true); try { setRuntime(await controlOmniRoute(client, action)) } finally { setPending(false) } }
  if (!runtime) return null
  const controlled = runtime.ownership === 'agentos'
  return <section className="omniroute-runtime" aria-label="Runtime OmniRoute"><div><p className="eyebrow">RUNTIME LOCAL</p><h3>OmniRoute {runtime.state === 'ready' ? 'Running' : runtime.state === 'external' ? 'External' : runtime.state}</h3><p>{runtime.ownership === 'external' ? 'Instância externa detectada; o Orin não a encerra.' : 'Gateway opcional, gerenciado sem bloquear o Orin.'}</p></div><label className="provider-panel__toggle"><input type="checkbox" checked={runtime.auto_start} disabled={pending || bootstrap.status === 'missing_csrf'} onChange={(event) => void configure(event.target.checked)} />Start OmniRoute when Orin launches</label>{runtime.failure && <p role="alert">OmniRoute couldn't start. {runtime.failure}</p>}<div className="omniroute-runtime__actions"><button type="button" className="button button--secondary" disabled={pending || runtime.state === 'ready' || bootstrap.status === 'missing_csrf'} onClick={() => void control('start')}>Start</button><button type="button" className="button button--secondary" disabled={pending || !controlled || bootstrap.status === 'missing_csrf'} onClick={() => void control('restart')}>Restart</button>{controlled && <button type="button" className="button button--secondary button--danger" disabled={pending || bootstrap.status === 'missing_csrf'} onClick={() => void control('stop')}>Stop</button>}</div></section>
}

function providerLabel(provider: ProviderName): string {
  switch (provider) {
    case 'openai':
      return 'OpenAI'
    case 'anthropic':
      return 'Anthropic'
    case 'openrouter':
      return 'OpenRouter'
    case 'omniroute':
      return 'OmniRoute'
  }
}

type OmniRouteSetupProps = {
  open: boolean
  installed: boolean
  enabled: boolean
  apiKey: string
  baseUrl: string
  action: ActionState
  canRevoke: boolean
  bootstrap: BrowserSessionBootstrap
  connection: { connected: boolean; models: number | null } | null
  onOpen: () => void
  onInstall: () => void
  onTest: () => void
  onSave: (event: FormEvent<HTMLFormElement>) => void
  onRevoke: () => void
  onApiKeyChange: (value: string) => void
  onBaseUrlChange: (value: string) => void
  onEnabledChange: (value: boolean) => void
}

function OmniRouteSetup({
  open, installed, enabled, apiKey, baseUrl, action, canRevoke, bootstrap, connection,
  onOpen, onInstall, onTest, onSave, onRevoke, onApiKeyChange, onBaseUrlChange, onEnabledChange,
}: OmniRouteSetupProps) {
  const sessionUnavailable = bootstrap.status === 'missing_csrf'
  const isInstalling = action.pending && action.kind === 'install'
  const isTesting = action.pending && action.kind === 'test'
  const isSaving = action.pending && action.kind === 'configure'

  if (!open) {
    return <div className="omniroute-flow__intro">
      <p className="omniroute-flow__kicker">GATEWAY LOCAL</p>
      <p>Instale o gateway local uma vez, conecte sua conta e mantenha modelos, fallback e roteamento em um único lugar.</p>
      <button type="button" className="button button--primary" onClick={onOpen}>Configurar OmniRoute</button>
    </div>
  }

  return <form className="omniroute-flow" onSubmit={onSave}>
    <ol className="omniroute-flow__steps" aria-label="Etapas de configuração do OmniRoute">
      <li className={`omniroute-step ${installed ? 'omniroute-step--complete' : ''}`}>
        <div className="omniroute-step__number" aria-hidden="true">1</div>
        <div className="omniroute-step__content">
          <div className="omniroute-step__heading">
            <div><p>PRIMEIRO PASSO</p><h3>Instale o gateway</h3></div>
            <span className="omniroute-step__state">{installed ? 'Instalado' : 'Necessário'}</span>
          </div>
          <p>O Orin executa o comando oficial <code>npm install -g omniroute</code>. Nenhuma credencial é enviada nesta etapa.</p>
          <button type="button" className="button button--primary" disabled={action.pending || sessionUnavailable || installed} onClick={onInstall}>
            {installed ? 'OmniRoute instalado' : isInstalling ? 'Instalando…' : 'Instalar OmniRoute'}
          </button>
          {installed && <p className="omniroute-step__success" aria-live="polite">Instalação concluída. Execute <code>omniroute</code> para iniciar o gateway.</p>}
        </div>
      </li>

      <li className={`omniroute-step ${connection?.connected ? 'omniroute-step--complete' : ''}`}>
        <div className="omniroute-step__number" aria-hidden="true">2</div>
        <div className="omniroute-step__content">
          <div className="omniroute-step__heading">
            <div><p>CONEXÃO</p><h3>Teste o acesso</h3></div>
            <span className="omniroute-step__state">{connection?.connected ? 'Conectado' : 'Pendente'}</span>
          </div>
          <p>Use a URL do gateway em execução. A chave é opcional para providers gratuitos configurados no próprio OmniRoute.</p>
          <div className="omniroute-flow__fields">
            <label htmlFor="omniroute-base-url">URL do gateway</label>
            <input id="omniroute-base-url" name="base-url" type="url" autoComplete="off" value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} />
            <label htmlFor="omniroute-api-key">Chave de API <span aria-hidden="true">opcional</span></label>
            <input id="omniroute-api-key" name="api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} placeholder="Deixe em branco para providers sem autenticação" />
          </div>
          <button type="button" className="button button--secondary" disabled={action.pending || sessionUnavailable} onClick={onTest}>
            {isTesting ? 'Testando conexão…' : 'Testar conexão'}
          </button>
          {connection?.connected && <p className="omniroute-step__success" aria-live="polite">Conexão pronta{connection.models === null ? '' : ` · ${connection.models} modelos disponíveis`}.</p>}
        </div>
      </li>

      <li className={`omniroute-step ${canRevoke ? 'omniroute-step--complete' : ''}`}>
        <div className="omniroute-step__number" aria-hidden="true">3</div>
        <div className="omniroute-step__content">
          <div className="omniroute-step__heading">
            <div><p>FINALIZAR</p><h3>Ative no Orin</h3></div>
            <span className="omniroute-step__state">{canRevoke ? 'Ativo' : 'Pendente'}</span>
          </div>
          <p>Salve esta conexão para disponibilizar o catálogo e permitir que seus agentes usem o gateway.</p>
          <label className="provider-panel__toggle" htmlFor="omniroute-enabled">
            <input id="omniroute-enabled" type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
            Ativar OmniRoute para os agentes
          </label>
          <div className="omniroute-flow__actions">
            <button type="submit" className="button button--primary" disabled={action.pending || sessionUnavailable}>{isSaving ? 'Salvando…' : 'Salvar e ativar'}</button>
            <button type="button" className="button button--secondary button--danger" disabled={action.pending || !canRevoke || sessionUnavailable} onClick={onRevoke}>Desativar acesso</button>
          </div>
        </div>
      </li>
    </ol>
    {sessionUnavailable && <p className="omniroute-flow__session" role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de continuar.</p>}
    {action.error && <ProviderErrorNotice error={action.error} action={action.kind} />}
  </form>
}

function OmniRouteFreeSetup() {
  return <details className="provider-panel__catalog">
    <summary>Configurar gratuitos</summary>
    <p>Opções legítimas documentadas pelo OmniRoute. Disponibilidade e limites dependem de cada provider.</p>
    <ul>
      {OMNIROUTE_FREE_PROVIDER_GUIDES.map((guide) => <li key={guide.id}>
        <strong>{guide.name}</strong> <span>{guide.access}</span>
        <p>{guide.summary}</p>
        <ol>{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
        <a href={guide.source} target="_blank" rel="noreferrer">Ver instruções oficiais</a>
        <small>Verificado em {guide.lastVerifiedAt}</small>
      </li>)}
    </ul>
  </details>
}

function describeState(load: LoadState): string {
  if (load.status === 'loading') return 'Carregando estado…'
  if (load.status === 'unavailable') return 'Estado indisponível no momento'
  const { enabled } = load.state
  if (enabled === null) return 'Não configurado'
  return enabled ? 'Habilitado' : 'Desabilitado'
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({ status: 0, code: 'request_failed', category: 'INTERNAL', messageKey: 'request_failed', retryable: false })
}

function ProviderErrorNotice({ error, action }: { error: ApiError; action: ProviderAction }) {
  return (
    <div className="provider-panel__error" role="alert">
      <span className="error-notice__icon" aria-hidden="true">!</span>
      <div>
        <strong>{errorHeadline(error, action)}</strong>
        {error.retryAfter !== null && <p>Tente novamente em {error.retryAfter}s.</p>}
        {error.correlationId && <p>Correlation ID: {error.correlationId}</p>}
      </div>
    </div>
  )
}

function errorHeadline(error: ApiError, action: ProviderAction): string {
  if (action === 'install' && (error.status === 404 || error.status === 405)) return 'A API do Orin precisa ser reiniciada para disponibilizar o instalador do OmniRoute.'
  if (action === 'install' && error.status >= 500) return 'Não foi possível instalar o OmniRoute. Confirme se Node.js e npm estão disponíveis neste computador.'
  if (isAuthenticationError(error as unknown)) return 'Sua sessão expirou. Entre novamente para salvar a chave.'
  if (isCsrfAuthorizationError(error as unknown)) return 'Atualize a página para renovar sua sessão.'
  if (error.category === 'RATE_LIMITED') return 'Muitas tentativas; aguarde antes de tentar novamente.'
  if (error.status >= 500) return 'O provider não está disponível no momento.'
  if (error.category === 'AUTHENTICATION' || error.category === 'AUTHORIZATION') return 'Sessão não autorizada para esta ação.'
  if (error.category === 'VALIDATION') return 'Não foi possível validar os dados enviados.'
  return 'Não foi possível concluir esta ação agora.'
}
