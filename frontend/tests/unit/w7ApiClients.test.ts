import { describe, expect, it } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ApiError } from '../../src/api/errors'
import {
  changePermissionRule, deleteAgent, getEffectiveSettings, getKeybindingsFiles, parseAgentsReport, parseHooksReport,
  saveAgent, saveHooks,
} from '../../src/api/settings'
import { enqueueMessage, getContext, listTasks, rewindCode, runStatusLine, stopTask } from '../../src/api/sessionTools'

type Call = { url: string; method: string; body: unknown; headers: Headers }

function client(responses: Array<{ status?: number; body: unknown }>) {
  const calls: Call[] = []
  const fetchImpl = (async (url: string, init: NonNullable<Parameters<typeof fetch>[1]>) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined, headers: new Headers(init.headers) })
    const next = responses.shift() ?? { body: {} }
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return { api: new ApiClient({ fetchImpl, maxAttempts: 1, csrfToken: 'csrf' }), calls }
}

describe('W7 settings client', () => {
  it('reads effective settings with per-rule provenance and keeps only known layers', async () => {
    const { api, calls } = client([{ body: {
      project_root: 'C:/p', trusted: true, values: { model: 'x' }, provenance: { model: 'user', bad: 3 },
      layers: [{ name: 'user', file: 'C:/u.json', data: {} }, { name: 'evil', data: {} }],
      files: { user: [{ path: 'C:/u.json', exists: true }] }, writable: { user: 'C:/u.json', project: null, local: 'C:/l.json' },
      permissions: { allow: [{ rule: 'Read', layer: 'managed', file: 'm.json', duplicate: false }, { rule: '', layer: 'user' }] },
      errors: [{ file: 'x', message: 'bad' }], untrusted_ignored: ['hooks'],
    } }])
    const settings = await getEffectiveSettings(api, 'chat_1')
    expect(calls[0].url).toBe('/v1/settings/effective?conversation_id=chat_1')
    expect(settings.layers.map((layer) => layer.name)).toEqual(['user'])
    expect(settings.provenance).toEqual({ model: 'user' })
    expect(settings.permissions.allow).toEqual([{ rule: 'Read', layer: 'managed', file: 'm.json', duplicate: false }])
    expect(settings.permissions.deny).toEqual([])
    expect(settings.writable).toEqual({ user: 'C:/u.json', project: null, local: 'C:/l.json' })
  })

  it('sends a rule change and surfaces the server problems on a 422', async () => {
    const { api, calls } = client([
      { body: { permissions: { deny: [{ rule: 'WebFetch', layer: 'user', file: null, duplicate: false }] } } },
      { status: 422, body: { error: { code: 'invalid_rule', details: { problems: ["'Bash(' is not a permission rule"] } } } },
    ])
    const rules = await changePermissionRule(api, { layer: 'user', kind: 'deny', rule: 'WebFetch', op: 'add' })
    expect(rules.deny[0].rule).toBe('WebFetch')
    expect(calls[0]).toMatchObject({ method: 'PUT', body: { layer: 'user', kind: 'deny', rule: 'WebFetch', op: 'add' } })
    expect(calls[0].headers.get('X-CSRF-Token')).toBe('csrf')
    const failure = await changePermissionRule(api, { layer: 'user', kind: 'allow', rule: 'Bash(', op: 'add' }).catch((error) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).problems).toEqual(["'Bash(' is not a permission rule"])
  })

  it('round-trips hooks, agents and keybindings shapes', async () => {
    const { api, calls } = client([
      { body: { disabled: false, effective: [{ event: 'PreToolUse', matcher: 'Bash', type: 'command', source: 'user', label: 'echo', timeout: 60 }], editable: { user: { file: 'u', hooks: { PreToolUse: [] } }, evil: {} } } },
      { body: { agents: [{ name: 'reviewer', description: 'd', origin: 'user', editable: true, prompt: 'p', tools: ['Read'], disallowed_tools: [], max_turns: 5 }] } },
      { body: { agents: [] } },
      { body: { orin: { path: 'k.json', text: '[]', error: null }, claude: { path: 'c.json', text: null, error: 'unreadable: OSError' } } },
    ])
    const hooks = await saveHooks(api, 'user', { PreToolUse: [] })
    expect(Object.keys(hooks.editable)).toEqual(['user'])
    expect(hooks.effective[0]).toMatchObject({ event: 'PreToolUse', matcher: 'Bash', timeout: 60 })
    const agents = await saveAgent(api, 'reviewer', { scope: 'user', description: 'd', prompt: 'p', tools: ['Read'], disallowedTools: [], model: null, permissionMode: null, maxTurns: 5, effort: null }, 'chat_1')
    expect(calls[1].body).toEqual({ scope: 'user', description: 'd', prompt: 'p', tools: ['Read'], disallowed_tools: [], max_turns: 5, conversation_id: 'chat_1' })
    expect(agents.agents[0]).toMatchObject({ name: 'reviewer', tools: ['Read'], maxTurns: 5, model: null })
    await deleteAgent(api, 'reviewer', 'user')
    expect(calls[2]).toMatchObject({ method: 'DELETE', url: '/v1/settings/agents/reviewer?scope=user' })
    const files = await getKeybindingsFiles(api)
    expect(files.claude).toEqual({ path: 'c.json', text: null, error: 'unreadable: OSError' })
  })

  it('parsers drop malformed entries', () => {
    expect(parseHooksReport({ effective: 'nope', editable: [] })).toEqual({ disabled: false, effective: [], editable: {}, errors: [] })
    expect(parseAgentsReport({ agents: [{ description: 'no name' }, 3] }).agents).toEqual([])
  })
})

describe('W7 session tools client', () => {
  it('queues, reads the context, stops a task and runs the status line', async () => {
    const { api, calls } = client([
      { body: { queued: true, queue_id: 'q1' } },
      { body: { available: true, limit_tokens: 1000, used_tokens: 250, percentage: 25, categories: [{ key: 'tools', tokens: 200 }, { key: 'free', tokens: -5 }] } },
      { body: { tasks: [{ task_id: 'shell:1', kind: 'shell', pid: 1, command: 'npm run dev', state: 'running', stoppable: true }, { task_id: 'x', kind: 'agent' }] } },
      { body: { stopped: true, tasks: [] } },
      { body: { enabled: true, lines: ['a', 'b', 'c', 'd', 'e', 'f'], error: null } },
    ])
    expect(await enqueueMessage(api, 'chat_1', 'next')).toEqual({ queued: true, queueId: 'q1', turnId: null })
    expect(calls[0].headers.get('Idempotency-Key')).toBeTruthy()
    const context = await getContext(api, 'chat_1')
    expect(context.categories).toEqual([{ key: 'tools', tokens: 200 }, { key: 'free', tokens: 0 }])
    const tasks = await listTasks(api, 'chat_1')
    expect(tasks).toHaveLength(1)
    expect(tasks[0]).toMatchObject({ taskId: 'shell:1', state: 'running', stoppable: true })
    expect((await stopTask(api, 'chat_1', 'shell:1')).stopped).toBe(true)
    expect(calls[3].url).toBe('/v1/conversations/chat_1/tasks/shell%3A1/stop')
    expect((await runStatusLine(api, 'chat_1')).lines).toHaveLength(5)
  })

  it('reads a blocked code rewind as a 409 with its files', async () => {
    const { api } = client([{ status: 409, body: { error: { code: 'rewind_blocked', category: 'CONFLICT', details: { files: [{ path: 'big.bin' }] } } } }])
    const failure = await rewindCode(api, 'chat_1', 'm1').catch((error) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('rewind_blocked')
  })
})
