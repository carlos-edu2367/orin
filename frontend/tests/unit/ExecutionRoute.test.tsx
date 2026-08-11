import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ExecutionRoute } from '../../src/features/executions/ExecutionRoute'

const view = {
  execution_id: 'exec-ctl',
  agent_id: 'agent-orbit',
  state: 'RUNNING',
  state_version: 2,
  parent_execution_id: null,
  created_at: '2026-08-07T10:00:00.000Z',
  updated_at: '2026-08-07T10:00:02.000Z',
  finished_at: null,
  result: null,
  failure: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderRoute(client: ApiClient) {
  return render(
    <MemoryRouter initialEntries={['/execution/exec-ctl']}>
      <Routes>
        <Route path="/execution/:executionId" element={<ExecutionRoute client={client} />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ExecutionRoute control intentions', () => {
  it('reuses one Idempotency-Key when the same WAITING_USER input reference is retried', async () => {
    let keys = 0
    const inputKeys: string[] = []
    const waitingView = { ...view, state: 'WAITING_USER', state_version: 6 }
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input)
      if (path === '/v1/executions/exec-ctl') return json(waitingView)
      if (path === '/v1/events/streams') return json({ stream_id: 'stream-input', cursor: 'cursor-1', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      if (path === '/v1/executions/exec-ctl/input') {
        inputKeys.push(new Headers(init?.headers).get('Idempotency-Key') ?? '')
        if (inputKeys.length === 1) throw new TypeError('network unavailable')
        return json({ outcome: 'accepted', execution_id: 'exec-ctl', state_version: 6 }, 202)
      }
      return json({ events: [], cursor: 'cursor-1' })
    })
    const client = new ApiClient({
      fetchImpl,
      maxAttempts: 1,
      createIdempotencyKey: () => `intent-${++keys}`,
    })
    const user = userEvent.setup()

    renderRoute(client)

    await user.click(await screen.findByRole('button', { name: 'Fornecer referência de entrada' }))
    await user.type(screen.getByLabelText('Referência de entrada'), 'input:retryable')
    const submit = screen.getByRole('button', { name: 'Enviar entrada' })
    await user.click(submit)
    expect(await screen.findByText('A entrada não foi confirmada. O estado atual foi preservado.')).toBeInTheDocument()

    await user.click(submit)
    await waitFor(() => expect(inputKeys).toHaveLength(2))

    expect(inputKeys).toEqual(['intent-1', 'intent-1'])
  })

  it('reuses one Idempotency-Key when the user retries the same control intention', async () => {
    let keys = 0
    const controlKeys: string[] = []
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input)
      if (path === '/v1/executions/exec-ctl') return json(view)
      if (path === '/v1/events/streams') return json({ stream_id: 'stream-ctl', cursor: 'cursor-1', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      if (path === '/v1/executions/exec-ctl/control') {
        controlKeys.push(new Headers(init?.headers).get('Idempotency-Key') ?? '')
        if (controlKeys.length === 1) throw new TypeError('network unavailable')
        return json({ outcome: 'accepted', execution_id: 'exec-ctl', state_version: 2 }, 202)
      }
      return json({ events: [], cursor: 'cursor-1' })
    })
    const client = new ApiClient({
      fetchImpl,
      maxAttempts: 1,
      createIdempotencyKey: () => `intent-${++keys}`,
    })
    const user = userEvent.setup()

    renderRoute(client)

    const pause = await screen.findByRole('button', { name: 'Pausar execução' })
    await waitFor(() => expect(pause).toBeEnabled())

    await user.click(pause)
    expect(await screen.findByText('O comando não foi confirmado. O estado atual foi preservado.')).toBeInTheDocument()

    await user.click(pause)
    await waitFor(() => expect(controlKeys).toHaveLength(2))

    expect(controlKeys).toEqual(['intent-1', 'intent-1'])
  })

  it('uses a distinct Idempotency-Key for a different control intention', async () => {
    let keys = 0
    const controlKeys: string[] = []
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input)
      if (path === '/v1/executions/exec-ctl') return json(view)
      if (path === '/v1/events/streams') return json({ stream_id: 'stream-ctl', cursor: 'cursor-1', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      if (path === '/v1/executions/exec-ctl/control') {
        controlKeys.push(new Headers(init?.headers).get('Idempotency-Key') ?? '')
        throw new TypeError('network unavailable')
      }
      return json({ events: [], cursor: 'cursor-1' })
    })
    const client = new ApiClient({
      fetchImpl,
      maxAttempts: 1,
      createIdempotencyKey: () => `intent-${++keys}`,
    })
    const user = userEvent.setup()

    renderRoute(client)

    const pause = await screen.findByRole('button', { name: 'Pausar execução' })
    await waitFor(() => expect(pause).toBeEnabled())

    await user.click(pause)
    await waitFor(() => expect(controlKeys).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Cancelar execução' }))
    await waitFor(() => expect(controlKeys).toHaveLength(2))

    expect(controlKeys).toEqual(['intent-1', 'intent-2'])
  })
})
