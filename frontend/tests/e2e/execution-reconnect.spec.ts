import { expect, test, type Route } from '@playwright/test'

// This fake is deliberately test-only and is not a production contract. It simulates
// only public responses documented in BACKEND_DISCOVERY.md:
//   - GET  /v1/executions/{id}                    -> ExecutionView
//   - POST /v1/events/streams                     -> { stream_id, cursor, stream_binding_digest, revocation_epoch }
//   - POST /v1/events/streams/{id}/read           -> { events: ClientEvent[], cursor }
//   - the sanitized error envelope { error: { code, category, message_key, correlation_id, retryable, retry_after } }
// The `invalid_cursor` code stands in for the documented "cursor inválido/antigo exige
// ressincronização" semantics; no other endpoint, event or field is simulated.
const execution = (state: 'RUNNING' | 'WAITING_USER' | 'COMPLETED' | 'CANCELLED', version: number) => ({
  execution_id: 'exec-e2e',
  agent_id: 'agent-orbit',
  state,
  state_version: version,
  parent_execution_id: null,
  created_at: '2026-08-07T10:00:00.000Z',
  updated_at: `2026-08-07T10:00:0${version}.000Z`,
  finished_at: state === 'COMPLETED' || state === 'CANCELLED' ? '2026-08-07T10:00:04.000Z' : null,
  result: state === 'COMPLETED' ? { result_ref: 'opaque-result' } : null,
  failure: null,
})

const transition = (id: string, sequence: number, state: 'WAITING_USER' | 'CANCELLED') => ({
  event_id: id,
  event_type: state === 'WAITING_USER' ? 'ExecutionWaitingForUser' : 'ExecutionCancelled',
  execution_id: 'exec-e2e',
  sequence,
  occurred_at: `2026-08-07T10:00:0${sequence}.000Z`,
  payload: { from_state: sequence === 3 ? 'RUNNING' : 'WAITING_USER', to_state: state, state_version: sequence },
})

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

test('recovers from a network disconnect and deduplicates replayed activity without state regression', async ({ page }) => {
  let snapshotReads = 0
  let replayReads = 0
  let snapshotState = execution('RUNNING', 2)

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === '/v1/executions/exec-e2e') {
      snapshotReads += 1
      await fulfillJson(route, snapshotState)
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: `stream-${snapshotReads}`, cursor: snapshotState.state_version === 2 ? 'cursor-2' : 'cursor-3', stream_binding_digest: 'public-digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      replayReads += 1
      if (replayReads === 1) {
        snapshotState = execution('WAITING_USER', 3)
        await fulfillJson(route, { events: [transition('evt-3', 3, 'WAITING_USER')], cursor: 'cursor-3' })
      } else if (replayReads === 2) {
        await route.abort('connectionreset')
      } else {
        snapshotState = execution('CANCELLED', 4)
        await fulfillJson(route, { events: [transition('evt-3', 3, 'WAITING_USER'), transition('evt-4', 4, 'CANCELLED')], cursor: 'cursor-4' })
      }
      return
    }
    await route.abort('failed')
  })

  await page.goto('/execution/exec-e2e')
  await expect.poll(() => snapshotReads).toBeGreaterThan(0)
  await expect(page.getByRole('heading', { name: 'Uma tarefa em andamento' })).toBeVisible()
  await expect(page.getByText('Trabalhando', { exact: true })).toBeVisible()
  await expect(page.getByText('Aguardando você', { exact: true })).toBeVisible()
  await expect(page.getByTestId('realtime-state')).toContainText(/Recuperando conexão|Ressincronizando dados/)
  await expect(page.getByText('Cancelado')).toBeVisible()
  await expect(page.getByTestId('activity-count')).toHaveText('2 atualizações aplicadas')
  await expect(page.getByText('A execução foi cancelada.')).toHaveCount(1)
})

test('resynchronizes an invalid cursor from a fresh snapshot while preserving accessible recovery text', async ({ page }) => {
  let snapshotReads = 0
  let replayReads = 0
  let snapshotState = execution('RUNNING', 2)

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === '/v1/executions/exec-e2e') {
      snapshotReads += 1
      if (snapshotReads > 1) await new Promise((resolve) => setTimeout(resolve, 180))
      await fulfillJson(route, snapshotState)
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: `stream-${snapshotReads}`, cursor: snapshotState.state_version === 2 ? 'cursor-2' : 'cursor-4', stream_binding_digest: 'public-digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      replayReads += 1
      if (replayReads === 1) {
        snapshotState = execution('COMPLETED', 4)
        await fulfillJson(route, {
          error: {
            code: 'invalid_cursor',
            category: 'VALIDATION',
            message_key: 'invalid_cursor',
            correlation_id: 'corr-public',
            retryable: false,
            retry_after: null,
          },
        }, 400)
      } else {
        await fulfillJson(route, { events: [], cursor: 'cursor-4' })
      }
      return
    }
    await route.abort('failed')
  })

  await page.goto('/execution/exec-e2e')
  await expect.poll(() => snapshotReads).toBeGreaterThan(0)
  await expect(page.getByRole('heading', { name: 'Uma tarefa em andamento' })).toBeVisible()
  await expect(page.getByTestId('realtime-state')).toContainText('Ressincronizando dados')
  await expect(page.getByText('Concluído')).toBeVisible()
  await expect(page.getByTestId('activity-count')).toHaveText('0 atualizações aplicadas')
  await expect(page.getByRole('button', { name: 'Pausar execução' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Cancelar execução' })).toBeDisabled()
})

test('resynchronizes a state_version gap and keeps the page keyboard accessible under reduced motion', async ({ page }) => {
  let snapshotReads = 0
  let replayReads = 0
  let snapshotState = execution('RUNNING', 2)

  await page.emulateMedia({ reducedMotion: 'reduce' })

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === '/v1/executions/exec-e2e') {
      snapshotReads += 1
      // The reconciliation snapshot is held long enough to observe the recovery text.
      if (snapshotReads > 1) await new Promise((resolve) => setTimeout(resolve, 800))
      await fulfillJson(route, snapshotState)
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: `stream-${snapshotReads}`, cursor: `cursor-${snapshotReads}`, stream_binding_digest: 'public-digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      replayReads += 1
      if (replayReads === 1) {
        // Version 3 was never delivered: the projection must refuse the gap and resync.
        snapshotState = execution('CANCELLED', 4)
        await fulfillJson(route, { events: [transition('evt-4', 4, 'CANCELLED')], cursor: 'cursor-gap' })
        return
      }
      await fulfillJson(route, { events: [], cursor: 'cursor-4' })
      return
    }
    await route.abort('failed')
  })

  await page.goto('/execution/exec-e2e')
  await expect(page.getByTestId('realtime-state')).toContainText('Ressincronizando dados')

  const status = page.getByTestId('realtime-state')
  await expect(status).toHaveAttribute('role', 'status')
  await expect(status).toHaveAttribute('aria-live', 'polite')

  await expect(page.getByRole('heading', { name: 'Uma tarefa em andamento' })).toBeVisible()
  await expect(page.getByText('Cancelado', { exact: true })).toBeVisible()
  await expect(page.getByText('A execução foi cancelada.')).toHaveCount(1)
  await expect(page.getByTestId('activity-count')).toHaveText('0 atualizações aplicadas')
  await expect.poll(() => snapshotReads).toBeGreaterThan(1)

  const inspector = page.getByRole('button', { name: 'Abrir inspector' })
  await inspector.focus()
  await expect(inspector).toBeFocused()
  await expect(inspector).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Enter')
  await expect(inspector).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('Detalhes técnicos')).toBeVisible()
})
