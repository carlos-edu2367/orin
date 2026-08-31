import type { ActivityGroup, ActivityState, ConversationActivityEvent } from './activityTypes'

/**
 * Collapse the raw event log into the short, human lines the chat shows.
 *
 * Three rules do the work: a `tool.started` is dropped once its `tool.finished`
 * arrives (the started event only exists so a running tool has a live state); a
 * continuous sequence of ordinary tools becomes one row, like Claude Desktop's
 * collapsed "used N tools"; and the `artifact.created` a write emits alongside
 * its own `tool.finished` folds into that same row instead of adding a second,
 * near-identical one -- the tool call already said "Escreveu X", so a
 * standalone "Criou X" row contributes nothing to the collapsed line and only
 * breaks the batch in two. The row keeps the latest human-readable activity as
 * its title, while the count and the expanded view preserve the exact audit
 * trail. Interactive approvals and browser captures stay separate because they
 * need their own controls and preview.
 */
export function summarizeActivities(events: ConversationActivityEvent[]): ActivityGroup[] {
  const groups: ActivityGroup[] = []
  const renderedCodeRuns = new Set<string>()
  const settled = new Set(
    events.filter((event) => event.type === 'tool.finished' && event.invocationId).map((event) => event.invocationId as string),
  )

  for (const event of events) {
    if (!isRenderable(event, settled)) continue
    if (isCodeModeEvent(event)) {
      const key = codeModeRunKey(event)
      // A Code execution is one durable workflow. Its card keeps the first
      // position in the transcript and receives later events as state updates
      // instead of adding a second card for every lifecycle transition.
      if (renderedCodeRuns.has(key)) continue
      renderedCodeRuns.add(key)
      groups.push(codeModeRunGroup(events, event))
      continue
    }
    if (event.kind === 'artifact') {
      const previous = groups[groups.length - 1]
      // Attaches to whatever tool batch is already open for this agent/turn --
      // an artifact event carries no record of which tool produced it (the
      // backend tags every one "tool_kind": "artifact"), so adjacency to the
      // call that just ran is the only signal available, and it is reliable:
      // the backend always emits ARTIFACT_CREATED immediately after the
      // TOOL_FINISHED whose payload named it.
      if (previous && previous.kind === 'tool' && previous.agentId === event.agentId && previous.events[0]?.turnId === event.turnId) {
        previous.events.push(event)
        previous.count += 1
        continue
      }
      // No open batch to attach to (e.g. the very first event of a turn): give
      // it its own row rather than dropping it.
      groups.push(soloGroup(event))
      continue
    }
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
    groups.push(soloGroup(event))
  }
  return groups
}

function soloGroup(event: ConversationActivityEvent): ActivityGroup {
  const group: ActivityGroup = {
    id: groupingKey(event),
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
  return group
}

export function isCodeModeEvent(event: ConversationActivityEvent): boolean {
  return event.type.startsWith('code_mode.')
}

export function codeModeRunKey(event: ConversationActivityEvent): string {
  return `code-mode:${event.turnId ?? event.agentId}`
}

/** Aggregate all lifecycle events from one Code execution into its stable card. */
export function codeModeRunGroup(events: ConversationActivityEvent[], anchor: ConversationActivityEvent): ActivityGroup {
  const id = codeModeRunKey(anchor)
  const runEvents = events.filter((event) => isCodeModeEvent(event) && codeModeRunKey(event) === id)
  const latest = runEvents.at(-1) ?? anchor
  return {
    id,
    kind: 'lifecycle',
    state: latest.state,
    label: latest.summary || 'Modo Code',
    count: runEvents.length,
    events: runEvents,
    agentId: anchor.agentId,
    agentName: anchor.agentName,
    failed: runEvents.some((event) => event.state === 'failed'),
  }
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
    // A tool sequence is one visible thought: moving from a file read to a
    // command or a web lookup should update the same line, not make the chat
    // look like a terminal log. The user can still open the card to see each
    // exact call. Browser observations keep their visual-capture card.
    if (event.toolKind === 'browser') return `browser:${event.agentId}:${event.turnId ?? ''}:${event.toolName ?? 'tool'}`
    return `tool:${event.agentId}:${event.turnId ?? ''}`
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
  // Counted and read from the tool calls alone: the `artifact.created` events
  // folded into this same group (see summarizeActivities) are metadata about
  // a call already counted here, not a call of their own, and their own
  // ("Criou X") summary must never be what ends up on the collapsed line --
  // the tool call's ("Escreveu X") already says what happened, and is what a
  // person narrating their own work would say.
  const toolEvents = group.events.filter((event) => event.kind === 'tool')
  const latest = toolEvents.at(-1) ?? group.events.at(-1) ?? group.events[0]
  if (toolEvents.length > 1 && latest.summary.startsWith('$')) return 'Executando comandos'
  return latest.summary || `Usou ${toolKindLabel(latest.toolKind)}`
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

export function formatActivityCount(count: number): string {
  return `${count} ${count === 1 ? 'ação' : 'ações'}`
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
