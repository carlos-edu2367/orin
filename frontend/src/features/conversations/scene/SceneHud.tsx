import {
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type JSX,
  type KeyboardEvent,
} from 'react'
import { Link, useInRouterContext } from 'react-router-dom'
import type { ConversationActivityEvent } from '../activityTypes'
import {
  sanitizeText,
  type TeamFinalDeliverable,
  type TeamPendingDecision,
  type TeamRecentActivityEntry,
  type TeamStepperData,
} from '../teamActivityModel'
import {
  SceneAgentActions,
  useSceneAgentControls,
  type SceneAgentControlResult,
  type SceneAgentControlsState,
} from './SceneAgentActions'
import {
  ScenePendingCards,
  type SceneActionHandlers,
} from './ScenePendingCards'
import { STATION_LABELS } from './sceneStations'
import type { SceneAgent, SceneState } from './sceneTypes'

export type { SceneAgentControlResult } from './SceneAgentActions'

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
  agentControls?: SceneAgentControlsState
  selectedAgentId: string | null
  onSelectAgent: (agentId: string | null) => void
  onOpenAgentModal?: (agentId: string) => void
  modalOpenAgentId?: string | null
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
  agentControls: externalControls,
  selectedAgentId,
  onSelectAgent,
  onOpenAgentModal,
  modalOpenAgentId = null,
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
  const inRouter = useInRouterContext()
  const [messageText, setMessageText] = useState('')
  const [sendingMessage, setSendingMessage] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  const internalControls = useSceneAgentControls({
    onStopAgent,
    onMessageAgent,
    onSendToOrin,
  })
  const controls = externalControls ?? internalControls

  const subagents = scene.agents.filter((ag) => !ag.isMain)
  const selectedAgent: SceneAgent | undefined =
    selectedAgentId !== null
      ? scene.agents.find((ag) => ag.id === selectedAgentId)
      : undefined
  const selectedPending = selectedAgent
    ? scene.pending.filter((item) => item.agentId === selectedAgent.id)
    : []
  const hudVisiblePending = modalOpenAgentId
    ? scene.pending.filter((item) => item.agentId !== modalOpenAgentId)
    : scene.pending

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
        pending={hudVisiblePending}
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
          <div className="scene-hud__section-header-right">
            <span className="scene-hud__section-meta">
              {scene.agents.length === 1
                ? '1 agente'
                : `${scene.agents.length} agentes`}
            </span>
            {inRouter ? (
              <Link
                to="/settings/characters"
                className="scene-hud__customize-link"
                data-testid="scene-customize-characters-link"
              >
                Personalizar personagens
              </Link>
            ) : (
              <a
                href="/settings/characters"
                className="scene-hud__customize-link"
                data-testid="scene-customize-characters-link"
              >
                Personalizar personagens
              </a>
            )}
          </div>
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
            const cleanCharName = !agent.isMain
              ? sanitizeText(agent.characterName)
              : ''
            const hasDistinctCharacter =
              Boolean(cleanCharName) &&
              cleanCharName.toLowerCase() !== cleanName.toLowerCase()
            const primaryName = hasDistinctCharacter ? cleanCharName : cleanName
            const cleanStatus =
              controls.stoppingAgentId === agent.id
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
                  <span className="scene-hud__agent-name">{primaryName}</span>
                  {hasDistinctCharacter ? (
                    <span
                      className="scene-hud__agent-tag"
                      data-testid={`scene-agent-tech-tag-${sanitizeText(agent.id)}`}
                    >
                      {cleanName}
                    </span>
                  ) : null}
                  {agent.subagentType &&
                  sanitizeText(agent.subagentType).toLowerCase() !==
                    cleanName.toLowerCase() ? (
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
          aria-label={
            !selectedAgent.isMain &&
            selectedAgent.characterName &&
            sanitizeText(selectedAgent.characterName).toLowerCase() !==
              sanitizeText(selectedAgent.name).toLowerCase()
              ? `Detalhes de ${sanitizeText(selectedAgent.characterName)} (${sanitizeText(selectedAgent.name)})`
              : `Detalhes de ${sanitizeText(selectedAgent.name)}`
          }
        >
          <div className="scene-hud__inspector-header">
            <div className="scene-hud__inspector-title-group">
              <h3 className="scene-hud__inspector-name">
                {!selectedAgent.isMain && selectedAgent.characterName
                  ? sanitizeText(selectedAgent.characterName)
                  : sanitizeText(selectedAgent.name)}
              </h3>
              {!selectedAgent.isMain &&
              selectedAgent.characterName &&
              sanitizeText(selectedAgent.characterName).toLowerCase() !==
                sanitizeText(selectedAgent.name).toLowerCase() ? (
                <span className="scene-hud__inspector-tag">
                  {sanitizeText(selectedAgent.name)}
                </span>
              ) : null}
              {selectedAgent.subagentType ? (
                <span className="scene-hud__inspector-badge">
                  {sanitizeText(selectedAgent.subagentType)}
                </span>
              ) : null}
            </div>
            <div className="scene-hud__inspector-actions">
              {onOpenAgentModal ? (
                <button
                  type="button"
                  className="scene-hud__btn scene-hud__btn--compact"
                  data-testid="scene-agent-open-modal"
                  onClick={() => onOpenAgentModal(selectedAgent.id)}
                >
                  Abrir detalhes
                </button>
              ) : null}
              <button
                type="button"
                className="scene-hud__btn scene-hud__btn--compact"
                data-testid="scene-agent-panel-close"
                onClick={() => onSelectAgent(null)}
              >
                Fechar painel
              </button>
            </div>
          </div>

          <dl className="scene-hud__inspector-grid">
            <div className="scene-hud__inspector-field">
              <dt>Estado</dt>
              <dd>
                {controls.stoppingAgentId === selectedAgent.id
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

          <SceneAgentActions
            agent={selectedAgent}
            controls={controls}
            onStopAgent={onStopAgent}
            onMessageAgent={onMessageAgent}
            onSendToOrin={onSendToOrin}
            testIdPrefix={modalOpenAgentId ? 'scene-hud-agent' : 'scene-agent'}
            variant="hud"
          />

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
