import { createElement } from 'react'
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { isRenderable, summarizeActivities } from '../../src/features/conversations/activitySummary'
import { AgentPulse, modeFromEvents } from '../../src/features/conversations/AgentPulse'
import { buildTurnTimeline } from '../../src/features/conversations/turnTimelineFold'
import { kindFor, stateFor, type ConversationActivityEvent } from '../../src/features/conversations/activityTypes'

let sequence = 0

function event(partial: Partial<ConversationActivityEvent> & { type: string }): ConversationActivityEvent {
  sequence += 1
  const toolKind = partial.toolKind
  return {
    eventId: partial.eventId ?? `activity:turn-1:${sequence}`,
    cursor: `a.${sequence}`,
    kind: partial.kind ?? kindFor(partial.type, toolKind),
    state: partial.state ?? stateFor(partial.type, partial.status, partial.errorCode),
    agentId: partial.agentId ?? 'agent:c:main',
    summary: partial.summary ?? partial.type,
    ...partial,
  }
}

function toolPair(name: string, toolKind: string, summary: string, invocationId: string, status = 'succeeded') {
  return [
    event({ type: 'tool.started', toolName: name, toolKind, invocationId, summary: `Executando ${name}` }),
    event({ type: 'tool.finished', toolName: name, toolKind, invocationId, status, summary }),
  ]
}

describe('activity grouping', () => {
  it('keeps one readable row while a sequence moves between tool families', () => {
    const events = [
      ...toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1'),
      ...toolPair('run_command', 'terminal', 'Executou os testes', 'call-2'),
      ...toolPair('search_web', 'web', 'Confirmou a documentação', 'call-3'),
    ]

    const groups = summarizeActivities(events)

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(3)
    expect(groups[0].label).toBe('Confirmou a documentação')
    expect(groups[0].events).toHaveLength(3)
  })

  it('keeps a single tool call labelled by what it actually did', () => {
    const groups = summarizeActivities(toolPair('run_command', 'terminal', '$ npm test', 'call-9'))

    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('$ npm test')
  })

  it('drops the started event once its finished event has arrived', () => {
    const events = toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1')

    expect(summarizeActivities(events)[0].events.every((item) => item.type === 'tool.finished')).toBe(true)
  })

  it('keeps a still-running tool visible before it finishes', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.started', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-live', summary: 'Executando run_command' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].state).toBe('waiting_tool')
  })

  it('updates the same row instead of splitting different ordinary tools', () => {
    const groups = summarizeActivities([
      ...toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1'),
      ...toolPair('run_command', 'terminal', '$ ls', 'call-2'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(2)
    expect(groups[0].label).toBe('Executando comandos')
  })

  it('keeps browser captures separate from the ordinary tool sequence', () => {
    const groups = summarizeActivities([
      ...toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1'),
      ...toolPair('inspect_page', 'browser', 'Inspecionou a página', 'call-2'),
      ...toolPair('run_command', 'terminal', 'Executou os testes', 'call-3'),
    ])

    expect(groups).toHaveLength(3)
    expect(groups.map((group) => group.label)).toEqual(['Leu a.txt', 'Inspecionou a página', 'Executou os testes'])
  })

  it('keeps a plugin approval card separate from plugin search activity', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', toolName: 'search_plugin', toolKind: 'plugin', invocationId: 'search-1', status: 'succeeded', summary: '1 plugin encontrado(s)' }),
      event({
        type: 'tool.finished', toolName: 'install_plugin', toolKind: 'plugin', invocationId: 'install-1', status: 'succeeded',
        summary: 'Aguardando aprovação do plugin superpowers',
        pluginApproval: {
          plugin_id: 'superpowers', version: '6.3.0', display_name: 'superpowers', description: '', author: '', warnings: [],
          skills: [{ skill_id: 'superpowers:brainstorming', name: 'brainstorming' }], mcp_servers: [], agents: [], commands: [], hooks: [], contribution_count: 1,
        },
      }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].events[0].toolName).toBe('search_plugin')
    expect(groups[1].events[0].toolName).toBe('install_plugin')
    expect(groups[1].events[0].pluginApproval?.plugin_id).toBe('superpowers')
  })

  it('marks a group as failed when any member failed', () => {
    const groups = summarizeActivities([
      ...toolPair('run_command', 'terminal', '$ ok', 'call-1'),
      ...toolPair('run_command', 'terminal', '$ boom', 'call-2', 'failed'),
    ])

    expect(groups[0].failed).toBe(true)
    expect(groups[0].state).toBe('failed')
  })

  it('promotes agent creation and messaging to their own rows', () => {
    const groups = summarizeActivities([
      event({ type: 'agent.created', label: 'Researcher', summary: 'Criou o agente Researcher', agentId: 'agent:c:sub' }),
      event({ type: 'agent.message_sent', label: 'Researcher', content: 'Investigue X', summary: 'Enviou uma tarefa para Researcher' }),
      event({ type: 'agent.message_received', label: 'Researcher', content: 'Encontrei Y', summary: 'Recebeu a resposta de Researcher', agentId: 'agent:c:sub' }),
    ])

    expect(groups.map((group) => group.events[0].type)).toEqual(['agent.created', 'agent.message_sent', 'agent.message_received'])
    expect(groups.every((group) => group.kind === 'agent')).toBe(true)
  })

  it('never renders raw assistant deltas as activity rows', () => {
    const groups = summarizeActivities([
      event({ type: 'assistant.delta', content: 'parcial', messageId: 'msg-1' }),
      event({ type: 'assistant.delta', content: 'mais', messageId: 'msg-1' }),
    ])

    expect(groups).toEqual([])
  })

  it('keeps one stable Code mode card while lifecycle events update its state', () => {
    const groups = summarizeActivities([
      event({ type: 'code_mode.activated', turnId: 'turn-code', codeStage: 'planning', summary: 'Modo Code ativado' }),
      event({ type: 'tool.finished', turnId: 'turn-code', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'plan', status: 'succeeded', summary: 'Plano salvo' }),
      event({ type: 'code_mode.plan_ready', turnId: 'turn-code', codeStage: 'planning', summary: 'Plano pronto' }),
      event({ type: 'code_mode.validation_started', turnId: 'turn-code', codeStage: 'validating', summary: 'Executando validação' }),
    ])

    const codeGroups = groups.filter((group) => group.id === 'code-mode:turn-code')
    expect(codeGroups).toHaveLength(1)
    expect(codeGroups[0]).toMatchObject({ count: 3, state: 'working', label: 'Executando validação' })
    expect(codeGroups[0].events.map((item) => item.codeStage)).toEqual(['planning', 'planning', 'validating'])
  })

  it('does not render a redundant waiting-for-you line next to the ask_user card', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.started', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-1', summary: 'Perguntou ao usuário', questions: [{ id: 'q1', question: 'Prints?', mode: 'text', options: [] }] }),
      event({ type: 'tool.finished', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-1', status: 'succeeded', summary: 'Perguntou ao usuário' }),
      event({ type: 'turn.waiting_user', summary: 'Aguardando sua resposta' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].kind).toBe('tool')
    expect(groups[0].events[0].toolName).toBe('ask_user')
  })

  it('keeps a second ask_user in a later turn as its own card instead of merging into the first', () => {
    const groups = summarizeActivities([
      event({
        type: 'tool.started', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-1', turnId: 'turn-1',
        summary: 'Perguntou ao usuário', questions: [{ id: 'q1', question: 'Qual API?', mode: 'text', options: [] }],
      }),
      event({
        type: 'tool.finished', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-1', turnId: 'turn-1', status: 'succeeded',
        summary: 'Perguntou ao usuário', questions: [{ id: 'q1', question: 'Qual API?', mode: 'text', options: [] }],
      }),
      event({ type: 'turn.waiting_user', turnId: 'turn-1', summary: 'Aguardando sua resposta' }),
      // The user answers, a new turn starts, and the agent asks again — a
      // second, unrelated question with nothing else observable in between.
      event({
        type: 'tool.started', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-2', turnId: 'turn-2',
        summary: 'Perguntou ao usuário', questions: [{ id: 'q2', question: 'Qual ambiente?', mode: 'text', options: [] }],
      }),
      event({
        type: 'tool.finished', toolName: 'ask_user', toolKind: 'user_input', invocationId: 'call-2', turnId: 'turn-2', status: 'succeeded',
        summary: 'Perguntou ao usuário', questions: [{ id: 'q2', question: 'Qual ambiente?', mode: 'text', options: [] }],
      }),
      event({ type: 'turn.waiting_user', turnId: 'turn-2', summary: 'Aguardando sua resposta' }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].events[0].turnId).toBe('turn-1')
    expect(groups[1].events[0].turnId).toBe('turn-2')
    expect(groups[1].events[0].questions?.[0].id).toBe('q2')
  })

  it('hides the bookkeeping lifecycle lines but keeps terminal ones', () => {
    const groups = summarizeActivities([
      event({ type: 'turn.started', summary: 'Turn started' }),
      event({ type: 'tool.requested', summary: 'Preparando ferramentas' }),
      event({ type: 'turn.completed', summary: 'Resposta concluída' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].events[0].type).toBe('turn.completed')
  })

  it('folds a write and the artifact.created it emits into the same row, not a duplicate one', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'w1', status: 'succeeded', summary: 'Escreveu a.txt' }),
      event({ type: 'artifact.created', turnId: 'turn-1', label: 'a.txt', path: 'a.txt', summary: 'Criou a.txt' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].kind).toBe('tool')
    expect(groups[0].label).toBe('Escreveu a.txt')
    expect(groups[0].state).toBe('completed')
    expect(groups[0].events).toHaveLength(2)
  })

  it('keeps a whole run of writes as one expandable batch instead of one card per file', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'w1', status: 'succeeded', summary: 'Escreveu a.tsx' }),
      event({ type: 'artifact.created', turnId: 'turn-1', label: 'a.tsx', path: 'a.tsx', summary: 'Criou a.tsx' }),
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'w2', status: 'succeeded', summary: 'Escreveu b.ts' }),
      event({ type: 'artifact.created', turnId: 'turn-1', label: 'b.ts', path: 'b.ts', summary: 'Criou b.ts' }),
    ])

    // The label follows the latest *tool* call, not the trailing artifact
    // record, and never turns into a generic count for an ordinary sequence.
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('Escreveu b.ts')
    expect(groups[0].events).toHaveLength(4)
  })

  it('gives a standalone artifact its own row when there is no open tool batch to attach to', () => {
    const groups = summarizeActivities([
      event({ type: 'artifact.created', turnId: 'turn-1', label: 'a.txt', path: 'a.txt', summary: 'Criou a.txt' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].kind).toBe('artifact')
    expect(groups[0].state).toBe('completed')
  })

  it('does not attach an artifact to a tool batch left over from an earlier turn', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'w1', status: 'succeeded', summary: 'Escreveu a.txt' }),
      event({ type: 'artifact.created', turnId: 'turn-2', label: 'b.txt', path: 'b.txt', summary: 'Criou b.txt' }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[1].kind).toBe('artifact')
  })

  it('merges tool.progressed outputTail into the running tool without inflating count, and drops it once finished', () => {
    const runningGroups = summarizeActivities([
      event({ type: 'tool.started', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-stream', summary: 'Executando run_command' }),
      event({ type: 'tool.progressed', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-stream', summary: 'Executando pytest', outputTail: '10 passed', elapsedMs: 650 }),
    ])

    expect(runningGroups).toHaveLength(1)
    expect(runningGroups[0].count).toBe(1)
    expect(runningGroups[0].state).toBe('waiting_tool')
    expect(runningGroups[0].events).toHaveLength(1)
    expect(runningGroups[0].events[0].outputTail).toBe('10 passed')

    const settledGroups = summarizeActivities([
      event({ type: 'tool.started', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-stream', summary: 'Executando run_command' }),
      event({ type: 'tool.progressed', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-stream', summary: 'Executando pytest', outputTail: '10 passed', elapsedMs: 650 }),
      event({ type: 'tool.finished', toolName: 'run_command', toolKind: 'terminal', invocationId: 'call-stream', status: 'succeeded', summary: '$ pytest' }),
    ])

    expect(settledGroups).toHaveLength(1)
    expect(settledGroups[0].count).toBe(1)
    expect(settledGroups[0].state).toBe('completed')
    expect(settledGroups[0].events).toHaveLength(1)
    expect(settledGroups[0].events[0].type).toBe('tool.finished')
  })

  it('never overwrites a completed tool.finished row when tool.progressed lacks invocationId or reuses synthetic tool-call:1 across iterations', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.started', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', invocationId: 'tool-call:1', summary: 'Executando run_command' }),
      event({ type: 'tool.progressed', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', summary: 'Executando npm create vite', outputTail: 'Operation cancelled' }),
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', invocationId: 'tool-call:1', status: 'succeeded', summary: '$ npm create vite' }),
      event({ type: 'tool.started', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', invocationId: 'tool-call:1', summary: 'Executando run_command' }),
      event({ type: 'tool.progressed', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', summary: 'Executando npm install', outputTail: 'added 50 packages' }),
      event({ type: 'tool.finished', turnId: 'turn-1', toolName: 'run_command', toolKind: 'terminal', invocationId: 'tool-call:1', status: 'succeeded', summary: '$ npm install' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(2)
    expect(groups[0].state).toBe('completed')
    expect(groups[0].events).toHaveLength(2)
    expect(groups[0].events.every((item) => item.type === 'tool.finished' && item.state === 'completed')).toBe(true)
  })

  it('anchors the Code mode card at the top of the turn without splitting a tool batch when code_mode.activated rolled out of the window', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', turnId: 'turn-code', toolName: 'run_command', toolKind: 'terminal', invocationId: 'c1', status: 'succeeded', summary: '$ npm create vite' }),
      event({ type: 'code_mode.validation_started', turnId: 'turn-code', codeStage: 'validating', summary: 'Executando validação' }),
      event({ type: 'tool.finished', turnId: 'turn-code', toolName: 'run_command', toolKind: 'terminal', invocationId: 'c2', status: 'succeeded', summary: '$ npm run build' }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].id).toBe('code-mode:turn-code')
    expect(groups[1].kind).toBe('tool')
    expect(groups[1].count).toBe(2)
  })

  it('keeps count at 1 when duplicate turn.failed lifecycle events arrive on cancel', () => {
    const groups = summarizeActivities([
      event({ type: 'turn.failed', turnId: 'turn-cancel', state: 'cancelled', summary: 'Execução cancelada' }),
      event({ type: 'turn.failed', turnId: 'turn-cancel', state: 'cancelled', summary: 'Cancelado' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(1)
    expect(groups[0].state).toBe('cancelled')
  })

  it('treats agent.thought_delta as renderable in isRenderable', () => {
    const thoughtEvent = event({
      type: 'agent.thought_delta',
      turnId: 'turn-1',
      detail: 'Pensando na arquitetura...',
    })
    expect(isRenderable(thoughtEvent, new Set())).toBe(true)
  })

  it('folds consecutive agent.thought_delta events into a streaming thought item and marks it completed with durationMs once later events arrive', () => {
    const streamingEvents = [
      event({
        type: 'agent.thought_delta',
        turnId: 'turn-1',
        occurredAt: '2026-10-16T12:00:00.000Z',
        payload: { delta: 'Analisando ' },
      }),
      event({
        type: 'agent.thought_delta',
        turnId: 'turn-1',
        occurredAt: '2026-10-16T12:00:02.500Z',
        detail: 'o esquema do banco.',
      }),
    ]

    const streamingTimeline = buildTurnTimeline(streamingEvents, 'turn-1', 'msg-1')
    expect(streamingTimeline).toHaveLength(1)
    expect(streamingTimeline[0]).toMatchObject({
      kind: 'thought',
      thought: {
        turnId: 'turn-1',
        content: 'Analisando o esquema do banco.',
        status: 'streaming',
        startedAt: '2026-10-16T12:00:00.000Z',
      },
    })

    const completedEvents = [
      ...streamingEvents,
      event({
        type: 'assistant.delta',
        turnId: 'turn-1',
        messageId: 'msg-1',
        occurredAt: '2026-10-16T12:00:03.000Z',
        content: 'Aqui está a solução.',
      }),
    ]

    const completedTimeline = buildTurnTimeline(completedEvents, 'turn-1', 'msg-1')
    expect(completedTimeline.map((item) => item.kind)).toEqual(['thought', 'text'])
    if (completedTimeline[0].kind === 'thought') {
      expect(completedTimeline[0].thought.status).toBe('completed')
      expect(completedTimeline[0].thought.content).toBe('Analisando o esquema do banco.')
      expect(completedTimeline[0].thought.durationMs).toBeGreaterThanOrEqual(2500)
    }
  })

  it('immediately creates a running tool call item when tool.progressed has phase === generating_arguments before tool.started', () => {
    const events = [
      event({
        type: 'tool.progressed',
        turnId: 'turn-1',
        invocationId: 'call-gen-1',
        payload: {
          phase: 'generating_arguments',
          tool_name: 'write_file',
          bytes_received: 4096,
        },
      }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')
    expect(timeline).toHaveLength(1)
    expect(timeline[0].kind).toBe('activity')
    if (timeline[0].kind === 'activity') {
      expect(timeline[0].group.label).toContain('Preparando write_file...')
      expect(timeline[0].group.events[0]).toMatchObject({
        status: 'running',
        toolName: 'write_file',
        summary: 'Preparando write_file...',
      })
    }
  })
})

describe('AgentPulse & modeFromEvents', () => {
  it('filters events by activeTurnId, recognizes agent.thought_delta and generating_arguments, and transitions stale assistant.delta (>=2500ms) from responding to thinking', () => {
    const now = Date.parse('2026-10-16T12:10:00.000Z')
    const pastTurnEvents = [
      event({ type: 'turn.completed', turnId: 'turn-old', state: 'completed' }),
    ]

    // Active turn has no events yet -> ignores turn-old's turn.completed and returns 'thinking'
    expect(modeFromEvents(pastTurnEvents, 'turn-active')).toBe('thinking')

    // Active turn has agent.thought_delta -> 'thinking'
    expect(
      modeFromEvents(
        [
          ...pastTurnEvents,
          event({ type: 'agent.thought_delta', turnId: 'turn-active', detail: 'Pensando...' }),
        ],
        'turn-active',
      ),
    ).toBe('thinking')

    // Active turn has tool.progressed with phase === 'generating_arguments' -> 'thinking'
    expect(
      modeFromEvents(
        [
          event({
            type: 'tool.progressed',
            turnId: 'turn-active',
            payload: { phase: 'generating_arguments', tool_name: 'edit_file' },
          }),
        ],
        'turn-active',
      ),
    ).toBe('thinking')

    // Recent assistant.delta (< 2500ms ago) -> 'responding'
    expect(
      modeFromEvents(
        [
          event({
            type: 'assistant.delta',
            turnId: 'turn-active',
            occurredAt: new Date(now - 800).toISOString(),
            content: 'Vou atualizar:',
          }),
        ],
        'turn-active',
        now,
      ),
    ).toBe('responding')

    // Stale assistant.delta (>= 2500ms ago) while turn is still running -> transitions to 'thinking'
    expect(
      modeFromEvents(
        [
          event({
            type: 'assistant.delta',
            turnId: 'turn-active',
            occurredAt: new Date(now - 3000).toISOString(),
            content: 'Vou atualizar:',
          }),
        ],
        'turn-active',
        now,
      ),
    ).toBe('thinking')
  })

  it('renders shimmer class on thinking/planning label and updates live elapsed counter every second even under reduced motion', () => {
    vi.useFakeTimers()
    try {
      const { container, unmount } = render(
        createElement(AgentPulse, { mode: 'thinking', activeTurnId: 'turn-1' }),
      )

      const label = container.querySelector('.agent-pulse__label')
      expect(label).not.toBeNull()
      expect(label?.className).toContain('agent-pulse__label--shimmer')
      expect(screen.getByText('Pensando')).toBeInTheDocument()
      expect(container.querySelector('.agent-pulse__elapsed')).toBeNull()

      act(() => {
        vi.advanceTimersByTime(3000)
      })

      const elapsed = container.querySelector('.agent-pulse__elapsed')
      expect(elapsed).not.toBeNull()
      expect(elapsed?.textContent).toBe('3s')

      act(() => {
        vi.advanceTimersByTime(72000)
      })
      expect(container.querySelector('.agent-pulse__elapsed')?.textContent).toBe('1m 15s')

      unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('auto-transitions AgentPulse from responding to thinking after >=2500ms of silence and respects prefers-reduced-motion', () => {
    vi.useFakeTimers()
    const originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    try {
      const { container, unmount } = render(
        createElement(AgentPulse, { mode: 'responding', activeTurnId: 'turn-live' }),
      )

      expect(screen.getByText('Respondendo')).toBeInTheDocument()
      expect(container.querySelector('.agent-pulse__stage .agent-pulse__core')).not.toBeNull()
      expect(container.querySelector('.agent-pulse__lines, .agent-pulse__steps, .agent-pulse__scan, .agent-pulse__link')).toBeNull()

      act(() => {
        vi.advanceTimersByTime(3000)
      })

      expect(screen.getByText('Pensando')).toBeInTheDocument()
      expect(container.querySelector('.agent-pulse')?.getAttribute('data-mode')).toBe('thinking')
      expect(container.querySelector('.agent-pulse__label')?.className).toContain('agent-pulse__label--shimmer')
      expect(container.querySelector('.agent-pulse__elapsed')?.textContent).toBe('3s')

      unmount()
    } finally {
      window.matchMedia = originalMatchMedia
      vi.useRealTimers()
    }
  })
})


