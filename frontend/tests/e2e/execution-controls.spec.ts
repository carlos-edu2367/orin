import { expect, test, type Route } from '@playwright/test'

// This fake is deliberately test-only and is not a production contract. It simulates
// only public responses documented in BACKEND_DISCOVERY.md, cross-checked against
// `src/agentos/api/gateway.py` for this phase:
//   - POST /v1/executions               -> the real `_receipt` envelope
//     (`outcome`, `execution_id`, `state_version`) that `create_execution` produces
//     for a 202 accepted intent.
//   - GET  /v1/executions/{id}          -> ExecutionView
//   - POST /v1/executions/{id}/control  -> the same `_receipt` envelope that
//     `control_execution` produces for a successful PAUSE/RESUME/CANCEL 202.
//   - POST /v1/events/streams           -> { stream_id, cursor, stream_binding_digest, revocation_epoch }
//   - POST /v1/events/streams/{id}/read -> { events: ClientEvent[], cursor }
//   - the sanitized error envelope { error: { code, category, message_key, correlation_id, retryable, retry_after } }
//
// Reading `src/agentos/api/gateway.py` directly for this phase confirms its
// `@app.exception_handler` registrations only ever raise VALIDATION (422),
// AUTHENTICATION (401), AUTHORIZATION (403), RATE_LIMITED (429) and INTERNAL (500)
// today. CONFLICT and INDETERMINATE are categories `api/errors.ts` (`shouldResync`)
// already knows how to interpret from a documented envelope shape, but no domain
// exception in this gateway raises either of them yet. The two tests below that use
// those categories are a fake standing in for that documented-but-unimplemented
// behavior, not a proven production contract — the same posture already used for
// `invalid_cursor` in execution-reconnect.spec.ts.
//
// There is no UI for `WAITING_USER` input (`provideExecutionInput` is implemented in
// `api/executions.ts` but no component calls it — see IMPLEMENTATION_PLAN.md, Fase 6
// "Decisões locais"), so this file covers create+control only, not input.

const EXEC_ID = 'exec-controls-e2e'

type ExecState = 'QUEUED' | 'RUNNING' | 'PAUSED' | 'CANCELLED'

function execution(state: ExecState, version: number) {
  return {
    execution_id: EXEC_ID,
    agent_id: 'agent-orbit',
    state,
    state_version: version,
    parent_execution_id: null,
    created_at: '2026-08-08T09:00:00.000Z',
    updated_at: `2026-08-08T09:00:${String(version).padStart(2, '0')}.000Z`,
    finished_at: state === 'CANCELLED' ? '2026-08-08T09:00:09.000Z' : null,
    result: null,
    failure: null,
  }
}

function transitionEvent(id: string, sequence: number, eventType: string, toState: string, stateVersion: number) {
  return {
    event_id: id,
    event_type: eventType,
    execution_id: EXEC_ID,
    sequence,
    occurred_at: `2026-08-08T09:00:${String(sequence).padStart(2, '0')}.000Z`,
    payload: { to_state: toState, state_version: stateVersion },
  }
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function errorBody(code: string, category: string, retryable: boolean, retryAfter: number | null = null) {
  return { error: { code, category, message_key: code, correlation_id: `corr-${code}`, retryable, retry_after: retryAfter } }
}

test('creates a conversation from the Home composer and lands on its chat page', async ({ page }) => {
  let releaseConversation: () => void = () => undefined
  const conversationReady = new Promise<void>((resolve) => { releaseConversation = resolve })
  const csrfTokens: string[] = []
  await page.route('**/', async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-conversation-e2e">')
    await route.fulfill({ response, body })
  })
  await page.route('**/v1/providers/openrouter/models', async (route) => {
    await fulfillJson(route, { items: [{ provider: 'openrouter', model_id: 'anthropic/model-a', display_name: 'Model A', context_window: 128000, capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null }], refreshed_at: null })
  })
  await page.route('**/v1/conversations', async (route) => {
    if (route.request().method() === 'POST') {
      csrfTokens.push(route.request().headers()['x-csrf-token'] ?? '')
      await conversationReady
      await fulfillJson(route, { conversation_id: 'conv-e2e', title: 'Organize os dados', turn_id: 'turn-e2e', message_id: 'message-e2e', state: 'queued' }, 201)
      return
    }
    await route.fallback()
  })
  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'GET' && pathname === `/v1/executions/${EXEC_ID}`) {
      await fulfillJson(route, execution('RUNNING', 1))
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: 'stream-create', cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      await fulfillJson(route, { events: [], cursor: 'cursor-0' })
      return
    }
    await route.fallback()
  })

  await page.goto('/')
  await page.getByRole('textbox', { name: 'Mensagem' }).fill('Organize os dados')
  await page.getByRole('button', { name: 'Enviar mensagem' }).click()

  await expect(page.getByTestId('home-submit-state')).toHaveAttribute('data-submitting', 'true')
  await expect(page.getByRole('status', { name: '' }).filter({ hasText: 'Preparando sua execução' })).toBeVisible()
  releaseConversation()

  await expect(page).toHaveURL(/\/chats\/conv-e2e$/)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  expect(csrfTokens).toEqual(['csrf-conversation-e2e'])
})

test('does not post a conversation when the Home has no CSRF bootstrap', async ({ page }) => {
  let conversationPosts = 0
  await page.route('**/v1/providers/openrouter/models', async (route) => {
    await fulfillJson(route, { items: [{ provider: 'openrouter', model_id: 'anthropic/model-a', display_name: 'Model A', context_window: 128000, capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null }], refreshed_at: null })
  })
  await page.route('**/v1/conversations', async (route) => {
    // Only a POST creates a conversation. The Home also *lists* conversations on
    // mount, and counting that GET would make this assertion about the wrong
    // thing entirely.
    if (route.request().method() === 'POST') {
      conversationPosts += 1
      await route.abort('failed')
      return
    }
    await fulfillJson(route, { items: [] })
  })

  await page.goto('/')
  await page.getByRole('textbox', { name: 'Mensagem' }).fill('Organize os dados')
  const send = page.getByRole('button', { name: 'Enviar mensagem' })
  await expect(send).toBeDisabled()
  await expect(page.getByRole('status').filter({ hasText: 'Atualize a página antes de enviar' })).toBeVisible()
  await page.locator('form.composer').evaluate((form: HTMLFormElement) => form.requestSubmit())

  expect(conversationPosts).toBe(0)
})

test('pauses, resumes and cancels a running execution through successful 202 receipts reflected by the realtime stream', async ({ page }) => {
  let sequence = 0
  const queuedEvents: Array<ReturnType<typeof transitionEvent>> = []
  let version = 5
  let state: ExecState = 'RUNNING'

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === `/v1/executions/${EXEC_ID}`) {
      await fulfillJson(route, execution(state, version))
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: 'stream-ctl', cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      const events = queuedEvents.splice(0)
      await fulfillJson(route, { events, cursor: `cursor-${sequence}` })
      return
    }
    if (request.method() === 'POST' && pathname === `/v1/executions/${EXEC_ID}/control`) {
      const body = request.postDataJSON() as { action: string; expected_state_version: number }
      sequence += 1
      if (body.action === 'PAUSE') {
        version = 6
        state = 'PAUSED'
        queuedEvents.push(transitionEvent(`evt-${sequence}`, sequence, 'ExecutionPaused', 'PAUSED', version))
      } else if (body.action === 'RESUME') {
        version = 7
        state = 'QUEUED'
        queuedEvents.push(transitionEvent(`evt-${sequence}`, sequence, 'ExecutionResumed', 'QUEUED', version))
      } else if (body.action === 'CANCEL') {
        version = 8
        state = 'CANCELLED'
        queuedEvents.push(transitionEvent(`evt-${sequence}`, sequence, 'ExecutionCancelled', 'CANCELLED', version))
      }
      await fulfillJson(route, { outcome: 'accepted', execution_id: EXEC_ID, state_version: body.expected_state_version }, 202)
      return
    }
    await route.fallback()
  })

  await page.goto(`/execution/${EXEC_ID}`)
  await expect(page.getByText('Trabalhando', { exact: true })).toBeVisible()

  const pause = page.getByRole('button', { name: 'Pausar execução' })
  await expect(pause).toBeEnabled()
  await pause.click()
  await expect(page.getByText('Pausado', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retomar execução' })).toBeVisible()

  await page.getByRole('button', { name: 'Retomar execução' }).click()
  await expect(page.getByText('Preparando', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pausar execução' })).toBeVisible()

  await page.getByRole('button', { name: 'Cancelar execução' }).click()
  await expect(page.getByText('Cancelado', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pausar execução' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Cancelar execução' })).toBeDisabled()
})

test('recovers from a simulated version conflict on control by resyncing without getting stuck', async ({ page }) => {
  let controlCalls = 0
  let snapshotReads = 0

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === `/v1/executions/${EXEC_ID}`) {
      snapshotReads += 1
      await fulfillJson(route, execution('RUNNING', 3))
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: `stream-${snapshotReads}`, cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      await fulfillJson(route, { events: [], cursor: 'cursor-0' })
      return
    }
    if (request.method() === 'POST' && pathname === `/v1/executions/${EXEC_ID}/control`) {
      controlCalls += 1
      // Fake standing in for a documented-but-unimplemented CONFLICT domain exception
      // (see file header): no adapter in gateway.py raises this today.
      await fulfillJson(route, errorBody('version_conflict', 'CONFLICT', false), 409)
      return
    }
    await route.fallback()
  })

  await page.goto(`/execution/${EXEC_ID}`)
  const pause = page.getByRole('button', { name: 'Pausar execução' })
  await expect(pause).toBeEnabled()
  await pause.click()

  expect(controlCalls).toBe(1)
  await expect(page.getByText('O comando não foi confirmado. O estado atual foi preservado.')).toBeVisible()
  await expect(page.getByTestId('realtime-state')).toContainText(/Ressincronizando dados|Atualizações em tempo real ativas/)
  await expect.poll(() => snapshotReads).toBeGreaterThan(1)
  await expect(page.getByTestId('realtime-state')).toContainText('Atualizações em tempo real ativas')
  await expect(pause).toBeEnabled()
})

test('recovers from a simulated indeterminate control outcome by resyncing without getting stuck', async ({ page }) => {
  let controlCalls = 0
  let snapshotReads = 0

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === `/v1/executions/${EXEC_ID}`) {
      snapshotReads += 1
      await fulfillJson(route, execution('RUNNING', 3))
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: `stream-${snapshotReads}`, cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      await fulfillJson(route, { events: [], cursor: 'cursor-0' })
      return
    }
    if (request.method() === 'POST' && pathname === `/v1/executions/${EXEC_ID}/control`) {
      controlCalls += 1
      // Fake standing in for a documented-but-unimplemented INDETERMINATE domain
      // exception (see file header): no adapter in gateway.py raises this today.
      await fulfillJson(route, errorBody('control_indeterminate', 'INDETERMINATE', false), 500)
      return
    }
    await route.fallback()
  })

  await page.goto(`/execution/${EXEC_ID}`)
  const cancel = page.getByRole('button', { name: 'Cancelar execução' })
  await expect(cancel).toBeEnabled()
  await cancel.click()

  expect(controlCalls).toBe(1)
  await expect(page.getByText('O comando não foi confirmado. O estado atual foi preservado.')).toBeVisible()
  await expect.poll(() => snapshotReads).toBeGreaterThan(1)
  await expect(page.getByTestId('realtime-state')).toContainText('Atualizações em tempo real ativas')
  await expect(cancel).toBeEnabled()
})

test('shows a visible retry deadline on rate limit and stays operable for a following successful control', async ({ page }) => {
  let controlCalls = 0
  let version = 4
  let state: ExecState = 'RUNNING'
  const queuedEvents: Array<ReturnType<typeof transitionEvent>> = []

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname

    if (request.method() === 'GET' && pathname === `/v1/executions/${EXEC_ID}`) {
      await fulfillJson(route, execution(state, version))
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: 'stream-rl', cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      const events = queuedEvents.splice(0)
      await fulfillJson(route, { events, cursor: `cursor-${controlCalls}` })
      return
    }
    if (request.method() === 'POST' && pathname === `/v1/executions/${EXEC_ID}/control`) {
      controlCalls += 1
      if (controlCalls === 1) {
        await fulfillJson(route, errorBody('rate_limited', 'RATE_LIMITED', true, 20), 429)
        return
      }
      version = 5
      state = 'PAUSED'
      queuedEvents.push(transitionEvent('evt-rl-2', controlCalls, 'ExecutionPaused', 'PAUSED', version))
      await fulfillJson(route, { outcome: 'accepted', execution_id: EXEC_ID, state_version: version }, 202)
      return
    }
    await route.fallback()
  })

  await page.goto(`/execution/${EXEC_ID}`)
  const pause = page.getByRole('button', { name: 'Pausar execução' })
  await expect(pause).toBeEnabled()
  await pause.click()

  await expect(page.getByText('Tente novamente em 20s.')).toBeVisible()
  await expect(pause).toBeEnabled()

  await pause.click()
  await expect(page.getByText('Pausado', { exact: true })).toBeVisible()
  expect(controlCalls).toBe(2)
})
