import { expect, test, type Route } from '@playwright/test'

// This test-only fake mirrors the public gateway contracts used by the input
// composer: a WAITING_USER ExecutionView, the 202 `_receipt` from
// POST /v1/executions/{id}/input, and the event-stream open/read endpoints.
// `input_ref` is deliberately opaque: the gateway accepts that reference plus
// `expected_state_version`, and does not authorize raw response text.

const EXECUTION_ID = 'exec-waiting-user'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

test('submits an opaque input reference from WAITING_USER through the documented 202 receipt', async ({ page }) => {
  let submitted: unknown = null

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'GET' && pathname === `/v1/executions/${EXECUTION_ID}`) {
      await fulfillJson(route, {
        execution_id: EXECUTION_ID,
        agent_id: 'agent-orbit',
        state: 'WAITING_USER',
        state_version: 4,
        parent_execution_id: null,
        created_at: '2026-08-10T12:00:00.000Z',
        updated_at: '2026-08-10T12:00:01.000Z',
        finished_at: null,
        result: null,
        failure: null,
      })
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/events/streams') {
      await fulfillJson(route, { stream_id: 'stream-input', cursor: 'cursor-0', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      return
    }
    if (request.method() === 'POST' && pathname.endsWith('/read')) {
      await fulfillJson(route, { events: [], cursor: 'cursor-0' })
      return
    }
    if (request.method() === 'POST' && pathname === `/v1/executions/${EXECUTION_ID}/input`) {
      submitted = request.postDataJSON()
      await fulfillJson(route, { outcome: 'accepted', execution_id: EXECUTION_ID, state_version: 4 }, 202)
      return
    }
    await route.fallback()
  })

  await page.goto(`/execution/${EXECUTION_ID}`)
  await page.getByRole('button', { name: 'Fornecer referência de entrada' }).click()
  await page.getByLabel('Referência de entrada').fill('input:verified-user-decision')
  await page.getByRole('button', { name: 'Enviar entrada' }).click()

  await expect.poll(() => submitted).toEqual({ input_ref: 'input:verified-user-decision', expected_state_version: 4 })
  await expect(page.getByText('Aguardando você', { exact: true })).toBeVisible()
})
