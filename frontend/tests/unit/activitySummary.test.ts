import { describe, expect, it } from 'vitest'
import { summarizeActivities } from '../../src/features/conversations/activitySummary'
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
  it('collapses a run of same-family tools into one readable row', () => {
    const events = [
      ...toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1'),
      ...toolPair('read_file', 'filesystem', 'Leu b.txt', 'call-2'),
      ...toolPair('read_file', 'filesystem', 'Leu c.txt', 'call-3'),
    ]

    const groups = summarizeActivities(events)

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(3)
    expect(groups[0].label).toBe('3 operações em arquivos')
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

  it('does not merge tools from different families', () => {
    const groups = summarizeActivities([
      ...toolPair('read_file', 'filesystem', 'Leu a.txt', 'call-1'),
      ...toolPair('run_command', 'terminal', '$ ls', 'call-2'),
    ])

    expect(groups.map((group) => group.kind)).toEqual(['tool', 'tool'])
    expect(groups).toHaveLength(2)
  })

  it('keeps a plugin approval card separate from plugin search activity', () => {
    const groups = summarizeActivities([
      event({ type: 'tool.finished', toolName: 'search_plugin', toolKind: 'plugin', invocationId: 'search-1', status: 'succeeded', summary: '1 plugin encontrado(s)' }),
      event({
        type: 'tool.finished', toolName: 'install_plugin', toolKind: 'plugin', invocationId: 'install-1', status: 'succeeded',
        summary: 'Aguardando aprovação do plugin superpowers',
        pluginApproval: {
          plugin_id: 'superpowers', version: '6.3.0', display_name: 'superpowers', description: '', author: '', warnings: [],
          skills: [{ skill_id: 'superpowers:brainstorming', name: 'brainstorming' }], mcp_servers: [], agents: [], contribution_count: 1,
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
})
