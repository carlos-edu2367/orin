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

  it('creates, edits, pauses/resumes, and permanently deletes a task through the UI', async () => {
    let items: Array<Record<string, unknown>> = []
    const requests: Array<{ method: string; path: string; search: string; body?: Record<string, unknown> }> = []
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const parsed = new URL(url, 'http://test.local')
      const path = parsed.pathname
      const method = String(init?.method ?? 'GET').toUpperCase()
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined
      requests.push({ method, path, search: parsed.search, body })

      if (path === '/v1/providers/openrouter/models') {
        return json({
          items: [
            {
              provider: 'openrouter',
              model_id: 'model-1',
              display_name: 'Model 1',
              context_window: 128000,
              capabilities: ['tools'],
              input_modalities: ['text'],
              output_modalities: ['text'],
              pricing: null,
              is_favorite: true,
              refreshed_at: null,
              route_kind: 'model',
            },
          ],
        })
      }
      if (path === '/v1/providers/google-antigravity/models') {
        return json({
          items: [
            {
              provider: 'google-antigravity',
              model_id: 'gemini-4-neon-high',
              display_name: 'Gemini 4 Neon (High) (Assinatura Antigravity)',
              context_window: 1000000,
              capabilities: ['tools'],
              input_modalities: ['text'],
              output_modalities: ['text'],
              pricing: null,
              is_favorite: true,
              refreshed_at: null,
              route_kind: 'model',
            },
          ],
        })
      }
      if (path === '/v1/projects') return json({ items: [] })
      if (path === '/v1/schedules' && method === 'GET') {
        return json({ items })
      }
      if (path === '/v1/schedules' && method === 'POST') {
        const createdItem = {
          schedule_id: 'schedule-1',
          state: 'ACTIVE',
          next_fire_at: '2026-08-16T12:00:00Z',
          recurrence: (body?.recurrence as { kind?: string })?.kind ?? 'once',
          project_id: null,
          message: body?.message ?? '',
          provider: (body?.selection as { provider?: string })?.provider ?? 'openrouter',
          model_id: (body?.selection as { model_id?: string })?.model_id ?? 'model-1',
          model_display_name: 'Model 1',
          conversation_id: null,
          timezone: body?.timezone ?? 'UTC',
          cron: null,
          source: 'user',
        }
        items = [createdItem]
        return json(createdItem, 201)
      }
      if (path === '/v1/schedules/schedule-1' && method === 'PATCH') {
        const current = items[0]
        const sel = body?.selection as { provider?: string; model_id?: string } | undefined
        const rec = body?.recurrence as { kind?: string; expression?: string } | undefined
        const updated = {
          ...current,
          ...(body?.message ? { message: body.message } : {}),
          ...(sel ? { provider: sel.provider, model_id: sel.model_id, model_display_name: 'Gemini 4 Neon (High) (Assinatura Antigravity)' } : {}),
          ...(body?.timezone ? { timezone: body.timezone } : {}),
          ...(rec ? { recurrence: rec.kind, cron: rec.expression ?? null } : {}),
          ...(body?.state ? { state: body.state, next_fire_at: body.state === 'PAUSED' ? null : '2026-08-17T15:00:00Z' } : {}),
        }
        items = [updated]
        return json(updated, 200)
      }
      if (path === '/v1/schedules/schedule-1' && method === 'DELETE') {
        items = []
        return new Response(null, { status: 204 })
      }
      return json({ items: [] })
    })
    vi.stubGlobal('fetch', fetchImpl)

    render(
      <MemoryRouter initialEntries={['/settings/schedules']}>
        <SchedulesPage />
      </MemoryRouter>,
    )
    const user = userEvent.setup()

    await user.click(screen.getByTestId('schedule-mode-once'))
    await user.type(await screen.findByLabelText('Instrução'), 'Verifique o relatório')
    expect(screen.getByLabelText('Fuso horário')).toHaveValue('America/Sao_Paulo')
    await user.selectOptions(screen.getByLabelText('Fuso horário'), 'UTC')
    fireEvent.change(screen.getByLabelText('Data e hora'), { target: { value: '2026-08-16T12:00' } })
    await user.click(screen.getByRole('button', { name: 'Criar tarefa' }))

    await waitFor(() => expect(requests.some((request) => request.method === 'POST' && request.path === '/v1/schedules')).toBe(true))
    expect(requests.find((request) => request.method === 'POST')?.body).toMatchObject({
      timezone: 'UTC',
      recurrence: { kind: 'once', fire_at: '2026-08-16T12:00' },
    })
    expect(await screen.findByText('Verifique o relatório')).toBeInTheDocument()

    // Edit: switch to Neon model and update message & recurrence
    await user.click(screen.getByTestId('schedule-edit-schedule-1'))
    const promptInput = screen.getByLabelText('Instrução')
    await user.clear(promptInput)
    await user.type(promptInput, 'Relatório atualizado')
    await user.click(screen.getByTestId('schedule-mode-repeat'))
    await user.selectOptions(screen.getByLabelText('Modelo'), 'google-antigravity::gemini-4-neon-high')
    await user.click(screen.getByRole('button', { name: 'Salvar alterações' }))

    await waitFor(() => expect(requests.some((req) => req.method === 'PATCH' && req.path === '/v1/schedules/schedule-1')).toBe(true))
    expect(requests.find((req) => req.method === 'PATCH')?.body).toMatchObject({
      message: 'Relatório atualizado',
      selection: { provider: 'google-antigravity', model_id: 'gemini-4-neon-high' },
    })
    expect(await screen.findByText('Relatório atualizado')).toBeInTheDocument()
    expect(screen.getByTestId('schedule-model-schedule-1')).toHaveTextContent('Gemini 4 Neon (High)')

    // Pause then switch to Pausadas tab and resume
    await user.click(screen.getByTestId('schedule-toggle-schedule-1'))
    await waitFor(() => expect(screen.getByTestId('schedules-tab-paused')).toHaveTextContent('Pausadas 1'))
    await user.click(screen.getByTestId('schedules-tab-paused'))
    expect(await screen.findByText('Relatório atualizado')).toBeInTheDocument()

    // Permanent delete with inline confirmation
    await user.click(screen.getByTestId('schedule-delete-schedule-1'))
    await user.click(screen.getByRole('button', { name: 'Confirmar' }))
    await waitFor(() =>
      expect(
        requests.some(
          (request) =>
            request.method === 'DELETE' &&
            request.path === '/v1/schedules/schedule-1' &&
            request.search === '?permanent=true',
        ),
      ).toBe(true),
    )
    expect(await screen.findByTestId('schedules-empty-state')).toBeInTheDocument()
  })
})
