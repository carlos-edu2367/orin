import type { ActivityGroup, ActivityState, ConversationActivityEvent } from './activityTypes'

/**
 * Collapse the raw event log into the short, human lines the chat shows.
 *
 * Two rules do the work: a `tool.started` is dropped once its `tool.finished`
 * arrives (the started event only exists so a running tool has a live state), and
 * consecutive finished tools of the same family collapse into one row — "Leu 8
 * arquivos" instead of eight lines. The individual events stay attached so the
 * card can expand into the detail.
 */
export function summarizeActivities(events: ConversationActivityEvent[]): ActivityGroup[] {
  const groups: ActivityGroup[] = []
  const settled = new Set(
    events.filter((event) => event.type === 'tool.finished' && event.invocationId).map((event) => event.invocationId as string),
  )

  for (const event of events) {
    if (!isRenderable(event, settled)) continue
    const key = groupingKey(event)
    const previous = groups[groups.length - 1]
    if (previous && previous.id === key && previous.kind === event.kind) {
      previous.count += 1
      previous.events.push(event)
      previous.state = event.state
      previous.failed = previous.failed || event.state === 'failed'
      previous.label = groupLabel(previous)
      continue
    }
    const group: ActivityGroup = {
      id: key,
      kind: event.kind,
      state: event.state,
      label: '',
      count: 1,
      events: [event],
      agentId: event.agentId,
      agentName: event.agentName,
      failed: event.state === 'failed',
    }
    group.label = groupLabel(group)
    groups.push(group)
  }
  return groups
}

export function isRenderable(event: ConversationActivityEvent, settled: Set<string>): boolean {
  if (event.type.startsWith('context.')) return false
  if (event.kind === 'message') return false
  // A lifecycle "turn started/working" line adds nothing next to the tool rows
  // that follow it, so only terminal lifecycle states earn a row.
  if (event.kind === 'lifecycle' && event.type === 'turn.started') return false
  // `turn.waiting_user` always accompanies an `ask_user` tool call in the same
  // turn, and the interactive question card built from that tool call already
  // shows this state (and, unlike this static line, keeps showing it correctly
  // once answered). Rendering both leaves a second, stale "waiting for you" row
  // sitting right after a card that has already moved on.
  if (event.type === 'turn.waiting_user') return false
  if (event.type === 'tool.requested') return false
  if (event.type === 'tool.started' && event.invocationId && settled.has(event.invocationId)) return false
  // The agent tools already emit their own agent.* events, which read better.
  if (event.kind === 'agent' && event.type.startsWith('tool.') ) return false
  return true
}

export function groupingKey(event: ConversationActivityEvent): string {
  // Scoped by turnId so a run of same-family calls *within* one turn still
  // collapses into one row, but a later turn's call of the same family never
  // merges into an earlier, already-settled group — that previously left a
  // second ask_user (or a second same-kind tool/agent/artifact event) stuck
  // rendering the first one's now-stale card instead of its own.
  if (event.kind === 'tool') {
    // Approval cards are interactive state, not ordinary tool telemetry. Keep
    // them separate from preceding search/inspect calls of the same tool
    // family; otherwise the first event wins rendering and the approval card
    // disappears inside a collapsed group.
    if (event.pluginApproval) return `approval:plugin:${event.agentId}:${event.turnId ?? ''}:${event.invocationId ?? event.eventId}`
    if (event.mcpApproval || event.questions) return `approval:user:${event.agentId}:${event.turnId ?? ''}:${event.invocationId ?? event.eventId}`
    return `tool:${event.agentId}:${event.turnId ?? ''}:${event.toolKind ?? event.toolName ?? 'tool'}`
  }
  if (event.kind === 'agent') return `agent:${event.agentId}:${event.turnId ?? ''}:${event.type}:${event.label ?? ''}`
  if (event.kind === 'artifact') return `artifact:${event.agentId}:${event.turnId ?? ''}:${event.label ?? ''}`
  return `lifecycle:${event.agentId}:${event.turnId ?? ''}:${event.type}`
}

export function groupLabel(group: ActivityGroup): string {
  const first = group.events[0]
  if (group.kind === 'tool') return toolGroupLabel(group)
  if (group.kind === 'agent') return agentLabel(first)
  if (group.kind === 'artifact') return first.label ? `Criou ${first.label}` : 'Criou um artefato'
  return first.summary || activityStateLabel(group.state)
}

function toolGroupLabel(group: ActivityGroup): string {
  if (group.count === 1) return group.events[0].summary || `Usou ${toolKindLabel(group.events[0].toolKind)}`
  const kind = group.events[0].toolKind
  const plural = group.count
  switch (kind) {
    case 'filesystem': return `${plural} operações em arquivos`
    case 'terminal': return `Terminal · ${plural} comandos`
    case 'web': return `Consultou ${plural} páginas`
    case 'browser': return `${plural} ações no navegador`
    case 'memory': return `${plural} atualizações de memória`
    default: return `${plural} ações de ${toolKindLabel(kind)}`
  }
}

function agentLabel(event: ConversationActivityEvent): string {
  const name = event.label ?? event.agentName
  switch (event.type) {
    case 'agent.created': return name ? `Criou o agente ${name}` : 'Criou um agente'
    case 'agent.message_sent': return name ? `Enviou uma tarefa para ${name}` : 'Enviou uma tarefa'
    case 'agent.message_received': return name ? `Recebeu a resposta de ${name}` : 'Recebeu uma resposta'
    case 'delegation.failed': return name ? `${name} não concluiu a tarefa` : 'Delegação falhou'
    default: return event.summary || 'Colaboração entre agentes'
  }
}

export function activityStateLabel(state: ActivityState): string {
  switch (state) {
    case 'queued': return 'Na fila'
    case 'working': return 'Trabalhando'
    case 'waiting_tool': return 'Executando ferramenta'
    case 'waiting_agent': return 'Aguardando agente'
    case 'waiting_user': return 'Aguardando você'
    case 'retrying': return 'Tentando novamente'
    case 'failed': return 'Falhou'
    case 'cancelled': return 'Cancelada'
    case 'completed': return 'Concluída'
    default: return 'Em andamento'
  }
}

export function toolKindLabel(toolKind: string | undefined): string {
  switch (toolKind) {
    case 'filesystem': return 'arquivos'
    case 'terminal': return 'terminal'
    case 'web': return 'web'
    case 'browser': return 'navegador'
    case 'memory': return 'memória'
    case 'agent': return 'agentes'
    case 'artifact': return 'artefatos'
    default: return 'ferramentas'
  }
}
