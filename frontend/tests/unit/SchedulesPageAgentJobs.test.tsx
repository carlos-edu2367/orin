import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchedulesPage } from '../../src/features/schedules/SchedulesPage'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SchedulesPage with jobs the agent scheduled', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists only ACTIVE jobs in Ativas tab, displays exact model_id without mixing cancelled Carbon jobs, and shows completed/cancelled in Concluídas', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(typeof input === 'string' ? input : String(input), 'http://test.local').pathname
      if (path === '/v1/schedules') {
        return json({
          items: [
            {
              schedule_id: 's1',
              state: 'ACTIVE',
              next_fire_at: '2026-10-20T12:00:00Z',
              recurrence: 'cron',
              project_id: null,
              message: 'Sessão 2 - Burocracias',
              provider: 'google-antigravity',
              model_id: 'gemini-4-neon-high',
              model_display_name: 'Gemini 4 Neon (High) (Assinatura Antigravity)',
              conversation_id: 'chat-1',
              conversation_title: 'freelas e vagas',
              source: 'cron',
              cron: '0 15 * * *',
              timezone: 'Etc/GMT+3',
            },
            {
              schedule_id: 's-old-carbon',
              state: 'CANCELLED',
              next_fire_at: null,
              recurrence: 'cron',
              project_id: null,
              message: 'Sessão 2 - Burocracias (antiga)',
              provider: 'google-antigravity',
              model_id: 'gemini-4-carbon-high',
              model_display_name: 'Gemini 4 Carbon (High) (Assinatura Antigravity)',
              conversation_id: 'chat-1',
              source: 'cron',
              cron: '0 15 * * *',
              timezone: 'Etc/GMT+3',
            },
            {
              schedule_id: 's2',
              state: 'EXPIRED',
              next_fire_at: null,
              recurrence: 'once',
              project_id: null,
              message: 'Relatório finalizado',
              provider: 'openrouter',
              model_id: 'model-1',
              conversation_id: null,
              source: 'user',
              cron: null,
            },
          ],
        })
      }
      return json({ items: [] })
    }))

    render(
      <MemoryRouter initialEntries={['/settings/schedules']}>
        <SchedulesPage />
      </MemoryRouter>,
    )
    const user = userEvent.setup()

    // Active tab strictly shows only ACTIVE schedule with Neon, never the cancelled Carbon job
    expect(await screen.findByText('Sessão 2 - Burocracias')).toBeInTheDocument()
    expect(screen.getByTestId('schedule-model-s1')).toHaveTextContent('Gemini 4 Neon (High)')
    expect(screen.getAllByText('pelo agente')).toHaveLength(1)
    expect(screen.queryByText('Sessão 2 - Burocracias (antiga)')).not.toBeInTheDocument()
    expect(screen.queryByText(/Carbon/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Relatório finalizado')).not.toBeInTheDocument()

    // Switching to Concluídas tab reveals expired and cancelled schedules
    expect(screen.getByTestId('schedules-tab-completed')).toHaveTextContent('Concluídas 2')
    await user.click(screen.getByTestId('schedules-tab-completed'))
    expect(await screen.findByText('Relatório finalizado')).toBeInTheDocument()
    expect(screen.getByText('Sessão 2 - Burocracias (antiga)')).toBeInTheDocument()
    expect(screen.getByText('Cancelada')).toBeInTheDocument()
    expect(screen.getByText('Concluída')).toBeInTheDocument()
  })
})
