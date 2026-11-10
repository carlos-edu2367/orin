import {
  useEffect,
  useRef,
  useState,
  type JSX,
} from 'react'
import { sanitizeText } from '../teamActivityModel'
import type { SceneAgent, SceneAgentStatus } from './sceneTypes'

export type SceneAgentControlResult =
  | { ok: true }
  | { ok: false; reason: 'not_running' | 'error'; message?: string }

export const CONTROLLABLE_SUBAGENT_STATUSES: ReadonlySet<SceneAgentStatus> = new Set([
  'working',
  'thinking',
  'waiting_agent',
  'waiting_user',
  // 'queued' is left out on purpose: the backend keeps a subagent waiting for a slot as idle and answers 409.
])

export type AgentMessageFeedback =
  | { kind: 'success'; text: string }
  | {
      kind: 'not_running'
      text: string
      agentId: string
      agentName: string
      resumeMessage: string
    }
  | { kind: 'error'; text: string }

function normalizeControlOutcome(
  result: SceneAgentControlResult | void,
  defaultError: string,
): SceneAgentControlResult {
  if (!result || result.ok) {
    return { ok: true }
  }
  if (result.reason === 'not_running') {
    return { ok: false, reason: 'not_running' }
  }
  return {
    ok: false,
    reason: 'error',
    message: sanitizeText(result.message) || defaultError,
  }
}

function normalizeThrownControlError(
  err: unknown,
  defaultError: string,
): SceneAgentControlResult {
  if (err && typeof err === 'object') {
    const rec = err as { status?: unknown; code?: unknown; message?: unknown }
    const code = typeof rec.code === 'string' ? rec.code.toUpperCase() : ''
    if (rec.status === 409 && code === 'AGENT_NOT_RUNNING') {
      return { ok: false, reason: 'not_running' }
    }
  }
  const msg =
    err instanceof Error && err.message ? sanitizeText(err.message) : ''
  return {
    ok: false,
    reason: 'error',
    message: msg || defaultError,
  }
}

export interface UseSceneAgentControlsOptions {
  onStopAgent?: (
    agentId: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onMessageAgent?: (
    agentId: string,
    message: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onSendToOrin?: (text: string) => Promise<void> | void
}

export interface SceneAgentControlsState {
  stopConfirmAgentId: string | null
  stoppingAgentId: string | null
  stopErrorByAgent: Record<string, string | null>
  messageOpenAgentId: string | null
  agentMessageDrafts: Record<string, string>
  sendingAgentMessageId: string | null
  agentFeedbackByAgent: Record<string, AgentMessageFeedback | null>
  openStopConfirm: (agentId: string) => void
  cancelStopConfirm: () => void
  confirmStop: (agent: SceneAgent) => Promise<void>
  openMessageForm: (agentId: string) => void
  closeMessageForm: () => void
  setMessageDraft: (agentId: string, value: string) => void
  submitAgentMessage: (agent: SceneAgent) => Promise<void>
  resumeAgentViaOrin: (feedback: Extract<AgentMessageFeedback, { kind: 'not_running' }>) => void
}

export function useSceneAgentControls({
  onStopAgent,
  onMessageAgent,
  onSendToOrin,
}: UseSceneAgentControlsOptions): SceneAgentControlsState {
  const [stopConfirmAgentId, setStopConfirmAgentId] = useState<string | null>(null)
  const [stoppingAgentId, setStoppingAgentId] = useState<string | null>(null)
  const [stopErrorByAgent, setStopErrorByAgent] = useState<Record<string, string | null>>({})

  const [messageOpenAgentId, setMessageOpenAgentId] = useState<string | null>(null)
  const [agentMessageDrafts, setAgentMessageDrafts] = useState<Record<string, string>>({})
  const [sendingAgentMessageId, setSendingAgentMessageId] = useState<string | null>(null)
  const [agentFeedbackByAgent, setAgentFeedbackByAgent] = useState<
    Record<string, AgentMessageFeedback | null>
  >({})

  const openStopConfirm = (agentId: string) => {
    setStopConfirmAgentId(agentId)
    setMessageOpenAgentId(null)
    setStopErrorByAgent((prev) => ({
      ...prev,
      [agentId]: null,
    }))
  }

  const cancelStopConfirm = () => {
    setStopConfirmAgentId(null)
  }

  const confirmStop = async (agent: SceneAgent) => {
    if (!onStopAgent || stoppingAgentId === agent.id) return
    const cleanName = sanitizeText(agent.name, 'Subagente')
    setStoppingAgentId(agent.id)
    setStopErrorByAgent((prev) => ({ ...prev, [agent.id]: null }))
    let outcome: SceneAgentControlResult
    try {
      const res = await onStopAgent(agent.id)
      outcome = normalizeControlOutcome(
        res,
        'Não foi possível parar o subagente.',
      )
    } catch (err) {
      outcome = normalizeThrownControlError(
        err,
        'Não foi possível parar o subagente.',
      )
    } finally {
      setStoppingAgentId((cur) => (cur === agent.id ? null : cur))
    }

    if (outcome.ok) {
      setStopConfirmAgentId((cur) => (cur === agent.id ? null : cur))
    } else if (outcome.reason === 'not_running') {
      setStopConfirmAgentId((cur) => (cur === agent.id ? null : cur))
      setStopErrorByAgent((prev) => ({
        ...prev,
        [agent.id]: `${cleanName} já terminou.`,
      }))
    } else {
      setStopErrorByAgent((prev) => ({
        ...prev,
        [agent.id]:
          outcome.message || 'Não foi possível parar o subagente.',
      }))
    }
  }

  const openMessageForm = (agentId: string) => {
    setMessageOpenAgentId(agentId)
    setStopConfirmAgentId(null)
    setAgentFeedbackByAgent((prev) => ({
      ...prev,
      [agentId]: null,
    }))
  }

  const closeMessageForm = () => {
    setMessageOpenAgentId(null)
  }

  const setMessageDraft = (agentId: string, value: string) => {
    setAgentMessageDrafts((prev) => ({
      ...prev,
      [agentId]: value,
    }))
    setAgentFeedbackByAgent((prev) => {
      const current = prev[agentId]
      if (current?.kind === 'error') {
        return {
          ...prev,
          [agentId]: null,
        }
      }
      return prev
    })
  }

  const submitAgentMessage = async (agent: SceneAgent) => {
    const draft = (agentMessageDrafts[agent.id] ?? '').trim()
    if (!onMessageAgent || sendingAgentMessageId === agent.id || !draft) return
    const cleanName = sanitizeText(agent.name, 'Subagente')
    setSendingAgentMessageId(agent.id)
    setAgentFeedbackByAgent((prev) => ({ ...prev, [agent.id]: null }))
    let outcome: SceneAgentControlResult
    try {
      const res = await onMessageAgent(agent.id, draft)
      outcome = normalizeControlOutcome(
        res,
        'Não foi possível enviar a mensagem ao subagente.',
      )
    } catch (err) {
      outcome = normalizeThrownControlError(
        err,
        'Não foi possível enviar a mensagem ao subagente.',
      )
    } finally {
      setSendingAgentMessageId((cur) => (cur === agent.id ? null : cur))
    }

    if (outcome.ok) {
      setAgentMessageDrafts((prev) => ({ ...prev, [agent.id]: '' }))
      setMessageOpenAgentId((cur) => (cur === agent.id ? null : cur))
      setAgentFeedbackByAgent((prev) => ({
        ...prev,
        [agent.id]: {
          kind: 'success',
          text: `Mensagem entregue a ${cleanName}; ele lê no próximo passo.`,
        },
      }))
    } else if (outcome.reason === 'not_running') {
      setMessageOpenAgentId((cur) => (cur === agent.id ? null : cur))
      setAgentFeedbackByAgent((prev) => ({
        ...prev,
        [agent.id]: {
          kind: 'not_running',
          text: `${cleanName} já terminou.`,
          agentId: agent.id,
          agentName: cleanName,
          resumeMessage: draft,
        },
      }))
    } else {
      setAgentFeedbackByAgent((prev) => ({
        ...prev,
        [agent.id]: {
          kind: 'error',
          text:
            outcome.message ||
            'Não foi possível enviar a mensagem ao subagente.',
        },
      }))
    }
  }

  const resumeAgentViaOrin = (
    feedback: Extract<AgentMessageFeedback, { kind: 'not_running' }>,
  ) => {
    const resumePrompt = `Retome o agente "${feedback.agentName}" (agent_id ${feedback.agentId}) com esta mensagem: ${feedback.resumeMessage}`
    Promise.resolve(onSendToOrin?.(resumePrompt)).catch(() => undefined)
  }

  return {
    stopConfirmAgentId,
    stoppingAgentId,
    stopErrorByAgent,
    messageOpenAgentId,
    agentMessageDrafts,
    sendingAgentMessageId,
    agentFeedbackByAgent,
    openStopConfirm,
    cancelStopConfirm,
    confirmStop,
    openMessageForm,
    closeMessageForm,
    setMessageDraft,
    submitAgentMessage,
    resumeAgentViaOrin,
  }
}

export interface SceneAgentActionsProps {
  agent: SceneAgent
  controls?: SceneAgentControlsState
  onStopAgent?: (
    agentId: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onMessageAgent?: (
    agentId: string,
    message: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onSendToOrin?: (text: string) => Promise<void> | void
  testIdPrefix?: string
  variant?: 'hud' | 'modal'
}

export function SceneAgentActions({
  agent,
  controls: externalControls,
  onStopAgent,
  onMessageAgent,
  onSendToOrin,
  testIdPrefix = 'scene-agent',
  variant = 'hud',
}: SceneAgentActionsProps): JSX.Element | null {
  const internalControls = useSceneAgentControls({
    onStopAgent,
    onMessageAgent,
    onSendToOrin,
  })
  const controls = externalControls ?? internalControls
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const cleanSelectedName = sanitizeText(
    agent.name,
    agent.isMain ? 'Orin' : 'Subagente',
  )
  const canControlSelected =
    !agent.isMain &&
    CONTROLLABLE_SUBAGENT_STATUSES.has(agent.status) &&
    Boolean(onStopAgent || onMessageAgent)
  const isStoppingSelected = controls.stoppingAgentId === agent.id
  const isConfirmingStop = controls.stopConfirmAgentId === agent.id
  const isMessagingSelected = controls.messageOpenAgentId === agent.id
  const isSendingSelectedMsg = controls.sendingAgentMessageId === agent.id
  const currentDraft = controls.agentMessageDrafts[agent.id] ?? ''
  const trimmedDraft = currentDraft.trim()
  const stopErr = controls.stopErrorByAgent[agent.id] ?? null
  const msgFeedback = controls.agentFeedbackByAgent[agent.id] ?? null

  useEffect(() => {
    if (isMessagingSelected && textareaRef.current) {
      textareaRef.current.focus()
    }
  }, [isMessagingSelected, agent.id])

  if (!canControlSelected && !stopErr && !msgFeedback) {
    return null
  }

  const areaClass =
    variant === 'modal'
      ? 'scene-hud__agent-controls-area scene-agent-modal__controls-area'
      : 'scene-hud__agent-controls-area'

  return (
    <div
      className={areaClass}
      data-testid={`${testIdPrefix}-controls-area`}
    >
      {canControlSelected ? (
        <div
          className="scene-hud__agent-control-actions"
          data-testid={`${testIdPrefix}-controls`}
        >
          {onStopAgent ? (
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--danger"
              data-testid={`${testIdPrefix}-stop-button`}
              aria-label={`Parar ${cleanSelectedName}`}
              disabled={isStoppingSelected}
              onClick={() => controls.openStopConfirm(agent.id)}
            >
              {isStoppingSelected ? 'parando…' : 'Parar'}
            </button>
          ) : null}

          {onMessageAgent ? (
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact"
              data-testid={`${testIdPrefix}-message-button`}
              aria-label={`Mensagem para ${cleanSelectedName}`}
              disabled={isStoppingSelected}
              onClick={() => controls.openMessageForm(agent.id)}
            >
              Mensagem
            </button>
          ) : null}
        </div>
      ) : null}

      {canControlSelected && isConfirmingStop && onStopAgent ? (
        <div
          className="scene-hud__agent-stop-confirm"
          data-testid={`${testIdPrefix}-stop-confirm`}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && !isStoppingSelected) {
              e.preventDefault()
              e.stopPropagation()
              controls.cancelStopConfirm()
            }
          }}
        >
          <p className="scene-hud__agent-stop-prompt">
            Parar {cleanSelectedName}? Ele interrompe o que está
            fazendo e para os agentes que ele iniciou.
          </p>
          <div className="scene-hud__agent-control-actions">
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--danger"
              data-testid={`${testIdPrefix}-stop-confirm-button`}
              aria-label={`Parar agora ${cleanSelectedName}`}
              disabled={isStoppingSelected}
              onClick={() => void controls.confirmStop(agent)}
            >
              {isStoppingSelected ? 'parando…' : 'Parar agora'}
            </button>
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact"
              data-testid={`${testIdPrefix}-stop-cancel-button`}
              aria-label={`Cancelar parada de ${cleanSelectedName}`}
              disabled={isStoppingSelected}
              onClick={() => controls.cancelStopConfirm()}
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}

      {stopErr ? (
        <p
          className="scene-hud__composer-error"
          data-testid={`${testIdPrefix}-stop-error`}
          role="alert"
        >
          {stopErr}
        </p>
      ) : null}

      {canControlSelected && isMessagingSelected && onMessageAgent ? (
        <form
          className="scene-hud__composer-form scene-hud__agent-message-form"
          data-testid={`${testIdPrefix}-message-form`}
          onSubmit={(e) => {
            e.preventDefault()
            void controls.submitAgentMessage(agent)
          }}
        >
          <textarea
            ref={textareaRef}
            className="scene-hud__composer-textarea"
            data-testid={`${testIdPrefix}-message-input`}
            aria-label={`Mensagem para ${cleanSelectedName}`}
            rows={2}
            placeholder={`Envie uma mensagem para ${cleanSelectedName}…`}
            value={currentDraft}
            disabled={isSendingSelectedMsg}
            onChange={(e) => controls.setMessageDraft(agent.id, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                controls.closeMessageForm()
                return
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void controls.submitAgentMessage(agent)
              }
            }}
          />
          <div className="scene-hud__composer-actions">
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact"
              data-testid={`${testIdPrefix}-message-cancel`}
              aria-label={`Cancelar mensagem para ${cleanSelectedName}`}
              disabled={isSendingSelectedMsg}
              onClick={() => controls.closeMessageForm()}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--active"
              data-testid={`${testIdPrefix}-message-submit`}
              aria-label={`Enviar mensagem para ${cleanSelectedName}`}
              disabled={isSendingSelectedMsg || !trimmedDraft}
            >
              {isSendingSelectedMsg ? 'Enviando…' : 'Enviar'}
            </button>
          </div>
        </form>
      ) : null}

      {msgFeedback?.kind === 'success' ? (
        <p
          className="scene-hud__agent-feedback scene-hud__agent-feedback--success"
          data-testid={`${testIdPrefix}-message-success`}
          role="status"
        >
          {msgFeedback.text}
        </p>
      ) : null}

      {msgFeedback?.kind === 'error' ? (
        <p
          className="scene-hud__composer-error"
          data-testid={`${testIdPrefix}-message-error`}
          role="alert"
        >
          {msgFeedback.text}
        </p>
      ) : null}

      {msgFeedback?.kind === 'not_running' ? (
        <div
          className="scene-hud__agent-feedback scene-hud__agent-feedback--not-running"
          data-testid={`${testIdPrefix}-not-running`}
          role="alert"
        >
          <p className="scene-hud__composer-error">
            {msgFeedback.text}
          </p>
          <button
            type="button"
            className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--active"
            data-testid={`${testIdPrefix}-resume-button`}
            aria-label={`Pedir ao Orin para retomá-lo (${msgFeedback.agentName})`}
            onClick={() => controls.resumeAgentViaOrin(msgFeedback)}
          >
            Pedir ao Orin para retomá-lo
          </button>
        </div>
      ) : null}
    </div>
  )
}
