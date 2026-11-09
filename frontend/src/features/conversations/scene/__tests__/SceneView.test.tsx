import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../../api/client'
import {
  sendConversationAgentMessage,
  stopConversationAgent,
} from '../../../../api/conversations'
import type { ConversationActivityEvent, TodoItem } from '../../activityTypes'
import { ScenePendingCards } from '../ScenePendingCards'
import { SceneView } from '../SceneView'
import { SCENE_SOUND_STORAGE_KEY } from '../sceneAudio'

beforeEach(() => {
  window.localStorage.removeItem(SCENE_SOUND_STORAGE_KEY)
})

afterEach(() => {
  cleanup()
  window.localStorage.removeItem(SCENE_SOUND_STORAGE_KEY)
})

describe('SceneView', () => {
  it('(1) renderiza título "Cena", subtítulo, botões Pausar equipe e Voltar à conversa, e estados vazios limpos por padrão (0 de 0 etapas)', () => {
    const onPauseTeam = vi.fn()
    const onBackToChat = vi.fn()

    render(
      <SceneView running={true} onPauseTeam={onPauseTeam} onBackToChat={onBackToChat} />,
    )

    const root = screen.getByTestId('scene-view')
    expect(root).toBeTruthy()
    expect(screen.getByTestId('scene-canvas')).toBeTruthy()

    expect(
      screen.getByRole('heading', { level: 2, name: 'Cena' }),
    ).toBeTruthy()
    expect(
      screen.getByText('O Orin coordena a equipe; acompanhe ao vivo.'),
    ).toBeTruthy()
    expect(screen.getByTestId('scene-live-indicator').textContent).toContain(
      'Ao vivo',
    )

    const pauseBtn = screen.getByTestId('scene-pause-button')
    expect(pauseBtn.textContent).toContain('Pausar equipe')
    fireEvent.click(pauseBtn)
    expect(onPauseTeam).toHaveBeenCalledTimes(1)

    const backBtn = screen.getByTestId('scene-back-to-chat-button')
    expect(backBtn.textContent).toContain('Voltar à conversa')
    fireEvent.click(backBtn)
    expect(onBackToChat).toHaveBeenCalledTimes(1)

    const stepper = screen.getByTestId('scene-stepper')
    expect(stepper.textContent).toContain('0 de 0 etapas')
    expect(stepper.textContent).toContain('Em andamento')

    expect(screen.getByTestId('scene-agents-empty').textContent).toContain(
      'Nenhum subagente em execução neste momento.',
    )
    expect(screen.getByTestId('scene-agent-button-agent:main')).toBeTruthy()
    expect(screen.queryByTestId('scene-decision-card')).toBeNull()
    expect(
      screen.getByTestId('scene-final-deliverable-card').textContent,
    ).toContain('Nenhum entregável gerado ainda')
    expect(screen.getByTestId('scene-recent-activity').textContent).toContain(
      'Nenhuma atividade registrada ainda.',
    )
  })

  it('(2) alterna o texto do botão para "Retomar equipe" quando paused=true e calcula etapas a partir de todos', () => {
    const todos: TodoItem[] = [
      { id: '1', content: 'Coletar referências', status: 'completed' },
      { id: '2', content: 'Analisar dados', status: 'completed' },
      { id: '3', content: 'Consolidar relatório', status: 'completed' },
      { id: '4', content: 'Revisão final', status: 'completed' },
    ]

    render(<SceneView paused={true} todos={todos} />)

    expect(screen.getByTestId('scene-pause-button').textContent).toContain(
      'Retomar equipe',
    )

    const stepper = screen.getByTestId('scene-stepper')
    expect(stepper.textContent).toContain('4 de 4 etapas')
    expect(stepper.textContent).toContain('Concluído')
    expect(within(stepper).getAllByText('✓').length).toBe(4)
  })

  it('(3) renderiza os especialistas a partir de events, mostra nome, tarefa, estado em texto e duração, e permite selecionar/fechar o painel com atividades', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-p-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'pesquisador',
        agentName: 'Pesquisador',
        parentAgentId: 'main',
        toolName: 'read_file',
        summary: 'Analisou 12 referências sobre ferramentas de mercado',
        occurredAt: '2026-10-29T14:14:00',
      },
      {
        eventId: 'ev-p-2',
        cursor: '2',
        type: 'tool.progressed',
        kind: 'tool',
        state: 'working',
        agentId: 'pesquisador',
        agentName: 'Pesquisador',
        parentAgentId: 'main',
        toolName: 'search_files',
        summary: 'Comparando referências',
        occurredAt: '2026-10-29T14:32:00',
      },
      {
        eventId: 'ev-a-1',
        cursor: '3',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'analista',
        agentName: 'Analista',
        parentAgentId: 'main',
        toolName: 'todo_write',
        summary: 'Organizando oportunidades',
        elapsedMs: 12 * 60 * 1000,
        occurredAt: '2026-10-29T14:20:00',
      },
      {
        eventId: 'ev-r-1',
        cursor: '4',
        type: 'delegation.waiting',
        kind: 'agent',
        state: 'queued',
        agentId: 'revisor',
        agentName: 'Revisor',
        summary: 'Aguardando análise',
        elapsedMs: 3 * 60 * 1000,
        occurredAt: '2026-10-29T14:25:00',
      },
    ]

    render(<SceneView events={events} running={true} />)

    const agentsList = screen.getByTestId('scene-agents-list')
    expect(agentsList).toBeTruthy()

    const pesquisadorBtn = screen.getByTestId('scene-agent-button-pesquisador')
    expect(pesquisadorBtn.textContent).toContain('Pesquisador')
    expect(pesquisadorBtn.textContent).toContain('Comparando referências')
    expect(pesquisadorBtn.textContent).toContain('Trabalhando')
    expect(pesquisadorBtn.textContent).toContain('18 min')
    expect(pesquisadorBtn.getAttribute('aria-pressed')).toBe('false')

    const analistaBtn = screen.getByTestId('scene-agent-button-analista')
    expect(analistaBtn.textContent).toContain('Analista')
    expect(analistaBtn.textContent).toContain('Organizando oportunidades')
    expect(analistaBtn.textContent).toContain('Trabalhando')
    expect(analistaBtn.textContent).toContain('12 min')

    const revisorBtn = screen.getByTestId('scene-agent-button-revisor')
    expect(revisorBtn.textContent).toContain('Revisor')
    expect(revisorBtn.textContent).toContain('Aguardando análise')
    // Queued without a parent: the subagent itself is waiting for a slot.
    expect(revisorBtn.textContent).toContain('Na fila')
    expect(revisorBtn.textContent).toContain('3 min')

    expect(screen.queryByTestId('scene-agent-panel')).toBeNull()

    fireEvent.click(pesquisadorBtn)
    expect(pesquisadorBtn.getAttribute('aria-pressed')).toBe('true')
    const panel = screen.getByTestId('scene-agent-panel')
    expect(panel).toBeTruthy()
    const eventsList = screen.getByTestId('scene-agent-events-pesquisador')
    expect(eventsList.textContent).toContain(
      'Analisou 12 referências sobre ferramentas de mercado',
    )
    expect(eventsList.textContent).toContain('14:32')

    fireEvent.click(screen.getByTestId('scene-agent-panel-close'))
    expect(pesquisadorBtn.getAttribute('aria-pressed')).toBe('false')
    expect(screen.queryByTestId('scene-agent-panel')).toBeNull()
  })

  it('(4) agrupa eventos reais por agentId e exibe tarefa mais recente, duração e atividades no painel', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'waiting_tool',
        agentId: 'agent-search',
        agentName: 'Pesquisador',
        parentAgentId: 'main',
        toolName: 'search_web',
        summary: 'Pesquisando concorrentes SaaS',
        occurredAt: '2026-10-29T14:10:00',
      },
      {
        eventId: 'ev-2',
        cursor: '2',
        type: 'tool.completed',
        kind: 'tool',
        state: 'working',
        agentId: 'agent-search',
        agentName: 'Pesquisador',
        parentAgentId: 'main',
        toolName: 'read_file',
        summary: 'Comparando referências do setor',
        occurredAt: '2026-10-29T14:25:00',
      },
      {
        eventId: 'ev-3',
        cursor: '3',
        type: 'delegation.completed',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent-review',
        agentName: 'Revisor',
        parentAgentId: 'main',
        summary: 'Revisão de qualidade concluída',
        occurredAt: '2026-10-29T14:28:00',
      },
    ]

    render(<SceneView events={events} running={true} />)

    const searchBtn = screen.getByTestId('scene-agent-button-agent-search')
    expect(searchBtn.textContent).toContain('Pesquisador')
    expect(searchBtn.textContent).toContain('Comparando referências do setor')
    expect(searchBtn.textContent).toContain('15 min')

    const reviewBtn = screen.getByTestId('scene-agent-button-agent-review')
    expect(reviewBtn.textContent).toContain('Revisor')
    expect(reviewBtn.textContent).toContain('Concluído')

    fireEvent.click(searchBtn)
    const agentEvents = screen.getByTestId('scene-agent-events-agent-search')
    expect(agentEvents.textContent).toContain('Pesquisando concorrentes SaaS')
    expect(agentEvents.textContent).toContain('Comparando referências do setor')
  })

  it('(5) renderiza Card de Decisão e Card de Entregável Final interativos', () => {
    const onResolveDecision = vi.fn()
    const onOpenDeliverable = vi.fn()

    const { rerender } = render(
      <SceneView
        pendingDecision={{
          id: 'decision-paid-tools',
          question: 'Incluir ferramentas pagas na comparação?',
          context:
            'Encontramos boas opções pagas que podem ampliar o escopo da análise.',
          options: [
            {
              id: 'opt-include-paid',
              label: 'Incluir opções pagas',
              value: 'Incluir opções pagas',
              primary: true,
            },
            {
              id: 'opt-free-only',
              label: 'Somente gratuitas',
              value: 'Somente gratuitas',
              primary: false,
            },
          ],
        }}
        finalDeliverable={{
          fileName: 'Relatório de oportunidades.md',
          status: 'preparing',
          statusLabel: 'Em preparação',
        }}
        onResolveDecision={onResolveDecision}
        onOpenDeliverable={onOpenDeliverable}
      />,
    )

    const decisionCard = screen.getByTestId('scene-decision-card')
    expect(decisionCard.textContent).toContain('Preciso de uma decisão')
    expect(decisionCard.textContent).toContain(
      'Incluir ferramentas pagas na comparação?',
    )
    expect(decisionCard.textContent).toContain(
      'Encontramos boas opções pagas que podem ampliar o escopo da análise.',
    )

    const includePaidBtn = within(decisionCard).getByRole('button', {
      name: 'Incluir opções pagas',
    })
    const freeOnlyBtn = within(decisionCard).getByRole('button', {
      name: 'Somente gratuitas',
    })

    fireEvent.click(includePaidBtn)
    expect(onResolveDecision).toHaveBeenCalledWith(
      'Incluir opções pagas',
      expect.objectContaining({
        question: 'Incluir ferramentas pagas na comparação?',
      }),
    )

    fireEvent.click(freeOnlyBtn)
    expect(onResolveDecision).toHaveBeenCalledWith(
      'Somente gratuitas',
      expect.objectContaining({
        question: 'Incluir ferramentas pagas na comparação?',
      }),
    )

    const deliverableCard = screen.getByTestId('scene-final-deliverable-card')
    expect(deliverableCard.textContent).toContain('Entregável final')
    expect(deliverableCard.textContent).toContain(
      'Relatório de oportunidades.md',
    )
    expect(deliverableCard.textContent).toContain('Em preparação')

    rerender(
      <SceneView
        finalDeliverable={{
          fileName: 'Relatório de oportunidades.md',
          path: 'docs/relatorio.md',
          status: 'ready',
        }}
        onOpenDeliverable={onOpenDeliverable}
      />,
    )

    const openBtn = within(
      screen.getByTestId('scene-final-deliverable-card'),
    ).getByRole('button', {
      name: /Abrir/i,
    })
    fireEvent.click(openBtn)
    expect(onOpenDeliverable).toHaveBeenCalledWith(
      expect.objectContaining({
        fileName: 'Relatório de oportunidades.md',
        status: 'ready',
      }),
    )
  })

  it('(6) renderiza Atividade recente com horários HH:MM e botão "Ver todas as atividades ›"', () => {
    const onViewAllActivities = vi.fn()

    render(
      <SceneView
        recentActivities={[
          {
            id: 'rec-1',
            time: '14:32',
            text: 'Pesquisador analisou 12 referências sobre ferramentas de mercado.',
          },
          {
            id: 'rec-2',
            time: '14:28',
            text: 'Analista organizou oportunidades por categoria.',
          },
        ]}
        onViewAllActivities={onViewAllActivities}
      />,
    )

    const recentSection = screen.getByTestId('scene-recent-activity')
    expect(recentSection.textContent).toContain('Atividade recente')
    expect(recentSection.textContent).toContain('14:32')
    expect(recentSection.textContent).toContain(
      'Pesquisador analisou 12 referências sobre ferramentas de mercado.',
    )
    expect(recentSection.textContent).toContain('14:28')
    expect(recentSection.textContent).toContain(
      'Analista organizou oportunidades por categoria.',
    )

    const viewAllBtn = screen.getByTestId('scene-view-all-activities')
    expect(viewAllBtn.textContent).toContain('Ver todas as atividades')
    fireEvent.click(viewAllBtn)
    expect(onViewAllActivities).toHaveBeenCalledTimes(1)
  })

  it('(7) [Red-Team] sanitiza entradas com events=[], todos=[], timestamps malformados, agentId vazio e caracteres de controle sem lançar erro', () => {
    const malformedEvents: ConversationActivityEvent[] = [
      {
        eventId: 'bad-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: '   \u0000\u0007   ',
        agentName: 'Agente\u0000Secreto\u001F',
        summary: 'Analisando\u0000 dados \u0007críticos',
        occurredAt: 'invalid-date',
      },
      {
        eventId: 'bad-2',
        cursor: '2',
        type: 'tool.completed',
        kind: 'tool',
        state: 'completed',
        agentId: '',
        summary: '\u0000\u0008',
        occurredAt: 'not-a-timestamp',
      },
    ]

    expect(() => {
      render(<SceneView events={[]} todos={[]} />)
    }).not.toThrow()

    cleanup()

    expect(() => {
      render(<SceneView events={malformedEvents} todos={[]} />)
    }).not.toThrow()

    const view = screen.getByTestId('scene-view')
    const hasControlChars = Array.from(view.textContent ?? '').some((ch) => {
      const code = ch.charCodeAt(0)
      return code <= 0x1f || code === 0x7f
    })
    expect(hasControlChars).toBe(false)
    expect(view.textContent).toContain('AgenteSecreto')
    expect(view.textContent).toContain('Analisando dados críticos')
  })

  it('(8) running=false coloca todos com estado "Descansando" e exibe indicador "Equipe descansando"', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-run-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-1',
        agentName: 'Especialista',
        parentAgentId: 'main',
        toolName: 'run_command',
        summary: 'Rodando comando',
      },
    ]

    render(<SceneView events={events} running={false} />)

    expect(screen.getByTestId('scene-live-indicator').textContent).toContain(
      'Equipe descansando',
    )

    const orinBtn = screen.getByTestId('scene-agent-button-agent:main')
    const subBtn = screen.getByTestId('scene-agent-button-sub-1')
    expect(orinBtn.textContent).toContain('Descansando')
    expect(subBtn.textContent).toContain('Descansando')
  })

  it('(9) clicar num botão de personagem abre o painel com ferramenta atual e estação em texto', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-tool-1',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'sub-dev',
        agentName: 'Implementador',
        parentAgentId: 'main',
        toolName: 'run_command',
        summary: 'Executando suite de testes',
      },
    ]

    render(<SceneView events={events} running={true} />)

    const devBtn = screen.getByTestId('scene-agent-button-sub-dev')
    fireEvent.click(devBtn)

    const panel = screen.getByTestId('scene-agent-panel')
    expect(panel.textContent).toContain('Implementador')
    expect(panel.textContent).toContain('run_command')
    expect(panel.textContent).toContain('Mesa do terminal')
    expect(panel.textContent).toContain('Trabalhando')
  })

  it('(11) turno encerrado com pergunta ao usuário mostra "Esperando você" em vez de "Equipe descansando"', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        turnId: 'turn-q',
        summary: '',
      },
      {
        eventId: 'ev-ask',
        cursor: '2',
        type: 'turn.waiting_user',
        kind: 'lifecycle',
        state: 'waiting_user',
        agentId: 'agent:main',
        turnId: 'turn-q',
        summary: 'Aguardando sua resposta',
        questions: [{ id: 'q1', question: 'Qual banco usar?', mode: 'single_choice', options: [] }],
      },
    ]

    render(<SceneView events={events} running={false} />)

    const indicator = screen.getByTestId('scene-live-indicator')
    expect(indicator.textContent).toContain('Esperando você')
    expect(indicator.textContent).not.toContain('Equipe descansando')
    expect(screen.getByTestId('scene-agent-button-agent:main').textContent).toContain('Esperando você')
  })

  it('(10) botão de som alterna aria-pressed e persiste em localStorage (padrão desligado)', () => {
    render(<SceneView />)

    const soundBtn = screen.getByTestId('scene-sound-button')
    expect(soundBtn.getAttribute('aria-pressed')).toBe('false')
    expect(soundBtn.textContent).toContain('Som desligado')
    expect(window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)).toBeNull()

    fireEvent.click(soundBtn)
    expect(soundBtn.getAttribute('aria-pressed')).toBe('true')
    expect(soundBtn.textContent).toContain('Som ligado')
    expect(window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)).toBe('true')

    fireEvent.click(soundBtn)
    expect(soundBtn.getAttribute('aria-pressed')).toBe('false')
    expect(soundBtn.textContent).toContain('Som desligado')
    expect(window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)).toBe('false')
  })

  it('(12) renderiza ToolPermissionCard para kind="permission" e chama onToolPermission (allow_once e deny) com o evento original', async () => {
    const permEvent: ConversationActivityEvent = {
      eventId: 'ev-perm-1',
      cursor: '1',
      type: 'tool.permission_required',
      kind: 'tool',
      state: 'waiting_user',
      turnId: 'turn-1',
      agentId: 'sub-exec',
      agentName: 'Executor',
      parentAgentId: 'main',
      summary: 'Executar script de migração',
      toolPermission: {
        requestId: 'req-perm-1',
        turnId: 'turn-1',
        toolName: 'run_command',
        command: 'npm run migrate',
        state: 'pending',
      },
    }

    const onToolPermission = vi.fn().mockResolvedValue(undefined)

    const { unmount } = render(
      <SceneView
        events={[permEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-1'])}
        actions={{ onToolPermission }}
      />,
    )

    const pendingSection = screen.getByTestId('scene-pending-section')
    expect(pendingSection.textContent).toContain('Esperando você')
    expect(pendingSection.textContent).toContain('Executor')

    const permCard = within(pendingSection).getByTestId('tool-permission-card')
    const allowBtn = within(permCard).getByRole('button', { name: 'Permitir' })
    fireEvent.click(allowBtn)

    await waitFor(() => {
      expect(onToolPermission).toHaveBeenCalledWith(permEvent, 'allow_once')
    })

    unmount()

    const onToolPermissionDeny = vi.fn().mockResolvedValue(undefined)
    render(
      <SceneView
        events={[permEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-1'])}
        actions={{ onToolPermission: onToolPermissionDeny }}
      />,
    )

    const denyBtn = within(
      screen.getByTestId('tool-permission-card'),
    ).getByRole('button', { name: 'Negar' })
    fireEvent.click(denyBtn)

    await waitFor(() => {
      expect(onToolPermissionDeny).toHaveBeenCalledWith(permEvent, 'deny')
    })
  })

  it('(13) renderiza UserQuestionCard para kind="question" e chama onAnswer com o evento original e respostas', async () => {
    const questionEvent: ConversationActivityEvent = {
      eventId: 'ev-q-1',
      cursor: '1',
      type: 'turn.waiting_user',
      kind: 'lifecycle',
      state: 'waiting_user',
      turnId: 'turn-q1',
      agentId: 'agent:main',
      summary: 'Escolha o banco de dados',
      questions: [
        {
          id: 'db-choice',
          question: 'Qual banco devemos usar?',
          mode: 'single_choice',
          options: [
            { id: 'pg', label: 'PostgreSQL' },
            { id: 'sqlite', label: 'SQLite' },
          ],
        },
      ],
    }

    const onAnswer = vi.fn().mockResolvedValue(undefined)

    render(
      <SceneView
        events={[questionEvent]}
        running={false}
        openQuestionTurnIds={new Set(['turn-q1'])}
        actions={{ onAnswer }}
      />,
    )

    const qCard = screen.getByTestId('user-question-card')
    expect(qCard.textContent).toContain('Qual banco devemos usar?')

    const pgRadio = within(qCard).getByLabelText(/PostgreSQL/i)
    fireEvent.click(pgRadio)

    const submitBtn = within(qCard).getByRole('button', {
      name: /Enviar respostas/i,
    })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(onAnswer).toHaveBeenCalledWith(questionEvent, [
        { id: 'db-choice', selected: ['pg'], note: '' },
      ])
    })
  })

  it('(14) renderiza McpApprovalCard para kind="mcp" e chama onMcpApprove e onMcpDecline com o evento original', async () => {
    const mcpEvent: ConversationActivityEvent = {
      eventId: 'ev-mcp-1',
      cursor: '1',
      type: 'tool.started',
      kind: 'tool',
      state: 'waiting_user',
      turnId: 'turn-mcp',
      agentId: 'agent:main',
      summary: 'Conectar GitHub MCP',
      mcpApproval: {
        server_id: 'srv-gh',
        display_name: 'GitHub MCP',
        transport: 'stdio',
        secret_names: ['GITHUB_TOKEN'],
        catalog_id: 'github',
      },
    }

    const onMcpApprove = vi.fn().mockResolvedValue(undefined)
    const onMcpDecline = vi.fn().mockResolvedValue(undefined)

    render(
      <SceneView
        events={[mcpEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-mcp'])}
        actions={{ onMcpApprove, onMcpDecline }}
      />,
    )

    const pendingSection = screen.getByTestId('scene-pending-section')
    const tokenInput = within(pendingSection).getByLabelText('GITHUB_TOKEN')
    fireEvent.change(tokenInput, { target: { value: 'ghp_secret123' } })

    const approveBtn = within(pendingSection).getByRole('button', {
      name: 'Conectar',
    })
    fireEvent.click(approveBtn)

    await waitFor(() => {
      expect(onMcpApprove).toHaveBeenCalledWith(mcpEvent, {
        GITHUB_TOKEN: 'ghp_secret123',
      })
    })

    const declineBtn = within(pendingSection).getByRole('button', {
      name: 'Recusar',
    })
    fireEvent.click(declineBtn)

    await waitFor(() => {
      expect(onMcpDecline).toHaveBeenCalledWith(mcpEvent)
    })
  })

  it('(15) renderiza PluginApprovalCard para kind="plugin" e chama onPluginApprove e onPluginDecline com o evento original', async () => {
    const pluginEvent: ConversationActivityEvent = {
      eventId: 'ev-plug-1',
      cursor: '1',
      type: 'tool.started',
      kind: 'tool',
      state: 'waiting_user',
      turnId: 'turn-plug',
      agentId: 'agent:main',
      summary: 'Instalar plugin SuperDesign',
      pluginApproval: {
        plugin_id: 'plug-1',
        display_name: 'SuperDesign',
        version: '1.2.0',
        description: 'Design system helper',
        author: 'Orin',
        contribution_count: 1,
        skills: [{ skill_id: 'sk-1', name: 'UI Audit', description: 'Audita UI' }],
        mcp_servers: [],
        agents: [],
        commands: [],
        hooks: [],
        warnings: [],
      },
    }

    const onPluginApprove = vi.fn().mockResolvedValue(undefined)
    const onPluginDecline = vi.fn().mockResolvedValue(undefined)

    render(
      <SceneView
        events={[pluginEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-plug'])}
        actions={{ onPluginApprove, onPluginDecline }}
      />,
    )

    const pendingSection = screen.getByTestId('scene-pending-section')
    expect(pendingSection.textContent).toContain('SuperDesign · v1.2.0')

    const installBtn = within(pendingSection).getByRole('button', {
      name: 'Instalar',
    })
    fireEvent.click(installBtn)

    await waitFor(() => {
      expect(onPluginApprove).toHaveBeenCalledWith(pluginEvent)
    })

    const declineBtn = within(pendingSection).getByRole('button', {
      name: 'Recusar',
    })
    fireEvent.click(declineBtn)

    await waitFor(() => {
      expect(onPluginDecline).toHaveBeenCalledWith(pluginEvent)
    })
  })

  it('(16) renderiza PlanApprovalCard para kind="plan" e chama onApprovePlanAcceptEdits / onApprovePlanAskPermissions / onRequestPlanChanges com o evento original', () => {
    const planEvent: ConversationActivityEvent = {
      eventId: 'ev-plan-1',
      cursor: '1',
      type: 'tool.permission_required',
      kind: 'tool',
      state: 'waiting_user',
      turnId: 'turn-plan',
      agentId: 'agent:main',
      summary: 'Revisar plano da Fase 2',
      planApproval: {
        plan_path: 'docs/plan.md',
        plan_content: '## Etapas do plano\n- Passo 1',
      },
      toolPermission: {
        requestId: 'req-plan-1',
        turnId: 'turn-plan',
        toolName: 'exit_plan_mode',
        state: 'pending',
        planApproval: {
          plan_path: 'docs/plan.md',
          plan_content: '## Etapas do plano\n- Passo 1',
        },
      },
    }

    const onApprovePlanAcceptEdits = vi.fn()
    const onApprovePlanAskPermissions = vi.fn()
    const onRequestPlanChanges = vi.fn()

    const { unmount } = render(
      <SceneView
        events={[planEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-plan'])}
        actions={{
          onApprovePlanAcceptEdits,
          onApprovePlanAskPermissions,
          onRequestPlanChanges,
        }}
      />,
    )

    const planCard = screen.getByTestId('plan-approval-card')
    expect(planCard.textContent).toContain('docs/plan.md')
    expect(planCard.textContent).toContain('Etapas do plano')

    fireEvent.click(within(planCard).getByTestId('plan-approve-accept-edits'))
    expect(onApprovePlanAcceptEdits).toHaveBeenCalledWith(planEvent)

    unmount()

    render(
      <SceneView
        events={[planEvent]}
        running={true}
        openQuestionTurnIds={new Set(['turn-plan'])}
        actions={{
          onApprovePlanAcceptEdits,
          onApprovePlanAskPermissions,
          onRequestPlanChanges,
        }}
      />,
    )

    fireEvent.click(screen.getByTestId('plan-approve-ask-permissions'))
    expect(onApprovePlanAskPermissions).toHaveBeenCalledWith(planEvent)
  })

  it('(17) cai no texto de resumo quando a pendência não tem evento correspondente ou falta handler, e acha evento por requestId quando eventId difere', () => {
    const permEvent: ConversationActivityEvent = {
      eventId: 'ev-real-id',
      cursor: '1',
      type: 'tool.permission_required',
      kind: 'tool',
      state: 'waiting_user',
      agentId: 'sub-1',
      agentName: 'Auditor',
      summary: 'Aprovar leitura de credenciais',
      toolPermission: {
        requestId: 'req-match-by-id',
        toolName: 'read_file',
        state: 'pending',
      },
    }

    // Sem actions -> cai no texto de resumo
    const { unmount } = render(
      <SceneView events={[permEvent]} running={true} />,
    )
    expect(
      screen.getByTestId('scene-pending-fallback-req-match-by-id').textContent,
    ).toBe('Aprovar leitura de credenciais')
    expect(screen.queryByTestId('tool-permission-card')).toBeNull()
    unmount()

    // Pendência órfã sem evento correspondente + pendência que acha evento por requestId
    const onToolPermission = vi.fn().mockResolvedValue(undefined)
    render(
      <ScenePendingCards
        pending={[
          {
            id: 'orphan-1',
            agentId: 'agent:main',
            kind: 'permission',
            eventId: 'non-existent-event',
            summary: 'Resumo de fallback órfão',
          },
          {
            id: 'req-match-by-id',
            agentId: 'sub-1',
            kind: 'permission',
            eventId: 'different-event-id',
            requestId: 'req-match-by-id',
            summary: 'Aprovar leitura de credenciais',
          },
        ]}
        agents={[]}
        events={[permEvent]}
        actions={{ onToolPermission }}
        onSelectAgent={vi.fn()}
      />,
    )

    expect(
      screen.getByTestId('scene-pending-fallback-orphan-1').textContent,
    ).toBe('Resumo de fallback órfão')
    expect(screen.getByTestId('tool-permission-card')).toBeTruthy()
  })

  it('(18) botão "Ver na cena" seleciona o agente, painel mostra "Esperando você: <summary>" sem duplicar o cartão e botão "Responder" foca a seção', () => {
    const permEvent: ConversationActivityEvent = {
      eventId: 'ev-sub-perm',
      cursor: '1',
      type: 'tool.permission_required',
      kind: 'tool',
      state: 'waiting_user',
      agentId: 'sub-tester',
      agentName: 'Testador',
      parentAgentId: 'main',
      summary: 'Rodar pytest no diretório backend',
      toolPermission: {
        requestId: 'req-pytest',
        toolName: 'run_command',
        command: 'pytest',
        state: 'pending',
      },
    }

    render(
      <SceneView
        events={[permEvent]}
        running={true}
        actions={{ onToolPermission: vi.fn() }}
      />,
    )

    // O cartão só é renderizado uma única vez
    expect(screen.getAllByTestId('tool-permission-card')).toHaveLength(1)
    expect(screen.queryByTestId('scene-agent-panel')).toBeNull()

    // Clicar em "Ver na cena" seleciona o agente e abre o painel
    const selectBtn = screen.getByTestId('scene-pending-select-req-pytest')
    fireEvent.click(selectBtn)

    const panel = screen.getByTestId('scene-agent-panel')
    expect(panel.textContent).toContain('Testador')
    const agentPending = within(panel).getByTestId('scene-agent-pending')
    expect(agentPending.textContent).toContain(
      'Esperando você: Rodar pytest no diretório backend',
    )
    // Não duplica o cartão dentro do painel do personagem
    expect(screen.getAllByTestId('tool-permission-card')).toHaveLength(1)

    const respondBtn = within(agentPending).getByTestId(
      'scene-agent-pending-respond-req-pytest',
    )
    fireEvent.click(respondBtn)
    expect(document.activeElement).toBe(
      screen.getByTestId('scene-pending-section'),
    )
  })

  it('(19) caixa "Falar com o Orin" chama onSendToOrin com texto aparado, limpa após sucesso e mantém o texto com alerta se rejeitar', async () => {
    let shouldFail = false
    const onSendToOrin = vi.fn().mockImplementation(async () => {
      if (shouldFail) {
        throw new Error('Falha de conexão ao enviar')
      }
    })

    render(<SceneView running={true} onSendToOrin={onSendToOrin} />)

    const input = screen.getByTestId(
      'scene-orin-message-input',
    ) as HTMLTextAreaElement
    const submitBtn = screen.getByTestId(
      'scene-orin-message-submit',
    ) as HTMLButtonElement

    expect(submitBtn.disabled).toBe(true)

    fireEvent.change(input, { target: { value: '   Foque nos testes de API   ' } })
    expect(submitBtn.disabled).toBe(false)

    // Shift+Enter não envia
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(onSendToOrin).not.toHaveBeenCalled()

    // Enter envia com texto aparado e limpa após sucesso
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: false })
    await waitFor(() => {
      expect(onSendToOrin).toHaveBeenCalledWith('Foque nos testes de API')
    })
    await waitFor(() => {
      expect(input.value).toBe('')
    })

    // Quando a promise rejeita, mantém o texto e exibe erro
    shouldFail = true
    fireEvent.change(input, { target: { value: 'Mensagem que vai falhar' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(screen.getByTestId('scene-orin-message-error').textContent).toContain(
        'Falha de conexão ao enviar',
      )
    })
    expect(input.value).toBe('Mensagem que vai falhar')
  })

  it('(20) botões "Parar" e "Mensagem" aparecem apenas para subagente ativo (não para Orin nem subagente concluído/parado/falho)', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Início',
      },
      {
        eventId: 'ev-active',
        cursor: '2',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:active',
        agentName: 'Ativo',
        parentAgentId: 'main',
        toolName: 'read_file',
        summary: 'Lendo arquivos',
      },
      {
        eventId: 'ev-done',
        cursor: '3',
        type: 'delegation.completed',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:conv:done',
        agentName: 'Finalizado',
        parentAgentId: 'main',
        summary: 'Concluído',
      },
      {
        eventId: 'ev-stopped',
        cursor: '4',
        type: 'delegation.failed',
        kind: 'agent',
        state: 'cancelled',
        agentId: 'agent:conv:stopped',
        agentName: 'Interrompido',
        parentAgentId: 'main',
        errorCode: 'STOPPED_BY_USER',
        summary: 'Parado',
      },
    ]

    render(
      <SceneView
        events={events}
        running={true}
        onStopAgent={vi.fn()}
        onMessageAgent={vi.fn()}
      />,
    )

    // Selecionar Orin principal -> sem botões Parar/Mensagem
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:main'))
    expect(screen.queryByTestId('scene-agent-stop-button')).toBeNull()
    expect(screen.queryByTestId('scene-agent-message-button')).toBeNull()

    // Selecionar subagente concluído -> sem botões Parar/Mensagem
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:done'))
    expect(screen.queryByTestId('scene-agent-stop-button')).toBeNull()
    expect(screen.queryByTestId('scene-agent-message-button')).toBeNull()

    // Selecionar subagente parado -> sem botões Parar/Mensagem
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:stopped'))
    expect(screen.queryByTestId('scene-agent-stop-button')).toBeNull()
    expect(screen.queryByTestId('scene-agent-message-button')).toBeNull()

    // Selecionar subagente ativo -> botões Parar e Mensagem presentes com aria-label explícito
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:active'))
    const stopBtn = screen.getByTestId('scene-agent-stop-button')
    const msgBtn = screen.getByTestId('scene-agent-message-button')
    expect(stopBtn.getAttribute('aria-label')).toBe('Parar Ativo')
    expect(msgBtn.getAttribute('aria-label')).toBe('Mensagem para Ativo')
  })

  it('(21) botão "Parar" exige confirmação inline, mostra "parando…" enquanto aguarda e exibe role="alert" se falhar', async () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Início',
      },
      {
        eventId: 'ev-sub',
        cursor: '2',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:sub1',
        agentName: 'Explorador',
        parentAgentId: 'main',
        toolName: 'search_code',
        summary: 'Buscando símbolos',
      },
    ]

    let resolveStop!: (val: { ok: true } | { ok: false; reason: 'error'; message: string }) => void
    const onStopAgent = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStop = resolve
        }),
    )

    render(
      <SceneView
        events={events}
        running={true}
        onStopAgent={onStopAgent}
        onMessageAgent={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:sub1'))
    fireEvent.click(screen.getByTestId('scene-agent-stop-button'))

    expect(onStopAgent).not.toHaveBeenCalled()
    const confirmBox = screen.getByTestId('scene-agent-stop-confirm')
    expect(confirmBox.textContent).toContain(
      'Parar Explorador? Ele interrompe o que está fazendo e para os agentes que ele iniciou.',
    )

    // Cancelar fecha a confirmação sem chamar onStopAgent
    fireEvent.click(screen.getByTestId('scene-agent-stop-cancel-button'))
    expect(screen.queryByTestId('scene-agent-stop-confirm')).toBeNull()
    expect(onStopAgent).not.toHaveBeenCalled()

    // Abrir novamente e confirmar
    fireEvent.click(screen.getByTestId('scene-agent-stop-button'))
    const confirmBtn = screen.getByTestId('scene-agent-stop-confirm-button')
    fireEvent.click(confirmBtn)

    expect(onStopAgent).toHaveBeenCalledWith('agent:conv:sub1')
    expect(confirmBtn.textContent).toBe('parando…')

    resolveStop({ ok: false, reason: 'error', message: 'Falha ao parar agente' })
    await waitFor(() => {
      const errAlert = screen.getByTestId('scene-agent-stop-error')
      expect(errAlert.getAttribute('role')).toBe('alert')
      expect(errAlert.textContent).toContain('Falha ao parar agente')
    })
  })

  it('(22) botão "Mensagem" foca textarea, fecha com Esc, envia com Enter e trata 409 AGENT_NOT_RUNNING com botão de retomar via onSendToOrin', async () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Início',
      },
      {
        eventId: 'ev-sub',
        cursor: '2',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:sub-msg',
        agentName: 'Analista',
        parentAgentId: 'main',
        toolName: 'read_file',
        summary: 'Analisando métricas',
      },
    ]

    const onMessageAgent = vi
      .fn()
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ok: false, reason: 'not_running' })
    const onSendToOrin = vi.fn().mockResolvedValue(undefined)

    render(
      <SceneView
        events={events}
        running={true}
        onMessageAgent={onMessageAgent}
        onSendToOrin={onSendToOrin}
      />,
    )

    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:sub-msg'))
    fireEvent.click(screen.getByTestId('scene-agent-message-button'))

    const textarea = screen.getByTestId(
      'scene-agent-message-input',
    ) as HTMLTextAreaElement
    expect(document.activeElement).toBe(textarea)

    // Esc fecha o campo
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(screen.queryByTestId('scene-agent-message-form')).toBeNull()

    // Abrir novamente e enviar mensagem com sucesso
    fireEvent.click(screen.getByTestId('scene-agent-message-button'))
    const textarea2 = screen.getByTestId(
      'scene-agent-message-input',
    ) as HTMLTextAreaElement
    fireEvent.change(textarea2, {
      target: { value: '  Verifique também os índices  ' },
    })
    fireEvent.keyDown(textarea2, { key: 'Enter', shiftKey: false })

    await waitFor(() => {
      expect(onMessageAgent).toHaveBeenCalledWith(
        'agent:conv:sub-msg',
        'Verifique também os índices',
      )
    })
    await waitFor(() => {
      expect(
        screen.getByTestId('scene-agent-message-success').textContent,
      ).toBe('Mensagem entregue a Analista; ele lê no próximo passo.')
    })

    // Segunda tentativa retorna 409 not_running
    fireEvent.click(screen.getByTestId('scene-agent-message-button'))
    const textarea3 = screen.getByTestId(
      'scene-agent-message-input',
    ) as HTMLTextAreaElement
    fireEvent.change(textarea3, {
      target: { value: 'Inclua o resumo de custos' },
    })
    fireEvent.click(screen.getByTestId('scene-agent-message-submit'))

    await waitFor(() => {
      const notRunningBox = screen.getByTestId('scene-agent-not-running')
      expect(notRunningBox.getAttribute('role')).toBe('alert')
      expect(notRunningBox.textContent).toContain('Analista já terminou.')
    })

    const resumeBtn = screen.getByTestId('scene-agent-resume-button')
    expect(resumeBtn.textContent).toBe('Pedir ao Orin para retomá-lo')
    fireEvent.click(resumeBtn)

    await waitFor(() => {
      expect(onSendToOrin).toHaveBeenCalledWith(
        'Retome o agente "Analista" (agent_id agent:conv:sub-msg) com esta mensagem: Inclua o resumo de custos',
      )
    })
  })

  it('(24) subagente na fila não mostra Parar/Mensagem, e um 409 de outro tipo lançado pelo handler é erro, não "já terminou"', async () => {
    const queued: ConversationActivityEvent[] = [
      {
        eventId: 'ev-q',
        cursor: '1',
        type: 'delegation.waiting',
        kind: 'agent',
        state: 'queued',
        agentId: 'agent:conv:fila',
        agentName: 'Revisor',
        summary: 'Aguardando vaga',
      },
    ]
    render(
      <SceneView events={queued} running={true} onStopAgent={vi.fn()} onMessageAgent={vi.fn()} />,
    )
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:fila'))
    expect(screen.queryByTestId('scene-agent-stop-button')).toBeNull()
    expect(screen.queryByTestId('scene-agent-message-button')).toBeNull()
    cleanup()

    const working: ConversationActivityEvent[] = [
      {
        eventId: 'ev-w',
        cursor: '1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:conv:ativo',
        agentName: 'Analista',
        parentAgentId: 'main',
        toolName: 'read_file',
        summary: 'Lendo',
      },
    ]
    const onMessageAgent = vi
      .fn()
      .mockRejectedValue({ status: 409, code: 'execution_conflict', message: 'conflito' })
    render(
      <SceneView events={working} running={true} onStopAgent={vi.fn()} onMessageAgent={onMessageAgent} />,
    )
    fireEvent.click(screen.getByTestId('scene-agent-button-agent:conv:ativo'))
    fireEvent.click(screen.getByTestId('scene-agent-message-button'))
    fireEvent.change(screen.getByTestId('scene-agent-message-input'), {
      target: { value: 'Olá' },
    })
    fireEvent.click(screen.getByTestId('scene-agent-message-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('scene-agent-message-error').getAttribute('role')).toBe('alert')
    })
    expect(screen.queryByTestId('scene-agent-not-running')).toBeNull()
  })

  it('(23) API stopConversationAgent e sendConversationAgentMessage usam encodeURIComponent no agentId e expectedStatus 202', async () => {
    const requestMock = vi.fn().mockImplementation(async (req: { parse: (v: unknown) => unknown }) =>
      req.parse({
        agent_id: 'agent:conv-1:0123456789abcdef01234567',
        status: 'stopping',
        control_id: 'ctrl-1',
      }),
    )
    const fakeClient = {
      createMutationIntent: () => ({ idempotencyKey: 'idem-1' }),
      request: requestMock,
    } as unknown as ApiClient

    const stopRes = await stopConversationAgent(
      fakeClient,
      'conv/1',
      'agent:conv-1:0123456789abcdef01234567',
    )
    expect(stopRes).toEqual({
      agent_id: 'agent:conv-1:0123456789abcdef01234567',
      status: 'stopping',
      control_id: 'ctrl-1',
    })
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/v1/conversations/conv%2F1/agents/agent%3Aconv-1%3A0123456789abcdef01234567/stop',
        method: 'POST',
        expectedStatus: 202,
      }),
    )

    await sendConversationAgentMessage(
      fakeClient,
      'conv/1',
      'agent:conv-1:0123456789abcdef01234567',
      'Olá subagente',
    )
    expect(requestMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: '/v1/conversations/conv%2F1/agents/agent%3Aconv-1%3A0123456789abcdef01234567/messages',
        method: 'POST',
        expectedStatus: 202,
        body: { message: 'Olá subagente' },
      }),
    )
  })
})
