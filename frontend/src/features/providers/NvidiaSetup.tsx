import { useState, type FormEvent } from 'react'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import type { NvidiaTestResult } from '../../api/providers'
import type { ProviderActionState } from './useProviderState'

export type NvidiaMode = 'cloud' | 'self-hosted'

export const DEFAULT_NVIDIA_CLOUD_URL = 'https://integrate.api.nvidia.com/v1'
export const DEFAULT_NVIDIA_SELF_HOSTED_URL = 'http://localhost:8000/v1'

export type NvidiaSetupProps = {
  mode?: NvidiaMode
  enabled?: boolean
  apiKey?: string
  baseUrl?: string
  action?: ProviderActionState
  canRevoke?: boolean
  bootstrap?: BrowserSessionBootstrap
  connection?: { connected: boolean; models: number | null } | null
  testResult?: NvidiaTestResult | null
  onModeChange?: (mode: NvidiaMode) => void
  onTest?: () => void
  onTestConnection?: (input: { apiKey: string; baseUrl: string }) => Promise<NvidiaTestResult | void> | NvidiaTestResult | void
  onSave?: (event: FormEvent<HTMLFormElement>) => void
  onRevoke?: () => void
  onApiKeyChange?: (value: string) => void
  onBaseUrlChange?: (value: string) => void
  onEnabledChange?: (value: boolean) => void
}

export function NvidiaSetup({
  mode: controlledMode,
  enabled: controlledEnabled,
  apiKey: controlledApiKey,
  baseUrl: controlledBaseUrl,
  action = { pending: false, error: null, kind: null },
  canRevoke = false,
  bootstrap = { status: 'ready', csrfToken: 'csrf' },
  connection = null,
  testResult: controlledTestResult = null,
  onModeChange,
  onTest,
  onTestConnection,
  onSave,
  onRevoke,
  onApiKeyChange,
  onBaseUrlChange,
  onEnabledChange,
}: NvidiaSetupProps) {
  const [internalMode, setInternalMode] = useState<NvidiaMode>(controlledMode ?? 'cloud')
  const [internalBaseUrl, setInternalBaseUrl] = useState<string>(controlledBaseUrl ?? DEFAULT_NVIDIA_CLOUD_URL)
  const [internalApiKey, setInternalApiKey] = useState<string>(controlledApiKey ?? '')
  const [internalEnabled, setInternalEnabled] = useState<boolean>(controlledEnabled ?? true)
  const [internalTestResult, setInternalTestResult] = useState<NvidiaTestResult | null>(null)
  const [testingInternal, setTestingInternal] = useState(false)

  const mode = controlledMode ?? internalMode
  const baseUrl = controlledBaseUrl ?? internalBaseUrl
  const apiKey = controlledApiKey ?? internalApiKey
  const enabled = controlledEnabled ?? internalEnabled
  const cloud = mode === 'cloud'
  const unavailable = bootstrap.status === 'missing_csrf'
  const isTesting = (action.pending && action.kind === 'test') || testingInternal
  const isSaving = action.pending && action.kind === 'configure'

  const effectiveTestResult: NvidiaTestResult | null =
    controlledTestResult ??
    internalTestResult ??
    (connection
      ? {
          reachable: connection.connected,
          authenticated: connection.connected,
          model_count: connection.models ?? 0,
        }
      : null)

  function handleModeSelect(nextMode: NvidiaMode) {
    const nextUrl = nextMode === 'cloud' ? DEFAULT_NVIDIA_CLOUD_URL : DEFAULT_NVIDIA_SELF_HOSTED_URL
    setInternalMode(nextMode)
    setInternalBaseUrl(nextUrl)
    setInternalTestResult(null)
    onModeChange?.(nextMode)
    onBaseUrlChange?.(nextUrl)
  }

  function handleBaseUrlChange(nextUrl: string) {
    setInternalBaseUrl(nextUrl)
    onBaseUrlChange?.(nextUrl)
  }

  function handleApiKeyChange(nextKey: string) {
    setInternalApiKey(nextKey)
    onApiKeyChange?.(nextKey)
  }

  function handleEnabledChange(nextEnabled: boolean) {
    setInternalEnabled(nextEnabled)
    onEnabledChange?.(nextEnabled)
  }

  async function handleTestClick() {
    onTest?.()
    if (!onTestConnection) return
    setTestingInternal(true)
    try {
      const result = await onTestConnection({ apiKey, baseUrl })
      if (result && typeof result === 'object' && 'reachable' in result) {
        setInternalTestResult(result)
      }
    } finally {
      setTestingInternal(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSave?.(event)
  }

  return (
    <form className="ollama-flow nvidia-flow" onSubmit={handleSubmit}>
      <section className="ollama-flow__overview" data-testid="nvidia-flow-overview">
        <div className="ollama-flow__overview-copy">
          <p className="ollama-flow__kicker">CONEXÃO NVIDIA NIM</p>
          <h3>NVIDIA NIM Cloud ou Self-Hosted NIM</h3>
          <p>
            Conecte ao catálogo hospedado na NVIDIA NIM Cloud ou aponte para um container NIM auto-hospedado na sua
            infraestrutura.
          </p>
        </div>
        <span className="ollama-flow__status" role="status">
          {cloud ? 'NVIDIA NIM Cloud' : 'Self-Hosted NIM'}
        </span>
      </section>

      <div className="provider-key-list__notice nvidia-flow__guidance" role="note">
        <span aria-hidden="true">↗</span>
        <p>
          <strong>Créditos gratuitos e rotação automática:</strong> gere chaves com créditos de avaliação gratuitos em{' '}
          <a href="https://build.nvidia.com" target="_blank" rel="noreferrer">
            build.nvidia.com
          </a>{' '}
          (formato <code>nvapi-...</code>). Ao cadastrar múltiplas chaves na lista abaixo, o Orin alterna
          automaticamente para a próxima chave ativa assim que uma chave ficar sem saldo ou atingir limite de
          requisições.
        </p>
      </div>

      <section
        className="ollama-flow__connection"
        aria-labelledby="nvidia-connection-title"
        data-testid="nvidia-flow-connection"
      >
        <div className="ollama-flow__section-heading">
          <div>
            <p className="ollama-flow__kicker">ENDPOINT E ACESSO</p>
            <h3 id="nvidia-connection-title">Configuração de conexão</h3>
          </div>
          <span>{cloud ? 'URL + chave nvapi-...' : 'Chave opcional'}</span>
        </div>

        <div className="ollama-flow__mode-field">
          <span className="ollama-flow__field-label">Ambiente</span>
          <div className="ollama-flow__modes" role="radiogroup" aria-label="Modo do NVIDIA NIM">
            <button
              type="button"
              role="radio"
              aria-checked={cloud}
              className={cloud ? 'chip is-selected' : 'chip'}
              disabled={action.pending || unavailable}
              onClick={() => handleModeSelect('cloud')}
            >
              NVIDIA NIM Cloud
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={!cloud}
              className={!cloud ? 'chip is-selected' : 'chip'}
              disabled={action.pending || unavailable}
              onClick={() => handleModeSelect('self-hosted')}
            >
              Self-Hosted NIM
            </button>
          </div>
        </div>

        <div className="ollama-flow__fields">
          <div className="ollama-flow__field">
            <label htmlFor="nvidia-base-url">URL do servidor</label>
            <input
              id="nvidia-base-url"
              name="base-url"
              type="url"
              autoComplete="off"
              value={baseUrl}
              onChange={(event) => handleBaseUrlChange(event.target.value)}
            />
            <small>
              {cloud
                ? 'Endpoint oficial da NVIDIA NIM Cloud (https://integrate.api.nvidia.com/v1).'
                : 'Endereço do container NVIDIA NIM auto-hospedado (ex.: http://localhost:8000/v1).'}
            </small>
          </div>

          <div className="ollama-flow__field">
            <label htmlFor="nvidia-api-key">Chave de API</label>
            <input
              id="nvidia-api-key"
              name="api-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => handleApiKeyChange(event.target.value)}
              placeholder={cloud ? 'nvapi-...' : 'Opcional para Self-Hosted NIM'}
            />
            <small>
              {cloud
                ? 'A chave é write-only e nunca é reexibida depois de salva.'
                : 'Deixe em branco se o seu container NIM local não exigir autenticação.'}
            </small>
          </div>
        </div>

        <div className="ollama-flow__footer" data-testid="nvidia-flow-footer">
          <label className="provider-panel__toggle" htmlFor="nvidia-enabled">
            <input
              id="nvidia-enabled"
              type="checkbox"
              checked={enabled}
              onChange={(event) => handleEnabledChange(event.target.checked)}
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
              disabled={action.pending || testingInternal || unavailable}
              onClick={() => void handleTestClick()}
            >
              {isTesting ? 'Testando conexão…' : 'Testar conexão'}
            </button>
            <button
              type="submit"
              className="button button--primary"
              disabled={action.pending || unavailable || (cloud && !apiKey && !canRevoke)}
            >
              {isSaving ? 'Salvando…' : 'Salvar'}
            </button>
            {onRevoke && (
              <button
                type="button"
                className="button button--secondary button--danger"
                disabled={action.pending || !canRevoke || unavailable}
                onClick={onRevoke}
              >
                Revogar acesso
              </button>
            )}
          </div>
        </div>
      </section>

      {effectiveTestResult && (
        <p className="omniroute-step__success" role="status" aria-live="polite">
          {effectiveTestResult.reachable ? 'Endpoint acessível' : 'Endpoint inacessível'}
          {' · '}
          {effectiveTestResult.authenticated ? 'Autenticado' : 'Não autenticado'}
          {' · '}
          {effectiveTestResult.model_count}{' '}
          {effectiveTestResult.model_count === 1 ? 'modelo disponível' : 'modelos disponíveis'}.
        </p>
      )}
    </form>
  )
}
