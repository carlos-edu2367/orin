import type { ConversationActivityEvent, TodoItem } from './activityTypes'

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

export interface TeamStepperStep {
  stepNumber: number
  label: string
  completed: boolean
}

export interface TeamStepperData {
  total: number
  completed: number
  statusText: string
  steps: TeamStepperStep[]
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

export function deriveRecentActivities(
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

export function resolvePendingDecision(
  pendingDecision?: TeamPendingDecision | null,
  events?: ConversationActivityEvent[],
): TeamPendingDecision | null {
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
}

export function resolveFinalDeliverable(
  finalDeliverable?: TeamFinalDeliverable | null,
  events?: ConversationActivityEvent[],
): TeamFinalDeliverable | null {
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
  const artifactEvent = events?.find((ev) => {
    if (ev?.type !== 'artifact.created') return false
    const candidate = sanitizeText(ev.filePath || ev.path || ev.title)
      .replace(/\\/g, '/')
      .replace(/^\.\//, '')
    if (
      !candidate ||
      candidate === '.orin' ||
      candidate.startsWith('.orin/') ||
      candidate.includes('/.orin/') ||
      candidate === 'uploads' ||
      candidate.startsWith('uploads/') ||
      candidate.includes('/uploads/')
    ) {
      return false
    }
    return true
  })
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
}

export function deriveStepperData({
  todos,
  completedStepsCount,
  totalStepsCount,
  running = true,
}: {
  todos?: TodoItem[]
  completedStepsCount?: number
  totalStepsCount?: number
  running?: boolean
} = {}): TeamStepperData {
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
  const isAllCompleted = !running || (total > 0 && completed >= total)
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
}
