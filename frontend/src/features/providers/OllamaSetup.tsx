import type { FormEvent } from 'react'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import type { ProviderActionState } from './useProviderState'

export function OllamaSetup({ mode, enabled, apiKey, baseUrl, action, canRevoke, bootstrap, connection, onModeChange, onTest, onSave, onRevoke, onApiKeyChange, onBaseUrlChange, onEnabledChange }: {
  mode: 'local' | 'cloud'; enabled: boolean; apiKey: string; baseUrl: string; action: ProviderActionState; canRevoke: boolean; bootstrap: BrowserSessionBootstrap; connection: { connected: boolean; models: number | null } | null
  onModeChange: (mode: 'local' | 'cloud') => void; onTest: () => void; onSave: (event: FormEvent<HTMLFormElement>) => void; onRevoke: () => void; onApiKeyChange: (value: string) => void; onBaseUrlChange: (value: string) => void; onEnabledChange: (value: boolean) => void
}) {
  const cloud = mode === 'cloud'
  const unavailable = bootstrap.status === 'missing_csrf'
  return <form className="ollama-flow" onSubmit={onSave}>
    <div className="ollama-flow__modes" role="radiogroup" aria-label="Modo do Ollama"><button type="button" role="radio" aria-checked={!cloud} className={!cloud ? 'chip is-selected' : 'chip'} onClick={() => onModeChange('local')}>Local</button><button type="button" role="radio" aria-checked={cloud} className={cloud ? 'chip is-selected' : 'chip'} onClick={() => onModeChange('cloud')}>Cloud</button></div>
    <div className="ollama-flow__fields"><label htmlFor="ollama-base-url">URL do servidor</label><input id="ollama-base-url" type="url" value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} />{cloud && <><label htmlFor="ollama-api-key">Chave de API</label><input id="ollama-api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} /></>}</div>
    <div className="ollama-flow__actions"><label className="provider-panel__toggle"><input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />Disponibilizar para os agentes</label><button type="button" className="button button--secondary" disabled={action.pending || unavailable} onClick={onTest}>{action.kind === 'test' ? 'Testando conexão…' : 'Testar conexão'}</button><button type="submit" className="button button--primary" disabled={action.pending || unavailable || (cloud && !apiKey)}>{action.kind === 'configure' ? 'Salvando…' : 'Salvar e ativar'}</button><button type="button" className="button button--secondary button--danger" disabled={action.pending || !canRevoke || unavailable} onClick={onRevoke}>Desativar acesso</button></div>
    {connection?.connected && <p className="omniroute-step__success" role="status">Conexão pronta{connection.models === null ? '' : ` · ${connection.models} modelos disponíveis`}.</p>}
  </form>
}
