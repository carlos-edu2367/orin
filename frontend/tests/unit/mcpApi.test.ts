import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ApiError } from '../../src/api/errors'
import {
  approveMcpServer,
  createMcpServer,
  deleteMcpServer,
  getMcpServer,
  listMcpCatalog,
  listMcpServers,
  setMcpServerEnabled,
} from '../../src/api/mcp'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

function server(overrides: Record<string, unknown> = {}) {
  return {
    server_id: 's1', slug: 'github', display_name: 'GitHub', transport: 'stdio', command: 'npx',
    args: ['-y', 'server-github'], url: null, secret_names: ['GITHUB_PERSONAL_ACCESS_TOKEN'], catalog_id: 'github',
    state: 'pending_approval', state_reason: '', protocol_version: '', tool_count: 0, ...overrides,
  }
}

function detail(overrides: Record<string, unknown> = {}) {
  return { ...server(overrides), tools: [] }
}

describe('MCP API client', () => {
  it('lists the curated catalog', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json({
      entries: [{
        catalog_id: 'github', display_name: 'GitHub', summary: 'Issues and pull requests.', transport: 'stdio',
        setup_instructions: 'Create a token.', arguments: [],
        secrets: [{ name: 'GITHUB_PERSONAL_ACCESS_TOKEN', label: 'Personal access token', how_to_obtain: 'github.com settings' }],
      }],
    }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const entries = await listMcpCatalog(client, 'github')

    expect(entries).toHaveLength(1)
    expect(entries[0].catalog_id).toBe('github')
    expect(entries[0].secrets[0].how_to_obtain).toContain('github.com')
    expect(String(fetchImpl.mock.calls[0][0])).toBe('/v1/mcp/catalog?query=github')
  })

  it('lists the configured servers', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json([server({ state: 'active', tool_count: 3 })]))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const servers = await listMcpServers(client)

    expect(servers).toEqual([server({ state: 'active', tool_count: 3 })])
  })

  it('creates a pending server and posts only the given fields', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(detail(), 201))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const created = await createMcpServer(client, { display_name: 'GitHub', catalog_id: 'github' })

    expect(created.state).toBe('pending_approval')
    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/v1/mcp/servers')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ display_name: 'GitHub', catalog_id: 'github' })
  })

  it('approves a server, sending secret values in the body and never logging them back', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(detail({ state: 'active', tool_count: 3 })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const approved = await approveMcpServer(client, 's1', { GITHUB_PERSONAL_ACCESS_TOKEN: 'ghp_secret' })

    expect(approved.state).toBe('active')
    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/v1/mcp/servers/s1/approve')
    expect(JSON.parse(String(init?.body))).toEqual({ secrets: { GITHUB_PERSONAL_ACCESS_TOKEN: 'ghp_secret' } })
    expect(JSON.stringify(approved)).not.toContain('ghp_secret')
  })

  it('parses the cached tool list with its per-tool enabled state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json({
      ...detail({ state: 'active' }),
      tools: [{ name: 'search', description: 'Search pages', enabled: true }, { name: 'archive', description: '', enabled: false }],
    }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const result = await getMcpServer(client, 's1')

    expect(result.tools).toEqual([
      { name: 'search', description: 'Search pages', enabled: true },
      { name: 'archive', description: '', enabled: false },
    ])
  })

  it('propagates a 502 from a failed approval as an ApiError', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(
      { error: { code: 'mcp_connection_failed', category: 'MCP', message_key: 'mcp_connection_failed', correlation_id: 'corr_1', retryable: true, retry_after: null } },
      502,
    ))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    await expect(approveMcpServer(client, 'bad', { TOKEN: 'x' })).rejects.toBeInstanceOf(ApiError)
  })

  it('toggles a server enabled state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(detail({ state: 'disabled' })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    const updated = await setMcpServerEnabled(client, 's1', false)

    expect(updated.state).toBe('disabled')
    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/v1/mcp/servers/s1/enabled')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: false })
  })

  it('deletes a server', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    await deleteMcpServer(client, 's1')

    const [url, init] = fetchImpl.mock.calls[0]
    expect(String(url)).toBe('/v1/mcp/servers/s1')
    expect(init?.method).toBe('DELETE')
  })
})
