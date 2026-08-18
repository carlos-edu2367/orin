import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { useProviderKeysState } from '../../src/features/providers/useProviderKeysState'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('useProviderKeysState', () => {
  it('loads the key list on mount', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([{ id: 1, label: null, position: 0, status: 'active', cooldown_until: null }])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))

    await waitFor(() => expect(result.current.keys).toHaveLength(1))
    expect(result.current.keys[0].id).toBe(1)
  })

  it('appends a newly added key and clears the pending input', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'POST') return Promise.resolve(json({ id: 2, label: 'conta paga', position: 1, status: 'active', cooldown_until: null }, 201))
      return Promise.resolve(json([]))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))
    await waitFor(() => expect(result.current.load.status).toBe('loaded'))

    await act(async () => { await result.current.add('sk-second-key', 'conta paga') })

    expect(result.current.keys.map((key) => key.id)).toEqual([2])
  })

  it('removes a key from local state after the server confirms', async () => {
    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
      return Promise.resolve(json([{ id: 1, label: null, position: 0, status: 'active', cooldown_until: null }]))
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const { result } = renderHook(() => useProviderKeysState(client, 'ollama', { status: 'ready', csrfToken: 'csrf' }))
    await waitFor(() => expect(result.current.keys).toHaveLength(1))

    await act(async () => { await result.current.remove(1) })

    expect(result.current.keys).toHaveLength(0)
  })
})
