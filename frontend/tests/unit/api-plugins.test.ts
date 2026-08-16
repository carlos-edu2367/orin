import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { fetchPluginLibrary, inferMcpLaunch } from '../../src/api/plugins'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('plugins api parsing', () => {
  it('parses installable_kind on library entries', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
      entries: [{ name: 'demo', description: 'd', source_url: 'https://github.com/acme/demo.git', origin: 'web', installable_kind: 'mcp_raw' }],
      web_search_available: true,
    }))
    const result = await fetchPluginLibrary(new ApiClient({ fetchImpl, maxAttempts: 1 }))
    expect(result.entries[0].installable_kind).toBe('mcp_raw')
  })

  it('rejects an invalid installable_kind', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
      entries: [{ name: 'demo', description: 'd', source_url: 'https://github.com/acme/demo.git', origin: 'web', installable_kind: 'bogus' }],
      web_search_available: true,
    }))
    await expect(fetchPluginLibrary(new ApiClient({ fetchImpl, maxAttempts: 1 }))).rejects.toThrow()
  })

  it('parses an inferred MCP launch guess', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toContain('/v1/plugins/library/infer-mcp')
      expect(JSON.parse(String(init?.body))).toEqual({ source_url: 'https://github.com/acme/demo.git' })
      return response({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
    })
    const guess = await inferMcpLaunch(new ApiClient({ fetchImpl, maxAttempts: 1 }), 'https://github.com/acme/demo.git')
    expect(guess).toEqual({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
  })

  it('parses a launch guess with no signal found', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ display_name: 'demo', transport: null, command: null, args: [], url: null, secret_names: [], confidence: 'none' }))
    const guess = await inferMcpLaunch(new ApiClient({ fetchImpl, maxAttempts: 1 }), 'https://github.com/acme/demo.git')
    expect(guess.transport).toBeNull()
    expect(guess.confidence).toBe('none')
  })
})
