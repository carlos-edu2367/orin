import type { FormEvent } from 'react'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import type { ProviderActionState } from './useProviderState'

export function OmniRouteSetup({ open, installed, apiKey, baseUrl, enabled, action, canRevoke, bootstrap, connection, onOpen, onInstall, onTest, onSave, onRevoke, onApiKeyChange, onBaseUrlChange, onEnabledChange }: {
  open: boolean; installed: boolean; apiKey: string; baseUrl: string; enabled: boolean; action: ProviderActionState; canRevoke: boolean; bootstrap: BrowserSessionBootstrap
  connection: { connected: boolean; models: number | null } | null; onOpen: () => void; onInstall: () => void; onTest: () => void; onSave: (event: FormEvent<HTMLFormElement>) => void; onRevoke: () => void; onApiKeyChange: (value: string) => void; onBaseUrlChange: (value: string) => void; onEnabledChange: (value: boolean) => void
}) {
  if (!open) return <div className="omniroute-flow__intro"><p className="omniroute-flow__kicker">GATEWAY LOCAL</p><p>Instale o gateway local uma vez, conecte sua conta e mantenha modelos, fallback e roteamento em um único lugar.</p><button type="button" className="button button--primary" onClick={onOpen}>Configurar OmniRoute</button></div>
  const unavailable = bootstrap.status === 'missing_csrf'
  return <form className="omniroute-flow" onSubmit={onSave}>
    <div className="omniroute-flow__fields"><label htmlFor="omniroute-base-url">URL do gateway</label><input id="omniroute-base-url" type="url" value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} /><label htmlFor="omniroute-api-key">Chave de API <span aria-hidden="true">opcional</span></label><input id="omniroute-api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} /></div>
    <div className="omniroute-flow__actions"><button type="button" className="button button--secondary" disabled={action.pending || unavailable} onClick={onInstall}>{installed ? 'OmniRoute instalado' : action.kind === 'install' ? 'Instalando…' : 'Instalar OmniRoute'}</button><button type="button" className="button button--secondary" disabled={action.pending || unavailable} onClick={onTest}>{action.kind === 'test' ? 'Testando conexão…' : 'Testar conexão'}</button><label className="provider-panel__toggle"><input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />Ativar OmniRoute</label><button type="submit" className="button button--primary" disabled={action.pending || unavailable}>{action.kind === 'configure' ? 'Salvando…' : 'Salvar e ativar'}</button><button type="button" className="button button--secondary button--danger" disabled={action.pending || !canRevoke || unavailable} onClick={onRevoke}>Desativar acesso</button></div>
    {connection?.connected && <p className="omniroute-step__success" role="status">Conexão pronta{connection.models === null ? '' : ` · ${connection.models} modelos disponíveis`}.</p>}
    {unavailable && <p role="status">Não foi possível confirmar sua sessão segura. Atualize a página antes de continuar.</p>}
  </form>
}
