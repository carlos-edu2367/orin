import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { approvePlugin, fetchPluginLibrary, inferMcpLaunch, inspectPlugin, listPluginCommands, listPlugins, removePlugin } from '../../src/api/plugins'

function client(fetchImpl: typeof fetch) { return new ApiClient({ fetchImpl, maxAttempts: 1 }) }
function response(body: unknown, status = 200) { return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }) }

describe('plugins api', () => {
  it('parses list and inspection responses', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([{ plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'', author:'', homepage:null, state:'active', warnings:[], contribution_count:1 }]))
    expect((await listPlugins(client(fetchImpl)))[0].plugin_id).toBe('demo')
    fetchImpl.mockResolvedValue(response({ plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'', author:'', homepage:null, state:'pending_approval', warnings:[], contribution_count:1, package_digest:'a', skills:[], mcp_servers:[], agents:[] }))
    expect((await inspectPlugin(client(fetchImpl), 'demo')).state).toBe('pending_approval')
  })
  it('uses approval and delete routes', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'', author:'', homepage:null, state:'active', warnings:[], contribution_count:1 }))
    await approvePlugin(client(fetchImpl), 'demo')
    fetchImpl.mockResolvedValue(response(undefined, 204))
    await removePlugin(client(fetchImpl), 'demo')
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })
  it('parses the plugin library response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [{ name: 'superpowers', description: 'd', source_url: 'https://github.com/obra/superpowers.git', origin: 'registry', installable_kind: 'plugin' }], web_search_available: false }))
    const library = await fetchPluginLibrary(client(fetchImpl))
    expect(library.entries[0].origin).toBe('registry')
    expect(library.web_search_available).toBe(false)
  })
  it('forwards the refresh flag as a query parameter', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => response({ entries: [], web_search_available: false }))
    await fetchPluginLibrary(client(fetchImpl), true)
    expect(String(fetchImpl.mock.calls[0][0])).toContain('refresh=true')
    await fetchPluginLibrary(client(fetchImpl))
    expect(String(fetchImpl.mock.calls[1][0])).not.toContain('refresh')
  })
  it('forwards a trimmed query as a query parameter', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => response({ entries: [], web_search_available: false }))
    await fetchPluginLibrary(client(fetchImpl), false, '  obsidian  ')
    expect(String(fetchImpl.mock.calls[0][0])).toContain('q=obsidian')
    await fetchPluginLibrary(client(fetchImpl), false, '   ')
    expect(String(fetchImpl.mock.calls[1][0])).not.toContain('q=')
  })
  it('rejects an invalid installable_kind on a library entry', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
      entries: [{ name: 'demo', description: 'd', source_url: 'https://github.com/acme/demo.git', origin: 'web', installable_kind: 'bogus' }],
      web_search_available: true,
    }))
    await expect(fetchPluginLibrary(client(fetchImpl))).rejects.toThrow()
  })
  it('parses an inferred MCP launch guess', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toContain('/v1/plugins/library/infer-mcp')
      expect(JSON.parse(String(init?.body))).toEqual({ source_url: 'https://github.com/acme/demo.git' })
      return response({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
    })
    const guess = await inferMcpLaunch(client(fetchImpl), 'https://github.com/acme/demo.git')
    expect(guess).toEqual({ display_name: 'demo', transport: 'stdio', command: 'npx', args: ['-y', 'demo'], url: null, secret_names: [], confidence: 'structured' })
  })
  it('parses a launch guess with no signal found', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ display_name: 'demo', transport: null, command: null, args: [], url: null, secret_names: [], confidence: 'none' }))
    const guess = await inferMcpLaunch(client(fetchImpl), 'https://github.com/acme/demo.git')
    expect(guess.transport).toBeNull()
    expect(guess.confidence).toBe('none')
  })
  it('lists the active plugin commands', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      { command_id: 'demo:daily', slug: 'daily', plugin_id: 'demo', description: 'd', argument_hint: '', qualified: false },
    ]))
    const commands = await listPluginCommands(client(fetchImpl))
    expect(commands).toEqual([
      { command_id: 'demo:daily', slug: 'daily', plugin_id: 'demo', description: 'd', argument_hint: '', qualified: false },
    ])
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/v1/plugins/commands')
  })
  it('rejects a malformed command payload', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([{ slug: 42 }]))
    await expect(listPluginCommands(client(fetchImpl))).rejects.toThrow()
  })
})
