/** Fixtures for the W7 Batch F browser tests: MCP servers per scope, a rewind blocked by size, a turn that changed a file. */
import type { ExtraRoute } from './w7Chat'

const GITHUB = { name: 'github', scope: 'project', source: 'C:/Projetos/pagamentos/.mcp.json', transport: 'stdio', shown: 'npx -y @modelcontextprotocol/server-github --token [REDACTED]' }
const DOCS = { name: 'docs', scope: 'user', source: 'C:/Users/p/.orin/mcp.json', transport: 'http', shown: 'https://docs.example.com', state: 'on' }

/** The settings route for MCP servers: github waits for approval until it is approved. */
export function mcpScopesRoutes(sent: Array<{ method: string; body: unknown }> = []): ExtraRoute {
  let approved = false
  return ({ method, path, body }) => {
    if (path === '/v1/settings/mcp/approval' && method === 'PUT') { sent.push({ method, body }); approved = true }
    if (!path.startsWith('/v1/settings/mcp')) return undefined
    return { body: {
      folder: 'C:/Projetos/pagamentos', trusted: true, managed_only: false, awaiting_trust: false, errors: [],
      servers: approved ? [{ ...GITHUB, state: 'on' }, DOCS] : [DOCS], pending: approved ? [] : [{ ...GITHUB, state: 'pending' }],
      choices: approved ? { enabledMcpjsonServers: ['github'] } : {},
    } }
  }
}

export const BIG_FILE = 'C:/Projetos/pagamentos/data/exports.csv'

/** A code rewind blocked only by a file too large to back up, until the person names it. */
export function blockedBySizeRoutes(sent: Array<{ method: string; path: string; body: unknown }> = []): ExtraRoute {
  return ({ method, path, body }) => {
    if (path.endsWith('/file-history')) {
      return { body: { items: [{ message_id: 'm-user-1', turn_id: 't1', kind: 'message', created_at: null, rewound_at: null, files: ['data/exports.csv', 'billing/charge.py'] }] } }
    }
    if (method === 'POST' && path.endsWith('/messages/m-user-1/rewind')) {
      sent.push({ method, path, body })
      const forced = (body as { force_unbacked?: string[] } | undefined)?.force_unbacked ?? []
      if (!forced.includes(BIG_FILE)) {
        return { status: 409, body: { error: { code: 'rewind_blocked', category: 'CONFLICT', details: { files: [{ path: BIG_FILE, reason: 'its current content is too large to keep an undo copy' }] } } } }
      }
      return { body: { message_id: 'm-user-1', restored: ['C:/Projetos/pagamentos/billing/charge.py'], deleted: [], unchanged: [], skipped: [], blocked: false, undo_id: 'u1', unbacked: [BIG_FILE] } }
    }
    return undefined
  }
}

/** The existing turn t1 also edited a file, so its timeline offers to go back to it. */
export const EDITED_TURN = {
  activities: [
    { event_id: 'f1', cursor: '1', turn_id: 't1', agent_id: 'agent:main', event_type: 'tool.finished', summary: 'Editou billing/charge.py', occurred_at: '2026-10-20T12:00:04.000Z',
      payload: { tool_name: 'write_file', tool_kind: 'filesystem', status: 'succeeded', invocation_id: 'call-1', path: 'billing/charge.py' } },
    { event_id: 'f2', cursor: '2', turn_id: 't1', agent_id: 'agent:main', event_type: 'turn.completed', summary: 'Concluído', occurred_at: '2026-10-20T12:00:09.000Z', payload: { state: 'completed' } },
  ],
  activity_cursor: '2',
}
