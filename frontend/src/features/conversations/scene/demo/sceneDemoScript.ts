import type { ConversationActivityEvent, TodoItem } from '../../activityTypes'
import { MAIN_AGENT_ID } from '../sceneTypes'

export interface SceneDemoFrame {
  events: ConversationActivityEvent[]
  todos: TodoItem[]
  running: boolean
}

function makeTodos(completedCount: number): TodoItem[] {
  const labels = [
    'Planejar arquitetura da entrega',
    'Investigar arquivos e referências',
    'Implementar módulos e rodar comandos',
    'Validar e publicar entregável',
  ]
  return labels.map((content, idx) => ({
    id: `todo-${idx + 1}`,
    content,
    status:
      idx < completedCount
        ? 'completed'
        : idx === completedCount
          ? 'in_progress'
          : 'pending',
  }))
}

function isoAt(baseMs: number, offsetMinutes: number): string {
  return new Date(baseMs + offsetMinutes * 60_000).toISOString()
}

/**
 * Builds the scripted timeline of frames for one demo cycle.
 */
export function buildDemoSequence(turnNumber: number): SceneDemoFrame[] {
  const turnId = `demo-turn-${turnNumber}`
  const baseMs = Date.UTC(2026, 10, 9, 13, 0, 0) + turnNumber * 3_600_000
  const acc: ConversationActivityEvent[] = []
  const frames: SceneDemoFrame[] = []

  const pushStep = (
    newEvents: ConversationActivityEvent[],
    completedTodos: number,
    running = true,
  ) => {
    acc.push(...newEvents)
    frames.push({
      events: [...acc],
      todos: makeTodos(completedTodos),
      running,
    })
  }

  // 1. turn.started of Orin
  pushStep(
    [
      {
        eventId: `${turnId}-ev-1`,
        cursor: '1',
        turnId,
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        summary: 'Iniciando coordenação do turno',
        occurredAt: isoAt(baseMs, 0),
      },
    ],
    0,
  )

  // 2. thought_delta of Orin
  pushStep(
    [
      {
        eventId: `${turnId}-ev-2`,
        cursor: '2',
        turnId,
        type: 'agent.thought_delta',
        kind: 'message',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        content: 'Vou dividir a tarefa entre Pesquisa, Implementação e Revisão.',
        summary: 'Planejando divisão de agentes',
        occurredAt: isoAt(baseMs, 1),
      },
    ],
    0,
  )

  // 3. todo_write (planning station)
  pushStep(
    [
      {
        eventId: `${turnId}-ev-3`,
        cursor: '3',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        toolName: 'todo_write',
        summary: 'Atualizando quadro de tarefas da equipe',
        occurredAt: isoAt(baseMs, 2),
      },
    ],
    1,
  )

  // 4. agent.created of 3 subagents (one nested under another) + agent.message_sent with payload.to_agent_id
  pushStep(
    [
      {
        eventId: `${turnId}-ev-4`,
        cursor: '4',
        turnId,
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        agentId: 'sub-pesquisador',
        agentName: 'Pesquisador',
        parentAgentId: MAIN_AGENT_ID,
        summary: 'Mapeando referências do repositório',
        payload: { subagent_type: 'Explore' },
        occurredAt: isoAt(baseMs, 3),
      },
      {
        eventId: `${turnId}-ev-5`,
        cursor: '5',
        turnId,
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        agentName: 'Pesquisador',
        content: 'Inspecione a estrutura de arquivos e referências.',
        summary: 'Delegando pesquisa ao Pesquisador',
        payload: { to_agent_id: 'sub-pesquisador' },
        occurredAt: isoAt(baseMs, 3),
      },
      {
        eventId: `${turnId}-ev-6`,
        cursor: '6',
        turnId,
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        agentId: 'sub-construtor',
        agentName: 'Construtor',
        parentAgentId: MAIN_AGENT_ID,
        summary: 'Implementando componentes da Cena',
        payload: { subagent_type: 'antigravity' },
        occurredAt: isoAt(baseMs, 4),
      },
      {
        eventId: `${turnId}-ev-7`,
        cursor: '7',
        turnId,
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'working',
        agentId: MAIN_AGENT_ID,
        agentName: 'Construtor',
        content: 'Implemente a interface e valide os comandos.',
        summary: 'Delegando implementação ao Construtor',
        payload: { to_agent_id: 'sub-construtor' },
        occurredAt: isoAt(baseMs, 4),
      },
      {
        eventId: `${turnId}-ev-8`,
        cursor: '8',
        turnId,
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        agentId: 'sub-revisor',
        agentName: 'Revisor',
        parentAgentId: 'sub-construtor',
        summary: 'Verificando contratos e testes',
        payload: { subagent_type: 'verifier' },
        occurredAt: isoAt(baseMs, 5),
      },
      {
        eventId: `${turnId}-ev-9`,
        cursor: '9',
        turnId,
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'working',
        agentId: 'sub-construtor',
        agentName: 'Revisor',
        content: 'Audite os componentes recém-criados.',
        summary: 'Subdelegando auditoria ao Revisor',
        payload: { to_agent_id: 'sub-revisor' },
        occurredAt: isoAt(baseMs, 5),
      },
    ],
    1,
  )

  // 5. Subagents using read_file, run_command, write_file
  pushStep(
    [
      {
        eventId: `${turnId}-ev-10`,
        cursor: '10',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-pesquisador',
        agentName: 'Pesquisador',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'read_file',
        summary: 'Lendo especificação da Cena',
        occurredAt: isoAt(baseMs, 6),
      },
      {
        eventId: `${turnId}-ev-11`,
        cursor: '11',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-construtor',
        agentName: 'Construtor',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'write_file',
        summary: 'Escrevendo SceneHud.tsx',
        occurredAt: isoAt(baseMs, 7),
      },
      {
        eventId: `${turnId}-ev-12`,
        cursor: '12',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-revisor',
        agentName: 'Revisor',
        parentAgentId: 'sub-construtor',
        toolName: 'run_command',
        summary: 'Executando vitest nos módulos da Cena',
        occurredAt: isoAt(baseMs, 8),
      },
    ],
    2,
  )

  // 6. Subagents using fetch_url, mcp__x__y, and an unknown tool
  pushStep(
    [
      {
        eventId: `${turnId}-ev-13`,
        cursor: '13',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-pesquisador',
        agentName: 'Pesquisador',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'fetch_url',
        summary: 'Consultando documentação de acessibilidade',
        occurredAt: isoAt(baseMs, 9),
      },
      {
        eventId: `${turnId}-ev-14`,
        cursor: '14',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-construtor',
        agentName: 'Construtor',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'mcp__x__y',
        summary: 'Sincronizando metadados via extensão MCP',
        occurredAt: isoAt(baseMs, 10),
      },
      {
        eventId: `${turnId}-ev-15`,
        cursor: '15',
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-revisor',
        agentName: 'Revisor',
        parentAgentId: 'sub-construtor',
        toolName: 'ferramenta_experimental_xyz',
        summary: 'Inspecionando heurísticas na mesa geral',
        occurredAt: isoAt(baseMs, 11),
      },
    ],
    2,
  )

  // 7. tool.permission_required (pending) on sub-construtor
  pushStep(
    [
      {
        eventId: `${turnId}-ev-16`,
        cursor: '16',
        turnId,
        type: 'tool.permission_required',
        kind: 'tool',
        state: 'waiting_user',
        agentId: 'sub-construtor',
        agentName: 'Construtor',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'run_command',
        summary: 'Permissão para rodar script de verificação de build',
        toolPermission: {
          requestId: `${turnId}-perm-1`,
          toolName: 'run_command',
          state: 'pending',
        },
        occurredAt: isoAt(baseMs, 12),
      },
    ],
    2,
  )

  // 8. tool.permission_resolved + tool.failed on sub-revisor
  pushStep(
    [
      {
        eventId: `${turnId}-ev-17`,
        cursor: '17',
        turnId,
        type: 'tool.permission_resolved',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-construtor',
        agentName: 'Construtor',
        parentAgentId: MAIN_AGENT_ID,
        toolName: 'run_command',
        summary: 'Permissão aprovada pelo usuário',
        toolPermission: {
          requestId: `${turnId}-perm-1`,
          toolName: 'run_command',
          state: 'allowed',
        },
        occurredAt: isoAt(baseMs, 13),
      },
      {
        eventId: `${turnId}-ev-18`,
        cursor: '18',
        turnId,
        type: 'tool.failed',
        kind: 'tool',
        state: 'failed',
        status: 'failed',
        agentId: 'sub-revisor',
        agentName: 'Revisor',
        parentAgentId: 'sub-construtor',
        toolName: 'run_command',
        summary: 'Falha ao validar snapshot antigo',
        occurredAt: isoAt(baseMs, 14),
      },
    ],
    3,
  )

  // 9. agent.message_received (return) of two and delegation.failed of one
  pushStep(
    [
      {
        eventId: `${turnId}-ev-19`,
        cursor: '19',
        turnId,
        type: 'agent.message_received',
        kind: 'agent',
        state: 'completed',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        summary: 'Pesquisador entregou o mapeamento completo',
        payload: { from_agent_id: 'sub-pesquisador' },
        occurredAt: isoAt(baseMs, 15),
      },
      {
        eventId: `${turnId}-ev-20`,
        cursor: '20',
        turnId,
        type: 'delegation.failed',
        kind: 'agent',
        state: 'failed',
        agentId: 'sub-revisor',
        agentName: 'Revisor',
        parentAgentId: 'sub-construtor',
        summary: 'Revisor encontrou divergência no teste antigo',
        payload: { from_agent_id: 'sub-revisor' },
        occurredAt: isoAt(baseMs, 16),
      },
      {
        eventId: `${turnId}-ev-21`,
        cursor: '21',
        turnId,
        type: 'agent.message_received',
        kind: 'agent',
        state: 'completed',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        summary: 'Construtor concluiu a integração da Cena',
        payload: { from_agent_id: 'sub-construtor' },
        occurredAt: isoAt(baseMs, 17),
      },
    ],
    3,
  )

  // 10. artifact.created
  pushStep(
    [
      {
        eventId: `${turnId}-ev-22`,
        cursor: '22',
        turnId,
        type: 'artifact.created',
        kind: 'artifact',
        state: 'completed',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        filePath: 'docs/relatorio-cena-pixel-art.md',
        title: 'relatorio-cena-pixel-art.md',
        summary: 'Entregável final consolidado',
        occurredAt: isoAt(baseMs, 18),
      },
    ],
    4,
  )

  // 11. turn.completed (everyone rests)
  pushStep(
    [
      {
        eventId: `${turnId}-ev-23`,
        cursor: '23',
        turnId,
        type: 'turn.completed',
        kind: 'lifecycle',
        state: 'completed',
        agentId: MAIN_AGENT_ID,
        agentName: 'Orin',
        summary: 'Turno finalizado com sucesso',
        occurredAt: isoAt(baseMs, 19),
      },
    ],
    4,
    false,
  )

  return frames
}

/**
 * Generates a stress-test frame with 12 simultaneous subagents across all stations.
 */
export function buildTwelveAgentsFrame(turnNumber: number): SceneDemoFrame {
  const turnId = `demo-12-turn-${turnNumber}`
  const baseMs = Date.UTC(2026, 10, 9, 15, 0, 0)
  const tools = [
    'run_command',
    'read_file',
    'write_file',
    'fetch_url',
    'todo_write',
    'mcp__github__list_prs',
    'ferramenta_custom_1',
    'search_code',
    'edit_file',
    'web_search',
    'verify_project',
    'configure_mcp',
  ]

  const events: ConversationActivityEvent[] = [
    {
      eventId: `${turnId}-start`,
      cursor: '1',
      turnId,
      type: 'turn.started',
      kind: 'lifecycle',
      state: 'working',
      agentId: MAIN_AGENT_ID,
      agentName: 'Orin',
      summary: 'Coordenando 12 especialistas simultâneos',
      occurredAt: isoAt(baseMs, 0),
    },
  ]

  for (let i = 1; i <= 12; i += 1) {
    const id = `sub-stress-${i}`
    const name = `Agente ${i}`
    const parentId = i > 9 ? `sub-stress-${i - 9}` : MAIN_AGENT_ID
    const toolName = tools[(i - 1) % tools.length]
    events.push(
      {
        eventId: `${turnId}-create-${i}`,
        cursor: String(i * 2),
        turnId,
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        agentId: id,
        agentName: name,
        parentAgentId: parentId,
        summary: `Iniciando frente #${i}`,
        payload: { subagent_type: i % 2 === 0 ? 'Explore' : 'antigravity' },
        occurredAt: isoAt(baseMs, i),
      },
      {
        eventId: `${turnId}-tool-${i}`,
        cursor: String(i * 2 + 1),
        turnId,
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: id,
        agentName: name,
        parentAgentId: parentId,
        toolName,
        summary: `Executando ${toolName} na frente #${i}`,
        occurredAt: isoAt(baseMs, i + 2),
      },
    )
  }

  return {
    events,
    todos: makeTodos(2),
    running: true,
  }
}
