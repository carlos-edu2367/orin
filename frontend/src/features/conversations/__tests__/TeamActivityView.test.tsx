import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConversationActivityEvent, TodoItem } from '../activityTypes'
import { TeamActivityView } from '../TeamActivityView'

afterEach(() => {
  cleanup()
})

describe('TeamActivityView (ref-10.png)', () => {
  it('renderiza o Hero, subtítulo, botão Pausar equipe, Voltar à conversa e estados vazios limpos por padrão (0 de 0 etapas)', () => {
    const onPauseTeam = vi.fn()
    const onBackToChat = vi.fn()

    render(
      <TeamActivityView
        onPauseTeam={onPauseTeam}
        onBackToChat={onBackToChat}
      />,
    )

    const root = screen.getByTestId('team-activity-view')
    expect(root).toBeTruthy()

    expect(
      screen.getByRole('heading', { level: 2, name: 'Uma equipe, um resultado.' }),
    ).toBeTruthy()
    expect(
      screen.getByText('O Orin coordena as etapas e reúne tudo para você.'),
    ).toBeTruthy()

    const pauseBtn = screen.getByTestId('team-pause-button')
    expect(pauseBtn.textContent).toContain('Pausar equipe')
    fireEvent.click(pauseBtn)
    expect(onPauseTeam).toHaveBeenCalledTimes(1)

    const backBtn = screen.getByTestId('team-back-to-chat-button')
    expect(backBtn.textContent).toContain('Voltar à conversa')
    fireEvent.click(backBtn)
    expect(onBackToChat).toHaveBeenCalledTimes(1)

    const stepper = screen.getByTestId('team-stepper')
    expect(stepper.textContent).toContain('0 de 0 etapas')
    expect(stepper.textContent).toContain('Em andamento')

    expect(screen.getByTestId('team-agents-empty').textContent).toContain(
      'Nenhum subagente em execução neste momento.',
    )
    expect(screen.queryByTestId('team-decision-card')).toBeNull()
    expect(screen.getByTestId('team-final-deliverable-card').textContent).toContain(
      'Nenhum entregável gerado ainda',
    )
    expect(screen.getByTestId('team-recent-activity').textContent).toContain(
      'Nenhuma atividade registrada ainda.',
    )
  })

  it('alterna o texto do botão para "Retomar equipe" quando paused=true e calcula etapas a partir de todos', () => {
    const todos: TodoItem[] = [
      { id: '1', content: 'Coletar referências', status: 'completed' },
      { id: '2', content: 'Analisar dados', status: 'completed' },
      { id: '3', content: 'Consolidar relatório', status: 'completed' },
      { id: '4', content: 'Revisão final', status: 'completed' },
    ]

    render(<TeamActivityView paused={true} todos={todos} />)

    expect(screen.getByTestId('team-pause-button').textContent).toContain('Retomar equipe')

    const stepper = screen.getByTestId('team-stepper')
    expect(stepper.textContent).toContain('4 de 4 etapas')
    expect(stepper.textContent).toContain('Concluído')
    expect(within(stepper).getAllByText('✓').length).toBe(4)
  })

  it('renderiza os especialistas fornecidos via prop agents e permite expandir/recolher "Ver atividade ⌄"', () => {
    render(
      <TeamActivityView
        agents={[
          {
            id: 'pesquisador',
            roleName: 'Pesquisador',
            currentTask: 'Comparando referências',
            status: 'running',
            durationLabel: '18 min',
            icon: 'search',
            activities: [
              {
                id: 'pesq-act-1',
                label: 'Analisou 12 referências sobre ferramentas de mercado',
                detail: '14:32',
              },
            ],
          },
          {
            id: 'analista',
            roleName: 'Analista',
            currentTask: 'Organizando oportunidades',
            status: 'running',
            durationLabel: '12 min',
            icon: 'chart',
            activities: [],
          },
          {
            id: 'revisor',
            roleName: 'Revisor',
            currentTask: 'Aguardando análise',
            status: 'queued',
            durationLabel: '3 min',
            icon: 'document',
            activities: [],
          },
        ]}
      />,
    )

    const agentsList = screen.getByTestId('team-agents-list')
    expect(agentsList).toBeTruthy()

    const pesquisadorCard = screen.getByTestId('team-agent-card-pesquisador')
    expect(pesquisadorCard.textContent).toContain('Pesquisador')
    expect(pesquisadorCard.textContent).toContain('Comparando referências')
    expect(pesquisadorCard.textContent).toContain('Em andamento')
    expect(pesquisadorCard.textContent).toContain('18 min')

    const analistaCard = screen.getByTestId('team-agent-card-analista')
    expect(analistaCard.textContent).toContain('Analista')
    expect(analistaCard.textContent).toContain('Organizando oportunidades')
    expect(analistaCard.textContent).toContain('Em andamento')
    expect(analistaCard.textContent).toContain('12 min')

    const revisorCard = screen.getByTestId('team-agent-card-revisor')
    expect(revisorCard.textContent).toContain('Revisor')
    expect(revisorCard.textContent).toContain('Aguardando análise')
    expect(revisorCard.textContent).toContain('Na fila')
    expect(revisorCard.textContent).toContain('3 min')

    const toggleBtn = screen.getByTestId('team-agent-toggle-pesquisador')
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('team-agent-events-pesquisador')).toBeNull()

    fireEvent.click(toggleBtn)
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('true')
    const eventsList = screen.getByTestId('team-agent-events-pesquisador')
    expect(eventsList).toBeTruthy()
    expect(eventsList.textContent).toContain('referências')

    fireEvent.click(toggleBtn)
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('team-agent-events-pesquisador')).toBeNull()
  })

  it('agrupa eventos reais por agentId e exibe as ferramentas executadas ao expandir o card', () => {
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

    render(<TeamActivityView events={events} />)

    const searchCard = screen.getByTestId('team-agent-card-agent-search')
    expect(searchCard.textContent).toContain('Pesquisador')
    expect(searchCard.textContent).toContain('Comparando referências do setor')
    expect(searchCard.textContent).toContain('15 min')

    const reviewCard = screen.getByTestId('team-agent-card-agent-review')
    expect(reviewCard.textContent).toContain('Revisor')
    expect(reviewCard.textContent).toContain('Concluído')

    fireEvent.click(screen.getByTestId('team-agent-toggle-agent-search'))
    const agentEvents = screen.getByTestId('team-agent-events-agent-search')
    expect(agentEvents.textContent).toContain('Pesquisando concorrentes SaaS')
    expect(agentEvents.textContent).toContain('Comparando referências do setor')
  })

  it('renderiza a coluna direita com Card de Decisão e Card de Entregável Final interativos', () => {
    const onResolveDecision = vi.fn()
    const onOpenDeliverable = vi.fn()

    const { rerender } = render(
      <TeamActivityView
        pendingDecision={{
          id: 'decision-paid-tools',
          question: 'Incluir ferramentas pagas na comparação?',
          context: 'Encontramos boas opções pagas que podem ampliar o escopo da análise.',
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

    const sideRail = screen.getByTestId('team-side-rail')
    expect(sideRail).toBeTruthy()

    const decisionCard = screen.getByTestId('team-decision-card')
    expect(decisionCard.textContent).toContain('Preciso de uma decisão')
    expect(decisionCard.textContent).toContain('Incluir ferramentas pagas na comparação?')
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

    const deliverableCard = screen.getByTestId('team-final-deliverable-card')
    expect(deliverableCard.textContent).toContain('Entregável final')
    expect(deliverableCard.textContent).toContain('Relatório de oportunidades.md')
    expect(deliverableCard.textContent).toContain('Em preparação')

    rerender(
      <TeamActivityView
        finalDeliverable={{
          fileName: 'Relatório de oportunidades.md',
          path: 'docs/relatorio.md',
          status: 'ready',
        }}
        onOpenDeliverable={onOpenDeliverable}
      />,
    )

    const openBtn = within(screen.getByTestId('team-final-deliverable-card')).getByRole('button', {
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

  it('renderiza o rodapé Atividade recente com horários HH:mm e botão "Ver todas as atividades ›"', () => {
    const onViewAllActivities = vi.fn()

    render(
      <TeamActivityView
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

    const recentSection = screen.getByTestId('team-recent-activity')
    expect(recentSection.textContent).toContain('Atividade recente')
    expect(recentSection.textContent).toContain('14:32')
    expect(recentSection.textContent).toContain(
      'Pesquisador analisou 12 referências sobre ferramentas de mercado.',
    )
    expect(recentSection.textContent).toContain('14:28')
    expect(recentSection.textContent).toContain('Analista organizou oportunidades por categoria.')

    const viewAllBtn = screen.getByTestId('team-view-all-activities')
    expect(viewAllBtn.textContent).toContain('Ver todas as atividades')
    fireEvent.click(viewAllBtn)
    expect(onViewAllActivities).toHaveBeenCalledTimes(1)
  })

  it('[Red-Team] sanitiza entradas com events=[], todos=[], timestamps malformados, agentId vazio e caracteres de controle sem lançar erro', () => {
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
      render(<TeamActivityView events={[]} todos={[]} />)
    }).not.toThrow()

    cleanup()

    expect(() => {
      render(<TeamActivityView events={malformedEvents} todos={[]} />)
    }).not.toThrow()

    const view = screen.getByTestId('team-activity-view')
    const hasControlChars = Array.from(view.textContent ?? '').some((ch) => {
      const code = ch.charCodeAt(0)
      return code <= 0x1f || code === 0x7f
    })
    expect(hasControlChars).toBe(false)
    expect(view.textContent).toContain('AgenteSecreto')
    expect(view.textContent).toContain('Analisando dados críticos')
  })
})
