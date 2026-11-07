import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../../api/browserSession'
import { getAntigravityCliStatus, type AntigravityCliStatus } from '../../api/providers'
import type { ProviderActionState } from './useProviderState'

export type AntigravitySetupProps = {
  client?: ApiClient
  enabled?: boolean
  riskAcknowledged?: boolean
  action?: ProviderActionState
  canRevoke?: boolean
  bootstrap?: BrowserSessionBootstrap
  initialStatus?: AntigravityCliStatus
  onSave?: (event: FormEvent<HTMLFormElement>) => void
  onRevoke?: () => void
  onEnabledChange?: (value: boolean) => void
  onRiskAcknowledgedChange?: (value: boolean) => void
}

const DEFAULT_ACTION: ProviderActionState = {
  pending: false,
  error: null,
  kind: null,
}

export function AntigravitySetup({
  client: providedClient,
  enabled: providedEnabled,
  riskAcknowledged: providedRiskAcknowledged,
  action = DEFAULT_ACTION,
  canRevoke = false,
  bootstrap: providedBootstrap,
  initialStatus,
  onSave,
  onRevoke,
  onEnabledChange,
  onRiskAcknowledgedChange,
}: AntigravitySetupProps) {
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
  const [internalRiskAcknowledged, setInternalRiskAcknowledged] = useState(false)
  const riskAcknowledged = providedRiskAcknowledged ?? internalRiskAcknowledged
  const [status, setStatus] = useState<AntigravityCliStatus | null>(initialStatus ?? null)
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
      const next = await getAntigravityCliStatus(client, controller.signal)
      if (controller.signal.aborted) return
      setStatus(next)
    } catch (error) {
      if (
        controller.signal.aborted ||
        (error instanceof DOMException && error.name === 'AbortError')
      ) {
        return
      }
      setCheckError('Não foi possível verificar o status do Antigravity CLI local.')
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
    getAntigravityCliStatus(client, controller.signal)
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
        setCheckError('Não foi possível verificar o status do Antigravity CLI local.')
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

  function handleRiskAcknowledgedChange(value: boolean) {
    if (providedRiskAcknowledged === undefined) {
      setInternalRiskAcknowledged(value)
    }
    onRiskAcknowledgedChange?.(value)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    if (action.pending || checking || unavailable || !isReady || !riskAcknowledged) {
      event.preventDefault()
      return
    }
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
    <form className="ollama-flow antigravity-setup" onSubmit={handleSubmit}>
      <section className="ollama-flow__overview">
        <div className="ollama-flow__overview-copy">
          <p className="ollama-flow__kicker">ASSINATURA GOOGLE / ANTIGRAVITY</p>
          <h3>Google Antigravity (Assinatura)</h3>
          <p>
            Usa o Antigravity CLI oficial (<code>agy</code>) instalado nesta máquina, sem ler nem copiar credenciais da sua conta Google. Este provedor vem desligado por padrão.
          </p>
        </div>
        <span className="ollama-flow__status" role="status">
          {statusBadgeLabel()}
        </span>
      </section>

      <section className="ollama-flow__connection" aria-labelledby="antigravity-connection-title">
        <div className="ollama-flow__section-heading">
          <div>
            <p className="ollama-flow__kicker">DIAGNÓSTICO LOCAL</p>
            <h3 id="antigravity-connection-title">Status do Antigravity CLI</h3>
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
              <strong>Instale o Antigravity CLI</strong>
              <p>
                Baixe o pacote oficial (<code>agy</code>) na versão mínima &gt;= 1.1.1 e verifique novamente.
              </p>
            </div>
          )}

          {status && status.installed && !status.supported && (
            <div className="codex-setup__notice" role="status">
              <strong>Versão incompatível detectada ({status.version ?? 'desconhecida'})</strong>
              <p>
                É necessário atualizar o Antigravity CLI (<code>agy</code>) para a versão &gt;= 1.1.1 para usar o isolamento seguro de ferramentas do Orin.
              </p>
            </div>
          )}

          {status && status.installed && !status.authenticated && (
            <div className="codex-setup__notice" role="status">
              <strong>Autenticação pendente no Antigravity CLI</strong>
              <p>
                Abra <code>{status.login_command || 'agy'}</code> uma vez no terminal e entre com sua conta Google; depois clique em &ldquo;Verificar novamente&rdquo;.
              </p>
            </div>
          )}

          {isReady && status && (
            <div className="omniroute-step__success" role="status">
              Antigravity CLI <code>v{status.version}</code> detectado e autenticado.
            </div>
          )}

          <div
            className="antigravity-setup__risk-warning"
            role="note"
            aria-label="Aviso de Termos de Uso do Google Antigravity"
          >
            <strong>Aviso importante sobre os Termos de Uso da Google</strong>
            <p>
              O FAQ oficial do Google Antigravity estabelece expressamente:
            </p>
            <blockquote>
              <p>
                &ldquo;Wrapping agy inside third-party coding agents or applications, or automating account rotation, is not supported under consumer plans.&rdquo;
              </p>
              <p>
                &ldquo;Using third-party software, tools, or services to access Antigravity is a violation of our Terms of Service.&rdquo;
              </p>
            </blockquote>
            <p>
              Ao utilizar o Antigravity CLI integrado ao Orin, <strong>sua conta Google pode ser restringida ou suspensa</strong> pela Google. Consulte o FAQ oficial em{' '}
              <a
                href="https://antigravity.google/docs/faq/"
                target="_blank"
                rel="noreferrer"
              >
                https://antigravity.google/docs/faq/
              </a>
              .
            </p>
            <label className="provider-panel__toggle">
              <input
                type="checkbox"
                checked={riskAcknowledged}
                onChange={(event) => handleRiskAcknowledgedChange(event.target.checked)}
              />
              <span>
                <strong>
                  Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco de ativar o Antigravity CLI.
                </strong>
              </span>
            </label>
          </div>
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
              <small>Permite selecionar os modelos do Antigravity CLI nas conversas.</small>
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
              disabled={action.pending || checking || unavailable || !isReady || !riskAcknowledged}
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
