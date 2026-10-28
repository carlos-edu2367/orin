import type { FormEvent } from 'react'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import type { ProviderActionState } from './useProviderState'

export function AnthropicSetup({
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
          <p className="ollama-flow__kicker">CONEXÃO ANTHROPIC</p>
          <h3>Assinatura Claude Code (CLI Local) ou Cloud API</h3>
          <p>
            Use os limites de sessão da sua assinatura Claude (Pro/Max/Team) via <code>claude -p</code> autenticado
            localmente (<code>claude auth login</code>) compartilhando 100% do harness do Orin, ou conecte uma chave de
            API do Anthropic Console.
          </p>
        </div>
        <span className="ollama-flow__status" role="status">
          {cloud ? 'Anthropic Cloud' : 'Assinatura CLI Local'}
        </span>
      </section>
      <section className="ollama-flow__connection" aria-labelledby="anthropic-connection-title">
        <div className="ollama-flow__section-heading">
          <div>
            <p className="ollama-flow__kicker">ENDPOINT E ACESSO</p>
            <h3 id="anthropic-connection-title">Configuração de conexão</h3>
          </div>
          <span>{cloud ? 'URL + chave de API' : 'Sem chave obrigatória'}</span>
        </div>
        <div className="ollama-flow__mode-field">
          <span className="ollama-flow__field-label">Ambiente</span>
          <div className="ollama-flow__modes" role="radiogroup" aria-label="Modo da Anthropic">
            <button
              type="button"
              role="radio"
              aria-checked={!cloud}
              className={!cloud ? 'chip is-selected' : 'chip'}
              onClick={() => onModeChange('local')}
            >
              Assinatura Claude Code (CLI Local)
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={cloud}
              className={cloud ? 'chip is-selected' : 'chip'}
              onClick={() => onModeChange('cloud')}
            >
              Cloud (Chave de API)
            </button>
          </div>
        </div>
        <div className="ollama-flow__fields">
          <div className="ollama-flow__field">
            <label htmlFor="anthropic-base-url">URL do servidor</label>
            <input
              id="anthropic-base-url"
              type="url"
              value={baseUrl}
              onChange={(event) => onBaseUrlChange(event.target.value)}
            />
            <small>
              {cloud
                ? 'Endpoint oficial da API da Anthropic.'
                : 'Endereço local da bridge Claude Code CLI (iniciado automaticamente com claude -p).'}
            </small>
          </div>
          <div className="ollama-flow__field">
            <label htmlFor="anthropic-api-key">Chave de API</label>
            <input
              id="anthropic-api-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder={cloud ? 'sk-ant-...' : 'Opcional na Assinatura CLI Local (usa OAuth do claude auth login)'}
            />
            <small>
              {cloud
                ? 'A chave é write-only e nunca é reexibida depois de salva.'
                : 'Deixe em branco para usar a sessão autenticada do Claude Code CLI.'}
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
