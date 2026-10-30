import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatHumanCron } from '../../src/api/schedules'
import { SchedulesPage } from '../../src/features/schedules/SchedulesPage'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SchedulesPage — Phase 6 (ref-09)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('formats cron expressions into human-friendly Portuguese strings and guards out-of-range values', () => {
    expect(formatHumanCron('0 9 * * 1-5')).toBe('Seg a sex · 09:00')
    expect(formatHumanCron('0 18 * * 5')).toBe('Sexta · 18:00')
    expect(formatHumanCron('*/30 * * * *')).toBe('A cada 30 min')
    expect(formatHumanCron('99 25 * * 1-5')).toBe('Cron · 99 25 * * 1-5')
  })

  it('renders header, tabs with 0 active count, table columns, and honest empty state when API returns empty list', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async () => json({ items: [] })))

    render(
      <MemoryRouter initialEntries={['/schedules']}>
        <SchedulesPage embedded />
      </MemoryRouter>,
    )

    const heading = await screen.findByTestId('schedules-heading')
    expect(heading).toHaveTextContent('Ações agendadas')
    expect(screen.getByText('Tarefas que o Orin executa sozinho nos horários combinados.')).toBeInTheDocument()

    expect(screen.getByTestId('schedules-tab-active')).toHaveTextContent('Ativas 0')
    expect(screen.getByTestId('schedules-tab-paused')).toHaveTextContent('Pausadas 0')
    expect(screen.getByTestId('schedules-tab-completed')).toHaveTextContent('Concluídas 0')

    expect(screen.getByRole('columnheader', { name: 'AÇÃO' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'QUANDO' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'PROJETO' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'STATUS' })).toBeInTheDocument()

    const tbody = screen.getByTestId('schedules-table-body')
    expect(tbody).toBeInTheDocument()
    const emptyCell = await screen.findByTestId('schedules-empty-state')
    expect(emptyCell).toHaveTextContent('Nenhuma ação agendada neste filtro. Use o painel ao lado para criar uma rotina.')

    expect(screen.queryByText('Resumo diário das vendas')).not.toBeInTheDocument()
    expect(screen.queryByText('Relatório semanal da Auryly')).not.toBeInTheDocument()
    expect(screen.queryByText('Checar site no ar')).not.toBeInTheDocument()
  })

  it('renders real scheduled items from the API and updates tab counters accurately', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(typeof input === 'string' ? input : String(input), 'http://test.local').pathname
      if (path === '/v1/projects') {
        return json({
          items: [
            {
              project_id: 'proj-core',
              name: 'Orin Core',
              description: null,
              workspace_id: 'ws-1',
              created_at: null,
              updated_at: null,
              archived_at: null,
            },
          ],
        })
      }
      if (path === '/v1/schedules') {
        return json({
          items: [
            {
              schedule_id: 'sched-1',
              state: 'ACTIVE',
              next_fire_at: '2026-10-31T09:00:00Z',
              recurrence: 'cron',
              project_id: 'proj-core',
              message: 'Verificar status do build matinal',
              provider: 'openrouter',
              model_id: 'model-1',
              conversation_id: null,
              cron: '0 9 * * 1-5',
            },
            {
              schedule_id: 'sched-2',
              state: 'PAUSED',
              next_fire_at: null,
              recurrence: 'cron',
              project_id: null,
              message: 'Revisão semanal de dependências',
              provider: 'openrouter',
              model_id: 'model-1',
              conversation_id: null,
              cron: '0 18 * * 5',
            },
          ],
        })
      }
      return json({ items: [] })
    }))

    render(
      <MemoryRouter initialEntries={['/schedules']}>
        <SchedulesPage embedded />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Verificar status do build matinal')).toBeInTheDocument()
    expect(screen.getByText('Seg a sex · 09:00')).toBeInTheDocument()
    expect(screen.getAllByText('Orin Core').length).toBeGreaterThan(0)
    expect(screen.getByTestId('schedules-tab-active')).toHaveTextContent('Ativas 1')
    expect(screen.getByTestId('schedules-tab-paused')).toHaveTextContent('Pausadas 1')
  })

  it('renders the Nova ação drawer with Repeat mode selected by default, neutral test simulation feedback, and cron submission', async () => {
    const requests: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const path = new URL(url, 'http://test.local').pathname
      const method = String(init?.method ?? 'GET').toUpperCase()
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
      requests.push({ method, path, body })

      if (path === '/v1/providers/openrouter/models') {
        return json({ items: [{ provider: 'openrouter', model_id: 'model-1', display_name: 'Model 1', context_window: 128000, capabilities: ['tools'], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: true, refreshed_at: null, route_kind: 'model' }] })
      }
      if (path === '/v1/schedules' && method === 'POST') {
        return json({ schedule_id: 'sched-cron-1', state: 'ACTIVE', next_fire_at: '2026-10-31T09:00:00Z', recurrence: 'cron' }, 201)
      }
      return json({ items: [] })
    }))

    render(
      <MemoryRouter initialEntries={['/schedules']}>
        <SchedulesPage />
      </MemoryRouter>,
    )

    const user = userEvent.setup()
    const drawer = await screen.findByTestId('schedules-drawer')
    expect(drawer).toBeInTheDocument()

    const modeOnce = screen.getByTestId('schedule-mode-once')
    const modeRepeat = screen.getByTestId('schedule-mode-repeat')
    expect(modeRepeat).toHaveClass('is-selected')
    expect(modeOnce).not.toHaveClass('is-selected')

    expect(screen.getByLabelText('Instrução')).toBeInTheDocument()
    expect(screen.getByLabelText('Frequência')).toBeInTheDocument()
    expect(screen.getByLabelText('Horário')).toBeInTheDocument()
    expect(screen.getByLabelText('Fuso horário')).toHaveValue('America/Sao_Paulo')
    expect(screen.getByLabelText('Data e hora')).toBeInTheDocument()

    const submitBtn = screen.getByTestId('schedules-submit-button')
    expect(submitBtn).toHaveTextContent('Criar ação')
    expect(submitBtn).toHaveAttribute('aria-label', 'Criar tarefa')

    // Clicking Testar agora when message is empty uses neutral fallback text
    await user.click(screen.getByTestId('schedule-test-now'))
    const emptyFeedback = await screen.findByTestId('schedule-test-feedback')
    expect(emptyFeedback).toHaveTextContent('Execução de teste da rotina')
    expect(emptyFeedback).not.toHaveTextContent('Resumo diário das vendas')

    await user.type(screen.getByLabelText('Instrução'), 'Gerar resumo matinal')
    await user.click(screen.getByTestId('schedule-test-now'))

    const feedback = await screen.findByTestId('schedule-test-feedback')
    expect(feedback).toHaveTextContent(/Gerar resumo matinal/i)

    await user.click(submitBtn)
    await waitFor(() => expect(requests.some((req) => req.method === 'POST' && req.path === '/v1/schedules')).toBe(true))
    expect(requests.find((req) => req.method === 'POST')?.body).toMatchObject({
      message: 'Gerar resumo matinal',
      timezone: 'America/Sao_Paulo',
      recurrence: { kind: 'cron', expression: '0 9 * * 1-5' },
    })
  })
})
