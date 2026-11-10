import { describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../../activityTypes'
import { foldScene, statusLabelFor } from '../sceneReducer'
import { MAIN_AGENT_ID } from '../sceneTypes'

function makeEvent(
  overrides: Partial<ConversationActivityEvent> & {
    eventId: string
    type: ConversationActivityEvent['type']
  },
): ConversationActivityEvent {
  return {
    cursor: overrides.eventId,
    kind: 'lifecycle',
    state: 'working',
    agentId: MAIN_AGENT_ID,
    summary: '',
    turnId: 'turn-1',
    ...overrides,
  }
}

describe('sceneReducer', () => {
  it('(a) com zero eventos retorna apenas o Orin, descansando quando running=false e ativo quando running=true', () => {
    const idleState = foldScene([], { running: false })
    expect(idleState.live).toBe(false)
    expect(idleState.lightsOn).toBe(false)
    expect(idleState.turnId).toBeNull()
    expect(idleState.agents).toHaveLength(1)
    expect(idleState.agents[0]).toMatchObject({
      id: MAIN_AGENT_ID,
      isMain: true,
      name: 'Orin',
      parentId: null,
      depth: 0,
      station: 'lounge',
      pose: 'sit',
      status: 'resting',
      statusLabel: 'Descansando',
      bubble: null,
    })
    expect(idleState.transfers).toEqual([])
    expect(idleState.effects).toEqual([])
    expect(idleState.pending).toEqual([])

    const activeState = foldScene(null, { running: true })
    expect(activeState.live).toBe(true)
    expect(activeState.lightsOn).toBe(true)
    expect(activeState.agents).toHaveLength(1)
    expect(activeState.agents[0]).toMatchObject({
      id: MAIN_AGENT_ID,
      isMain: true,
      station: 'meeting',
      pose: 'idle',
      status: 'working',
      statusLabel: 'Trabalhando',
    })
  })

  it('(b) mantém dois subagentes em paralelo com eventos intercalados isolados em suas próprias estações, ferramentas e tarefas', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started', summary: 'Início' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-alpha',
        parentAgentId: 'agent:conv:main',
        agentName: 'Alpha',
        payload: { subagent_type: 'Explore' },
      }),
      makeEvent({
        eventId: 'e3',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-beta',
        parentAgentId: 'main',
        agentName: 'Beta',
        payload: { subagent_type: 'antigravity' },
      }),
      makeEvent({
        eventId: 'e4',
        type: 'tool.started',
        kind: 'tool',
        agentId: 'sub-alpha',
        toolName: 'read_file',
        summary: 'Lendo schema.ts',
      }),
      makeEvent({
        eventId: 'e5',
        type: 'tool.started',
        kind: 'tool',
        agentId: 'sub-beta',
        toolName: 'run_command',
        summary: 'Rodando vitest',
      }),
      makeEvent({
        eventId: 'e6',
        type: 'tool.progressed',
        kind: 'tool',
        agentId: 'sub-alpha',
        toolName: 'search_code',
        summary: 'Buscando referências',
      }),
    ]

    const state = foldScene(events, { running: true })
    expect(state.agents.map((a) => a.id)).toEqual([MAIN_AGENT_ID, 'sub-alpha', 'sub-beta'])

    const alpha = state.agents.find((a) => a.id === 'sub-alpha')!
    const beta = state.agents.find((a) => a.id === 'sub-beta')!

    expect(alpha).toMatchObject({
      name: 'Alpha',
      subagentType: 'Explore',
      parentId: MAIN_AGENT_ID,
      depth: 1,
      station: 'files',
      pose: 'work',
      status: 'working',
      currentTool: 'search_code',
      task: 'Buscando referências',
    })
    expect(alpha.activities.map((act) => act.label)).toEqual([
      'Lendo schema.ts',
      'Buscando referências',
    ])

    expect(beta).toMatchObject({
      name: 'Beta',
      subagentType: 'antigravity',
      parentId: MAIN_AGENT_ID,
      depth: 1,
      station: 'terminal',
      pose: 'work',
      status: 'working',
      currentTool: 'run_command',
      task: 'Rodando vitest',
    })
    expect(beta.activities.map((act) => act.label)).toEqual(['Rodando vitest'])
  })

  it('(c) constrói a árvore de delegação depth-first com parentId e depth corretos para subagente criado por outro subagente', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-lead-1',
        parentAgentId: MAIN_AGENT_ID,
        agentName: 'Arquiteto',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-lead-2',
        parentAgentId: MAIN_AGENT_ID,
        agentName: 'QA',
      }),
      makeEvent({
        eventId: 'e4',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-nested-1',
        parentAgentId: 'sub-lead-1',
        agentName: 'Implementador',
      }),
    ]

    const state = foldScene(events, { running: true })
    expect(
      state.agents.map((a) => ({ id: a.id, parentId: a.parentId, depth: a.depth })),
    ).toEqual([
      { id: MAIN_AGENT_ID, parentId: null, depth: 0 },
      { id: 'sub-lead-1', parentId: MAIN_AGENT_ID, depth: 1 },
      { id: 'sub-nested-1', parentId: 'sub-lead-1', depth: 2 },
      { id: 'sub-lead-2', parentId: MAIN_AGENT_ID, depth: 1 },
    ])
  })

  it('(d) agent.created + agent.message_sent geram transfer delegation do pai para o filho, e agent.message_received gera return filho→pai com filho completed', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-1',
        parentAgentId: 'agent:conv:main',
        agentName: 'Pesquisador',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'agent.message_sent',
        kind: 'agent',
        agentId: 'agent:conv:main',
        agentName: 'Pesquisador',
        content: 'Analise os arquivos de rotas',
        payload: { to_agent_id: 'sub-1' },
      }),
    ]

    const midState = foldScene(events, { running: true })
    const orinMid = midState.agents[0]
    const subMid = midState.agents[1]

    // Orin name must remain Orin (not overwritten by recipient agentName on message_sent)
    expect(orinMid.name).toBe('Orin')
    expect(orinMid.status).toBe('waiting_agent')
    expect(orinMid.pose).toBe('wait')
    expect(subMid.name).toBe('Pesquisador')
    expect(
      midState.transfers.some(
        (t) => t.kind === 'delegation' && t.fromId === MAIN_AGENT_ID && t.toId === 'sub-1',
      ),
    ).toBe(true)
    expect(
      midState.effects.some((ef) => ef.kind === 'poof' && ef.agentId === 'sub-1'),
    ).toBe(true)

    const afterReturn = foldScene(
      [
        ...events,
        makeEvent({
          eventId: 'e4',
          type: 'agent.message_received',
          kind: 'agent',
          agentId: 'sub-1',
          parentAgentId: 'agent:conv:main',
          summary: 'Rotas analisadas',
          occurredAt: '2026-11-09T12:00:00.000Z',
          payload: { from_agent_id: 'sub-1' },
        }),
      ],
      { running: true },
    )

    const orinAfter = afterReturn.agents[0]
    const subAfter = afterReturn.agents[1]
    expect(subAfter.status).toBe('completed')
    expect(subAfter.statusLabel).toBe('Concluído')
    expect(subAfter.pose).toBe('deliver')
    // The receipt describes the parent receiving: not the child's task.
    expect(subAfter.task).toBe('Analise os arquivos de rotas')
    expect(orinAfter.status).toBe('working')
    expect(orinAfter.pose).toBe('idle')
    expect(afterReturn.transfers).toContainEqual(
      expect.objectContaining({
        id: 'e4',
        kind: 'return',
        fromId: 'sub-1',
        toId: MAIN_AGENT_ID,
      }),
    )
  })

  it('(e) tool.failed gera effect spark na estação, pose scared e bubble error', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'tool.started',
        kind: 'tool',
        toolName: 'run_command',
        summary: 'npm test',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'tool.failed',
        kind: 'tool',
        state: 'failed',
        toolName: 'run_command',
        summary: 'Comando falhou com código 1',
      }),
    ]

    const state = foldScene(events, { running: true })
    const orin = state.agents[0]
    expect(orin.station).toBe('terminal')
    expect(orin.pose).toBe('scared')
    expect(orin.status).toBe('failed')
    expect(orin.statusLabel).toBe('Falhou')
    expect(orin.bubble).toEqual({
      kind: 'error',
      text: 'Comando falhou com código 1',
      eventId: 'e3',
    })
    expect(state.effects).toContainEqual({
      id: 'e3',
      kind: 'spark',
      agentId: MAIN_AGENT_ID,
      station: 'terminal',
    })
  })

  it('(f) permissão pendente cria pending no agente certo com raise_hand e desaparece quando resolvida', () => {
    const pendingEvents: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'tool.permission_required',
        kind: 'tool',
        state: 'waiting_user',
        agentId: 'sub-writer',
        agentName: 'Escritor',
        summary: 'Permitir escrita em config.json',
        toolPermission: {
          requestId: 'perm-1',
          toolName: 'write_file',
          state: 'pending',
        },
      }),
    ]

    const waitingState = foldScene(pendingEvents, { running: true })
    const writerWaiting = waitingState.agents.find((a) => a.id === 'sub-writer')!
    expect(writerWaiting.pose).toBe('raise_hand')
    expect(writerWaiting.status).toBe('waiting_user')
    expect(writerWaiting.statusLabel).toBe('Esperando você')
    expect(waitingState.pending).toEqual([
      {
        id: 'perm-1',
        agentId: 'sub-writer',
        kind: 'permission',
        eventId: 'e2',
        summary: 'Permitir escrita em config.json',
        requestId: 'perm-1',
      },
    ])

    const deniedState = foldScene(
      [
        ...pendingEvents,
        makeEvent({
          eventId: 'e3',
          type: 'tool.permission_resolved',
          kind: 'tool',
          state: 'failed',
          agentId: 'sub-writer',
          toolPermission: {
            requestId: 'perm-1',
            toolName: 'write_file',
            state: 'denied',
          },
        }),
      ],
      { running: true },
    )

    const writerAfter = deniedState.agents.find((a) => a.id === 'sub-writer')!
    expect(deniedState.pending).toEqual([])
    expect(writerAfter.pose).toBe('idle')
    expect(writerAfter.status).toBe('working')
    expect(writerAfter.bubble).toEqual({
      kind: 'speech',
      text: 'ok',
      eventId: 'e3',
    })
  })

  it('(g) turn.completed coloca todos os agentes no lounge descansando e limpa pending', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'tool.started',
        kind: 'tool',
        agentId: 'sub-1',
        toolName: 'write_file',
        summary: 'Escrevendo arquivo',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'turn.waiting_user',
        state: 'waiting_user',
        summary: 'Dúvida',
        questions: [
          {
            id: 'q1',
            question: 'Qual banco usar?',
            mode: 'single_choice',
            options: [],
          },
        ],
      }),
      makeEvent({
        eventId: 'e4',
        type: 'turn.completed',
        state: 'completed',
        summary: 'Turno concluído',
      }),
    ]

    const state = foldScene(events, { running: true })
    expect(state.pending).toEqual([])
    for (const agent of state.agents) {
      expect(agent.station).toBe('lounge')
      expect(agent.pose).toBe('sit')
      expect(agent.status).toBe('resting')
      expect(agent.statusLabel).toBe('Descansando')
    }
  })

  it('(h) running=false zera transfers, effects, pending e bubbles (sem replay), mantendo histórico textual dos agentes', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-1',
        agentName: 'Executor',
        summary: 'Iniciando executor',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'tool.started',
        kind: 'tool',
        agentId: 'sub-1',
        toolName: 'bash',
        summary: 'Executando build',
      }),
      makeEvent({
        eventId: 'e4',
        type: 'tool.finished',
        kind: 'tool',
        agentId: 'sub-1',
        toolName: 'bash',
        status: 'succeeded',
        summary: 'Build concluído',
      }),
    ]

    const state = foldScene(events, { running: false })
    expect(state.live).toBe(false)
    expect(state.transfers).toEqual([])
    expect(state.effects).toEqual([])
    expect(state.pending).toEqual([])

    for (const agent of state.agents) {
      expect(agent.station).toBe('lounge')
      expect(agent.pose).toBe('sit')
      expect(agent.status).toBe('resting')
      expect(agent.bubble).toBeNull()
    }

    const sub = state.agents.find((a) => a.id === 'sub-1')!
    expect(sub.name).toBe('Executor')
    expect(sub.task).toBe('Build concluído')
    expect(sub.activities.length).toBeGreaterThan(0)
  })

  it('(i) considera apenas os eventos do turno mais recente', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({
        eventId: 't1-start',
        turnId: 'turn-old',
        type: 'turn.started',
      }),
      makeEvent({
        eventId: 't1-sub',
        turnId: 'turn-old',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-old',
        agentName: 'Antigo',
      }),
      makeEvent({
        eventId: 't1-end',
        turnId: 'turn-old',
        type: 'turn.completed',
      }),
      makeEvent({
        eventId: 't2-start',
        turnId: 'turn-new',
        type: 'turn.started',
      }),
      makeEvent({
        eventId: 't2-tool',
        turnId: 'turn-new',
        type: 'tool.started',
        kind: 'tool',
        toolName: 'web_fetch',
        summary: 'Consultando documentação',
      }),
    ]

    const state = foldScene(events, { running: true })
    expect(state.turnId).toBe('turn-new')
    expect(state.agents.map((a) => a.id)).toEqual([MAIN_AGENT_ID])
    expect(state.agents[0].station).toBe('browser')
    expect(state.agents[0].task).toBe('Consultando documentação')
  })

  it('(j) ignora entradas malformadas/null e sanitiza controles, agentId vazio e datas inválidas sem lançar erro', () => {
    const rawEvents = [
      null,
      undefined,
      'invalid',
      makeEvent({
        eventId: 'bad-1',
        type: 'tool.started',
        kind: 'tool',
        agentId: '   \u0000\u0007   ',
        toolName: 'read_file',
        summary: 'Lendo\u0000 arquivo\u0007 secreto\u001F',
        occurredAt: 'not-a-valid-date',
      }),
      makeEvent({
        eventId: 'bad-2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'sub-\u0000clean',
        agentName: 'Agente\u0000Limpo\u001F',
        summary: 'Tarefa\u0008 inicial',
        occurredAt: 'invalid-timestamp',
      }),
    ] as unknown as ConversationActivityEvent[]

    expect(() => foldScene(rawEvents, { running: true })).not.toThrow()
    const state = foldScene(rawEvents, { running: true })

    expect(state.agents[0].task).toBe('Lendo arquivo secreto')
    const sub = state.agents.find((a) => a.id === 'sub-clean')!
    expect(sub).toBeDefined()
    expect(sub.name).toBe('AgenteLimpo')
    expect(sub.task).toBe('Tarefa inicial')
    expect(sub.durationLabel).toBe('1 min')
  })

  it('(k) thought_delta acumula pensamento, trunca lastThought (~200 chars) e gera bubble thought truncada (~60 chars)', () => {
    const longThought1 =
      'Analisando cuidadosamente a arquitetura do módulo de conversas para garantir isolamento entre subagentes '
    const longThought2 =
      'e verificando cada transição de estado na máquina pura da cena pixel art sem efeitos colaterais.'

    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.thought_delta',
        delta: longThought1,
      }),
      makeEvent({
        eventId: 'e3',
        type: 'agent.thought_delta',
        delta: longThought2,
      }),
    ]

    const state = foldScene(events, { running: true })
    const orin = state.agents[0]
    expect(orin.pose).toBe('think')
    expect(orin.status).toBe('thinking')
    expect(orin.statusLabel).toBe(statusLabelFor('thinking'))
    expect(orin.bubble).not.toBeNull()
    expect(orin.bubble?.kind).toBe('thought')
    expect(orin.bubble?.eventId).toBe('e3')
    expect(orin.bubble?.text.startsWith('…')).toBe(true)
    expect(orin.bubble!.text.length).toBeLessThanOrEqual(60)
    expect(orin.bubble?.text).toContain('efeitos colaterais.')
    expect(orin.lastThought.length).toBeLessThanOrEqual(200)
    expect(orin.lastThought).toContain('efeitos colaterais.')
  })

  it('(l) streaming longo: 1.500 deltas dobram em tempo linear e a bolha mostra o fim do texto', () => {
    const speechChunks: string[] = []
    const thoughtChunks: string[] = []
    const events: ConversationActivityEvent[] = [makeEvent({ eventId: 's0', type: 'turn.started' })]
    for (let i = 0; i < 1500; i += 1) {
      const thought = `pens${String(i).padStart(4, '0')} `
      thoughtChunks.push(thought)
      events.push(makeEvent({ eventId: `t${i}`, type: 'agent.thought_delta', agentId: 'sub-1', delta: thought }))
    }
    for (let i = 0; i < 1500; i += 1) {
      const piece = i === 1499 ? 'fim da resposta.' : `pal${String(i).padStart(4, '0')} `
      speechChunks.push(piece)
      events.push(makeEvent({ eventId: `d${i}`, type: 'assistant.delta', content: piece }))
    }

    const startedAt = performance.now()
    const state = foldScene(events, { running: true })
    const elapsedMs = performance.now() - startedAt
    expect(elapsedMs).toBeLessThan(150)

    const fullSpeech = speechChunks.join('').replace(/\s+/g, ' ').trim()
    const orin = state.agents[0]
    expect(orin.bubble).toEqual({
      kind: 'speech',
      text: `${fullSpeech.slice(0, 59)}…`,
      fullText: fullSpeech.slice(0, 2000).trimEnd(),
      eventId: 'd1499',
    })
    expect(orin.lastSpeech).toBe(fullSpeech.slice(0, 2000).trimEnd())

    const fullThought = thoughtChunks.join('').replace(/\s+/g, ' ').trim()
    const sub = state.agents.find((a) => a.id === 'sub-1')!
    expect(sub.lastThought).toBe(fullThought.slice(-200))
  })

  it('(m) turno que terminou perguntando ao usuário mantém o Orin com a placa e o pending, mesmo sem turno rodando', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({ eventId: 'e2', type: 'tool.started', kind: 'tool', toolName: 'read_file', summary: 'Lendo' }),
      makeEvent({
        eventId: 'e3',
        type: 'turn.waiting_user',
        state: 'waiting_user',
        summary: 'Aguardando sua resposta',
        questions: [{ id: 'q1', question: 'Qual banco usar?', mode: 'single_choice', options: [] }],
      }),
    ]

    const state = foldScene(events, { running: false })
    expect(state.live).toBe(false)
    expect(state.waitingUser).toBe(true)
    expect(state.agents[0]).toMatchObject({
      pose: 'raise_hand',
      status: 'waiting_user',
      statusLabel: 'Esperando você',
      station: 'files',
    })
    expect(state.pending).toEqual([
      { id: 'e3', agentId: MAIN_AGENT_ID, kind: 'question', eventId: 'e3', summary: 'Qual banco usar?' },
    ])

    // A question answered inside the turn (in_turn) is not this state.
    const inTurn = foldScene(
      events.map((ev) => (ev.eventId === 'e3' ? { ...ev, payload: { in_turn: true } } : ev)),
      { running: false },
    )
    expect(inTurn.waitingUser).toBe(false)
    expect(inTurn.agents[0].status).toBe('resting')
    expect(inTurn.pending).toEqual([])
  })

  it('(n) com delegações em paralelo, o pai só volta a trabalhar quando todas retornam', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({ eventId: 'e2', type: 'agent.message_sent', kind: 'agent', agentName: 'A', payload: { to_agent_id: 'sub-a' } }),
      makeEvent({ eventId: 'e3', type: 'agent.message_sent', kind: 'agent', agentName: 'B', payload: { to_agent_id: 'sub-b' } }),
      makeEvent({ eventId: 'e4', type: 'agent.message_received', kind: 'agent', agentId: 'sub-a', payload: { from_agent_id: 'sub-a' } }),
    ]

    const oneBack = foldScene(events, { running: true })
    expect(oneBack.agents[0].status).toBe('waiting_agent')
    expect(oneBack.agents.find((a) => a.id === 'sub-a')!.status).toBe('completed')

    const allBack = foldScene(
      [
        ...events,
        makeEvent({ eventId: 'e5', type: 'delegation.failed', kind: 'agent', agentId: 'sub-b', payload: { from_agent_id: 'sub-b' } }),
      ],
      { running: true },
    )
    expect(allBack.agents[0].status).toBe('working')
  })

  it('(o) eventos de ciclo de vida e de recebimento não viram a tarefa do agente', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started', summary: 'Turno iniciado' }),
      makeEvent({ eventId: 'e2', type: 'tool.started', kind: 'tool', toolName: 'edit_file', summary: 'Editando app.ts' }),
      makeEvent({ eventId: 'e3', type: 'context.updated', summary: 'Contexto atualizado' }),
      makeEvent({
        eventId: 'e4',
        type: 'agent.message_received',
        kind: 'agent',
        agentId: 'sub-1',
        summary: 'Recebeu a resposta de Revisor',
        payload: { from_agent_id: 'sub-1' },
      }),
      makeEvent({ eventId: 'e5', type: 'turn.completed', summary: 'Turno concluído' }),
    ]

    const state = foldScene(events, { running: false })
    expect(state.agents[0].task).toBe('Editando app.ts')
    const sub = state.agents.find((a) => a.id === 'sub-1')!
    expect(sub.task).toBe('')
    expect(sub.activities).toEqual([])
  })

  it('(p) delegation.waiting sem pai deixa o próprio subagente "Na fila"', () => {
    const state = foldScene(
      [
        makeEvent({ eventId: 'e1', type: 'turn.started' }),
        makeEvent({ eventId: 'e2', type: 'delegation.waiting', kind: 'agent', agentId: 'sub-1', agentName: 'Revisor' }),
      ],
      { running: true },
    )
    const sub = state.agents.find((a) => a.id === 'sub-1')!
    expect(sub.status).toBe('queued')
    expect(sub.statusLabel).toBe('Na fila')
    expect(state.agents[0].status).toBe('working')
  })

  it('(q) em repouso a luz não depende de o turn.started estar na página carregada', () => {
    const withoutStart = foldScene(
      [makeEvent({ eventId: 'e9', type: 'turn.completed', summary: 'Turno concluído' })],
      { running: false },
    )
    expect(withoutStart.lightsOn).toBe(true)
    expect(foldScene([], { running: false }).lightsOn).toBe(false)
  })

  it('(r) DELEGATION_FAILED com STOPPED_BY_USER deixa o subagente stopped ("parado por você"), pose idle, sem alarme, sem scared e sem efeito/transfer de erro', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'agent:conv-1:sub1',
        parentAgentId: 'agent:conv-1:main',
        agentName: 'Explorador',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'agent.message_sent',
        kind: 'agent',
        agentId: 'agent:conv-1:main',
        agentName: 'Explorador',
        content: 'Investigue os logs',
        payload: { to_agent_id: 'agent:conv-1:sub1' },
      }),
      makeEvent({
        eventId: 'e4',
        type: 'delegation.failed',
        kind: 'agent',
        agentId: 'agent:conv-1:sub1',
        parentAgentId: 'agent:conv-1:main',
        errorCode: 'STOPPED_BY_USER',
        payload: {
          from_agent_id: 'agent:conv-1:sub1',
          error_code: 'STOPPED_BY_USER',
        },
        summary: 'Parado pelo usuário',
      }),
    ]

    const state = foldScene(events, { running: true })
    const orin = state.agents[0]
    const sub = state.agents.find((a) => a.id === 'agent:conv-1:sub1')!

    expect(state.alarmEventId).toBeNull()
    expect(sub.status).toBe('stopped')
    expect(sub.statusLabel).toBe('parado por você')
    expect(sub.pose).toBe('idle')
    expect(sub.bubble).toBeNull()
    expect(
      state.effects.some((fx) => fx.kind === 'spark' && fx.agentId === 'agent:conv-1:sub1'),
    ).toBe(false)
    expect(
      state.transfers.some((tr) => tr.kind === 'failed_delegation'),
    ).toBe(false)
    expect(orin.status).toBe('working')
    expect(orin.pose).toBe('idle')
  })

  it('(s) AGENT_MESSAGE_SENT com source="user" cria balão speech "Você: …" no subagente destino sem transfer do Orin', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e1', type: 'turn.started' }),
      makeEvent({
        eventId: 'e2',
        type: 'agent.created',
        kind: 'agent',
        agentId: 'agent:conv-1:sub2',
        parentAgentId: 'agent:conv-1:main',
        agentName: 'Executor',
      }),
      makeEvent({
        eventId: 'e3',
        type: 'tool.started',
        kind: 'tool',
        agentId: 'agent:conv-1:sub2',
        toolName: 'read_file',
        summary: 'Lendo rotas',
      }),
      makeEvent({
        eventId: 'e4',
        type: 'agent.message_sent',
        kind: 'agent',
        agentId: 'agent:conv-1:sub2',
        agentName: 'Executor',
        title: 'Você enviou uma mensagem para Executor',
        content: 'Priorize a validação de schema',
        payload: {
          agent_name: 'Executor',
          recipient_name: 'Executor',
          sender_name: 'Você',
          label: 'Executor',
          content: 'Priorize a validação de schema',
          to_agent_id: 'agent:conv-1:sub2',
          source: 'user',
          tool_kind: 'agent',
        },
      }),
    ]

    const state = foldScene(events, { running: true })
    const sub = state.agents.find((a) => a.id === 'agent:conv-1:sub2')!

    expect(sub.bubble).toEqual({
      kind: 'speech',
      text: 'Você: Priorize a validação de schema',
      eventId: 'e4',
    })
    // User message must not overwrite subagent's current task or create envelope/delegation transfer
    expect(sub.task).toBe('Lendo rotas')
    expect(
      state.transfers.some((tr) => tr.id === 'e4'),
    ).toBe(false)
  })

  it('(t) 30 thought_delta seguidos com delta vazio e summary "Pensando" não concatenam "PensandoPensando" no balão; caso misto mostra apenas o texto real', () => {
    const emptyThoughtEvents: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'e0', type: 'turn.started' }),
    ]
    for (let i = 1; i <= 30; i += 1) {
      emptyThoughtEvents.push(
        makeEvent({
          eventId: `th-${i}`,
          type: 'agent.thought_delta',
          summary: 'Pensando',
          detail: 'Pensando',
          title: 'Pensando',
          delta: '',
          payload: { title: 'Pensando', delta: '' },
        }),
      )
    }

    const emptyState = foldScene(emptyThoughtEvents, { running: true })
    const orinEmpty = emptyState.agents[0]
    expect(orinEmpty.pose).toBe('think')
    expect(orinEmpty.status).toBe('thinking')
    expect(orinEmpty.lastThought).toBe('')
    expect(orinEmpty.bubble).not.toBeNull()
    expect(orinEmpty.bubble?.kind).toBe('thought')
    expect(orinEmpty.bubble?.text).not.toContain('PensandoPensando')
    expect(orinEmpty.bubble?.text).toBe('…')

    const mixedEvents: ConversationActivityEvent[] = [
      makeEvent({ eventId: 'm0', type: 'turn.started' }),
      makeEvent({
        eventId: 'm1',
        type: 'agent.thought_delta',
        summary: 'Pensando',
        detail: 'Pensando',
        delta: '',
        payload: { delta: '' },
      }),
      makeEvent({
        eventId: 'm2',
        type: 'agent.thought_delta',
        summary: 'Pensando',
        payload: { delta: 'Inspecionando ' },
      }),
      makeEvent({
        eventId: 'm3',
        type: 'agent.thought_delta',
        summary: 'Pensando',
        detail: 'Pensando',
        delta: '',
      }),
      makeEvent({
        eventId: 'm4',
        type: 'agent.thought_delta',
        summary: 'Pensando',
        delta: 'o reducer da cena.',
      }),
      makeEvent({
        eventId: 'm5',
        type: 'agent.thought_delta',
        summary: 'Pensando',
        payload: { delta: '' },
      }),
    ]

    const mixedState = foldScene(mixedEvents, { running: true })
    const orinMixed = mixedState.agents[0]
    expect(orinMixed.pose).toBe('think')
    expect(orinMixed.status).toBe('thinking')
    expect(orinMixed.lastThought).toBe('Inspecionando o reducer da cena.')
    expect(orinMixed.bubble).toEqual({
      kind: 'thought',
      text: '…Inspecionando o reducer da cena.',
      fullText: 'Inspecionando o reducer da cena.',
      eventId: 'm5',
    })
  })

  it('(u) fala de 200 chars em assistant.delta mantém o começo no bubble.text com "…" no fim (sem "…" no começo) e preserva a frase inteira em lastSpeech e bubble.fullText', () => {
    const part1 =
      'Iniciando a verificação completa dos componentes visuais da Cena pixel art, '
    const part2 =
      'garantindo que os balões de fala mostrem o início da frase do agente sem cortar '
    const part3 =
      'as primeiras palavras e mantendo o texto completo salvo em lastSpeech.'
    const fullPhrase = `${part1}${part2}${part3}`
    expect(fullPhrase.length).toBeGreaterThanOrEqual(200)

    const events: ConversationActivityEvent[] = [
      makeEvent({ eventId: 's0', type: 'turn.started' }),
      makeEvent({ eventId: 's1', type: 'assistant.delta', content: part1 }),
      makeEvent({ eventId: 's2', type: 'assistant.delta', content: part2 }),
      makeEvent({ eventId: 's3', type: 'assistant.delta', content: part3 }),
    ]

    const state = foldScene(events, { running: true })
    const orin = state.agents[0]
    expect(orin.lastSpeech).toBe(fullPhrase)
    expect(orin.bubble).not.toBeNull()
    expect(orin.bubble?.kind).toBe('speech')
    expect(orin.bubble?.fullText).toBe(fullPhrase)
    expect(orin.bubble?.text.startsWith('Iniciando a verificação completa')).toBe(true)
    expect(orin.bubble?.text.endsWith('…')).toBe(true)
    expect(orin.bubble?.text.startsWith('…')).toBe(false)
    expect(orin.bubble!.text.length).toBe(60)
  })
})
