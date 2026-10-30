import { useMemo, useState } from 'react'
import type { ConversationActivityEvent, TodoItem } from './activityTypes'
import './teamActivity.css'

export type TeamAgentRoleIcon = 'search' | 'chart' | 'document'

export type TeamAgentStatus = 'running' | 'queued' | 'completed' | 'failed'

export interface TeamAgentActivityItem {
  id: string
  label: string
  detail?: string
  status?: TeamAgentStatus
  timestamp?: string
}

export interface TeamAgentCardData {
  id: string
  roleName: string
  currentTask: string
  status: TeamAgentStatus
  durationLabel: string
  icon: TeamAgentRoleIcon
  activities: TeamAgentActivityItem[]
}

export interface TeamDecisionOption {
  id: string
  label: string
  value: string
  primary?: boolean
}

export interface TeamPendingDecision {
  id?: string
  question: string
  context?: string
  options: TeamDecisionOption[]
}

export interface TeamFinalDeliverable {
  fileName: string
  path?: string
  status: 'preparing' | 'ready'
  statusLabel?: string
}

export interface TeamRecentActivityEntry {
  id: string
  time: string
  text: string
}

export interface TeamActivityViewProps {
  events?: ConversationActivityEvent[]
  todos?: TodoItem[]
  agents?: TeamAgentCardData[]
  pendingDecision?: TeamPendingDecision | null
  finalDeliverable?: TeamFinalDeliverable | null
  recentActivities?: TeamRecentActivityEntry[]
  paused?: boolean
  running?: boolean
  completedStepsCount?: number
  totalStepsCount?: number
  userDisplayName?: string
  onPauseTeam?: () => void
  onBackToChat?: () => void
  onResolveDecision?: (optionValue: string, decision?: TeamPendingDecision) => void
  onOpenDeliverable?: (deliverable: TeamFinalDeliverable) => void
  onViewAllActivities?: () => void
  onOpenOverflowMenu?: () => void
}

export function sanitizeText(value: unknown, fallback = ''): string {
  if (typeof value !== 'string') return fallback
  const withoutControls = Array.from(value)
    .filter((ch) => {
      const code = ch.charCodeAt(0)
      return code > 0x1f && code !== 0x7f
    })
    .join('')
  const cleaned = withoutControls.replace(/\s+/g, ' ').trim()
  return cleaned || fallback
}

function sanitizeAgentId(value: unknown, fallback = 'especialista'): string {
  const text = sanitizeText(value)
  const slug = text.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  return slug || fallback
}

export function formatTimeHHMM(raw?: string, fallback = '--:--'): string {
  const cleaned = sanitizeText(raw)
  if (!cleaned) return fallback
  if (/^\d{2}:\d{2}$/.test(cleaned)) return cleaned
  const ms = Date.parse(cleaned)
  if (Number.isNaN(ms)) return fallback
  const date = new Date(ms)
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}

function inferRoleIcon(roleOrId: string): TeamAgentRoleIcon {
  const lower = roleOrId.toLowerCase()
  if (lower.includes('analis') || lower.includes('chart') || lower.includes('metric') || lower.includes('data')) {
    return 'chart'
  }
  if (lower.includes('revis') || lower.includes('review') || lower.includes('doc') || lower.includes('redat')) {
    return 'document'
  }
  return 'search'
}

function inferRoleName(agentId: string): string {
  const lower = agentId.toLowerCase()
  if (lower.includes('pesquis') || lower.includes('search') || lower.includes('research')) return 'Pesquisador'
  if (lower.includes('analis') || lower.includes('analyst')) return 'Analista'
  if (lower.includes('revis') || lower.includes('review')) return 'Revisor'
  if (lower === 'especialista') return 'Especialista'
  return agentId.charAt(0).toUpperCase() + agentId.slice(1)
}

function statusLabelFor(status: TeamAgentStatus): string {
  switch (status) {
    case 'running':
      return 'Em andamento'
    case 'queued':
      return 'Na fila'
    case 'completed':
      return 'Concluído'
    case 'failed':
      return 'Falhou'
  }
}

function deriveAgentsFromEvents(events?: ConversationActivityEvent[]): TeamAgentCardData[] {
  if (!events || events.length === 0) return []

  const relevantEvents = events.filter((ev) => {
    if (!ev || typeof ev !== 'object') return false
    const cleanId = sanitizeText(ev.agentId)
    return (
      ev.kind === 'tool' ||
      ev.kind === 'agent' ||
      Boolean(ev.parentAgentId) ||
      Boolean(sanitizeText(ev.agentName)) ||
      (Boolean(cleanId) && cleanId !== 'main' && cleanId !== 'assistant')
    )
  })

  if (relevantEvents.length === 0) return []

  const grouped = new Map<string, ConversationActivityEvent[]>()
  for (const ev of relevantEvents) {
    const id = sanitizeAgentId(ev.agentId, 'especialista')
    const bucket = grouped.get(id) ?? []
    bucket.push(ev)
    grouped.set(id, bucket)
  }

  const cards: TeamAgentCardData[] = []
  for (const [id, agentEvents] of grouped.entries()) {
    let roleName = ''
    let currentTask = ''
    const validTimestamps: number[] = []
    let totalElapsedMs = 0
    const activities: TeamAgentActivityItem[] = []

    for (let i = 0; i < agentEvents.length; i += 1) {
      const ev = agentEvents[i]
      const candidateName = sanitizeText(ev.agentName)
      if (candidateName) roleName = candidateName

      const candidateSummary = sanitizeText(
        ev.summary || ev.description || ev.title || ev.toolName,
      )
      if (candidateSummary) {
        currentTask = candidateSummary
        activities.push({
          id: sanitizeText(ev.eventId, `${id}-ev-${i}`),
          label: candidateSummary,
          detail: sanitizeText(ev.toolName || ev.path || ''),
          timestamp: ev.occurredAt ? formatTimeHHMM(ev.occurredAt, '') : undefined,
        })
      }

      if (ev.occurredAt) {
        const parsed = Date.parse(ev.occurredAt)
        if (!Number.isNaN(parsed)) validTimestamps.push(parsed)
      }
      if (typeof ev.elapsedMs === 'number' && Number.isFinite(ev.elapsedMs) && ev.elapsedMs > 0) {
        totalElapsedMs += ev.elapsedMs
      }
    }

    const finalRoleName = roleName || inferRoleName(id)
    const finalTask = currentTask || 'Processando etapa'
    const lastEvent = agentEvents[agentEvents.length - 1]

    let status: TeamAgentStatus = 'running'
    if (lastEvent) {
      if (lastEvent.state === 'completed' || lastEvent.type === 'delegation.completed') {
        status = 'completed'
      } else if (lastEvent.state === 'queued' || lastEvent.type === 'delegation.waiting') {
        status = 'queued'
      } else if (lastEvent.state === 'failed' || lastEvent.type === 'delegation.failed') {
        status = 'failed'
      } else {
        status = 'running'
      }
    }

    let durationLabel = '1 min'
    if (validTimestamps.length >= 2) {
      const diffMs = Math.max(...validTimestamps) - Math.min(...validTimestamps)
      const mins = Math.max(1, Math.round(diffMs / 60000))
      durationLabel = `${mins} min`
    } else if (totalElapsedMs > 0) {
      const mins = Math.max(1, Math.round(totalElapsedMs / 60000))
      durationLabel = `${mins} min`
    }

    if (activities.length === 0) {
      activities.push({
        id: `${id}-default-act`,
        label: finalTask,
      })
    }

    cards.push({
      id,
      roleName: finalRoleName,
      currentTask: finalTask,
      status,
      durationLabel,
      icon: inferRoleIcon(`${finalRoleName} ${id}`),
      activities,
    })
  }

  return cards
}

function deriveRecentActivities(
  events?: ConversationActivityEvent[],
  explicitRecent?: TeamRecentActivityEntry[],
): TeamRecentActivityEntry[] {
  if (explicitRecent && explicitRecent.length > 0) {
    return explicitRecent.map((item, idx) => ({
      id: sanitizeText(item.id, `rec-${idx}`),
      time: formatTimeHHMM(item.time, '--:--'),
      text: sanitizeText(item.text, 'Atividade registrada'),
    }))
  }

  if (!events || events.length === 0) {
    return []
  }

  const entries: TeamRecentActivityEntry[] = []
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i]
    if (!ev || typeof ev !== 'object') continue
    const summary = sanitizeText(ev.summary || ev.description || ev.title)
    if (!summary) continue
    const agentName = sanitizeText(ev.agentName)
    const text =
      agentName && !summary.toLowerCase().startsWith(agentName.toLowerCase())
        ? `${agentName}: ${summary}`
        : summary
    entries.push({
      id: sanitizeText(ev.eventId, `ev-rec-${i}`),
      time: formatTimeHHMM(ev.occurredAt, '--:--'),
      text,
    })
  }

  return entries.slice(0, 6)
}

function AgentRoleSvg({ icon }: { icon: TeamAgentRoleIcon }) {
  if (icon === 'chart') {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="4" y="12" width="4" height="8" rx="1" stroke="currentColor" strokeWidth="1.8" />
        <rect x="10" y="6" width="4" height="14" rx="1" stroke="currentColor" strokeWidth="1.8" />
        <rect x="16" y="9" width="4" height="11" rx="1" stroke="currentColor" strokeWidth="1.8" />
      </svg>
    )
  }
  if (icon === 'document') {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path d="M14 3v5h5M9 13h6M9 17h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    )
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.8" />
      <path d="m16 16 4.2 4.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}

export function TeamActivityView({
  events,
  todos,
  agents,
  pendingDecision,
  finalDeliverable,
  recentActivities,
  paused = false,
  completedStepsCount,
  totalStepsCount,
  onPauseTeam,
  onBackToChat,
  onResolveDecision,
  onOpenDeliverable,
  onViewAllActivities,
  onOpenOverflowMenu,
}: TeamActivityViewProps) {
  const [expandedAgentIds, setExpandedAgentIds] = useState<Record<string, boolean>>({})

  const stepperData = useMemo(() => {
    const validTodos = Array.isArray(todos) ? todos.filter((t) => t && typeof t === 'object') : []
    const total =
      typeof totalStepsCount === 'number' && totalStepsCount >= 0
        ? totalStepsCount
        : validTodos.length > 0
          ? validTodos.length
          : 0

    const completed =
      typeof completedStepsCount === 'number' && completedStepsCount >= 0
        ? Math.min(total, completedStepsCount)
        : validTodos.length > 0
          ? validTodos.filter((t) => t.status === 'completed').length
          : 0

    const defaultStepNames = ['Entender', 'Dividir', 'Executar', 'Revisar', 'Entregar']
    const isAllCompleted = total > 0 && completed >= total
    const steps = Array.from({ length: total }, (_, idx) => {
      const stepNumber = idx + 1
      const stepCompleted =
        validTodos[idx] !== undefined
          ? validTodos[idx].status === 'completed'
          : stepNumber <= completed
      const label = sanitizeText(validTodos[idx]?.content, defaultStepNames[idx] || `Etapa ${stepNumber}`)
      return {
        stepNumber,
        label,
        completed: stepCompleted,
      }
    })

    return {
      total,
      completed,
      statusText: isAllCompleted ? 'Concluído' : 'Em andamento',
      steps,
    }
  }, [todos, completedStepsCount, totalStepsCount])

  const resolvedAgents = useMemo(() => {
    if (agents && agents.length > 0) {
      return agents.map((agent, idx) => ({
        ...agent,
        id: sanitizeAgentId(agent.id, `especialista-${idx + 1}`),
        roleName: sanitizeText(agent.roleName, 'Especialista'),
        currentTask: sanitizeText(agent.currentTask, 'Processando etapa'),
        durationLabel: sanitizeText(agent.durationLabel, '1 min'),
      }))
    }
    return deriveAgentsFromEvents(events)
  }, [agents, events])

  const resolvedDecision = useMemo<TeamPendingDecision | null>(() => {
    if (pendingDecision === null) return null
    if (pendingDecision !== undefined) {
      return {
        ...pendingDecision,
        question: sanitizeText(pendingDecision.question, 'Decisão pendente'),
        context: sanitizeText(pendingDecision.context, ''),
      }
    }
    const questionEvent = events?.find((ev) => Array.isArray(ev?.questions) && ev.questions.length > 0)
    if (questionEvent && questionEvent.questions?.[0]) {
      const q = questionEvent.questions[0]
      return {
        id: sanitizeText(q.id, 'decision-event'),
        question: sanitizeText(q.question, 'Decisão pendente'),
        context: sanitizeText(q.header || questionEvent.summary, ''),
        options:
          Array.isArray(q.options) && q.options.length > 0
            ? q.options.map((opt, idx) => ({
                id: sanitizeText(opt.id, `opt-${idx}`),
                label: sanitizeText(opt.label, `Opção ${idx + 1}`),
                value: sanitizeText(opt.label || opt.id, `opt-${idx}`),
                primary: idx === 0,
              }))
            : [],
      }
    }
    return null
  }, [pendingDecision, events])

  const resolvedDeliverable = useMemo<TeamFinalDeliverable | null>(() => {
    if (finalDeliverable === null) return null
    if (finalDeliverable) {
      return {
        fileName: sanitizeText(finalDeliverable.fileName, 'Entregável'),
        path: finalDeliverable.path ? sanitizeText(finalDeliverable.path) : undefined,
        status: finalDeliverable.status,
        statusLabel:
          sanitizeText(finalDeliverable.statusLabel) ||
          (finalDeliverable.status === 'ready' ? 'Concluído' : 'Em preparação'),
      }
    }
    const artifactEvent = events?.find((ev) => ev?.type === 'artifact.created')
    if (artifactEvent) {
      const rawPath = sanitizeText(
        artifactEvent.filePath || artifactEvent.path || artifactEvent.title,
        'Entregável',
      )
      const fileName = rawPath.split(/[\\/]/).pop() || 'Entregável'
      return {
        fileName,
        path: rawPath,
        status: 'ready',
        statusLabel: 'Concluído',
      }
    }
    return null
  }, [finalDeliverable, events])

  const resolvedRecentActivities = useMemo(
    () => deriveRecentActivities(events, recentActivities),
    [events, recentActivities],
  )

  const toggleAgentExpanded = (agentId: string) => {
    setExpandedAgentIds((prev) => ({
      ...prev,
      [agentId]: !prev[agentId],
    }))
  }

  return (
    <section
      className="team-activity"
      data-testid="team-activity-view"
      aria-label="Equipe em atividade"
    >
      {/* 1. Hero & Controls */}
      <header className="team-activity__hero">
        <div className="team-activity__hero-copy">
          <h2 className="team-activity__title">Uma equipe, um resultado.</h2>
          <p className="team-activity__subtitle">
            O Orin coordena as etapas e reúne tudo para você.
          </p>
        </div>

        <div className="team-activity__hero-controls">
          {onBackToChat ? (
            <button
              type="button"
              className="team-activity__btn"
              data-testid="team-back-to-chat-button"
              onClick={onBackToChat}
            >
              Voltar à conversa
            </button>
          ) : null}

          <button
            type="button"
            className="team-activity__btn"
            data-testid="team-pause-button"
            onClick={() => onPauseTeam?.()}
          >
            <span aria-hidden="true">{paused ? '▶' : '⏸'}</span>
            <span>{paused ? 'Retomar equipe' : 'Pausar equipe'}</span>
          </button>

          <button
            type="button"
            className="team-activity__btn team-activity__btn--icon"
            data-testid="team-overflow-button"
            aria-label="Mais opções da equipe"
            onClick={() => onOpenOverflowMenu?.()}
          >
            •••
          </button>
        </div>
      </header>

      {/* 2. Horizontal Stepper */}
      <div className="team-stepper" data-testid="team-stepper">
        <div className="team-stepper__track" role="list" aria-label="Progresso das etapas da equipe">
          {stepperData.steps.map((step, idx) => {
            const isLast = idx === stepperData.steps.length - 1
            const nextStep = stepperData.steps[idx + 1]
            const connectorActive = step.completed && Boolean(nextStep?.completed || idx < stepperData.completed)
            return (
              <div
                key={step.stepNumber}
                className="team-stepper__item"
                role="listitem"
                aria-label={`Etapa ${step.stepNumber}: ${step.label} (${step.completed ? 'Concluída' : 'Pendente'})`}
                data-testid={`team-step-${step.stepNumber}`}
                data-completed={step.completed ? 'true' : 'false'}
              >
                <span
                  className={
                    step.completed
                      ? 'team-stepper__node team-stepper__node--completed'
                      : 'team-stepper__node'
                  }
                >
                  {step.completed ? '✓' : String(step.stepNumber)}
                </span>
                {!isLast ? (
                  <span
                    className={
                      connectorActive
                        ? 'team-stepper__connector team-stepper__connector--active'
                        : 'team-stepper__connector'
                    }
                    aria-hidden="true"
                  />
                ) : null}
              </div>
            )
          })}
        </div>

        <div className="team-stepper__summary">
          <span className="team-stepper__count">
            {stepperData.completed} de {stepperData.total} etapas
          </span>
          <span className="team-stepper__state">{stepperData.statusText}</span>
        </div>
      </div>

      {/* 3. Main 2-Column Grid */}
      <div className="team-activity__grid">
        {/* Left Column: Specialist Cards */}
        <div className="team-agents-list" data-testid="team-agents-list">
          {resolvedAgents.length === 0 ? (
            <p className="team-empty-state" data-testid="team-agents-empty">
              Nenhum subagente em execução neste momento.
            </p>
          ) : (
            resolvedAgents.map((agent) => {
              const isExpanded = Boolean(expandedAgentIds[agent.id])
              return (
                <article
                  key={agent.id}
                  className="team-agent-card"
                  data-testid={`team-agent-card-${agent.id}`}
                >
                  <div className="team-agent-card__main">
                    <div className="team-agent-card__left">
                      <div className="team-agent-card__icon">
                        <AgentRoleSvg icon={agent.icon} />
                      </div>
                      <div className="team-agent-card__info">
                        <h3 className="team-agent-card__role">{agent.roleName}</h3>
                        <p className="team-agent-card__task">{agent.currentTask}</p>
                        <span
                          className={`team-agent-card__status team-agent-card__status--${agent.status}`}
                        >
                          <span className="team-agent-card__status-dot" aria-hidden="true" />
                          <span>{statusLabelFor(agent.status)}</span>
                        </span>
                      </div>
                    </div>

                    <div className="team-agent-card__right">
                      <span className="team-agent-card__duration" title="Duração da atividade">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
                          <path
                            d="M12 7v5l3 2"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                          />
                        </svg>
                        <span>{agent.durationLabel}</span>
                      </span>

                      <button
                        type="button"
                        className="team-agent-card__toggle"
                        data-testid={`team-agent-toggle-${agent.id}`}
                        aria-expanded={isExpanded ? 'true' : 'false'}
                        onClick={() => toggleAgentExpanded(agent.id)}
                      >
                        <span>Ver atividade</span>
                        <span aria-hidden="true">{isExpanded ? '⌃' : '⌄'}</span>
                      </button>
                    </div>
                  </div>

                  {isExpanded ? (
                    <ul
                      className="team-agent-card__events"
                      data-testid={`team-agent-events-${agent.id}`}
                    >
                      {agent.activities.map((act) => (
                        <li key={act.id} className="team-agent-card__event-item">
                          <span className="team-agent-card__event-label">{act.label}</span>
                          {act.detail || act.timestamp ? (
                            <span className="team-agent-card__event-detail">
                              {act.timestamp || act.detail}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </article>
              )
            })
          )}
        </div>

        {/* Right Column: Supervision & Final Deliverable */}
        <aside className="team-side-rail" data-testid="team-side-rail">
          {resolvedDecision ? (
            <section className="team-decision-card" data-testid="team-decision-card">
              <div className="team-decision-card__eyebrow">
                <span aria-hidden="true">⚠️</span>
                <span>Preciso de uma decisão</span>
              </div>
              <h3 className="team-decision-card__question">{resolvedDecision.question}</h3>
              {resolvedDecision.context ? (
                <p className="team-decision-card__context">{resolvedDecision.context}</p>
              ) : null}
              <div className="team-decision-card__actions">
                {resolvedDecision.options.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className={
                      option.primary
                        ? 'team-decision-card__btn team-decision-card__btn--primary'
                        : 'team-decision-card__btn'
                    }
                    onClick={() => onResolveDecision?.(option.value, resolvedDecision)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          <section
            className="team-deliverable-card"
            data-testid="team-final-deliverable-card"
          >
            <div className="team-deliverable-card__header">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                />
                <path
                  d="M14 3v5h5M9 13h6M9 17h4"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
              <span>Entregável final</span>
            </div>

            {resolvedDeliverable ? (
              <div className="team-deliverable-card__box">
                <div className="team-deliverable-card__file">
                  <span className="team-deliverable-card__file-icon" aria-hidden="true">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                      <path
                        d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"
                        stroke="currentColor"
                        strokeWidth="1.7"
                        strokeLinejoin="round"
                      />
                      <path
                        d="M14 3v5h5M9 13h6M9 17h4"
                        stroke="currentColor"
                        strokeWidth="1.7"
                        strokeLinecap="round"
                      />
                    </svg>
                  </span>
                  <div className="team-deliverable-card__file-info">
                    <span className="team-deliverable-card__filename">
                      {resolvedDeliverable.fileName}
                    </span>
                    <span
                      className="team-deliverable-card__status"
                      data-testid="team-deliverable-status"
                    >
                      <span aria-hidden="true">
                        {resolvedDeliverable.status === 'ready' ? '●' : '◌'}
                      </span>
                      <span>
                        {resolvedDeliverable.statusLabel ||
                          (resolvedDeliverable.status === 'ready' ? 'Concluído' : 'Em preparação')}
                      </span>
                    </span>
                  </div>
                </div>

                {resolvedDeliverable.status === 'ready' ? (
                  <button
                    type="button"
                    className="team-deliverable-card__open-btn"
                    data-testid="team-deliverable-open-button"
                    onClick={() => onOpenDeliverable?.(resolvedDeliverable)}
                  >
                    Abrir ↗
                  </button>
                ) : null}
              </div>
            ) : (
              <p className="team-empty-state" data-testid="team-deliverable-empty">
                Nenhum entregável gerado ainda
              </p>
            )}
          </section>
        </aside>
      </div>

      {/* 4. Footer: Recent Activity Timeline */}
      <footer className="team-recent-activity" data-testid="team-recent-activity">
        <div className="team-recent-activity__header">
          <h3 className="team-recent-activity__title">Atividade recente</h3>
          <button
            type="button"
            className="team-recent-activity__link"
            data-testid="team-view-all-activities"
            onClick={() => onViewAllActivities?.()}
          >
            <span>Ver todas as atividades</span>
            <span aria-hidden="true">›</span>
          </button>
        </div>

        {resolvedRecentActivities.length === 0 ? (
          <p className="team-empty-state" data-testid="team-recent-activity-empty">
            Nenhuma atividade registrada ainda.
          </p>
        ) : (
          <ol className="team-recent-activity__list">
            {resolvedRecentActivities.map((item) => (
              <li key={item.id} className="team-recent-activity__item">
                <span className="team-recent-activity__dot" aria-hidden="true" />
                <time className="team-recent-activity__time">{item.time}</time>
                <span className="team-recent-activity__text">{item.text}</span>
              </li>
            ))}
          </ol>
        )}
      </footer>
    </section>
  )
}
export default TeamActivityView
