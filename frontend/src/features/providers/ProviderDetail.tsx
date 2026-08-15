import { useState, type CSSProperties } from 'react'
import type { ApiClient } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import type { ProviderName } from '../../api/providers'
import { SettingsDrawer } from '../settings/SettingsDrawer'
import { providerBrand } from './providerBrand'
import { OllamaSetup } from './OllamaSetup'
import { OmniRouteSetup } from './OmniRouteSetup'
import { useProviderState } from './useProviderState'

export function ProviderDetail({ provider, client, bootstrap, onClose }: { provider: ProviderName; client: ApiClient; bootstrap?: BrowserSessionBootstrap; onClose: () => void }) {
  const session = bootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))
  const state = useProviderState(client, provider, session)
  const brand = providerBrand(provider)
  const [omniOpen, setOmniOpen] = useState(false)
  const title = brand.label
  function onRevoke() {
    if (window.confirm(`Revogar o acesso de ${brand.label}?`)) void state.revoke()
  }
  return <SettingsDrawer title={title} onClose={onClose}>
    <div className="provider-detail__identity"><span className="provider-card__mark" style={{ '--card-accent': brand.accent } as CSSProperties} aria-hidden="true" dangerouslySetInnerHTML={{ __html: brand.mark }} /><div><p className="eyebrow">PROVIDER / CONEXÃO</p><p className="provider-detail__state" role="status">{describeState(state.load)}</p></div></div>
    {provider === 'omniroute' && <OmniRouteSetup open={omniOpen} installed={state.installed} enabled={state.enabled} apiKey={state.apiKey} baseUrl={state.baseUrl} action={state.action} canRevoke={state.canRevoke} bootstrap={session} connection={state.connection} onOpen={() => setOmniOpen(true)} onInstall={() => void state.install()} onTest={() => void state.testConnection()} onSave={(event) => void state.configure(event)} onRevoke={onRevoke} onApiKeyChange={state.setApiKey} onBaseUrlChange={state.setBaseUrl} onEnabledChange={state.setEnabled} />}
    {provider === 'ollama' && <OllamaSetup mode={state.ollamaMode} enabled={state.enabled} apiKey={state.apiKey} baseUrl={state.baseUrl} action={state.action} canRevoke={state.canRevoke} bootstrap={session} connection={state.connection} onModeChange={(mode) => { state.setOllamaMode(mode); state.setBaseUrl(mode === 'cloud' ? 'https://ollama.com' : 'http://localhost:11434'); state.setApiKey('') }} onTest={() => void state.testConnection()} onSave={(event) => void state.configure(event)} onRevoke={onRevoke} onApiKeyChange={state.setApiKey} onBaseUrlChange={state.setBaseUrl} onEnabledChange={state.setEnabled} />}
    {provider !== 'omniroute' && provider !== 'ollama' && <form className="provider-panel__form" onSubmit={(event) => void state.configure(event)}><label htmlFor={`${provider}-detail-api-key`}>Chave de API</label><input id={`${provider}-detail-api-key`} type="password" autoComplete="off" value={state.apiKey} onChange={(event) => state.setApiKey(event.target.value)} placeholder="Inserir uma nova chave" /><label className="provider-panel__toggle"><input type="checkbox" checked={state.enabled} onChange={(event) => state.setEnabled(event.target.checked)} />Habilitado</label><div className="provider-panel__actions"><button type="submit" className="button button--primary" disabled={state.action.pending || !state.apiKey || session.status === 'missing_csrf'}>Salvar</button><button type="button" className="button button--secondary button--danger" disabled={state.action.pending || !state.canRevoke || session.status === 'missing_csrf'} onClick={onRevoke}>Revogar acesso</button><button type="button" className="button button--secondary" disabled={state.action.pending || !state.canRevoke || session.status === 'missing_csrf'} onClick={() => void state.refreshModels()}>Atualizar catálogo</button></div></form>}
    {state.canRevoke && <section className="provider-panel__catalog" aria-label={`Modelos de ${title}`}><button type="button" className="button button--secondary" onClick={() => void state.loadModels()} disabled={state.catalogLoading}>{state.catalogLoading ? 'Carregando modelos' : 'Ver modelos autorizados'}</button>{state.models.length > 0 && <ul>{state.models.map((model) => <li key={model.model_id}><span>{model.display_name} <code>{model.model_id}</code></span><button type="button" className="button button--secondary" onClick={() => void state.setFavorite(model)} disabled={state.action.pending || session.status === 'missing_csrf'}>{model.is_favorite ? 'Remover favorito' : 'Favoritar'}</button></li>)}</ul>}{state.catalog.error && <p role="alert">Não foi possível carregar o catálogo.</p>}{state.catalog.loaded && !state.catalog.error && state.models.length === 0 && <p role="status">Nenhum modelo no catálogo. Use "Atualizar catálogo" para buscá-los do provider.</p>}</section>}
    {state.action.error && <p role="alert">Não foi possível concluir esta ação agora.</p>}
  </SettingsDrawer>
}

function describeState(load: ReturnType<typeof useProviderState>['load']): string {
  if (load.status === 'loading') return 'Carregando estado…'
  if (load.status === 'unavailable') return 'Estado indisponível no momento'
  if (load.state.enabled === null) return 'Não configurado'
  return load.state.enabled ? 'Habilitado' : 'Desabilitado'
}
