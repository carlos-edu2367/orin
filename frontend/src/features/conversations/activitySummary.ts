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
export function isSubagentSpawnPlaceholderEvent(event: ConversationActivityEvent): boolean {
  const phase = event.payload?.phase ?? event.phase
  return phase === 'spawning_subagent'
}

export function isWaitingModelEvent(event: ConversationActivityEvent): boolean {
  if (isSubagentSpawnPlaceholderEvent(event)) {
    return true
  }
  const phase = event.payload?.phase ?? event.phase
  if (phase === 'waiting_model' || event.payload?.waiting_model === true) {
    return true
  }
  if (
    event.type === 'tool.progressed' &&
    !event.toolName &&
    !event.tool &&
    typeof event.summary === 'string' &&
    /^pensando(?:\s+há\s+\d+\s*s)?$/i.test(event.summary.trim())
  ) {
    return true
  }
  return false
}

export function isSubagentCoordinationToolName(rawName: unknown): boolean {
  if (typeof rawName !== 'string') return false
  const clean = rawName.trim()
  if (!clean) return false
  return /^(?:mcp__[^_]+__)?(?:check_?subagent|run_?subagent|agent|ask_?agents?|list_?agents)$/i.test(clean)
}

export function isWaitingSubagentsEvent(event: ConversationActivityEvent): boolean {
  return (
    event.waitingSubagents === true ||
    event.payload?.waiting_subagents === true ||
    event.state === 'waiting_agent' ||
    event.payload?.state === 'waiting_subagents' ||
    event.phase === 'waiting_subagents' ||
    event.payload?.phase === 'waiting_subagents' ||
    event.type === 'waiting_subagents' ||
    event.type === 'delegation.waiting'
  )
}

export function isSubagentCoordinationOrWaitEvent(event: ConversationActivityEvent): boolean {
  if (event.kind === 'agent' || event.toolKind === 'agent') return true
  if (event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') return true
  if (
    event.type.startsWith('context.') ||
    event.type === 'CONTEXT_UPDATED' ||
    event.type === 'CONTEXT_COMPACTED'
  ) {
    return true
  }
  if (isWaitingModelEvent(event) || isWaitingSubagentsEvent(event)) return true
  const toolName = event.toolName ?? event.tool ?? event.payload?.tool_name
  if (isSubagentCoordinationToolName(toolName)) return true
  return false
}

export type SubagentSpawnTarget = {
  agentId: string
  agentName: string
}

export function extractSubagentSpawnTarget(
  event: ConversationActivityEvent,
  primaryAgentId?: string | null,
): SubagentSpawnTarget | null {
  if (
    (event.type === 'agent.message_sent' || event.type === 'AGENT_MESSAGE_SENT') &&
    event.payload?.source !== 'user'
  ) {
    const rawTo =
      event.payload?.to_agent_id ??
      event.payload?.toAgentId ??
      (event as { to_agent_id?: unknown; toAgentId?: unknown }).to_agent_id ??
      (event as { to_agent_id?: unknown; toAgentId?: unknown }).toAgentId
    if (typeof rawTo === 'string' && rawTo.trim()) {
      const targetAgentId = rawTo.trim()
      if (primaryAgentId && targetAgentId === primaryAgentId) return null
      const payloadName =
        (typeof event.payload?.agent_name === 'string' && event.payload.agent_name.trim()) ||
        (typeof event.payload?.recipient_name === 'string' && event.payload.recipient_name.trim()) ||
        ''
      const eventAgentName =
        event.agentName && event.agentName.trim() && event.agentName !== 'Main'
          ? event.agentName.trim()
          : ''
      const labelName =
        (event.label && event.label.trim()) ||
        (typeof event.payload?.label === 'string' && event.payload.label.trim()) ||
        ''
      const targetAgentName =
        payloadName ||
        eventAgentName ||
        labelName ||
        targetAgentId.replace(/^agent:/, '') ||
        targetAgentId
      return { agentId: targetAgentId, agentName: targetAgentName }
    }
  }
  if (
    (event.type === 'turn.started' || event.type === 'TURN_STARTED') &&
    Boolean(event.parentAgentId || event.payload?.parent_agent_id)
  ) {
    const targetAgentId = (event.agentId ?? '').trim()
    if (!targetAgentId || (primaryAgentId && targetAgentId === primaryAgentId)) return null
    const targetAgentName =
      (event.agentName && event.agentName.trim()) ||
      (typeof event.payload?.agent_name === 'string' && event.payload.agent_name.trim()) ||
      (event.label && event.label.trim()) ||
      targetAgentId.replace(/^agent:/, '') ||
      targetAgentId
    return { agentId: targetAgentId, agentName: targetAgentName }
  }
  return null
}

export function computeSubagentDelegationStatus(
  events: ConversationActivityEvent[],
): Map<string, 'completed' | 'failed'> {
  const statusByTarget = new Map<string, 'completed' | 'failed'>()
  const setTarget = (target: unknown, status: 'completed' | 'failed') => {
    if (typeof target !== 'string') return
    const clean = target.trim()
    if (!clean) return
    statusByTarget.set(clean, status)
  }
  const clearTarget = (target: unknown) => {
    if (typeof target !== 'string') return
    const clean = target.trim()
    if (!clean) return
    statusByTarget.delete(clean)
  }
  for (const event of events) {
    const turnKey = event.turnId ?? ''
    const spawnTarget = extractSubagentSpawnTarget(event)
    if (spawnTarget) {
      clearTarget(`${turnKey}:${spawnTarget.agentId}`)
      clearTarget(spawnTarget.agentId)
      clearTarget(`${turnKey}:${spawnTarget.agentName}`)
      clearTarget(spawnTarget.agentName)
    }
    const recordForEvent = (status: 'completed' | 'failed') => {
      const includeEventAgent =
        Boolean(event.parentAgentId || event.payload?.parent_agent_id) ||
        (!event.payload?.from_agent_id && !event.payload?.agent_id)
      const candidates = [
        ...(includeEventAgent ? [event.agentId, event.agentName] : []),
        event.payload?.from_agent_id,
        event.payload?.agent_id,
        event.label,
        event.payload?.agent_name,
        event.payload?.sender_name,
        event.payload?.label,
      ]
      for (const candidate of candidates) {
        if (typeof candidate === 'string' && candidate.trim()) {
          setTarget(`${turnKey}:${candidate.trim()}`, status)
          setTarget(candidate.trim(), status)
        }
      }
    }
    if (
      event.type === 'delegation.failed' ||
      event.type === 'DELEGATION_FAILED' ||
      ((event.type === 'turn.failed' ||
        event.type === 'TURN_FAILED' ||
        event.type === 'turn.cancelled' ||
        event.type === 'TURN_CANCELLED') &&
        Boolean(event.parentAgentId || event.payload?.parent_agent_id))
    ) {
      recordForEvent('failed')
    } else if (
      event.type === 'delegation.completed' ||
      event.type === 'DELEGATION_COMPLETED' ||
      event.type === 'agent.message_received' ||
      event.type === 'AGENT_MESSAGE_RECEIVED' ||
      ((event.type === 'turn.completed' || event.type === 'TURN_COMPLETED') &&
        Boolean(event.parentAgentId || event.payload?.parent_agent_id))
    ) {
      recordForEvent('completed')
    } else if (
      (event.type === 'tool.finished' ||
        event.type === 'tool.completed' ||
        event.type === 'tool.failed') &&
      typeof event.payload?.agent_id === 'string' &&
      event.payload.agent_id.trim()
    ) {
      const isFailed =
        event.type === 'tool.failed' ||
        event.status === 'failed' ||
        event.state === 'failed' ||
        Boolean(event.errorCode) ||
        event.payload?.status === 'failed' ||
        event.payload?.state === 'failed'
      const isStillRunning =
        !isFailed &&
        (event.payload?.status === 'running' ||
          event.payload?.state === 'running' ||
          event.payload?.run_in_background === true ||
          event.payload?.background === true)
      if (!isStillRunning) {
        const outcome: 'completed' | 'failed' = isFailed ? 'failed' : 'completed'
        const payloadCandidates = [
          event.payload.agent_id,
          event.payload.from_agent_id,
          event.payload.agent_name,
          event.payload.recipient_name,
          event.payload.sender_name,
          event.payload.label,
        ]
        for (const candidate of payloadCandidates) {
          if (typeof candidate === 'string' && candidate.trim()) {
            setTarget(`${turnKey}:${candidate.trim()}`, outcome)
            setTarget(candidate.trim(), outcome)
          }
        }
      }
    }
  }
  return statusByTarget
}

export function resolveSubagentDelegationOutcome(
  statusByTarget: Map<string, 'completed' | 'failed'>,
  agentId?: string,
  agentName?: string,
  turnId?: string,
): 'completed' | 'failed' | null {
  const turnKey = turnId ?? ''
  const keys = [
    agentId ? `${turnKey}:${agentId}` : '',
    agentName ? `${turnKey}:${agentName}` : '',
    agentId ?? '',
    agentName ?? '',
  ].filter(Boolean)
  for (const key of keys) {
    const status = statusByTarget.get(key)
    if (status === 'completed') return 'completed'
    if (status === 'failed') return 'failed'
  }
  return null
}

export function isSubagentDelegationFailed(
  statusByTarget: Map<string, 'completed' | 'failed'>,
  agentId?: string,
  agentName?: string,
  turnId?: string,
): boolean {
  return resolveSubagentDelegationOutcome(statusByTarget, agentId, agentName, turnId) === 'failed'
}

export function createSubagentSpawnPlaceholderEvent(
  event: ConversationActivityEvent,
  target: SubagentSpawnTarget,
  primaryAgentId: string | undefined | null,
  subagentDelegationStatus: Map<string, 'completed' | 'failed'>,
  turnCompleted = false,
): ConversationActivityEvent {
  const delegationOutcome = resolveSubagentDelegationOutcome(
    subagentDelegationStatus,
    target.agentId,
    target.agentName,
    event.turnId,
  )
  const isSettled = turnCompleted || delegationOutcome !== null
  const state: ActivityState =
    delegationOutcome === 'failed'
      ? 'failed'
      : isSettled
        ? 'completed'
        : 'working'
  const status =
    delegationOutcome === 'failed'
      ? 'failed'
      : isSettled
        ? 'succeeded'
        : 'running'
  const summary =
    delegationOutcome === 'failed'
      ? 'Subagente não concluiu a tarefa'
      : isSettled
        ? 'Concluído'
        : 'Iniciando subagente…'
  const parentId =
    event.parentAgentId ||
    (typeof event.payload?.parent_agent_id === 'string' ? event.payload.parent_agent_id : undefined) ||
    primaryAgentId ||
    event.agentId ||
    'agent:main'
  return {
    ...event,
    eventId: `spawn:${target.agentId}:${event.originEventId ?? event.eventId}`,
    originEventId: event.originEventId ?? event.eventId,
    kind: 'tool',
    type: 'tool.progressed',
    state,
    status,
    agentId: target.agentId,
    agentName: target.agentName,
    parentAgentId: parentId,
    phase: 'spawning_subagent',
    payload: { ...(event.payload ?? {}), phase: 'spawning_subagent' },
    summary,
  }
}

export function normalizeGeneratingArgumentsEvent(
  event: ConversationActivityEvent,
): ConversationActivityEvent {
  const phase = event.payload?.phase ?? event.phase
  if (event.type !== 'tool.progressed' || phase !== 'generating_arguments') {
    return event
  }
  const toolName = String(
    event.payload?.tool_name ?? event.toolName ?? event.tool ?? event.title ?? 'ferramenta',
  )
  const hasCustomSummary =
    Boolean(event.summary) &&
    event.summary !== 'tool.progressed' &&
    event.summary.startsWith('Preparando')
  const summary = hasCustomSummary ? event.summary : `Preparando ${toolName}...`
  return {
    ...event,
    kind: 'tool',
    state: 'waiting_tool',
    status: 'running',
    toolName,
    tool: toolName,
    phase: 'generating_arguments',
    summary,
  }
}

export function summarizeActivities(events: ConversationActivityEvent[]): ActivityGroup[] {
  const groups: ActivityGroup[] = []
  const renderedCodeRuns = new Set<string>()
  const settled = computeSettledEventIds(events)
  const subagentDelegationStatus = computeSubagentDelegationStatus(events)
  const firstCodeEventByTurn = new Map<string, ConversationActivityEvent>()
  const primaryAgentByTurn = new Map<string, string>()
  for (const event of events) {
    if (isCodeModeEvent(event) && event.turnId && !firstCodeEventByTurn.has(event.turnId)) {
      firstCodeEventByTurn.set(event.turnId, event)
    }
    const turnKey = event.turnId ?? ''
    if (!primaryAgentByTurn.has(turnKey) && !event.parentAgentId && event.type !== 'agent.created' && event.agentId) {
      primaryAgentByTurn.set(turnKey, event.agentId)
    }
  }
  for (const event of events) {
    const turnKey = event.turnId ?? ''
    if (!primaryAgentByTurn.has(turnKey) && event.parentAgentId) {
      primaryAgentByTurn.set(turnKey, event.parentAgentId)
    }
  }
  const hasTerminalTurnEvent = events.some(
    (ev) => !ev.parentAgentId && TERMINAL_TURN_TYPES.has(ev.type),
  )

  const isSubagentEvent = (event: ConversationActivityEvent): boolean => {
    if (event.parentAgentId) return true
    const primary = primaryAgentByTurn.get(event.turnId ?? '')
    return Boolean(primary && event.agentId && event.agentId !== primary && event.type !== 'agent.created')
  }

  const subagentWaveGroups = new Map<string, ActivityGroup>()
  const settledWaveSubagents = new Set<string>()
  const openToolOriginsByScope = new Map<string, ConversationActivityEvent[]>()

  const isWaveGroupSettled = (group: ActivityGroup): boolean => {
    if (
      (group.agentId && settledWaveSubagents.has(group.agentId)) ||
      (group.agentName && settledWaveSubagents.has(group.agentName))
    ) {
      return true
    }
    const realTools = group.events.filter((ev) => ev.kind === 'tool' && !isWaitingModelEvent(ev))
    if (realTools.length === 0) return false
    return group.events.every(
      (ev) =>
        (ev.state === 'completed' || ev.state === 'failed' || ev.state === 'cancelled') &&
        ev.status !== 'running' &&
        ev.status !== 'in_progress' &&
        ev.status !== 'started',
    )
  }

  for (const rawEvent of events) {
    let event = normalizeGeneratingArgumentsEvent(rawEvent)
    if (
      event.type === 'delegation.failed' ||
      event.type === 'DELEGATION_FAILED' ||
      event.type === 'delegation.completed' ||
      event.type === 'DELEGATION_COMPLETED' ||
      event.type === 'agent.message_received' ||
      event.type === 'AGENT_MESSAGE_RECEIVED' ||
      (TERMINAL_TURN_TYPES.has(event.type) &&
        Boolean(event.parentAgentId || event.payload?.parent_agent_id))
    ) {
      for (const candidate of [
        event.agentId,
        event.agentName,
        event.label,
        event.payload?.from_agent_id,
        event.payload?.agent_id,
        event.payload?.agent_name,
        event.payload?.sender_name,
      ]) {
        if (typeof candidate === 'string' && candidate.trim()) {
          settledWaveSubagents.add(candidate.trim())
        }
      }
    } else if (
      (event.type === 'tool.finished' ||
        event.type === 'tool.completed' ||
        event.type === 'tool.failed') &&
      typeof event.payload?.agent_id === 'string' &&
      event.payload.agent_id.trim()
    ) {
      const isFailed =
        event.type === 'tool.failed' ||
        event.status === 'failed' ||
        event.state === 'failed' ||
        Boolean(event.errorCode) ||
        event.payload?.status === 'failed' ||
        event.payload?.state === 'failed'
      const isStillRunning =
        !isFailed &&
        (event.payload?.status === 'running' ||
          event.payload?.state === 'running' ||
          event.payload?.run_in_background === true ||
          event.payload?.background === true)
      if (!isStillRunning) {
        settledWaveSubagents.add(event.payload.agent_id.trim())
        if (typeof event.payload.agent_name === 'string' && event.payload.agent_name.trim()) {
          settledWaveSubagents.add(event.payload.agent_name.trim())
        }
      }
    }
    const toolScope = `${event.agentId}:${event.turnId ?? ''}`
    if (event.type === 'tool.started' || event.type === 'tool.progressed') {
      const openList = openToolOriginsByScope.get(toolScope) ?? []
      const prior =
        openList.length > 0
          ? (event.invocationId
              ? openList.find((item) => item.invocationId === event.invocationId)
              : undefined) ??
            (event.type === 'tool.started'
              ? [...openList]
                  .reverse()
                  .find(
                    (item) =>
                      item.type === 'tool.progressed' &&
                      (!event.toolName || !item.toolName || item.toolName === event.toolName),
                  )
              : undefined)
          : undefined
      const originId = event.originEventId || prior?.originEventId || prior?.eventId
      if (originId && originId !== event.originEventId) {
        event = { ...event, originEventId: originId }
      }
      openList.push(event)
      openToolOriginsByScope.set(toolScope, openList)
    } else if (
      event.type === 'tool.finished' ||
      event.type === 'tool.completed' ||
      event.type === 'tool.failed'
    ) {
      const openList = openToolOriginsByScope.get(toolScope)
      const prior =
        openList && openList.length > 0
          ? (event.invocationId
              ? openList.find((item) => item.invocationId === event.invocationId)
              : undefined) ??
            (event.toolName
              ? [...openList].reverse().find((item) => item.toolName === event.toolName)
              : undefined) ??
            openList[openList.length - 1]
          : undefined
      const originId = event.originEventId || prior?.originEventId || prior?.eventId
      if (originId && originId !== event.originEventId) {
        event = { ...event, originEventId: originId }
      }
      if (openList && prior) {
        const priorOrigin = prior.originEventId ?? prior.eventId
        openToolOriginsByScope.set(
          toolScope,
          openList.filter(
            (item) =>
              item !== prior &&
              (event.invocationId
                ? item.invocationId !== event.invocationId
                : (item.originEventId ?? item.eventId) !== priorOrigin),
          ),
        )
      }
    }
    const subagentEvent = isSubagentEvent(event)
    if (!subagentEvent && event.kind === 'message' && event.content && event.content.trim()) {
      subagentWaveGroups.clear()
      settledWaveSubagents.clear()
    }
    if (
      !subagentEvent &&
      (event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') &&
      subagentWaveGroups.size > 0 &&
      [...subagentWaveGroups.values()].every(isWaveGroupSettled)
    ) {
      subagentWaveGroups.clear()
      settledWaveSubagents.clear()
    }
    const spawnTarget = extractSubagentSpawnTarget(
      event,
      primaryAgentByTurn.get(event.turnId ?? ''),
    )
    if (spawnTarget) {
      settledWaveSubagents.delete(spawnTarget.agentId)
      settledWaveSubagents.delete(spawnTarget.agentName)
      const key = `tool:${spawnTarget.agentId}:${event.turnId ?? ''}`
      const existingWaveGroup = subagentWaveGroups.get(key)
      if (existingWaveGroup) {
        if (!existingWaveGroup.agentName && spawnTarget.agentName) {
          existingWaveGroup.agentName = spawnTarget.agentName
        }
        continue
      }
      const spawnEvent = createSubagentSpawnPlaceholderEvent(
        event,
        spawnTarget,
        primaryAgentByTurn.get(event.turnId ?? ''),
        subagentDelegationStatus,
        hasTerminalTurnEvent,
      )
      const created = soloGroup(spawnEvent)
      created.failed = isSubagentDelegationFailed(
        subagentDelegationStatus,
        spawnTarget.agentId,
        spawnTarget.agentName,
        event.turnId,
      )
      groups.push(created)
      subagentWaveGroups.set(key, created)
      continue
    }
    if (!isRenderable(event, settled)) continue
    if (event.turnId && firstCodeEventByTurn.has(event.turnId)) {
      const anchor = firstCodeEventByTurn.get(event.turnId)!
      const codeKey = codeModeRunKey(anchor)
      if (!renderedCodeRuns.has(codeKey)) {
        renderedCodeRuns.add(codeKey)
        groups.push(codeModeRunGroup(events, anchor))
      }
    }
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
    if (!subagentEvent && !isSubagentCoordinationOrWaitEvent(event)) {
      subagentWaveGroups.clear()
    }
    if (event.kind === 'artifact') {
      if (subagentEvent) {
        const waveToolGroup = subagentWaveGroups.get(`tool:${event.agentId}:${event.turnId ?? ''}`)
        if (waveToolGroup) {
          waveToolGroup.events.push(event)
          waveToolGroup.count += 1
          continue
        }
      }
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
    if (subagentEvent && event.kind === 'tool' && key.startsWith('tool:')) {
      const delegationFailed = isSubagentDelegationFailed(
        subagentDelegationStatus,
        event.agentId,
        event.agentName,
        event.turnId,
      )
      const existingWaveGroup = subagentWaveGroups.get(key)
      if (existingWaveGroup) {
        applyEventToGroup(existingWaveGroup, event)
        if (event.agentName) {
          existingWaveGroup.agentName = event.agentName
        }
        existingWaveGroup.failed = delegationFailed
        continue
      }
      const created = soloGroup(event)
      created.failed = delegationFailed
      groups.push(created)
      subagentWaveGroups.set(key, created)
      continue
    }
    const previous = groups[groups.length - 1]
    if (previous && previous.id === key && previous.kind === event.kind) {
      applyEventToGroup(previous, event)
      if (subagentEvent && event.kind === 'tool') {
        previous.failed = isSubagentDelegationFailed(
          subagentDelegationStatus,
          previous.agentId,
          previous.agentName,
          event.turnId,
        )
      }
      continue
    }
    const created = soloGroup(event)
    if (subagentEvent && event.kind === 'tool') {
      created.failed = isSubagentDelegationFailed(
        subagentDelegationStatus,
        created.agentId,
        created.agentName,
        event.turnId,
      )
    }
    groups.push(created)
  }
  return groups
}

export function applyEventToGroup(group: ActivityGroup, event: ConversationActivityEvent): void {
  if (isWaitingModelEvent(event)) {
    const existingWaitIdx = group.events.findIndex((item) => isWaitingModelEvent(item))
    if (existingWaitIdx >= 0) {
      group.events[existingWaitIdx] = event
    } else {
      group.events.push(event)
    }
    group.state = event.state
    group.label = groupLabel(group)
    return
  }
  const waitIdx = group.events.findIndex((item) => isWaitingModelEvent(item))
  if (waitIdx >= 0 && event.kind === 'tool') {
    group.events.splice(waitIdx, 1)
  }
  if (event.type === 'tool.progressed') {
    const matchIndex = event.invocationId
      ? group.events.findIndex((item) => item.invocationId === event.invocationId && item.type !== 'tool.finished')
      : findLastOpenToolIndex(group.events, event.toolName)
    if (matchIndex >= 0) {
      const existing = group.events[matchIndex]
      const isAlreadyFinished =
        existing.type === 'tool.finished' ||
        existing.type === 'tool.completed' ||
        existing.type === 'tool.failed'
      const nextState = isAlreadyFinished ? existing.state : event.state
      const nextSummary =
        isAlreadyFinished && existing.summary
          ? existing.summary
          : event.summary || existing.summary
      group.events[matchIndex] = {
        ...existing,
        toolName: event.toolName ?? existing.toolName,
        toolKind: event.toolKind ?? existing.toolKind,
        status: isAlreadyFinished ? existing.status : event.status ?? existing.status,
        phase: event.phase ?? existing.phase,
        bytesReceived: event.bytesReceived ?? existing.bytesReceived,
        payload: event.payload ?? existing.payload,
        summary: nextSummary,
        command: event.command ?? existing.command,
        description: event.description ?? existing.description,
        bashOutput: event.bashOutput ?? existing.bashOutput,
        outputTail: event.outputTail ?? existing.outputTail,
        elapsedMs: event.elapsedMs ?? existing.elapsedMs,
        state: nextState,
      }
      group.state = nextState
      group.label = groupLabel(group)
      return
    }
  }
  if (event.kind === 'lifecycle') {
    group.events[group.events.length - 1] = event
    group.state = event.state
    group.failed = group.failed || event.state === 'failed'
    group.label = groupLabel(group)
    return
  }
  group.count += 1
  group.events.push(event)
  group.state = event.state
  group.failed = group.failed || event.state === 'failed'
  group.label = groupLabel(group)
}

function findLastOpenToolIndex(events: ConversationActivityEvent[], toolName?: string): number {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const item = events[index]
    if (
      item.kind === 'tool' &&
      item.type !== 'tool.finished' &&
      item.type !== 'tool.completed' &&
      item.type !== 'tool.failed' &&
      (!toolName || !item.toolName || item.toolName === toolName)
    ) {
      return index
    }
  }
  return -1
}

const TERMINAL_TURN_TYPES = new Set([
  'turn.completed',
  'TURN_COMPLETED',
  'turn.failed',
  'TURN_FAILED',
  'turn.cancelled',
  'TURN_CANCELLED',
  'execution.canceled',
  'EXECUTION_CANCELED',
  'code_mode.completed',
  'code_mode.completed_with_caveats',
  'code_mode.blocked',
])

/**
 * Walks the event stream in chronological order and records the `eventId` of
 * every `tool.started` / `tool.progressed` event that was later settled by its
 * corresponding `tool.finished` or by a terminal turn event. Tracking by
 * `eventId` (instead of a global `Set<invocationId>`) ensures that a new
 * running tool call stays visible even if an earlier iteration reused a
 * synthetic `invocationId` like `tool-call:1`, while also settling any
 * `tool.progressed` events that lacked an `invocationId`.
 */
export function computeSettledEventIds(
  events: ConversationActivityEvent[],
  turnCompleted = false,
  /**
   * The turn timeline shows a finished turn's last progress of a tool that never reported its end (a PowerShell
   * run with its output) as completed, so it keeps it; only the "Preparando…" rows of arguments are hidden.
   */
  { keepOpenToolProgress = false }: { keepOpenToolProgress?: boolean } = {},
): Set<string> {
  const settled = new Set<string>()
  const openByTurn = new Map<string, ConversationActivityEvent[]>()
  const hasTerminalTurnEvent = events.some(
    (ev) => !ev.parentAgentId && TERMINAL_TURN_TYPES.has(ev.type),
  )
  const effectiveTurnCompleted = turnCompleted || hasTerminalTurnEvent

  for (const event of events) {
    const turnScope = `${event.agentId ?? ''}:${event.turnId ?? ''}`
    const eventCallId = event.invocationId
    if (event.type === 'tool.started') {
      const list = openByTurn.get(turnScope) ?? []
      const remaining: ConversationActivityEvent[] = []
      for (const openEvent of list) {
        const openCallId = openEvent.invocationId
        const isGeneratingArgs =
          openEvent.type === 'tool.progressed' &&
          (openEvent.phase === 'generating_arguments' ||
            openEvent.payload?.phase === 'generating_arguments' ||
            /^Preparando\b/i.test(openEvent.summary ?? ''))
        const sameToolName = Boolean(
          event.toolName && openEvent.toolName && event.toolName === openEvent.toolName,
        )
        const isPreStartProgress =
          openEvent.type === 'tool.progressed' &&
          (isWaitingModelEvent(openEvent) ||
            (eventCallId
              ? openCallId === eventCallId || !openCallId || (isGeneratingArgs && sameToolName)
              : true))
        if (isPreStartProgress) {
          if (openEvent.eventId) settled.add(openEvent.eventId)
        } else {
          remaining.push(openEvent)
        }
      }
      remaining.push(event)
      openByTurn.set(turnScope, remaining)
      continue
    }
    if (event.type === 'tool.progressed') {
      const list = openByTurn.get(turnScope) ?? []
      if (isWaitingModelEvent(event)) {
        const remaining: ConversationActivityEvent[] = []
        for (const openEvent of list) {
          if (isWaitingModelEvent(openEvent)) {
            if (openEvent.eventId) settled.add(openEvent.eventId)
          } else {
            remaining.push(openEvent)
          }
        }
        remaining.push(event)
        openByTurn.set(turnScope, remaining)
      } else {
        list.push(event)
        openByTurn.set(turnScope, list)
      }
      continue
    }
    if (event.type === 'tool.finished' || event.type === 'tool.completed' || event.type === 'tool.failed') {
      const payloadAgentId =
        typeof event.payload?.agent_id === 'string' ? event.payload.agent_id.trim() : ''
      if (payloadAgentId) {
        const isFailed =
          event.type === 'tool.failed' ||
          event.status === 'failed' ||
          event.state === 'failed' ||
          Boolean(event.errorCode) ||
          event.payload?.status === 'failed' ||
          event.payload?.state === 'failed'
        const isStillRunning =
          !isFailed &&
          (event.payload?.status === 'running' ||
            event.payload?.state === 'running' ||
            event.payload?.run_in_background === true ||
            event.payload?.background === true)
        if (!isStillRunning) {
          for (const [scope, subList] of openByTurn.entries()) {
            const [scopeAgentId] = scope.split(':', 1)
            if (scopeAgentId === payloadAgentId) {
              for (const openEvent of subList) {
                if (openEvent.eventId) settled.add(openEvent.eventId)
              }
              openByTurn.set(scope, [])
            }
          }
        }
      }
      const list = openByTurn.get(turnScope)
      if (!list || list.length === 0) continue
      const remaining: ConversationActivityEvent[] = []
      for (const openEvent of list) {
        const openCallId = openEvent.invocationId
        const isGeneratingArgs =
          openEvent.type === 'tool.progressed' &&
          (openEvent.phase === 'generating_arguments' ||
            openEvent.payload?.phase === 'generating_arguments' ||
            /^Preparando\b/i.test(openEvent.summary ?? ''))
        const sameToolName = Boolean(
          event.toolName && openEvent.toolName && event.toolName === openEvent.toolName,
        )
        const matchesInvocation =
          isWaitingModelEvent(openEvent) ||
          (eventCallId
            ? openCallId === eventCallId || !openCallId || (isGeneratingArgs && sameToolName)
            : true)
        if (matchesInvocation) {
          if (openEvent.eventId) settled.add(openEvent.eventId)
        } else {
          remaining.push(openEvent)
        }
      }
      openByTurn.set(turnScope, remaining)
      continue
    }
    if (
      event.type === 'delegation.failed' ||
      event.type === 'DELEGATION_FAILED' ||
      event.type === 'delegation.completed' ||
      event.type === 'DELEGATION_COMPLETED' ||
      event.type === 'agent.message_received' ||
      event.type === 'AGENT_MESSAGE_RECEIVED'
    ) {
      const isMessageReceived =
        event.type === 'agent.message_received' || event.type === 'AGENT_MESSAGE_RECEIVED'
      const includeEventAgentId =
        !isMessageReceived ||
        Boolean(event.parentAgentId || event.payload?.parent_agent_id) ||
        (!event.payload?.from_agent_id && !event.payload?.agent_id)
      const targetIds = new Set(
        [
          includeEventAgentId ? event.agentId : undefined,
          typeof event.payload?.from_agent_id === 'string' ? event.payload.from_agent_id : undefined,
          typeof event.payload?.agent_id === 'string' ? event.payload.agent_id : undefined,
        ].filter((id): id is string => Boolean(id)),
      )
      const targetNames = new Set(
        [
          includeEventAgentId ? event.agentName : undefined,
          event.label,
          typeof event.payload?.agent_name === 'string' ? event.payload.agent_name : undefined,
          typeof event.payload?.sender_name === 'string' ? event.payload.sender_name : undefined,
          typeof event.payload?.label === 'string' ? event.payload.label : undefined,
        ]
          .map((name) => (typeof name === 'string' ? name.trim() : ''))
          .filter(Boolean),
      )
      for (const [scope, list] of openByTurn.entries()) {
        const [scopeAgentId] = scope.split(':', 1)
        const matchesAgent =
          targetIds.has(scopeAgentId) ||
          list.some((openEvent) =>
            Boolean(openEvent.agentName && targetNames.has(openEvent.agentName.trim())),
          )
        if (matchesAgent) {
          for (const openEvent of list) {
            if (openEvent.eventId) settled.add(openEvent.eventId)
          }
          openByTurn.set(scope, [])
        }
      }
    }
    if (TERMINAL_TURN_TYPES.has(event.type)) {
      const isSubagentTerminal = Boolean(event.parentAgentId || event.payload?.parent_agent_id)
      for (const [scope, list] of openByTurn.entries()) {
        const [scopeAgentId] = scope.split(':', 1)
        const matchesScope = isSubagentTerminal
          ? Boolean(event.agentId && scopeAgentId === event.agentId)
          : scope === turnScope || Boolean(event.turnId && scope.endsWith(`:${event.turnId}`))
        if (matchesScope) {
          for (const openEvent of list) {
            if (openEvent.eventId) settled.add(openEvent.eventId)
          }
          openByTurn.set(scope, [])
        }
      }
    }
  }

  if (effectiveTurnCompleted) {
    for (const list of openByTurn.values()) {
      for (const openEvent of list) {
        if (keepOpenToolProgress && openEvent.type === 'tool.progressed') continue
        if (openEvent.eventId) settled.add(openEvent.eventId)
      }
    }
    for (const ev of events) {
      if (
        ev.type === 'tool.progressed' &&
        (isWaitingModelEvent(ev) ||
          ev.phase === 'generating_arguments' ||
          ev.payload?.phase === 'generating_arguments' ||
          /^Preparando\b/i.test(ev.summary ?? ''))
      ) {
        if (ev.eventId) settled.add(ev.eventId)
      }
    }
  }

  return settled
}

function soloGroup(event: ConversationActivityEvent): ActivityGroup {
  const waitingModel = isWaitingModelEvent(event)
  const group: ActivityGroup = {
    id: groupingKey(event),
    kind: event.kind,
    state: event.state,
    label: '',
    count: waitingModel ? 0 : 1,
    events: [event],
    agentId: event.agentId,
    agentName: event.agentName,
    failed: waitingModel ? false : event.state === 'failed',
  }
  group.label = groupLabel(group)
  return group
}

export function isVerifierEvent(event: ConversationActivityEvent): boolean {
  return (
    event.type === 'verifier.model_selected' ||
    event.type === 'verifier.started' ||
    event.type === 'verifier.finished'
  )
}

export function isMergeEvent(event: ConversationActivityEvent): boolean {
  return (
    event.type === 'merge.applied' ||
    event.type === 'merge.conflict' ||
    event.type === 'merge.check_failed'
  )
}

export function isCodeModeEvent(event: ConversationActivityEvent): boolean {
  return event.type.startsWith('code_mode.') && !event.toolPermission
}

export function codeModeRunKey(event: ConversationActivityEvent): string {
  return `code-mode:${event.turnId ?? event.agentId}`
}

/** Aggregate all lifecycle events from one Code execution into its stable card. */
export function codeModeRunGroup(events: ConversationActivityEvent[], anchor: ConversationActivityEvent): ActivityGroup {
  const id = codeModeRunKey(anchor)
  const runEvents = events.filter((event) => isCodeModeEvent(event) && codeModeRunKey(event) === id)
  const latest = runEvents.at(-1) ?? anchor
  const turnFailure = anchor.turnId
    ? [...events].reverse().find((event) => event.turnId === anchor.turnId && !event.parentAgentId && event.type === 'turn.failed')
    : undefined
  const state: ActivityState =
    turnFailure?.state === 'cancelled'
      ? 'cancelled'
      : latest.state === 'working' && turnFailure?.state === 'failed'
        ? 'failed'
        : latest.state
  return {
    id,
    kind: 'lifecycle',
    state,
    label: latest.summary || 'Modo Code',
    count: runEvents.length,
    events: runEvents,
    agentId: anchor.agentId,
    agentName: anchor.agentName,
    failed: state === 'failed' || runEvents.some((event) => event.state === 'failed'),
  }
}

export function isFriendQueuedInputEvent(event: ConversationActivityEvent): boolean {
  const origin = typeof event.payload?.origin === 'string' ? event.payload.origin.trim().toLowerCase() : ''
  if (origin !== 'friend') return false
  return (
    event.type === 'turn.started' ||
    event.type === 'TURN_STARTED' ||
    event.type === 'turn.queued_input' ||
    event.type === 'queued_input'
  )
}

export function isRenderable(event: ConversationActivityEvent, settled: Set<string>): boolean {
  if (event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') return true
  if (event.type === 'friend.delivery_status') return false
  if (event.type === 'agent.progress') return false
  if (isMergeEvent(event)) return true
  if (isFriendQueuedInputEvent(event)) return true
  if (event.type.startsWith('context.')) return false
  if (event.kind === 'message') return false
  // A lifecycle "turn started/working" line adds nothing next to the tool rows
  // that follow it, so only terminal lifecycle states or stream-resume notices earn a row.
  if (
    event.kind === 'lifecycle' &&
    event.type === 'turn.started' &&
    event.payload?.stream_resuming !== true &&
    !event.summary.startsWith('Conexão com o modelo caiu; retomando')
  ) {
    return false
  }
  // `turn.waiting_user` always accompanies an `ask_user` tool call in the same
  // turn, and the interactive question card built from that tool call already
  // shows this state (and, unlike this static line, keeps showing it correctly
  // once answered). Rendering both leaves a second, stale "waiting for you" row
  // sitting right after a card that has already moved on.
  if (event.type === 'turn.waiting_user') return false
  if (event.type === 'tool.requested') return false
  if (
    (event.type === 'tool.started' || event.type === 'tool.progressed') &&
    (settled.has(event.eventId) || Boolean(event.invocationId && settled.has(event.invocationId)))
  ) {
    return false
  }
  // The agent tools already emit their own agent.* events, which read better.
  if (event.kind === 'agent' && event.type.startsWith('tool.') ) return false
  return true
}

export function groupingKey(event: ConversationActivityEvent): string {
  if (isVerifierEvent(event)) {
    return `verifier:${event.agentId}:${event.turnId ?? ''}`
  }
  if (isMergeEvent(event)) {
    const targetAgent =
      (typeof event.payload?.agent_id === 'string' && event.payload.agent_id.trim()) ||
      event.agentId
    return `merge:${targetAgent}:${event.turnId ?? ''}`
  }
  if (isFriendQueuedInputEvent(event)) {
    const queueId = typeof event.payload?.queue_id === 'string' ? event.payload.queue_id : event.eventId
    return `queued-input:friend:${event.agentId}:${event.turnId ?? ''}:${queueId}`
  }
  if (event.toolPermission) {
    return `approval:tool:${event.agentId}:${event.turnId ?? ''}:${event.toolPermission.requestId}`
  }
  if (event.codeSessionProposal) {
    return `approval:code-session:${event.agentId}:${event.turnId ?? ''}:${event.codeSessionProposal.proposal_id || event.invocationId || event.eventId}`
  }
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
    if (
      (event.toolName === 'manage_session' ||
        event.toolName === 'control_code_session' ||
        event.payload?.action === 'send_message') &&
      event.payload?.action === 'send_message' &&
      typeof event.payload?.delivery === 'string'
    ) {
      const deliveryKey =
        (typeof event.payload?.queue_id === 'string' && event.payload.queue_id) ||
        (typeof event.payload?.turn_id === 'string' && event.payload.turn_id) ||
        event.invocationId ||
        event.eventId
      return `friend-delivery:${event.agentId}:${event.turnId ?? ''}:${deliveryKey}`
    }
    // A tool sequence is one visible thought: moving from a file read to a
    // command or a web lookup should update the same line, not make the chat
    // look like a terminal log. The user can still open the card to see each
    // exact call. Browser observations keep their visual-capture card.
    if (event.toolKind === 'browser') return `browser:${event.agentId}:${event.turnId ?? ''}:${event.toolName ?? 'tool'}`
    return `tool:${event.agentId}:${event.turnId ?? ''}`
  }
  if (event.kind === 'agent') return `agent:${event.agentId}:${event.turnId ?? ''}:${event.type}:${event.label ?? ''}`
  if (event.kind === 'artifact') return `artifact:${event.agentId}:${event.turnId ?? ''}:${event.label ?? ''}`
  if (event.type === 'process.exited') {
    const pid = event.pid ?? (typeof event.payload?.pid === 'number' ? event.payload.pid : undefined)
    return `process:${event.agentId}:${event.turnId ?? ''}:${pid ?? event.eventId}`
  }
  return `lifecycle:${event.agentId}:${event.turnId ?? ''}:${event.type}`
}

export function processExitedLabel(event: ConversationActivityEvent): string {
  const summary = (event.summary ?? '').trim()
  if (summary.startsWith('Processo ')) {
    return summary
  }
  const pid =
    event.pid ??
    (typeof event.payload?.pid === 'number' && Number.isInteger(event.payload.pid) ? event.payload.pid : undefined)
  const rawLabel =
    event.label ??
    event.description ??
    event.command ??
    (typeof event.payload?.label === 'string' ? event.payload.label : undefined) ??
    (typeof event.payload?.description === 'string' ? event.payload.description : undefined) ??
    (typeof event.payload?.command === 'string' ? event.payload.command : undefined) ??
    ''
  const cleanLabel = rawLabel.trim().replace(/\s+/g, ' ').slice(0, 120)
  const prefix = pid !== undefined
    ? (cleanLabel ? `Processo ${pid} (${cleanLabel})` : `Processo ${pid}`)
    : (cleanLabel ? `Processo (${cleanLabel})` : 'Processo')
  const exitCode =
    event.exitCode !== undefined
      ? event.exitCode
      : typeof event.payload?.exit_code === 'number' && Number.isInteger(event.payload.exit_code)
        ? event.payload.exit_code
        : null
  if (exitCode !== null && exitCode !== undefined) {
    if (exitCode !== 0) {
      return `${prefix} terminou com erro (exit ${exitCode})`
    }
    return `${prefix} terminou (exit 0)`
  }
  return `${prefix} terminou`
}

export function groupLabel(group: ActivityGroup): string {
  const first = group.events[0]
  if (first && isVerifierEvent(first)) return 'Verificação independente'
  if (first && isMergeEvent(first)) return 'Integração da worktree'
  if (group.kind === 'tool') return toolGroupLabel(group)
  if (group.kind === 'agent') return agentLabel(first)
  if (group.kind === 'artifact') return first.label ? `Criou ${first.label}` : 'Criou um artefato'
  const latest = group.events[group.events.length - 1] ?? first
  if (latest.type === 'process.exited') {
    return processExitedLabel(latest)
  }
  const payloadMessage = typeof latest.payload?.message === 'string' ? latest.payload.message.trim() : ''
  if (payloadMessage && (!latest.summary || latest.summary === 'Aguardando reconciliação de efeito externo')) {
    return payloadMessage
  }
  return latest.summary || first.summary || activityStateLabel(group.state)
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
  const summaryText = latest.summary ?? ''
  if (toolEvents.length > 1 && summaryText.startsWith('$')) return 'Executando comandos'
  return summaryText || `Usou ${toolKindLabel(latest.toolKind)}`
}

function agentLabel(event: ConversationActivityEvent): string {
  const name = event.label ?? event.agentName
  switch (event.type) {
    case 'agent.created': return name ? `Criou o agente ${name}` : 'Criou um agente'
    case 'agent.message_sent': return name ? `Enviou uma tarefa para ${name}` : 'Enviou uma tarefa'
    case 'agent.message_received': return name ? `Recebeu a resposta de ${name}` : 'Recebeu uma resposta'
    case 'agent.message_queued': return name ? `Na fila para ${name}` : 'Na fila para o subagente'
    case 'agent.message_delivered': return name ? `Entregue a ${name}` : 'Entregue ao subagente'
    case 'agent.message_forwarded': return name ? `Repassada ao Orin (${name})` : 'Repassada ao Orin'
    case 'agent.message_expired': return name ? `Expirou (${name})` : 'Mensagem expirou'
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
    case 'completed_with_caveats': return 'Concluído com pendências'
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
    case 'media': return 'mídia'
    default: return 'ferramentas'
  }
}

type ActionClass = 'read' | 'edit' | 'command' | 'web' | 'memory' | 'skill' | 'tool'

const READ_TOOLS = new Set(['read_file', 'view_file', 'transcribe_pdf', 'list_files', 'search_files', 'search_code', 'project_map', 'read_process_output'])
const EDIT_TOOLS = new Set(['write_file', 'edit_file', 'write_contract'])
const COMMAND_TOOLS = new Set(['run_command', 'bash', 'powershell', 'stop_process', 'verify_project', 'verify_frontend'])

function actionClass(event: ConversationActivityEvent): ActionClass {
  const name = event.toolName ?? ''
  if (READ_TOOLS.has(name)) return 'read'
  if (EDIT_TOOLS.has(name)) return 'edit'
  if (COMMAND_TOOLS.has(name)) return 'command'
  if (name.includes('skill')) return 'skill'
  if (event.toolKind === 'filesystem') return 'read'
  if (event.toolKind === 'terminal') return 'command'
  if (event.toolKind === 'web' || event.toolKind === 'browser') return 'web'
  if (event.toolKind === 'memory') return 'memory'
  return 'tool'
}

const ACTION_PHRASES: Record<ActionClass, [singular: string, plural: string]> = {
  read: ['leu um arquivo', 'explorou {n} arquivos'],
  edit: ['editou um arquivo', 'editou {n} arquivos'],
  command: ['executou um comando', 'executou {n} comandos'],
  web: ['consultou a web', 'consultou a web {n} vezes'],
  memory: ['usou a memória', 'usou a memória {n} vezes'],
  skill: ['usou uma skill', 'usou {n} skills'],
  tool: ['usou uma ferramenta', 'usou {n} ferramentas'],
}

const ACTION_ORDER: ActionClass[] = ['read', 'edit', 'command', 'web', 'memory', 'skill', 'tool']

/** The calls a tool group really made; folded `artifact.created` records and `waiting_model` notices are not calls. */
export function toolCalls(group: ActivityGroup): ConversationActivityEvent[] {
  return group.events.filter((event) => event.kind === 'tool' && !isWaitingModelEvent(event))
}

/**
 * The settled one-line account of a tool batch, in the order a person would
 * narrate it: "Explorou 3 arquivos, executou 4 comandos, usou uma ferramenta".
 * A batch of one call keeps that call's own label, which already says it best.
 */
export function toolBatchOverview(group: ActivityGroup): string {
  const calls = toolCalls(group)
  if (calls.length <= 1) return group.label
  const counts = new Map<ActionClass, number>()
  for (const call of calls) {
    const key = actionClass(call)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const parts = ACTION_ORDER.filter((key) => counts.has(key)).map((key) => {
    const n = counts.get(key) as number
    const [singular, plural] = ACTION_PHRASES[key]
    return n === 1 ? singular : plural.replace('{n}', String(n))
  })
  const sentence = parts.join(', ')
  return sentence.charAt(0).toUpperCase() + sentence.slice(1)
}

/** `5 min 28 s`, `42 s` — the elapsed clock shown beside a running batch. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`
}

/**
 * Formats how long a completed turn took from `started_at` to `finished_at`:
 * `"Trabalhou por 14s"`, `"Trabalhou por 2m 15s"`, `"Trabalhou por 1h 5m"`.
 */
export function formatTurnWorkDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string | null {
  if (!startedAt || !finishedAt) return null
  const startMs = Date.parse(startedAt)
  const endMs = Date.parse(finishedAt)
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return null
  }
  const totalSeconds = Math.max(1, Math.round((endMs - startMs) / 1000))
  if (totalSeconds < 60) {
    return `Trabalhou por ${totalSeconds}s`
  }
  if (totalSeconds < 3600) {
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return seconds > 0
      ? `Trabalhou por ${minutes}m ${seconds}s`
      : `Trabalhou por ${minutes}m`
  }
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  return minutes > 0
    ? `Trabalhou por ${hours}h ${minutes}m`
    : `Trabalhou por ${hours}h`
}

/**
 * Splits a call's one-line summary into the verb ("Leu") and its target
 * ("Home.jsx") so the row can set the target in heavier type. Commands (`$ ...`)
 * and summaries without a leading verb stay whole.
 */
export function splitActionSummary(summary: string): { verb: string; target: string } {
  const text = summary.trim()
  if (text.startsWith('$')) return { verb: 'Executou', target: text.replace(/^\$\s*/, '') }
  if (!text) return { verb: '', target: text }
  const space = text.indexOf(' ')
  if (space <= 0) return { verb: '', target: text }
  return { verb: text.slice(0, space), target: text.slice(space + 1) }
}
