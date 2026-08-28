import { useState, type CSSProperties, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { isAuthenticationError, isCsrfAuthorizationError } from '../../api/errors'
import { setProviderKeyCooldownSeconds, type ProviderName } from '../../api/providers'
import { SettingsDrawer } from '../settings/SettingsDrawer'
import { providerBrand } from './providerBrand'
import { OllamaSetup } from './OllamaSetup'
import { OmniRouteSetup } from './OmniRouteSetup'
import { ProviderKeyList } from './ProviderKeyList'
import { useProviderKeysState } from './useProviderKeysState'
import { useProviderState } from './useProviderState'

export function ProviderDetail({ provider, client, bootstrap, onClose }: { provider: ProviderName; client: ApiClient; bootstrap?: BrowserSessionBootstrap; onClose: () => void }) {
  const session = bootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))
  const state = useProviderState(client, provider, session)
  const keysState = useProviderKeysState(client, provider, session)
  const [cooldownSeconds, setCooldownSeconds] = useState(60)
  const [cooldownError, setCooldownError] = useState(false)
  // Adopt the saved cooldown during render rather than from an effect: an
  // effect repaints the control once with the default before correcting it.
  const loadedCooldown = state.load.status === 'loaded' ? state.load.state.extra.key_cooldown_seconds : undefined
  const [seenCooldown, setSeenCooldown] = useState(loadedCooldown)
  if (loadedCooldown !== seenCooldown) {
    setSeenCooldown(loadedCooldown)
    if (typeof loadedCooldown === 'number') setCooldownSeconds(loadedCooldown)
  }

  async function saveCooldownSeconds(seconds: number) {
    if (session.status === 'missing_csrf') return
    setCooldownError(false)
    try {
      // Only reflects the new value once the server confirms it: setting
      // this before the request settles left the field showing a value the
      // server never actually received whenever the save failed.
      await setProviderKeyCooldownSeconds(client, provider, seconds)
      setCooldownSeconds(seconds)
    } catch {
      setCooldownError(true)
    }
  }
  const brand = providerBrand(provider)
  const [omniOpen, setOmniOpen] = useState(false)
  const [manualModelId, setManualModelId] = useState('')
  const title = brand.label
  function onRevoke() {
    if (window.confirm(`Revogar o acesso de ${brand.label}?`)) void state.revoke()
  }
  async function addManualModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const modelId = manualModelId.trim()
    if (!modelId) return
    if (await state.addCustomModel(modelId)) setManualModelId('')
  }
  return <SettingsDrawer title={title} onClose={onClose}>
    <div className="provider-detail__identity"><span className="provider-card__mark" style={{ '--card-accent': brand.accent } as CSSProperties} aria-hidden="true" dangerouslySetInnerHTML={{ __html: brand.mark }} /><div><p className="eyebrow">PROVIDER / CONEXÃO</p><p className="provider-detail__state" role="status">{describeState(state.load)}</p></div></div>
    {provider === 'omniroute' && <OmniRouteSetup open={omniOpen} installed={state.installed} enabled={state.enabled} apiKey={state.apiKey} baseUrl={state.baseUrl} action={state.action} canRevoke={state.canRevoke} bootstrap={session} connection={state.connection} onOpen={() => setOmniOpen(true)} onInstall={() => void state.install()} onTest={() => void state.testConnection()} onSave={(event) => void state.configure(event)} onRevoke={onRevoke} onApiKeyChange={state.setApiKey} onBaseUrlChange={state.setBaseUrl} onEnabledChange={state.setEnabled} />}
    {provider === 'ollama' && <OllamaSetup mode={state.ollamaMode} enabled={state.enabled} apiKey={state.apiKey} baseUrl={state.baseUrl} action={state.action} canRevoke={state.canRevoke} bootstrap={session} connection={state.connection} onModeChange={(mode) => { state.setOllamaMode(mode); state.setBaseUrl(mode === 'cloud' ? 'https://ollama.com' : 'http://localhost:11434'); state.setApiKey('') }} onTest={() => void state.testConnection()} onSave={(event) => void state.configure(event)} onRevoke={onRevoke} onApiKeyChange={state.setApiKey} onBaseUrlChange={state.setBaseUrl} onEnabledChange={state.setEnabled} />}
    {provider !== 'omniroute' && provider !== 'ollama' && <form className="provider-panel__form" onSubmit={(event) => void state.configure(event)}><div className="provider-form__intro"><p className="eyebrow">CREDENCIAL DE ACESSO</p><p>A chave é write-only e nunca é reexibida depois de salva.</p></div><div className="provider-panel__field"><label htmlFor={`${provider}-detail-api-key`}>Chave de API</label><input id={`${provider}-detail-api-key`} type="password" autoComplete="off" value={state.apiKey} onChange={(event) => state.setApiKey(event.target.value)} placeholder="Inserir uma nova chave" /></div><label className="provider-panel__toggle"><input type="checkbox" checked={state.enabled} onChange={(event) => state.setEnabled(event.target.checked)} /><span><strong>Disponibilizar para os agentes</strong><small>Permite usar este provider em novas conversas.</small></span></label><div className="provider-panel__actions"><button type="submit" className="button button--primary" disabled={state.action.pending || !state.apiKey || session.status === 'missing_csrf'}>Salvar</button><button type="button" className="button button--secondary button--danger" disabled={state.action.pending || !state.canRevoke || session.status === 'missing_csrf'} onClick={onRevoke}>Revogar acesso</button><button type="button" className="button button--secondary" disabled={state.action.pending || !state.canRevoke || session.status === 'missing_csrf'} onClick={() => void state.refreshModels()}>Atualizar catálogo</button></div></form>}
    <ProviderKeyList
      keys={keysState.keys}
      pending={keysState.action.pending}
      cooldownSeconds={cooldownSeconds}
      onAdd={(apiKey, label) => keysState.add(apiKey, label)}
      onRename={(keyId, label) => void keysState.rename(keyId, label)}
      onRemove={(keyId) => void keysState.remove(keyId)}
      onMoveUp={(keyId) => void keysState.moveUp(keyId)}
      onMoveDown={(keyId) => void keysState.moveDown(keyId)}
      onCooldownSecondsChange={(seconds) => void saveCooldownSeconds(seconds)}
    />
    {cooldownError && <p role="alert">Não foi possível salvar o tempo de cooldown.</p>}
    {state.canRevoke && <section className="provider-panel__catalog" aria-label={`Modelos de ${title}`}><div className="provider-panel__section-heading"><div><p className="eyebrow">CATÁLOGO</p><h3>Modelos disponíveis</h3></div><span>Atualizado sob demanda</span></div><div className="provider-panel__catalog-actions">{(provider === 'omniroute' || provider === 'ollama') && <button type="button" className="button button--secondary" disabled={state.action.pending || session.status === 'missing_csrf'} onClick={() => void state.refreshModels()}>{state.action.pending && state.action.kind === 'refresh' ? 'Atualizando…' : 'Atualizar catálogo'}</button>}<button type="button" className="button button--secondary" onClick={() => void state.loadModels()} disabled={state.catalogLoading}>{state.catalogLoading ? 'Carregando modelos' : 'Ver modelos autorizados'}</button></div><form className="provider-panel__custom-model" onSubmit={(event) => void addManualModel(event)}><label htmlFor={`${provider}-custom-model`}>Adicionar modelo manualmente<input id={`${provider}-custom-model`} value={manualModelId} onChange={(event) => setManualModelId(event.target.value)} placeholder="Ex.: deepseek-v4-flash" /></label><button type="submit" className="button button--secondary" disabled={state.action.pending || !manualModelId.trim() || session.status === 'missing_csrf'}>Adicionar modelo</button></form>{state.models.length > 0 && <ul>{state.models.map((model) => <li key={model.model_id}><div><strong>{model.display_name}</strong>{model.is_custom && <span className="provider-panel__model-badge">Manual</span>}<code>{model.model_id}</code></div><div className="provider-panel__model-actions"><button type="button" className="button button--secondary" onClick={() => void state.setFavorite(model)} disabled={state.action.pending || session.status === 'missing_csrf'}>{model.is_favorite ? 'Remover favorito' : 'Favoritar'}</button>{model.is_custom && <button type="button" className="button button--secondary button--danger" onClick={() => { if (window.confirm(`Remover o modelo ${model.model_id}?`)) void state.removeCustomModel(model.model_id) }} disabled={state.action.pending || session.status === 'missing_csrf'}>Remover modelo</button>}</div></li>)}</ul>}{state.catalog.error && <p role="alert">Não foi possível carregar o catálogo.</p>}{state.catalog.loaded && !state.catalog.error && state.models.length === 0 && <p role="status">Nenhum modelo no catálogo. Use "Atualizar catálogo" para buscá-los do provider.</p>}</section>}
    {state.action.error && <div className="provider-panel__error" role="alert"><div><strong>Não foi possível concluir a ação</strong><p>{providerErrorMessage(state.action.error)}{state.action.error.retryAfter !== null && ` Tente novamente em ${state.action.error.retryAfter}s.`}</p></div></div>}
  </SettingsDrawer>
}

function providerErrorMessage(error: { status: number; category: string; messageKey: string }): string {
  if (isAuthenticationError(error)) return 'Sua sessão expirou. Entre novamente para salvar a chave.'
  if (isCsrfAuthorizationError(error)) return 'Atualize a página para renovar sua sessão.'
  if (error.category === 'RATE_LIMITED') return 'Muitas tentativas; aguarde antes de tentar novamente.'
  if (error.status >= 500) return 'O provider não está disponível no momento.'
  return 'Não foi possível concluir esta ação agora.'
}

function describeState(load: ReturnType<typeof useProviderState>['load']): string {
  if (load.status === 'loading') return 'Carregando estado…'
  if (load.status === 'unavailable') return 'Estado indisponível no momento'
  if (load.state.enabled === null) return 'Não configurado'
  return load.state.enabled ? 'Habilitado' : 'Desabilitado'
}
