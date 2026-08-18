import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { addProviderKey, listProviderKeys, reorderProviderKeys } from '../../src/api/providers'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('provider API key client', () => {
  it('parses a listed key, including a cooldown timestamp', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([
      { id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldown_until: '2026-08-18T12:00:00Z' },
    ])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const keys = await listProviderKeys(client, 'ollama')

    expect(keys).toEqual([{ id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldownUntil: '2026-08-18T12:00:00Z' }])
  })

  it('sends the api key and label when adding a key', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ id: 2, label: null, position: 1, status: 'active', cooldown_until: null }, 201)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    await addProviderKey(client, 'ollama', { apiKey: 'sk-second' })

    const [, init] = fetchImpl.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({ api_key: 'sk-second' })
  })

  it('reorders and returns the new ordering', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([
      { id: 2, label: null, position: 0, status: 'active', cooldown_until: null },
      { id: 1, label: null, position: 1, status: 'active', cooldown_until: null },
    ])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-test' })

    const reordered = await reorderProviderKeys(client, 'ollama', [2, 1])

    expect(reordered.map((key) => key.id)).toEqual([2, 1])
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/keys:reorder')
  })
})
