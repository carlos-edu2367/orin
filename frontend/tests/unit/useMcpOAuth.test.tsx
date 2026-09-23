import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { MCP_OAUTH_POLL_MS, MCP_OAUTH_TIMEOUT_MS, useMcpOAuth } from '../../src/features/mcp/useMcpOAuth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    server_id: 's1', slug: 'auryly', display_name: 'Auryly', transport: 'http', command: null, args: [],
    url: 'https://auryly.com/mcp', secret_names: [], catalog_id: null, state: 'pending_approval', state_reason: '',
    protocol_version: '', tool_count: 0, auth_kind: 'oauth', tools: [], ...overrides,
  }
}

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, csrfToken: 'csrf-test', maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })
}

const started = { authorization_url: 'https://auth.example.com/authorize?state=x', expires_at: '2026-09-23T12:00:00+00:00' }

describe('useMcpOAuth', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('opens the authorization page and reports success when the server turns active', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json(started))
      .mockResolvedValueOnce(json(detail()))
      .mockResolvedValueOnce(json(detail({ state: 'active' })))
    const openWindow = vi.fn().mockReturnValue({} as Window)
    const onConnected = vi.fn()
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', onConnected, { openWindow }))

    await act(async () => { await result.current.start() })
    expect(openWindow).toHaveBeenCalledWith(started.authorization_url)
    expect(result.current.phase).toBe('waiting')

    await act(async () => { await vi.advanceTimersByTimeAsync(MCP_OAUTH_POLL_MS * 2) })
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(result.current.phase).toBe('idle')
  })

  it('offers the link when the browser blocks the new tab', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(json(started)).mockResolvedValue(json(detail()))
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', vi.fn(), { openWindow: () => null }))

    await act(async () => { await result.current.start() })

    expect(result.current.pendingUrl).toBe(started.authorization_url)
  })

  it('fails with the reason the server recorded', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json(started))
      .mockResolvedValueOnce(json(detail({ state_reason: 'Autorização recusada' })))
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', vi.fn(), { openWindow: () => ({} as Window) }))

    await act(async () => { await result.current.start() })
    await act(async () => { await vi.advanceTimersByTimeAsync(MCP_OAUTH_POLL_MS) })

    expect(result.current.phase).toBe('failed')
    expect(result.current.reason).toBe('Autorização recusada')
  })

  it('reads the reason when the start itself fails', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ error: { code: 'mcp_oauth_unsupported', category: 'MCP', message_key: 'mcp_oauth_unsupported', correlation_id: 'c', retryable: false, retry_after: null } }, 422))
      .mockResolvedValueOnce(json(detail({ state_reason: 'Não foi possível iniciar o login: sem metadados' })))
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', vi.fn(), { openWindow: () => ({} as Window) }))

    await act(async () => { await result.current.start() })

    expect(result.current.phase).toBe('failed')
    expect(result.current.reason).toContain('sem metadados')
  })

  it('gives up after the time limit and cancels on the server', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(json(started)).mockImplementation(async (input) =>
      String(input).includes('/oauth/cancel') ? new Response(null, { status: 204 }) : json(detail()))
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', vi.fn(), { openWindow: () => ({} as Window) }))

    await act(async () => { await result.current.start() })
    await act(async () => { await vi.advanceTimersByTimeAsync(MCP_OAUTH_TIMEOUT_MS) })

    expect(result.current.phase).toBe('failed')
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes('/oauth/cancel'))).toBe(true)
  })

  it('cancel stops waiting', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(json(started)).mockResolvedValue(new Response(null, { status: 204 }))
    const { result } = renderHook(() => useMcpOAuth(client(fetchImpl), 's1', vi.fn(), { openWindow: () => ({} as Window) }))

    await act(async () => { await result.current.start() })
    await act(async () => { await result.current.cancel() })

    expect(result.current.phase).toBe('idle')
  })
})
