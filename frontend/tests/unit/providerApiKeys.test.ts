import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { activateProviderApiKey, addProviderKey, listProviderKeys, reorderProviderKeys, testNvidiaConnection } from '../../src/api/providers'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('provider API key client', () => {
  it('parses a listed key, including a cooldown timestamp and exhausted status', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json([
      { id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldown_until: '2026-08-18T12:00:00Z' },
      { id: 2, label: 'nvidia trial', position: 1, status: 'exhausted', cooldown_until: null },
    ])))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const keys = await listProviderKeys(client, 'nvidia')

    expect(keys).toEqual([
      { id: 1, label: 'conta free 1', position: 0, status: 'cooldown', cooldownUntil: '2026-08-18T12:00:00Z' },
      { id: 2, label: 'nvidia trial', position: 1, status: 'exhausted', cooldownUntil: null },
    ])
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

  it('activates an exhausted key and resets its status to active', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ id: 7, label: 'NVIDIA Free 1', position: 0, status: 'active', cooldown_until: null })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-activate' })

    const activated = await activateProviderApiKey(client, 'nvidia', 7)

    expect(activated.status).toBe('active')
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/v1/providers/nvidia/keys/7:activate')
  })

  it('tests an NVIDIA NIM connection', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ reachable: true, authenticated: true, model_count: 14 })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1, createIdempotencyKey: () => 'intent-nvidia-test' })

    const result = await testNvidiaConnection(client, { apiKey: 'nvapi-123', baseUrl: 'https://integrate.api.nvidia.com/v1' })

    expect(result.reachable).toBe(true)
    expect(result.authenticated).toBe(true)
    expect(result.model_count).toBe(14)
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/v1/providers/nvidia/test')
  })
})
