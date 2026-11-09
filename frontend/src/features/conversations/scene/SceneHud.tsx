import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type JSX,
  type KeyboardEvent,
} from 'react'
import type { ConversationActivityEvent } from '../activityTypes'
import {
  sanitizeText,
  type TeamFinalDeliverable,
  type TeamPendingDecision,
  type TeamRecentActivityEntry,
  type TeamStepperData,
} from '../teamActivityModel'
import {
  ScenePendingCards,
  type SceneActionHandlers,
} from './ScenePendingCards'
import { STATION_LABELS } from './sceneStations'
import type { SceneAgent, SceneAgentStatus, SceneState } from './sceneTypes'

export type SceneAgentControlResult =
  | { ok: true }
  | { ok: false; reason: 'not_running' | 'error'; message?: string }

const CONTROLLABLE_SUBAGENT_STATUSES: ReadonlySet<SceneAgentStatus> = new Set([
  'working',
  'thinking',
  'waiting_agent',
  'waiting_user',
  // 'queued' is left out on purpose: the backend keeps a subagent waiting for a slot as idle and answers 409.
])

type AgentMessageFeedback =
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

export interface SceneHudProps {
  scene: SceneState
  events?: ConversationActivityEvent[]
  actions?: SceneActionHandlers
  openQuestionTurnIds?: ReadonlySet<string>
  onSendToOrin?: (text: string) => Promise<void> | void
  onStopAgent?: (
    agentId: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onMessageAgent?: (
    agentId: string,
    message: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  selectedAgentId: string | null
  onSelectAgent: (agentId: string | null) => void
  paused?: boolean
  soundEnabled: boolean
  onToggleSound: () => void
  stepperData: TeamStepperData
  pendingDecision: TeamPendingDecision | null
  finalDeliverable: TeamFinalDeliverable | null
  recentActivities: TeamRecentActivityEntry[]
  onPauseTeam?: () => void
  onBackToChat?: () => void
  onResolveDecision?: (optionValue: string, decision?: TeamPendingDecision) => void
  onOpenDeliverable?: (deliverable: TeamFinalDeliverable) => void
  onViewAllActivities?: () => void
}

export function SceneHud({
  scene,
  events,
  actions,
  openQuestionTurnIds,
  onSendToOrin,
  onStopAgent,
  onMessageAgent,
  selectedAgentId,
  onSelectAgent,
  paused = false,
  soundEnabled,
  onToggleSound,
  stepperData,
  pendingDecision,
  finalDeliverable,
  recentActivities,
  onPauseTeam,
  onBackToChat,
  onResolveDecision,
  onOpenDeliverable,
  onViewAllActivities,
}: SceneHudProps): JSX.Element {
  const pendingSectionRef = useRef<HTMLElement | null>(null)
  const agentMessageTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [messageText, setMessageText] = useState('')
  const [sendingMessage, setSendingMessage] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  const [stopConfirmAgentId, setStopConfirmAgentId] = useState<string | null>(null)
  const [stoppingAgentId, setStoppingAgentId] = useState<string | null>(null)
  const [stopErrorByAgent, setStopErrorByAgent] = useState<Record<string, string | null>>({})

  const [messageOpenAgentId, setMessageOpenAgentId] = useState<string | null>(null)
  const [agentMessageDrafts, setAgentMessageDrafts] = useState<Record<string, string>>({})
  const [sendingAgentMessageId, setSendingAgentMessageId] = useState<string | null>(null)
  const [agentFeedbackByAgent, setAgentFeedbackByAgent] = useState<
    Record<string, AgentMessageFeedback | null>
  >({})

  const subagents = scene.agents.filter((ag) => !ag.isMain)
  const selectedAgent: SceneAgent | undefined =
    selectedAgentId !== null
      ? scene.agents.find((ag) => ag.id === selectedAgentId)
      : undefined
  const selectedPending = selectedAgent
    ? scene.pending.filter((item) => item.agentId === selectedAgent.id)
    : []

  const isSelectedMessageOpen =
    Boolean(selectedAgent) && messageOpenAgentId === selectedAgent?.id

  useEffect(() => {
    if (isSelectedMessageOpen && agentMessageTextareaRef.current) {
      agentMessageTextareaRef.current.focus()
    }
  }, [isSelectedMessageOpen, selectedAgent?.id])

  const handleViewAll = onViewAllActivities ?? onBackToChat
  const trimmedMessage = messageText.trim()

  const handleFocusPendingSection = () => {
    const el = pendingSectionRef.current
    if (!el) return
    if (typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    if (typeof el.focus === 'function') {
      el.focus({ preventScroll: true })
    }
  }

  const handleSendToOrin = async () => {
    if (!onSendToOrin || sendingMessage || !trimmedMessage) return
    setSendingMessage(true)
    setSendError(null)
    try {
      await onSendToOrin(trimmedMessage)
      setMessageText('')
    } catch (err) {
      const msg =
        err instanceof Error && err.message
          ? sanitizeText(err.message)
          : 'Não foi possível enviar a mensagem. Tente novamente.'
      setSendError(msg || 'Não foi possível enviar a mensagem. Tente novamente.')
    } finally {
      setSendingMessage(false)
    }
  }

  const handleMessageKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSendToOrin()
    }
  }

  const handleMessageSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void handleSendToOrin()
  }

  const handleConfirmStop = async (agent: SceneAgent) => {
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

  const handleSubmitAgentMessage = async (agent: SceneAgent) => {
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

  return (
    <div className="scene-hud" data-testid="scene-hud">
      {/* Top bar: title, subtitle, live status indicator, and controls */}
      <header className="scene-hud__topbar">
        <div className="scene-hud__heading">
          <div className="scene-hud__title-row">
            <h2 className="scene-hud__title">Cena</h2>
            <span
              className={
                scene.live
                  ? 'scene-hud__live-badge scene-hud__live-badge--live'
                  : scene.waitingUser
                    ? 'scene-hud__live-badge scene-hud__live-badge--waiting'
                    : 'scene-hud__live-badge scene-hud__live-badge--resting'
              }
              role="status"
              data-testid="scene-live-indicator"
            >
              <span className="scene-hud__live-dot" aria-hidden="true" />
              <span>
                {scene.live
                  ? 'Ao vivo'
                  : scene.waitingUser
                    ? 'Esperando você'
                    : 'Equipe descansando — sem turno em execução'}
              </span>
            </span>
          </div>
          <p className="scene-hud__subtitle">
            O Orin coordena a equipe; acompanhe ao vivo.
          </p>
        </div>

        <div className="scene-hud__controls">
          {onBackToChat ? (
            <button
              type="button"
              className="scene-hud__btn"
              data-testid="scene-back-to-chat-button"
              onClick={onBackToChat}
            >
              Voltar à conversa
            </button>
          ) : null}

          <button
            type="button"
            className="scene-hud__btn"
            data-testid="scene-pause-button"
            onClick={() => onPauseTeam?.()}
          >
            <span aria-hidden="true">{paused ? '▶' : '⏸'}</span>
            <span>{paused ? 'Retomar equipe' : 'Pausar equipe'}</span>
          </button>

          <button
            type="button"
            className={
              soundEnabled
                ? 'scene-hud__btn scene-hud__btn--active'
                : 'scene-hud__btn'
            }
            data-testid="scene-sound-button"
            aria-pressed={soundEnabled}
            onClick={onToggleSound}
          >
            <span aria-hidden="true">{soundEnabled ? '🔊' : '🔇'}</span>
            <span>{soundEnabled ? 'Som ligado' : 'Som desligado'}</span>
          </button>
        </div>
      </header>

      {/* Pending user approvals / questions */}
      <ScenePendingCards
        pending={scene.pending}
        agents={scene.agents}
        events={events}
        actions={actions}
        openQuestionTurnIds={openQuestionTurnIds}
        onSelectAgent={onSelectAgent}
        sectionRef={pendingSectionRef}
      />

      {/* Message to Orin composer */}
      {onSendToOrin ? (
        <section
          className="scene-hud__card scene-hud__composer"
          data-testid="scene-orin-composer"
          aria-label="Falar com o Orin"
        >
          <form
            className="scene-hud__composer-form"
            onSubmit={handleMessageSubmit}
          >
            <div className="scene-hud__section-header">
              <label
                htmlFor="scene-orin-message-input"
                className="scene-hud__section-title"
              >
                Falar com o Orin
              </label>
            </div>
            <textarea
              id="scene-orin-message-input"
              className="scene-hud__composer-textarea"
              data-testid="scene-orin-message-input"
              rows={2}
              placeholder="Envie uma mensagem para o Orin…"
              value={messageText}
              disabled={sendingMessage}
              onChange={(event) => {
                setMessageText(event.target.value)
                if (sendError) setSendError(null)
              }}
              onKeyDown={handleMessageKeyDown}
            />
            {sendError ? (
              <p
                className="scene-hud__composer-error"
                data-testid="scene-orin-message-error"
                role="alert"
              >
                {sendError}
              </p>
            ) : null}
            <div className="scene-hud__composer-actions">
              <button
                type="submit"
                className="scene-hud__btn scene-hud__btn--active"
                data-testid="scene-orin-message-submit"
                disabled={sendingMessage || !trimmedMessage}
              >
                {sendingMessage ? 'Enviando…' : 'Enviar'}
              </button>
            </div>
          </form>
        </section>
      ) : null}

      {/* Stepper (todos) */}
      <section
        className="scene-hud__card scene-stepper"
        data-testid="scene-stepper"
        aria-label="Etapas da equipe"
      >
        <div
          className="scene-stepper__track"
          role="list"
          aria-label="Progresso das etapas da equipe"
        >
          {stepperData.steps.map((step, idx) => {
            const isLast = idx === stepperData.steps.length - 1
            const nextStep = stepperData.steps[idx + 1]
            const connectorActive =
              step.completed &&
              Boolean(nextStep?.completed || idx < stepperData.completed)
            const cleanLabel = sanitizeText(step.label, `Etapa ${step.stepNumber}`)
            return (
              <div
                key={step.stepNumber}
                className="scene-stepper__item"
                role="listitem"
                aria-label={`Etapa ${step.stepNumber}: ${cleanLabel} (${step.completed ? 'Concluída' : 'Pendente'})`}
                data-testid={`scene-step-${step.stepNumber}`}
                data-completed={step.completed ? 'true' : 'false'}
              >
                <span
                  className={
                    step.completed
                      ? 'scene-stepper__node scene-stepper__node--completed'
                      : 'scene-stepper__node'
                  }
                >
                  {step.completed ? '✓' : String(step.stepNumber)}
                </span>
                {!isLast ? (
                  <span
                    className={
                      connectorActive
                        ? 'scene-stepper__connector scene-stepper__connector--active'
                        : 'scene-stepper__connector'
                    }
                    aria-hidden="true"
                  />
                ) : null}
              </div>
            )
          })}
        </div>

        <div className="scene-stepper__summary">
          <span className="scene-stepper__count">
            {stepperData.completed} de {stepperData.total} etapas
          </span>
          <span className="scene-stepper__state">
            {sanitizeText(stepperData.statusText)}
          </span>
        </div>
      </section>

      {/* Keyboard-accessible character list */}
      <section
        className="scene-hud__card scene-hud__agents"
        aria-label="Personagens em cena"
      >
        <div className="scene-hud__section-header">
          <h3 className="scene-hud__section-title">Personagens</h3>
          <span className="scene-hud__section-meta">
            {scene.agents.length === 1
              ? '1 agente'
              : `${scene.agents.length} agentes`}
          </span>
        </div>

        <div className="scene-hud__agents-list" data-testid="scene-agents-list">
          {scene.agents.map((agent) => {
            const isSelected = selectedAgent?.id === agent.id
            const stationLabel =
              STATION_LABELS[agent.station] ?? sanitizeText(agent.station)
            const cleanName = sanitizeText(
              agent.name,
              agent.isMain ? 'Orin' : 'Subagente',
            )
            const cleanStatus =
              stoppingAgentId === agent.id
                ? 'parando…'
                : sanitizeText(agent.statusLabel, 'Descansando')
            const cleanTask = sanitizeText(agent.task)
            const cleanDuration = sanitizeText(agent.durationLabel)
            const isNested = agent.depth > 1

            return (
              <button
                key={agent.id}
                type="button"
                className={[
                  'scene-hud__agent-btn',
                  isSelected ? 'scene-hud__agent-btn--selected' : '',
                  agent.depth > 0 ? 'scene-hud__agent-btn--subagent' : '',
                  isNested ? 'scene-hud__agent-btn--nested' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                style={
                  agent.depth > 0
                    ? ({ '--agent-depth': agent.depth } as CSSProperties)
                    : undefined
                }
                data-depth={agent.depth}
                data-testid={`scene-agent-button-${sanitizeText(agent.id)}`}
                aria-pressed={isSelected}
                onClick={() => onSelectAgent(agent.id)}
              >
                <div className="scene-hud__agent-main">
                  <span className="scene-hud__agent-name">{cleanName}</span>
                  {agent.subagentType ? (
                    <span className="scene-hud__agent-type">
                      {sanitizeText(agent.subagentType)}
                    </span>
                  ) : null}
                  <span
                    className={`scene-hud__agent-status scene-hud__agent-status--${agent.status}`}
                  >
                    {cleanStatus}
                  </span>
                </div>
                <div className="scene-hud__agent-meta">
                  <span className="scene-hud__agent-station">{stationLabel}</span>
                  {cleanTask ? (
                    <span className="scene-hud__agent-task">{cleanTask}</span>
                  ) : null}
                  {cleanDuration ? (
                    <span className="scene-hud__agent-duration">
                      {cleanDuration}
                    </span>
                  ) : null}
                </div>
              </button>
            )
          })}
        </div>

        {subagents.length === 0 ? (
          <p className="scene-hud__empty" data-testid="scene-agents-empty">
            Nenhum subagente em execução neste momento.
          </p>
        ) : null}
      </section>

      {/* Selected character inspector panel */}
      {selectedAgent ? (
        <section
          className="scene-hud__card scene-hud__inspector"
          data-testid="scene-agent-panel"
          aria-label={`Detalhes de ${sanitizeText(selectedAgent.name)}`}
        >
          <div className="scene-hud__inspector-header">
            <div className="scene-hud__inspector-title-group">
              <h3 className="scene-hud__inspector-name">
                {sanitizeText(selectedAgent.name)}
              </h3>
              {selectedAgent.subagentType ? (
                <span className="scene-hud__inspector-badge">
                  {sanitizeText(selectedAgent.subagentType)}
                </span>
              ) : null}
            </div>
            <button
              type="button"
              className="scene-hud__btn scene-hud__btn--compact"
              data-testid="scene-agent-panel-close"
              onClick={() => onSelectAgent(null)}
            >
              Fechar painel
            </button>
          </div>

          <dl className="scene-hud__inspector-grid">
            <div className="scene-hud__inspector-field">
              <dt>Estado</dt>
              <dd>
                {stoppingAgentId === selectedAgent.id
                  ? 'parando…'
                  : sanitizeText(selectedAgent.statusLabel)}
              </dd>
            </div>
            <div className="scene-hud__inspector-field">
              <dt>Estação</dt>
              <dd>
                {STATION_LABELS[selectedAgent.station] ??
                  sanitizeText(selectedAgent.station)}
              </dd>
            </div>
            <div className="scene-hud__inspector-field">
              <dt>Ferramenta atual</dt>
              <dd>
                {selectedAgent.currentTool
                  ? sanitizeText(selectedAgent.currentTool)
                  : 'Nenhuma'}
              </dd>
            </div>
            <div className="scene-hud__inspector-field">
              <dt>Duração</dt>
              <dd>{sanitizeText(selectedAgent.durationLabel)}</dd>
            </div>
            <div className="scene-hud__inspector-field scene-hud__inspector-field--wide">
              <dt>Tarefa</dt>
              <dd>
                {sanitizeText(selectedAgent.task) || 'Nenhuma tarefa ativa'}
              </dd>
            </div>
            <div className="scene-hud__inspector-field scene-hud__inspector-field--wide">
              <dt>Último pensamento</dt>
              <dd>
                {sanitizeText(selectedAgent.lastThought) ||
                  'Nenhum pensamento registrado'}
              </dd>
            </div>
          </dl>

          {(() => {
            const cleanSelectedName = sanitizeText(
              selectedAgent.name,
              selectedAgent.isMain ? 'Orin' : 'Subagente',
            )
            const canControlSelected =
              !selectedAgent.isMain &&
              CONTROLLABLE_SUBAGENT_STATUSES.has(selectedAgent.status) &&
              Boolean(onStopAgent || onMessageAgent)
            const isStoppingSelected = stoppingAgentId === selectedAgent.id
            const isConfirmingStop = stopConfirmAgentId === selectedAgent.id
            const isMessagingSelected = messageOpenAgentId === selectedAgent.id
            const isSendingSelectedMsg =
              sendingAgentMessageId === selectedAgent.id
            const currentDraft = agentMessageDrafts[selectedAgent.id] ?? ''
            const trimmedDraft = currentDraft.trim()
            const stopErr = stopErrorByAgent[selectedAgent.id] ?? null
            const msgFeedback = agentFeedbackByAgent[selectedAgent.id] ?? null

            if (!canControlSelected && !stopErr && !msgFeedback) {
              return null
            }

            return (
              <div
                className="scene-hud__agent-controls-area"
                data-testid="scene-agent-controls-area"
              >
                {canControlSelected ? (
                  <div
                    className="scene-hud__agent-control-actions"
                    data-testid="scene-agent-controls"
                  >
                    {onStopAgent ? (
                      <button
                        type="button"
                        className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--danger"
                        data-testid="scene-agent-stop-button"
                        aria-label={`Parar ${cleanSelectedName}`}
                        disabled={isStoppingSelected}
                        onClick={() => {
                          setStopConfirmAgentId(selectedAgent.id)
                          setMessageOpenAgentId(null)
                          setStopErrorByAgent((prev) => ({
                            ...prev,
                            [selectedAgent.id]: null,
                          }))
                        }}
                      >
                        {isStoppingSelected ? 'parando…' : 'Parar'}
                      </button>
                    ) : null}

                    {onMessageAgent ? (
                      <button
                        type="button"
                        className="scene-hud__btn scene-hud__btn--compact"
                        data-testid="scene-agent-message-button"
                        aria-label={`Mensagem para ${cleanSelectedName}`}
                        disabled={isStoppingSelected}
                        onClick={() => {
                          setMessageOpenAgentId(selectedAgent.id)
                          setStopConfirmAgentId(null)
                          setAgentFeedbackByAgent((prev) => ({
                            ...prev,
                            [selectedAgent.id]: null,
                          }))
                        }}
                      >
                        Mensagem
                      </button>
                    ) : null}
                  </div>
                ) : null}

                {canControlSelected && isConfirmingStop && onStopAgent ? (
                  <div
                    className="scene-hud__agent-stop-confirm"
                    data-testid="scene-agent-stop-confirm"
                    onKeyDown={(e) => {
                      if (e.key === 'Escape' && !isStoppingSelected) {
                        e.preventDefault()
                        setStopConfirmAgentId(null)
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
                        data-testid="scene-agent-stop-confirm-button"
                        aria-label={`Parar agora ${cleanSelectedName}`}
                        disabled={isStoppingSelected}
                        onClick={() => void handleConfirmStop(selectedAgent)}
                      >
                        {isStoppingSelected ? 'parando…' : 'Parar agora'}
                      </button>
                      <button
                        type="button"
                        className="scene-hud__btn scene-hud__btn--compact"
                        data-testid="scene-agent-stop-cancel-button"
                        aria-label={`Cancelar parada de ${cleanSelectedName}`}
                        disabled={isStoppingSelected}
                        onClick={() => setStopConfirmAgentId(null)}
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                ) : null}

                {stopErr ? (
                  <p
                    className="scene-hud__composer-error"
                    data-testid="scene-agent-stop-error"
                    role="alert"
                  >
                    {stopErr}
                  </p>
                ) : null}

                {canControlSelected && isMessagingSelected && onMessageAgent ? (
                  <form
                    className="scene-hud__composer-form scene-hud__agent-message-form"
                    data-testid="scene-agent-message-form"
                    onSubmit={(e) => {
                      e.preventDefault()
                      void handleSubmitAgentMessage(selectedAgent)
                    }}
                  >
                    <textarea
                      ref={agentMessageTextareaRef}
                      className="scene-hud__composer-textarea"
                      data-testid="scene-agent-message-input"
                      aria-label={`Mensagem para ${cleanSelectedName}`}
                      rows={2}
                      placeholder={`Envie uma mensagem para ${cleanSelectedName}…`}
                      value={currentDraft}
                      disabled={isSendingSelectedMsg}
                      onChange={(e) => {
                        const val = e.target.value
                        setAgentMessageDrafts((prev) => ({
                          ...prev,
                          [selectedAgent.id]: val,
                        }))
                        if (msgFeedback?.kind === 'error') {
                          setAgentFeedbackByAgent((prev) => ({
                            ...prev,
                            [selectedAgent.id]: null,
                          }))
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                          e.preventDefault()
                          setMessageOpenAgentId(null)
                          return
                        }
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault()
                          void handleSubmitAgentMessage(selectedAgent)
                        }
                      }}
                    />
                    <div className="scene-hud__composer-actions">
                      <button
                        type="button"
                        className="scene-hud__btn scene-hud__btn--compact"
                        data-testid="scene-agent-message-cancel"
                        aria-label={`Cancelar mensagem para ${cleanSelectedName}`}
                        disabled={isSendingSelectedMsg}
                        onClick={() => setMessageOpenAgentId(null)}
                      >
                        Cancelar
                      </button>
                      <button
                        type="submit"
                        className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--active"
                        data-testid="scene-agent-message-submit"
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
                    data-testid="scene-agent-message-success"
                    role="status"
                  >
                    {msgFeedback.text}
                  </p>
                ) : null}

                {msgFeedback?.kind === 'error' ? (
                  <p
                    className="scene-hud__composer-error"
                    data-testid="scene-agent-message-error"
                    role="alert"
                  >
                    {msgFeedback.text}
                  </p>
                ) : null}

                {msgFeedback?.kind === 'not_running' ? (
                  <div
                    className="scene-hud__agent-feedback scene-hud__agent-feedback--not-running"
                    data-testid="scene-agent-not-running"
                    role="alert"
                  >
                    <p className="scene-hud__composer-error">
                      {msgFeedback.text}
                    </p>
                    <button
                      type="button"
                      className="scene-hud__btn scene-hud__btn--compact scene-hud__btn--active"
                      data-testid="scene-agent-resume-button"
                      aria-label={`Pedir ao Orin para retomá-lo (${msgFeedback.agentName})`}
                      onClick={() => {
                        const resumePrompt = `Retome o agente "${msgFeedback.agentName}" (agent_id ${msgFeedback.agentId}) com esta mensagem: ${msgFeedback.resumeMessage}`
                        Promise.resolve(onSendToOrin?.(resumePrompt)).catch(
                          () => undefined,
                        )
                      }}
                    >
                      Pedir ao Orin para retomá-lo
                    </button>
                  </div>
                ) : null}
              </div>
            )
          })()}

          {selectedPending.length > 0 ? (
            <div
              className="scene-hud__inspector-pending"
              data-testid="scene-agent-pending"
            >
              {selectedPending.map((item) => (
                <div key={item.id} className="scene-hud__pending-row">
                  <p className="scene-hud__pending-item">
                    Esperando você: {sanitizeText(item.summary)}
                  </p>
                  <button
                    type="button"
                    className="scene-hud__btn scene-hud__btn--compact"
                    data-testid={`scene-agent-pending-respond-${sanitizeText(item.id)}`}
                    onClick={handleFocusPendingSection}
                  >
                    Responder
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className="scene-hud__inspector-activities">
            <h4 className="scene-hud__inspector-subtitle">Atividades</h4>
            {selectedAgent.activities.length === 0 ? (
              <p className="scene-hud__empty">
                Nenhuma atividade registrada para este personagem.
              </p>
            ) : (
              <ul
                className="scene-hud__activity-list"
                data-testid={`scene-agent-events-${sanitizeText(selectedAgent.id)}`}
              >
                {selectedAgent.activities.map((act) => (
                  <li key={act.id} className="scene-hud__activity-item">
                    <span className="scene-hud__activity-label">
                      {sanitizeText(act.label)}
                    </span>
                    {act.timestamp || act.detail ? (
                      <span className="scene-hud__activity-time">
                        {sanitizeText(act.timestamp || act.detail)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      ) : null}

      {/* Pending decision card */}
      {pendingDecision ? (
        <section
          className="scene-hud__card scene-decision-card"
          data-testid="scene-decision-card"
        >
          <div className="scene-decision-card__eyebrow">
            <span aria-hidden="true">⚠️</span>
            <span>Preciso de uma decisão</span>
          </div>
          <h3 className="scene-decision-card__question">
            {sanitizeText(pendingDecision.question)}
          </h3>
          {pendingDecision.context ? (
            <p className="scene-decision-card__context">
              {sanitizeText(pendingDecision.context)}
            </p>
          ) : null}
          <div className="scene-decision-card__actions">
            {pendingDecision.options.map((option) => (
              <button
                key={option.id}
                type="button"
                className={
                  option.primary
                    ? 'scene-decision-card__btn scene-decision-card__btn--primary'
                    : 'scene-decision-card__btn'
                }
                onClick={() =>
                  onResolveDecision?.(
                    sanitizeText(option.value),
                    pendingDecision,
                  )
                }
              >
                {sanitizeText(option.label)}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {/* Final deliverable card */}
      <section
        className="scene-hud__card scene-deliverable-card"
        data-testid="scene-final-deliverable-card"
      >
        <div className="scene-hud__section-header">
          <h3 className="scene-hud__section-title">Entregável final</h3>
        </div>

        {finalDeliverable ? (
          <div className="scene-deliverable-card__box">
            <div className="scene-deliverable-card__info">
              <span className="scene-deliverable-card__filename">
                {sanitizeText(finalDeliverable.fileName)}
              </span>
              <span
                className="scene-deliverable-card__status"
                data-testid="scene-deliverable-status"
              >
                <span aria-hidden="true">
                  {finalDeliverable.status === 'ready' ? '●' : '◌'}
                </span>
                <span>
                  {sanitizeText(finalDeliverable.statusLabel) ||
                    (finalDeliverable.status === 'ready'
                      ? 'Concluído'
                      : 'Em preparação')}
                </span>
              </span>
            </div>

            {finalDeliverable.status === 'ready' ? (
              <button
                type="button"
                className="scene-hud__btn scene-hud__btn--compact scene-deliverable-card__open-btn"
                data-testid="scene-deliverable-open-button"
                onClick={() => onOpenDeliverable?.(finalDeliverable)}
              >
                Abrir ↗
              </button>
            ) : null}
          </div>
        ) : (
          <p className="scene-hud__empty" data-testid="scene-deliverable-empty">
            Nenhum entregável gerado ainda
          </p>
        )}
      </section>

      {/* Recent activities footer */}
      <footer
        className="scene-hud__card scene-recent-activity"
        data-testid="scene-recent-activity"
      >
        <div className="scene-hud__section-header">
          <h3 className="scene-hud__section-title">Atividade recente</h3>
          <button
            type="button"
            className="scene-recent-activity__link"
            data-testid="scene-view-all-activities"
            onClick={() => handleViewAll?.()}
          >
            <span>Ver todas as atividades</span>
            <span aria-hidden="true">›</span>
          </button>
        </div>

        {recentActivities.length === 0 ? (
          <p
            className="scene-hud__empty"
            data-testid="scene-recent-activity-empty"
          >
            Nenhuma atividade registrada ainda.
          </p>
        ) : (
          <ol className="scene-recent-activity__list">
            {recentActivities.map((item) => (
              <li key={item.id} className="scene-recent-activity__item">
                <span
                  className="scene-recent-activity__dot"
                  aria-hidden="true"
                />
                <time className="scene-recent-activity__time">
                  {sanitizeText(item.time)}
                </time>
                <span className="scene-recent-activity__text">
                  {sanitizeText(item.text)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </footer>
    </div>
  )
}
export default SceneHud
