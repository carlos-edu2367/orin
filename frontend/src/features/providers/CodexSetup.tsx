import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { getCodexCliStatus, type CodexCliStatus } from '../../api/providers'
import type { ProviderActionState } from './useProviderState'

export type CodexSetupProps = {
  client?: ApiClient
  enabled?: boolean
  action?: ProviderActionState
  canRevoke?: boolean
  bootstrap?: BrowserSessionBootstrap
  initialStatus?: CodexCliStatus
  onSave?: (event: FormEvent<HTMLFormElement>) => void
  onRevoke?: () => void
  onEnabledChange?: (value: boolean) => void
}

const DEFAULT_ACTION: ProviderActionState = {
  pending: false,
  error: null,
  kind: null,
}

export function CodexSetup({
  client: providedClient,
  enabled: providedEnabled,
  action = DEFAULT_ACTION,
  canRevoke = false,
  bootstrap: providedBootstrap,
  initialStatus,
  onSave,
  onRevoke,
  onEnabledChange,
}: CodexSetupProps) {
  const client = useMemo(
    () => providedClient ?? createBrowserApiClient(),
    [providedClient],
  )
  const bootstrap: BrowserSessionBootstrap = useMemo(() => {
    if (providedBootstrap) return providedBootstrap
    return typeof document === 'undefined'
      ? { status: 'missing_csrf' as const }
      : readBrowserSessionBootstrap(document)
  }, [providedBootstrap])

  const [internalEnabled, setInternalEnabled] = useState(true)
  const enabled = providedEnabled ?? internalEnabled
  const [status, setStatus] = useState<CodexCliStatus | null>(initialStatus ?? null)
  const [checking, setChecking] = useState<boolean>(initialStatus === undefined)
  const [checkError, setCheckError] = useState<string | null>(null)
  const activeAbortRef = useRef<AbortController | null>(null)

  const checkStatus = useCallback(async () => {
    activeAbortRef.current?.abort()
    const controller = new AbortController()
    activeAbortRef.current = controller
    setChecking(true)
    setCheckError(null)
    try {
      const next = await getCodexCliStatus(client, controller.signal)
      if (controller.signal.aborted) return
      setStatus(next)
    } catch (error) {
      if (
        controller.signal.aborted ||
        (error instanceof DOMException && error.name === 'AbortError')
      ) {
        return
      }
      setCheckError('Não foi possível verificar o status do Codex CLI local.')
    } finally {
      if (!controller.signal.aborted) {
        setChecking(false)
      }
    }
  }, [client])

  useEffect(() => {
    if (initialStatus !== undefined) return
    const controller = new AbortController()
    activeAbortRef.current = controller
    getCodexCliStatus(client, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return
        setStatus(next)
        setCheckError(null)
      })
      .catch((error: unknown) => {
        if (
          controller.signal.aborted ||
          (error instanceof DOMException && error.name === 'AbortError')
        ) {
          return
        }
        setCheckError('Não foi possível verificar o status do Codex CLI local.')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setChecking(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [client, initialStatus])

  const unavailable = bootstrap.status === 'missing_csrf'
  const isReady = Boolean(
    status && status.installed && status.supported && status.authenticated,
  )
  const isSaving = action.pending && action.kind === 'configure'

  function handleEnabledChange(value: boolean) {
    if (providedEnabled === undefined) {
      setInternalEnabled(value)
    }
    onEnabledChange?.(value)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    if (onSave) {
      onSave(event)
      return
    }
    event.preventDefault()
  }

  function statusBadgeLabel(): string {
    if (checking && !status) return 'Verificando…'
    if (checkError && !status) return 'Indisponível'
    if (!status || !status.installed) return 'CLI não encontrado'
    if (!status.supported) return 'Atualização necessária'
    if (!status.authenticated) return 'Login pendente'
    return 'Pronto'
  }

  return (
    <form className="ollama-flow codex-setup" onSubmit={handleSubmit}>
      <section className="ollama-flow__overview">
        <div className="ollama-flow__overview-copy">
          <p className="ollama-flow__kicker">ASSINATURA CHATGPT / CODEX</p>
          <h3>OpenAI Codex (Assinatura)</h3>
          <p>
            Usa sua assinatura ChatGPT/Codex via o Codex CLI oficial instalado nesta máquina. Sujeito aos limites do seu plano ChatGPT.
          </p>
        </div>
        <span className="ollama-flow__status" role="status">
          {statusBadgeLabel()}
        </span>
      </section>

      <section className="ollama-flow__connection" aria-labelledby="codex-connection-title">
        <div className="ollama-flow__section-heading">
          <div>
            <p className="ollama-flow__kicker">DIAGNÓSTICO LOCAL</p>
            <h3 id="codex-connection-title">Status do Codex CLI</h3>
          </div>
          <span>Sem chave de API</span>
        </div>

        <div className="ollama-flow__fields">
          {checkError && (
            <div className="provider-panel__error" role="alert">
              <strong>{checkError}</strong>
            </div>
          )}

          {status && !status.installed && (
            <div className="codex-setup__notice" role="status">
              <strong>Instale o Codex CLI</strong>
              <p>
                Execute <code>npm install -g @openai/codex</code> (ou use o aplicativo do Codex) na versão mínima &gt;= 0.167.0.
              </p>
            </div>
          )}

          {status && status.installed && !status.supported && (
            <div className="codex-setup__notice" role="status">
              <strong>Versão incompatível detectada ({status.version ?? 'desconhecida'})</strong>
              <p>
                É necessário atualizar o Codex CLI para a versão &gt;= 0.167.0 (ex.: <code>npm update -g @openai/codex</code>) para usar o isolamento seguro do Orin.
              </p>
            </div>
          )}

          {status && status.installed && !status.authenticated && (
            <div className="codex-setup__notice" role="status">
              <strong>Autenticação pendente no Codex CLI</strong>
              <p>
                Execute <code>{status.login_command || 'codex login'}</code> no seu terminal para entrar com sua conta ChatGPT e clique em &ldquo;Verificar novamente&rdquo;.
              </p>
            </div>
          )}

          {isReady && status && (
            <div className="omniroute-step__success" role="status">
              Codex CLI <code>v{status.version}</code> detectado e autenticado.
            </div>
          )}
        </div>

        <div className="ollama-flow__footer">
          <label className="provider-panel__toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => handleEnabledChange(event.target.checked)}
            />
            <span>
              <strong>Disponibilizar para os agentes</strong>
              <small>Permite selecionar os modelos da sua assinatura Codex nas conversas.</small>
            </span>
          </label>

          <div className="ollama-flow__actions">
            <button
              type="button"
              className="button button--secondary"
              disabled={checking || action.pending}
              onClick={() => void checkStatus()}
            >
              {checking ? 'Verificando…' : 'Verificar novamente'}
            </button>
            <button
              type="submit"
              className="button button--primary"
              disabled={action.pending || checking || unavailable || !isReady}
            >
              {isSaving ? 'Salvando…' : 'Salvar e ativar'}
            </button>
            <button
              type="button"
              className="button button--secondary button--danger"
              disabled={action.pending || !canRevoke || unavailable}
              onClick={onRevoke}
            >
              Desativar
            </button>
          </div>
        </div>
      </section>
    </form>
  )
}
