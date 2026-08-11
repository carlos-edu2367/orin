import { afterEach, describe, expect, it, vi } from 'vitest'
import { readBrowserSessionBootstrap } from '../../src/api/browserSession'
import { ApiClient, createBrowserApiClient } from '../../src/api/client'
import { ApiError, isAuthenticationError, isCsrfAuthorizationError } from '../../src/api/errors'
import { createExecution } from '../../src/api/executions'
import { readEventStream } from '../../src/api/events'

afterEach(() => {
  document.head.querySelector('meta[name="csrf-token"]')?.remove()
  document.head.querySelector('meta[name="agentos-auth-mode"]')?.remove()
})

describe('browser session bootstrap', () => {
  it('reads the trimmed CSRF token from the host meta element', () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    meta.content = ' csrf-test '
    document.head.append(meta)

    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'ready', csrfToken: 'csrf-test' })
  })

  it('reports a missing CSRF bootstrap after the host meta element is removed', () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    document.head.append(meta)
    meta.remove()

    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'missing_csrf' })
  })

  it('rejects CSRF bootstrap values longer than 255 characters', () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    meta.content = 'x'.repeat(256)
    document.head.append(meta)

    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'missing_csrf' })
  })

  it('accepts the explicit loopback mode without a browser CSRF token', () => {
    const mode = document.createElement('meta')
    mode.name = 'agentos-auth-mode'
    mode.content = 'loopback'
    document.head.append(mode)

    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'loopback' })
  })

  it('does not treat an unrecognized auth mode as a local trust bootstrap', () => {
    const mode = document.createElement('meta')
    mode.name = 'agentos-auth-mode'
    mode.content = 'local'
    document.head.append(mode)

    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'missing_csrf' })
  })

  it('adds the bootstrapped CSRF token to browser mutations', async () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    meta.content = 'csrf-test'
    document.head.append(meta)
    const fetchImpl = successfulFetch()
    const client = createBrowserApiClient({ fetchImpl })

    await client.request({ path: '/v1/mutate', method: 'POST', parse: value => value })

    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).get('X-CSRF-Token')).toBe('csrf-test')
  })

  it('does not add a CSRF header to browser mutations without a usable bootstrap', async () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    meta.content = ''
    document.head.append(meta)
    const fetchImpl = successfulFetch()
    const client = createBrowserApiClient({ fetchImpl })

    await client.request({ path: '/v1/mutate', method: 'POST', parse: value => value })

    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('keeps an injected Bearer client independent from the browser bootstrap', async () => {
    const meta = document.createElement('meta')
    meta.name = 'csrf-token'
    meta.content = 'csrf-test'
    document.head.append(meta)
    const fetchImpl = successfulFetch()
    const client = new ApiClient({ bearerToken: 'automation-token', fetchImpl, maxAttempts: 1 })

    await client.request({ path: '/v1/mutate', method: 'POST', parse: value => value })

    const headers = new Headers(fetchImpl.mock.calls[0][1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer automation-token')
    expect(headers.has('X-CSRF-Token')).toBe(false)
  })
})

describe('authentication error classification', () => {
  it('classifies sanitized authentication and CSRF authorization errors', () => {
    expect(isAuthenticationError(new ApiError({ status: 401, category: ' authentication ', code: 'authentication-required' }))).toBe(true)
    expect(isCsrfAuthorizationError(new ApiError({ status: 403, category: ' authorization ', code: 'access-denied' }))).toBe(true)
  })

  it('does not classify unrelated errors as authentication or CSRF authorization errors', () => {
    const validationError = new ApiError({ status: 422, category: 'VALIDATION', code: 'invalid_request' })

    expect(isAuthenticationError(validationError)).toBe(false)
    expect(isCsrfAuthorizationError(validationError)).toBe(false)
  })
})

describe('ApiClient mutations', () => {
  it('reuses one Idempotency-Key when a network retry belongs to the same intention', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError('network unavailable'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        outcome: 'accepted',
        execution_id: 'exec-created',
        state_version: 1,
      }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    const client = new ApiClient({
      fetchImpl,
      maxAttempts: 2,
      retryDelayMs: 0,
      createIdempotencyKey: () => 'intent-key-1',
    })

    const receipt = await createExecution(client, {
      agent_id: 'agent-orbit',
      task_ref: 'task-known',
      workspace_id: null,
      limits: {},
      expected_agent_version: null,
    })

    expect(receipt).toEqual({ outcome: 'accepted', execution_id: 'exec-created', state_version: 1 })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    const firstHeaders = new Headers(fetchImpl.mock.calls[0][1]?.headers)
    const secondHeaders = new Headers(fetchImpl.mock.calls[1][1]?.headers)
    expect(firstHeaders.get('Idempotency-Key')).toBe('intent-key-1')
    expect(secondHeaders.get('Idempotency-Key')).toBe('intent-key-1')
  })
})

describe('event replay transport', () => {
  it('sends an opaque cursor in the POST body and never places it in the URL', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      events: [],
      cursor: 'opaque-next',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    await readEventStream(client, 'stream-1', 'opaque-current')

    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/v1/events/streams/stream-1/read')
    expect(String(url)).not.toContain('opaque-current')
    expect(JSON.parse(String(init?.body))).toEqual({ cursor: 'opaque-current', maximum_events: 100 })
  })
})

function successfulFetch() {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }))
}
