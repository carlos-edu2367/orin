import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { forkConversation, streamBtwQuestion, type BtwStreamEvent, type BtwUsage } from '../../api/sessionTools'
import { useKeybindings } from '../keybindings/engine'
import { conversationDraftKey, writeDraft } from './useComposerDraft'

export type BtwStatus =
  | 'connecting'
  | 'waiting'
  | 'thinking'
  | 'streaming'
  | 'completed'
  | 'cancelled'
  | 'error'

export type BtwSessionState = {
  btwId: string | null
  question: string
  answer: string
  thinking: string
  status: BtwStatus
  provider: string | null
  modelId: string | null
  effort: string | null
  activeTurnIncluded: boolean
  contextMessages: number
  durationMs: number | null
  usage: BtwUsage | null
  waiting: { position: number | null; limit: number | null; reason: string | null } | null
  error: { code: string; message: string; retryable: boolean } | null
  minimized: boolean
  expanded: boolean
}

export type AskBtwOptions = {
  provider?: string | null
  modelId?: string | null
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function isBtwStreamingStatus(status: BtwStatus | undefined): boolean {
  return status === 'connecting' || status === 'waiting' || status === 'thinking' || status === 'streaming'
}

export function useBtwSession(client: ApiClient, conversationId: string) {
  const [state, setState] = useState<BtwSessionState | null>(null)
  const [prevConversationId, setPrevConversationId] = useState(conversationId)
  if (prevConversationId !== conversationId) {
    setPrevConversationId(conversationId)
    setState(null)
  }
  const stateRef = useRef<BtwSessionState | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const runIdRef = useRef(0)
  const lastOptionsRef = useRef<AskBtwOptions>({})

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const abortActive = useCallback(() => {
    const current = abortRef.current
    if (current) {
      abortRef.current = null
      current.abort()
    }
  }, [])

  // Abort in-flight stream when switching conversations or unmounting.
  useEffect(() => {
    runIdRef.current += 1
    abortActive()
    return () => {
      runIdRef.current += 1
      abortActive()
    }
  }, [conversationId, abortActive])

  const ask = useCallback(
    async (rawQuestion: string, options: AskBtwOptions = {}) => {
      const question = rawQuestion.trim()
      if (!question || !conversationId) return

      abortActive()
      const runId = runIdRef.current + 1
      runIdRef.current = runId
      const controller = new AbortController()
      abortRef.current = controller
      lastOptionsRef.current = {
        provider: options.provider ?? null,
        modelId: options.modelId ?? null,
      }

      setState({
        btwId: null,
        question,
        answer: '',
        thinking: '',
        status: 'connecting',
        provider: options.provider ?? null,
        modelId: options.modelId ?? null,
        effort: null,
        activeTurnIncluded: false,
        contextMessages: 0,
        durationMs: null,
        usage: null,
        waiting: null,
        error: null,
        minimized: false,
        expanded: false,
      })

      let sawTerminal = false

      const handleEvent = (event: BtwStreamEvent) => {
        if (runIdRef.current !== runId) return
        switch (event.type) {
          case 'start':
            setState((current) =>
              current
                ? {
                    ...current,
                    btwId: event.btwId || current.btwId,
                    provider: event.provider || current.provider,
                    modelId: event.modelId || current.modelId,
                    effort: event.effort,
                    activeTurnIncluded: event.activeTurnIncluded,
                    contextMessages: event.contextMessages,
                  }
                : current,
            )
            break
          case 'waiting':
            setState((current) =>
              current
                ? {
                    ...current,
                    status: 'waiting',
                    waiting: {
                      position: event.position,
                      limit: event.limit,
                      reason: event.reason,
                    },
                  }
                : current,
            )
            break
          case 'thinking':
            setState((current) =>
              current
                ? {
                    ...current,
                    status: current.answer ? 'streaming' : 'thinking',
                    waiting: null,
                    thinking: current.thinking + event.delta,
                  }
                : current,
            )
            break
          case 'delta':
            setState((current) =>
              current
                ? {
                    ...current,
                    status: 'streaming',
                    waiting: null,
                    answer: current.answer + event.delta,
                  }
                : current,
            )
            break
          case 'done':
            sawTerminal = true
            setState((current) =>
              current
                ? {
                    ...current,
                    btwId: event.btwId || current.btwId,
                    answer: event.text || current.answer,
                    status: 'completed',
                    waiting: null,
                    error: null,
                    durationMs: event.durationMs,
                    provider: event.provider || current.provider,
                    modelId: event.modelId || current.modelId,
                    effort: event.effort,
                    usage: event.usage,
                  }
                : current,
            )
            break
          case 'error':
            sawTerminal = true
            setState((current) =>
              current
                ? {
                    ...current,
                    status: 'error',
                    waiting: null,
                    error: {
                      code: event.code,
                      message: event.message,
                      retryable: event.retryable,
                    },
                  }
                : current,
            )
            break
          case 'heartbeat':
            break
        }
      }

      try {
        await streamBtwQuestion(client, conversationId, {
          question,
          provider: options.provider,
          modelId: options.modelId,
          signal: controller.signal,
          onEvent: handleEvent,
        })
        if (runIdRef.current !== runId) return
        if (!sawTerminal) {
          setState((current) => {
            if (!current) return current
            return {
              ...current,
              status: 'error',
              waiting: null,
              error: {
                code: 'stream_closed',
                message: current.answer.trim()
                  ? 'A conexão do /btw foi interrompida antes da resposta concluir.'
                  : 'A conexão do /btw encerrou antes da resposta concluir.',
                retryable: true,
              },
            }
          })
        }
      } catch (caught) {
        if (runIdRef.current !== runId) return
        if (isAbortError(caught) || controller.signal.aborted) return
        let message = 'Não foi possível conectar ao /btw. Tente novamente.'
        let code = 'network_error'
        let retryable = true
        if (caught instanceof ApiError) {
          code = caught.code
          retryable = caught.retryable
          if (caught.status === 401) {
            message = 'Sua sessão expirou. Faça login novamente para usar o /btw.'
            retryable = false
          } else if (caught.status === 403) {
            message = 'Acesso recusado ao /btw. Recarregue a página e tente novamente.'
            retryable = false
          } else if (caught.status === 404) {
            message = 'Conversa não encontrada para responder ao /btw.'
            retryable = false
          } else if (caught.status === 422) {
            message = 'Digite uma pergunta válida após /btw.'
            retryable = false
          } else if (caught.status === 429) {
            message = 'Muitas perguntas /btw em andamento. Aguarde alguns segundos e tente novamente.'
            retryable = true
          }
        }
        setState((current) =>
          current
            ? {
                ...current,
                status: 'error',
                waiting: null,
                error: {
                  code,
                  message,
                  retryable,
                },
              }
            : current,
        )
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null
        }
      }
    },
    [client, conversationId, abortActive],
  )

  const stop = useCallback(() => {
    runIdRef.current += 1
    abortActive()
    setState((current) =>
      current && isBtwStreamingStatus(current.status)
        ? { ...current, status: 'cancelled', waiting: null }
        : current,
    )
  }, [abortActive])

  const dismiss = useCallback(() => {
    runIdRef.current += 1
    abortActive()
    setState(null)
  }, [abortActive])

  const retry = useCallback(() => {
    const current = stateRef.current
    if (!current?.question) return
    void ask(current.question, {
      provider: lastOptionsRef.current.provider ?? current.provider,
      modelId: lastOptionsRef.current.modelId ?? current.modelId,
    })
  }, [ask])

  const toggleMinimized = useCallback(() => {
    setState((current) => (current ? { ...current, minimized: !current.minimized } : current))
  }, [])

  const setMinimized = useCallback((minimized: boolean) => {
    setState((current) => (current ? { ...current, minimized } : current))
  }, [])

  const toggleExpanded = useCallback(() => {
    setState((current) => (current ? { ...current, expanded: !current.expanded, minimized: false } : current))
  }, [])

  return {
    state,
    isOpen: state !== null && !state.minimized,
    isStreaming: isBtwStreamingStatus(state?.status),
    ask,
    stop,
    retry,
    dismiss,
    toggleMinimized,
    setMinimized,
    toggleExpanded,
  }
}

export type BtwSessionController = ReturnType<typeof useBtwSession>

function isOtherEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.id === 'composer-message') return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

/** Composer actions, fork handoff, and Alt+B / Escape keyboard bindings for `/btw`. */
export function useBtwChatControls(options: {
  btw: BtwSessionController
  client: ApiClient
  conversationId: string
  conversationTitle: string
  projectId?: string | null
  escapeBlocked: boolean
  setMessage: (updater: (current: string) => string) => void
  focusComposer: () => void
  notify: (text: string) => void
  navigate: (path: string) => void
}) {
  const {
    btw,
    client,
    conversationId,
    conversationTitle,
    projectId = null,
    escapeBlocked,
    setMessage,
    focusComposer,
    notify,
    navigate,
  } = options
  const { match } = useKeybindings()

  const triggerBtwComposer = useCallback(() => {
    if (btw.state?.minimized) {
      btw.setMinimized(false)
      focusComposer()
      return
    }
    setMessage((current) => {
      const trimmed = current.trimStart()
      if (/^\/btw(?:\s|$)/i.test(trimmed)) {
        return trimmed.replace(/^\/btw\s*/i, '')
      }
      return trimmed ? `/btw ${trimmed}` : '/btw '
    })
    focusComposer()
  }, [btw, focusComposer, setMessage])

  const insertBtwIntoComposer = useCallback(
    (answer: string) => {
      const clean = answer.trim()
      if (!clean) return
      setMessage((current) => (current.trim() ? `${current.trimEnd()}\n\n${clean}` : clean))
      focusComposer()
    },
    [focusComposer, setMessage],
  )

  const continueBtwInFork = useCallback(
    async (question: string, answer: string) => {
      try {
        const baseTitle = (conversationTitle || 'Conversa').trim().slice(0, 110)
        const forkTitle = `${baseTitle} · btw`.slice(0, 160)
        const fork = await forkConversation(client, conversationId, { title: forkTitle })
        const snippet = answer.trim().slice(0, 1800)
        const draft = snippet
          ? `Sobre minha pergunta paralela ("${question.trim()}"):\n\n> ${snippet.replace(/\n/g, '\n> ')}\n\nQuero continuar a partir disso: `
          : question.trim()
        writeDraft(conversationDraftKey(fork.conversationId), draft)
        window.dispatchEvent(new Event('orin:conversations-updated'))
        btw.dismiss()
        const targetProjectId = fork.projectId ?? projectId
        navigate(
          targetProjectId
            ? `/projects/${encodeURIComponent(targetProjectId)}/chats/${encodeURIComponent(fork.conversationId)}`
            : `/chats/${encodeURIComponent(fork.conversationId)}`,
        )
      } catch {
        notify('Não foi possível criar um /fork a partir do /btw. Tente novamente.')
      }
    },
    [btw, client, conversationId, conversationTitle, navigate, notify, projectId],
  )

  useEffect(() => {
    const onBtwShortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return
      if (isOtherEditableTarget(event.target)) return
      if (document.querySelector('[role="dialog"], [role="listbox"], [role="search"]')) return
      if (match(event, ['Chat']) !== 'chat:btw') return
      event.preventDefault()
      triggerBtwComposer()
    }
    window.addEventListener('keydown', onBtwShortcut)
    return () => window.removeEventListener('keydown', onBtwShortcut)
  }, [match, triggerBtwComposer])

  useEffect(() => {
    if (!btw.isOpen) return
    const onEscapeBtw = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (escapeBlocked) return
      if (document.querySelector('[role="dialog"], [role="listbox"], [role="search"]')) return
      event.preventDefault()
      btw.dismiss()
    }
    window.addEventListener('keydown', onEscapeBtw)
    return () => window.removeEventListener('keydown', onEscapeBtw)
  }, [btw, escapeBlocked])

  return { triggerBtwComposer, insertBtwIntoComposer, continueBtwInFork }
}
