import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { approvePlugin, fetchPluginLibrary, inspectPlugin, listPlugins, removePlugin } from '../../src/api/plugins'

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
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [{ name: 'superpowers', description: 'd', source_url: 'https://github.com/obra/superpowers.git', origin: 'registry' }], web_search_available: false }))
    const library = await fetchPluginLibrary(client(fetchImpl))
    expect(library.entries[0].origin).toBe('registry')
    expect(library.web_search_available).toBe(false)
  })
})
