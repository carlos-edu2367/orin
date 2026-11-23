import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { MarkdownMessage } from './MarkdownMessage'
import { isBtwStreamingStatus, type BtwSessionState } from './useBtwSession'
import type { WorkspaceFilePreviewHandler } from './WorkspaceFileCard'
import './btwCard.css'

export type BtwCardProps = {
  state: BtwSessionState | null
  conversationId?: string
  client?: ApiClient
  onStop: () => void
  onRetry: () => void
  onDismiss: () => void
  onToggleMinimized: () => void
  onToggleExpanded: () => void
  onInsertIntoComposer: (answer: string) => void
  onContinueInFork: (question: string, answer: string) => Promise<void> | void
  onPreview?: WorkspaceFilePreviewHandler
  onPreviewWorkspaceFile?: (path: string, label?: string) => void
  copyText?: (text: string) => Promise<void>
}

function shortModelName(modelId: string | null): string | null {
  if (!modelId) return null
  const clean = modelId.trim()
  if (!clean) return null
  return clean.includes('/') ? clean.slice(clean.lastIndexOf('/') + 1) : clean
}

function formatDuration(durationMs: number | null): string | null {
  if (durationMs === null || durationMs < 0) return null
  if (durationMs < 1000) return `${durationMs}ms`
  return `${(durationMs / 1000).toFixed(1)}s`
}

function formatTokens(state: BtwSessionState): string | null {
  const out = state.usage?.outputTokens
  if (typeof out === 'number' && out > 0) {
    return `${out.toLocaleString('pt-BR')} tok`
  }
  return null
}

function waitingLabel(state: BtwSessionState): string {
  const pos = state.waiting?.position
  if (state.waiting?.reason === 'memory') {
    return 'Aguardando memória livre para iniciar o /btw…'
  }
  if (typeof pos === 'number' && pos > 0) {
    return `Aguardando slot de CLI (posição ${pos})…`
  }
  return 'Aguardando slot livre para responder…'
}

export function BtwCard({
  state,
  conversationId,
  client,
  onStop,
  onRetry,
  onDismiss,
  onToggleMinimized,
  onToggleExpanded,
  onInsertIntoComposer,
  onContinueInFork,
  onPreview,
  onPreviewWorkspaceFile,
  copyText,
}: BtwCardProps) {
  const reduced = useReducedMotion()
  const bodyRef = useRef<HTMLDivElement>(null)
  const pinnedToBottomRef = useRef(true)
  const [copied, setCopied] = useState(false)
  const [forking, setForking] = useState(false)
  const forkingRef = useRef(false)
  const copiedTimerRef = useRef<number | null>(null)
  const questionKey = `${state?.btwId ?? ''}:${state?.question ?? ''}`
  const [prevQuestionKey, setPrevQuestionKey] = useState(questionKey)
  if (questionKey !== prevQuestionKey) {
    setPrevQuestionKey(questionKey)
    setCopied(false)
    setForking(false)
  }

  const streaming = isBtwStreamingStatus(state?.status)
  const hasAnswer = Boolean(state?.answer.trim())

  useEffect(() => {
    return () => {
      if (copiedTimerRef.current !== null) {
        window.clearTimeout(copiedTimerRef.current)
      }
    }
  }, [])

  // Re-pin auto-scroll and clear fork guard whenever a new question starts.
  useEffect(() => {
    pinnedToBottomRef.current = true
    forkingRef.current = false
  }, [questionKey])

  // Auto-scroll smoothly while streaming unless the user scrolled up to read.
  useEffect(() => {
    const el = bodyRef.current
    if (!el || !streaming || !pinnedToBottomRef.current) return
    el.scrollTop = el.scrollHeight
  }, [state?.answer, state?.thinking, streaming])

  function handleBodyScroll() {
    const el = bodyRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    pinnedToBottomRef.current = distance <= 32
  }

  async function handleCopy() {
    if (!state?.answer.trim()) return
    const writer = copyText ?? ((text: string) => navigator.clipboard.writeText(text))
    try {
      await writer(state.answer)
      setCopied(true)
      if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current)
      copiedTimerRef.current = window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Ignore clipboard failures cleanly.
    }
  }

  async function handleFork() {
    if (!state || forkingRef.current || forking) return
    forkingRef.current = true
    setForking(true)
    try {
      await onContinueInFork(state.question, state.answer)
    } finally {
      forkingRef.current = false
      setForking(false)
    }
  }

  const metaParts = state
    ? [
        shortModelName(state.modelId),
        formatDuration(state.durationMs),
        formatTokens(state),
      ].filter((part): part is string => Boolean(part))
    : []

  return (
    <AnimatePresence initial={false}>
      {state && state.minimized ? (
        <motion.div
          key="btw-pill"
          className="btw-pill"
          role="region"
          aria-label="Pergunta paralela /btw minimizada"
          data-testid="btw-minimized-pill"
          initial={reduced ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: 6 }}
          transition={{ duration: 0.16, ease: 'easeOut' }}
        >
          <button
            type="button"
            className="btw-pill__trigger"
            onClick={onToggleMinimized}
            aria-label="Reabrir resposta paralela /btw"
          >
            <span className="btw-card__badge">BTW</span>
            <span className="btw-pill__question">{state.question}</span>
            <span className="btw-pill__status">
              {streaming
                ? 'Respondendo…'
                : state.status === 'error'
                  ? 'Erro · abrir'
                  : 'Ver resposta'}
            </span>
          </button>
          <button
            type="button"
            className="btw-card__icon-btn"
            aria-label="Fechar /btw"
            title="Fechar /btw"
            onClick={onDismiss}
          >
            <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </motion.div>
      ) : state ? (
        <motion.section
          key="btw-card"
          className={`btw-card${state.expanded ? ' btw-card--expanded' : ''}`}
          role="region"
          aria-label="Resposta paralela /btw"
          aria-live="polite"
          data-testid="btw-card"
          initial={reduced ? false : { opacity: 0, y: 10, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.99 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
        >
          <header className="btw-card__header">
            <div className="btw-card__identity">
              <span className="btw-card__badge">BTW</span>
              <span className="btw-card__context-tag">
                {state.activeTurnIncluded && <span className="btw-card__live-dot" aria-hidden="true" />}
                {state.activeTurnIncluded
                  ? 'Fora do histórico · inclui turno ativo'
                  : 'Fora do histórico'}
              </span>
            </div>
            <div className="btw-card__header-right">
              {metaParts.length > 0 && (
                <span className="btw-card__meta" title={metaParts.join(' · ')}>
                  {metaParts.join(' · ')}
                </span>
              )}
              <button
                type="button"
                className="btw-card__icon-btn"
                aria-label={state.expanded ? 'Compactar altura do /btw' : 'Expandir altura do /btw'}
                title={state.expanded ? 'Compactar altura' : 'Expandir altura'}
                onClick={onToggleExpanded}
              >
                {state.expanded ? (
                  <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6 10 2.5 13.5M2.5 10.5v3h3M10 6l3.5-3.5M13.5 5.5v-3h-3" />
                  </svg>
                ) : (
                  <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9.5 6.5 13.5 2.5M10.5 2.5h3v3M6.5 9.5 2.5 13.5M5.5 13.5h-3v-3" />
                  </svg>
                )}
              </button>
              <button
                type="button"
                className="btw-card__icon-btn"
                aria-label="Minimizar /btw"
                title="Minimizar"
                onClick={onToggleMinimized}
              >
                <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M3.5 11.5h9" />
                </svg>
              </button>
              <button
                type="button"
                className="btw-card__icon-btn"
                aria-label="Fechar /btw"
                title="Fechar (Esc)"
                onClick={onDismiss}
              >
                <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            </div>
          </header>

          <div className="btw-card__question">
            <span className="btw-card__question-label">Você</span>
            <p className="btw-card__question-text">{state.question}</p>
          </div>

          <div
            ref={bodyRef}
            className="btw-card__body"
            onScroll={handleBodyScroll}
            tabIndex={0}
            aria-label="Conteúdo da resposta paralela"
          >
            {state.thinking.trim() && (
              <details className="btw-card__thinking">
                <summary>
                  {state.status === 'thinking' ? 'Pensando… (ver raciocínio)' : 'Ver raciocínio'}
                </summary>
                <pre>{state.thinking}</pre>
              </details>
            )}

            {state.status === 'waiting' && !hasAnswer && (
              <div className="btw-card__status-row" role="status">
                <span className="btw-card__spinner" aria-hidden="true" />
                <span>{waitingLabel(state)}</span>
              </div>
            )}

            {(state.status === 'connecting' || state.status === 'thinking') && !hasAnswer && (
              <div className="btw-card__status-row" role="status">
                <span className="btw-card__spinner" aria-hidden="true" />
                <span>
                  {state.status === 'thinking'
                    ? 'Pensando na resposta…'
                    : 'Consultando contexto da conversa…'}
                </span>
              </div>
            )}

            {hasAnswer && (
              <div className="btw-card__answer">
                <MarkdownMessage
                  content={state.answer}
                  conversationId={conversationId}
                  client={client}
                  onPreview={onPreview}
                  onPreviewWorkspaceFile={onPreviewWorkspaceFile}
                  openExternalLinksInNewTab
                />
                {streaming && <span className="btw-card__cursor" aria-hidden="true" />}
              </div>
            )}

            {state.status === 'cancelled' && (
              <p className="btw-card__cancelled" role="status">
                Resposta paralela interrompida.
              </p>
            )}

            {state.status === 'error' && state.error && (
              <div className="btw-card__error" role="alert">
                <span>{state.error.message}</span>
              </div>
            )}
          </div>

          <footer className="btw-card__footer">
            <div className="btw-card__actions">
              {streaming ? (
                <button
                  type="button"
                  className="btw-card__action-btn btw-card__action-btn--stop"
                  onClick={onStop}
                >
                  Parar /btw
                </button>
              ) : (
                <>
                  {hasAnswer && (
                    <>
                      <button
                        type="button"
                        className={`btw-card__action-btn${copied ? ' btw-card__action-btn--success' : ''}`}
                        onClick={() => { void handleCopy() }}
                      >
                        {copied ? 'Copiado' : 'Copiar'}
                      </button>
                      <button
                        type="button"
                        className="btw-card__action-btn"
                        onClick={() => onInsertIntoComposer(state.answer)}
                      >
                        Inserir no composer
                      </button>
                      <button
                        type="button"
                        className="btw-card__action-btn"
                        disabled={forking}
                        onClick={() => { void handleFork() }}
                      >
                        {forking ? 'Criando /fork…' : 'Continuar em /fork'}
                      </button>
                    </>
                  )}
                  {(state.status === 'cancelled' || (state.status === 'error' && state.error?.retryable)) && (
                    <button
                      type="button"
                      className="btw-card__action-btn"
                      onClick={onRetry}
                    >
                      Tentar novamente
                    </button>
                  )}
                </>
              )}
            </div>
            <span className="btw-card__hint">Esc fecha</span>
          </footer>
        </motion.section>
      ) : null}
    </AnimatePresence>
  )
}
