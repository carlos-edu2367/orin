import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchedulesPage } from '../../src/features/schedules/SchedulesPage'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SchedulesPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('creates, lists, and cancels a task through the form', async () => {
    let created = false
    const requests: Array<{ method: string; path: string }> = []
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const path = new URL(url, 'http://test.local').pathname
      const method = String(init?.method ?? 'GET').toUpperCase()
      requests.push({ method, path })

      if (path === '/v1/providers/openrouter/models') {
        return json({ items: [{ provider: 'openrouter', model_id: 'model-1', display_name: 'Model 1', context_window: 128000, capabilities: ['tools'], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: true, refreshed_at: null, route_kind: 'model' }] })
      }
      if (path === '/v1/projects') return json({ items: [] })
      if (path === '/v1/schedules' && method === 'GET') {
        return json({ items: created ? [{ schedule_id: 'schedule-1', state: 'ACTIVE', next_fire_at: '2026-08-16T12:00:00+00:00', recurrence: 'once', project_id: null, message: 'Verifique o relatório', provider: 'openrouter', model_id: 'model-1', conversation_id: null }] : [] })
      }
      if (path === '/v1/schedules' && method === 'POST') {
        created = true
        return json({ schedule_id: 'schedule-1', state: 'ACTIVE', next_fire_at: '2026-08-16T12:00:00+00:00', recurrence: 'once' }, 201)
      }
      if (path === '/v1/schedules/schedule-1' && method === 'DELETE') {
        created = false
        return new Response(null, { status: 204 })
      }
      return json({ items: [] })
    })
    vi.stubGlobal('fetch', fetchImpl)

    render(<MemoryRouter initialEntries={['/settings/schedules']}><SchedulesPage /></MemoryRouter>)
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Instrução'), 'Verifique o relatório')
    fireEvent.change(screen.getByLabelText('Data e hora'), { target: { value: '2026-08-16T12:00' } })
    await user.click(screen.getByRole('button', { name: 'Criar tarefa' }))

    await waitFor(() => expect(requests).toContainEqual({ method: 'POST', path: '/v1/schedules' }))
    expect(await screen.findByText('Verifique o relatório')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(requests).toContainEqual({ method: 'DELETE', path: '/v1/schedules/schedule-1' }))
  })
})
