import type { FormEvent } from 'react'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import type { ProviderActionState } from './useProviderState'

export function OpenCodeSetup({
  mode,
  enabled,
  apiKey,
  baseUrl,
  action,
  canRevoke,
  bootstrap,
  connection,
  onModeChange,
  onTest,
  onSave,
  onRevoke,
  onApiKeyChange,
  onBaseUrlChange,
  onEnabledChange,
}: {
  mode: 'local' | 'cloud'
  enabled: boolean
  apiKey: string
  baseUrl: string
  action: ProviderActionState
  canRevoke: boolean
  bootstrap: BrowserSessionBootstrap
  connection: { connected: boolean; models: number | null } | null
  onModeChange: (mode: 'local' | 'cloud') => void
  onTest: () => void
  onSave: (event: FormEvent<HTMLFormElement>) => void
  onRevoke: () => void
  onApiKeyChange: (value: string) => void
  onBaseUrlChange: (value: string) => void
  onEnabledChange: (value: boolean) => void
}) {
  const cloud = mode === 'cloud'
  const unavailable = bootstrap.status === 'missing_csrf'
  return (
    <form className="ollama-flow" onSubmit={onSave}>
      <section className="ollama-flow__overview">
        <div className="ollama-flow__overview-copy">
          <p className="ollama-flow__kicker">CONEXÃO OPENCODE</p>
          <h3>Bridge Local (Grátis) ou OpenCode Zen</h3>
          <p>
            Dirija o daemon oficial <code>opencode serve</code> localmente para usar modelos gratuitos (como{' '}
            <code>big-pickle</code> e <code>mimo-v2.6-flash-free</code>) compartilhando o harness completo do Orin, ou
            conecte sua conta OpenCode Zen.
          </p>
        </div>
        <span className="ollama-flow__status" role="status">
          {cloud ? 'OpenCode Zen' : 'Bridge local'}
        </span>
      </section>
      <section className="ollama-flow__connection" aria-labelledby="opencode-connection-title">
        <div className="ollama-flow__section-heading">
          <div>
            <p className="ollama-flow__kicker">ENDPOINT E ACESSO</p>
            <h3 id="opencode-connection-title">Configuração de conexão</h3>
          </div>
          <span>{cloud ? 'URL + chave de API' : 'Sem chave obrigatória'}</span>
        </div>
        <div className="ollama-flow__mode-field">
          <span className="ollama-flow__field-label">Ambiente</span>
          <div className="ollama-flow__modes" role="radiogroup" aria-label="Modo do OpenCode">
            <button
              type="button"
              role="radio"
              aria-checked={!cloud}
              className={!cloud ? 'chip is-selected' : 'chip'}
              onClick={() => onModeChange('local')}
            >
              Bridge Local (Grátis)
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={cloud}
              className={cloud ? 'chip is-selected' : 'chip'}
              onClick={() => onModeChange('cloud')}
            >
              Cloud (Zen)
            </button>
          </div>
        </div>
        <div className="ollama-flow__fields">
          <div className="ollama-flow__field">
            <label htmlFor="opencode-base-url">URL do servidor</label>
            <input
              id="opencode-base-url"
              type="url"
              value={baseUrl}
              onChange={(event) => onBaseUrlChange(event.target.value)}
            />
            <small>
              {cloud
                ? 'Endpoint oficial do OpenCode Zen.'
                : 'Endereço local do opencode serve (iniciado automaticamente se instalado).'}
            </small>
          </div>
          <div className="ollama-flow__field">
            <label htmlFor="opencode-api-key">Chave de API</label>
            <input
              id="opencode-api-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder={cloud ? 'Inserir uma nova chave' : 'Opcional na Bridge Local'}
            />
            <small>
              {cloud
                ? 'A chave é write-only e nunca é reexibida depois de salva.'
                : 'Deixe em branco para usar os modelos gratuitos da Bridge Local.'}
            </small>
          </div>
        </div>
        <div className="ollama-flow__footer">
          <label className="provider-panel__toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => onEnabledChange(event.target.checked)}
            />
            <span>
              <strong>Disponibilizar para os agentes</strong>
              <small>Permite selecionar este provider em novas conversas.</small>
            </span>
          </label>
          <div className="ollama-flow__actions">
            <button
              type="button"
              className="button button--secondary"
              disabled={action.pending || unavailable}
              onClick={onTest}
            >
              {action.kind === 'test' ? 'Testando conexão…' : 'Testar conexão'}
            </button>
            <button
              type="submit"
              className="button button--primary"
              disabled={action.pending || unavailable || (cloud && !apiKey)}
            >
              {action.kind === 'configure' ? 'Salvando…' : 'Salvar'}
            </button>
            <button
              type="button"
              className="button button--secondary button--danger"
              disabled={action.pending || !canRevoke || unavailable}
              onClick={onRevoke}
            >
              Revogar acesso
            </button>
          </div>
        </div>
      </section>
      {connection?.connected && (
        <p className="omniroute-step__success" role="status">
          Conexão pronta{connection.models === null ? '' : ` · ${connection.models} modelos disponíveis`}.
        </p>
      )}
    </form>
  )
}
