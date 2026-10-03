import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { readBrowserSessionBootstrap } from '../../src/api/browserSession'
import { getSessionCsrf, notifyUnauthenticated, onUnauthenticated, setSessionCsrf } from '../../src/api/session'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => setSessionCsrf(undefined))

describe('session-aware client', () => {
  it('reads the CSRF token at request time', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({})))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, csrfToken: getSessionCsrf })
    setSessionCsrf('first')
    await client.request({ path: '/v1/a', method: 'POST', body: {}, parse: (v) => v })
    setSessionCsrf('second')
    await client.request({ path: '/v1/a', method: 'POST', body: {}, parse: (v) => v })
    const tokens = fetchImpl.mock.calls.map(([, init]) => new Headers(init?.headers).get('X-CSRF-Token'))
    expect(tokens).toEqual(['first', 'second'])
  })

  it('reports a 401 before throwing', async () => {
    const onUnauthenticated = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ error: { code: 'authentication_required', category: 'AUTHENTICATION', retryable: false } }, 401)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, onUnauthenticated })
    await expect(client.request({ path: '/v1/a', parse: (v) => v })).rejects.toMatchObject({ status: 401 })
    expect(onUnauthenticated).toHaveBeenCalledOnce()
  })

  it('broadcasts unauthenticated events to subscribers', () => {
    const listener = vi.fn()
    const unsubscribe = onUnauthenticated(listener)
    notifyUnauthenticated()
    unsubscribe()
    notifyUnauthenticated()
    expect(listener).toHaveBeenCalledOnce()
  })

  it('recognises the session auth mode', () => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="session">'
    expect(readBrowserSessionBootstrap(document)).toEqual({ status: 'session' })
    document.head.innerHTML = ''
  })
})
