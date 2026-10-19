import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchedulesPage } from '../../src/features/schedules/SchedulesPage'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SchedulesPage with jobs the agent scheduled', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists cron jobs and marks what the agent made', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(typeof input === 'string' ? input : String(input), 'http://test.local').pathname
      if (path === '/v1/schedules') {
        return json({ items: [
          { schedule_id: 's1', state: 'ACTIVE', next_fire_at: '2026-10-20T12:00:00Z', recurrence: 'cron', project_id: null, message: 'Verifique o CI', provider: 'openrouter', model_id: 'model-1', conversation_id: 'chat-1', source: 'cron', cron: '0 9 * * 1-5' },
          { schedule_id: 's2', state: 'EXPIRED', next_fire_at: null, recurrence: 'once', project_id: null, message: 'Relatório', provider: 'openrouter', model_id: 'model-1', conversation_id: null, source: 'user', cron: null },
        ] })
      }
      return json({ items: [] })
    }))
    render(<MemoryRouter initialEntries={['/settings/schedules']}><SchedulesPage /></MemoryRouter>)
    expect(await screen.findByText('Verifique o CI')).toBeInTheDocument()
    expect(screen.getByText(/cron 0 9 \* \* 1-5/)).toBeInTheDocument()
    expect(screen.getAllByText('pelo agente')).toHaveLength(1)
    expect(screen.getByText('Concluída')).toBeInTheDocument()
  })
})
