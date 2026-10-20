import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { parseConversationActivityEvent } from '../../src/api/conversations'
import { chatCommandHandlers, type ChatCommandDeps } from '../../src/features/commands/useChatCommands'
import { BUILTIN_COMMANDS } from '../../src/features/commands/builtins'
import { slashItems } from '../../src/features/conversations/CommandPicker'
import { parseAnsi } from '../../src/features/conversations/ansi'
import { ContextGrid, contextCells } from '../../src/features/conversations/ContextGrid'
import { StatusLineBar } from '../../src/features/conversations/StatusLineBar'
import { TasksPanel, safeUrl } from '../../src/features/conversations/TasksPanel'
import { buildTranscript, TranscriptView } from '../../src/features/conversations/TranscriptView'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'
import { notifySystem, requestSystemNotifications } from '../../src/features/notifications/systemNotify'

function fakeClient(responses: Record<string, unknown | ((method: string) => unknown)>) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  const fetchImpl = (async (url: string, init: NonNullable<Parameters<typeof fetch>[1]>) => {
    const method = init.method ?? 'GET'
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(responses).find((item) => url.includes(item))
    const value = key ? responses[key] : {}
    const body = typeof value === 'function' ? (value as (method: string) => unknown)(method) : value
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return { client: new ApiClient({ fetchImpl, maxAttempts: 1 }), calls }
}

// The status line waits 300 ms before it runs, and the panel loads two routes: room for a busy machine.
const SLOW = { timeout: 3000 }

const event = (overrides: Partial<ConversationActivityEvent>): ConversationActivityEvent => ({
  eventId: overrides.eventId ?? `e${Math.random()}`, cursor: '1', type: 'turn.started', kind: 'lifecycle', state: 'completed',
  agentId: 'agent:main', summary: '', ...overrides,
})

describe('ANSI in the status line', () => {
  it('reads colours, 256 and true colour, styles and resets', () => {
    expect(parseAnsi('\x1b[1;32mok\x1b[0m main \x1b[38;5;208mlaranja\x1b[38;2;10;20;30mrgb\x1b[39m fim')).toEqual([
      { text: 'ok', bold: true, color: 2 },
      { text: ' main ' },
      { text: 'laranja', color: 'rgb(255, 135, 0)' },
      { text: 'rgb', color: 'rgb(10, 20, 30)' },
      { text: ' fim' },
    ])
    expect(parseAnsi('\x1b[94mazul\x1b[22;2mx')).toEqual([{ text: 'azul', color: 12 }, { text: 'x', color: 12, dim: true }])
  })

  it('drops every other escape and control character, and never makes markup', () => {
    expect(parseAnsi('\x1b]8;;https://evil\x07link\x1b]8;;\x07\x1b[2K\x1b[1Atexto\x07\x00')).toEqual([{ text: 'linktexto' }])
    render(<div>{parseAnsi('<img src=x onerror=alert(1)>').map((segment, index) => <span key={index}>{segment.text}</span>)}</div>)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
  })
})

describe('the /context grid', () => {
  const report = (categories: Array<[string, number]>, limit = 1000) => ({
    available: true, limitTokens: limit, usedTokens: categories.filter(([key]) => key !== 'free').reduce((sum, [, tokens]) => sum + tokens, 0),
    percentage: 0, compactionCount: 0, categories: categories.map(([key, tokens]) => ({ key, tokens })), measuredAt: null,
  })

  it('fills the grid in proportion and always adds up to 100 cells', () => {
    const cells = contextCells(report([['system_prompt', 123], ['tools', 77], ['messages', 305], ['free', 495]]))
    expect(cells.reduce((sum, item) => sum + item.cells, 0)).toBe(100)
    expect(cells).toEqual([{ key: 'system_prompt', cells: 12 }, { key: 'tools', cells: 8 }, { key: 'messages', cells: 31 }, { key: 'free', cells: 49 }])
  })

  it('keeps to the grid when a measurement is over the limit, and when there is no limit', () => {
    const over = contextCells(report([['messages', 1500], ['tools', 500]]))
    expect(over.reduce((sum, item) => sum + item.cells, 0)).toBe(100)
    expect(over.find((item) => item.key === 'free')).toBeUndefined()
    expect(contextCells(report([], 0))).toEqual([{ key: 'free', cells: 100 }])
  })

  it('shows the cells and a legend with each category', () => {
    render(<ContextGrid report={{ ...report([['messages', 250], ['free', 750]]), percentage: 25 }} />)
    expect(screen.getByRole('img', { name: /250 de 1\.000 tokens \(25%\)/ })).toBeInTheDocument()
    expect(document.querySelectorAll('.context-grid__cells .context-grid__cell--messages')).toHaveLength(25)
    expect(screen.getByText('Mensagens')).toBeInTheDocument()
    expect(screen.getByText('Livre')).toBeInTheDocument()
  })
})

describe('the detailed transcript', () => {
  const messages = [
    { message_id: 'u1', role: 'user' as const, content: 'rode os testes', turn_id: 't1' },
    { message_id: 'a1', role: 'assistant' as const, content: 'Todos passaram.', turn_id: 't1' },
  ]
  const events = [
    event({ type: 'agent.thought_delta', turnId: 't1', delta: 'Vou rodar ' }),
    event({ type: 'agent.thought_delta', turnId: 't1', delta: 'o pytest.' }),
    event({ type: 'tool.started', turnId: 't1', invocationId: 'i1', toolName: 'run_command', command: 'pytest', summary: 'Rodando', state: 'working' }),
    event({ type: 'assistant.delta', turnId: 't1', summary: 'Todos' }),
    event({ type: 'tool.finished', turnId: 't1', invocationId: 'i1', toolName: 'run_command', command: 'pytest', summary: 'Rodou', state: 'completed', exitCode: 0 }),
    event({ type: 'plugin.hook_executed', turnId: 't1', summary: 'Hook PostToolUse (project): blocked', status: 'blocked', payload: { hook_event: 'PostToolUse', source: 'project', exit_code: 2, blocked: true, reason: 'lint falhou' } }),
  ]

  it('puts a turn’s reasoning, tools and hooks between its messages, with the last state of each tool call', () => {
    const entries = buildTranscript(messages, events)
    expect(entries.map((entry) => entry.kind)).toEqual(['user', 'thinking', 'tool', 'hook', 'assistant'])
    expect(entries[1].body).toBe('Vou rodar o pytest.')
    expect(entries[2]).toMatchObject({ label: 'Ferramenta run_command', meta: 'concluída · código de saída 0' })
    expect(entries[2].body).toContain('$ pytest')
    expect(entries[3]).toMatchObject({ label: 'Hook PostToolUse (project)', meta: 'status: blocked · código de saída 2 · bloqueou' })
    expect(entries[3].body).toContain('Motivo: lint falhou')
  })

  it('shortens long entries until ctrl+e, and closes on Escape', () => {
    const onClose = vi.fn()
    render(<TranscriptView messages={[{ message_id: 'u1', role: 'user', content: 'x'.repeat(900), turn_id: 't1' }]} events={[]} onClose={onClose} />)
    expect(screen.getByText(/^x+…$/)).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'e', ctrlKey: true })
    expect(screen.getByText('x'.repeat(900))).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps a hook’s outcome from the server’s event', () => {
    const parsed = parseConversationActivityEvent({
      event_id: 'h1', event_type: 'plugin.hook_executed', summary: 'Hook Stop (user): ok', agent_id: 'agent:main',
      payload: { hook_event: 'Stop', source: 'user', type: 'command', status: 'ok', exit_code: 0, blocked: false, duration_ms: 41.6, reason: 'x'.repeat(301) },
    }, '1')
    expect(parsed.payload).toMatchObject({ hook_event: 'Stop', source: 'user', hook_type: 'command', exit_code: 0, blocked: false, duration_ms: 42 })
    expect(parsed.payload?.reason).toBeUndefined() // too long: dropped, not trusted
  })
})

describe('the tasks panel', () => {
  it('opens only web addresses', () => {
    expect(safeUrl('http://localhost:5173/')).toBe('http://localhost:5173/')
    expect(safeUrl('javascript:alert(1)')).toBeNull()
    expect(safeUrl('file:///etc/passwd')).toBeNull()
    expect(safeUrl('not a url')).toBeNull()
  })

  it('lists shells and subagents, and stops a shell that can be stopped', async () => {
    const shell = { task_id: 'shell:42', kind: 'shell', pid: 42, command: 'npm run dev', description: 'servidor', started_at: '2026-10-20T12:00:00Z', url: 'http://localhost:5173', state: 'running', stoppable: true, log_tail: 'ready in 300 ms' }
    const { client, calls } = fakeClient({
      '/stop': { task_id: 'shell:42', stopped: true, tasks: [{ ...shell, state: 'finished', stoppable: false }] },
      '/tasks': { tasks: [shell] },
      '/overview': {
        conversation_id: 'c1', title: 'T', state: 'running', provider: 'p', model_id: 'm',
        token_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: true },
        agents: [
          { agent_id: 'agent:main', name: 'Main', role: 'main', parent_agent_id: null, provider: 'p', model_id: 'm', state: 'working', token_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: true } },
          { agent_id: 'agent:2', name: 'Revisor', role: 'reviewer', parent_agent_id: 'agent:main', provider: 'p', model_id: 'm', state: 'working', token_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: true } },
        ],
        tools: [], messages: [], errors: [], turns: [], activity_count: 0, duration_seconds: null,
      },
    })
    render(<TasksPanel client={client} conversationId="c1" onClose={vi.fn()} />)
    expect(await screen.findByText('npm run dev', {}, SLOW)).toBeInTheDocument()
    expect(await screen.findByText('Revisor', {}, SLOW)).toBeInTheDocument()
    expect(screen.queryByText('Main')).toBeNull()
    expect(screen.getByRole('link', { name: 'http://localhost:5173/' })).toHaveAttribute('rel', 'noopener noreferrer')
    fireEvent.click(screen.getByRole('button', { name: 'Parar' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Parar' })).toBeNull(), SLOW)
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/tasks/shell%3A42/stop'))).toBe(true)
  })
})

describe('the status line', () => {
  it('shows the command’s lines, and nothing when it is off or fails', async () => {
    let answer: unknown = { enabled: true, lines: ['\x1b[32mmain\x1b[0m · 12%'], error: null }
    const { client } = fakeClient({ '/statusline': () => answer })
    const { rerender } = render(<StatusLineBar client={client} conversationId="c1" refreshKey="idle:1" />)
    expect(await screen.findByRole('status', { name: 'Linha de status' }, SLOW)).toHaveTextContent('main · 12%')
    expect(screen.getByText('main')).toHaveClass('ansi-fg-2')
    answer = { enabled: false, lines: [], error: 'workspace_not_trusted' }
    rerender(<StatusLineBar client={client} conversationId="c1" refreshKey="idle:2" />)
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Linha de status' })).toBeNull(), SLOW)
  })
})

type Permission = 'default' | 'denied' | 'granted'

describe('system notifications', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    delete (window as { orinDesktop?: unknown }).orinDesktop
  })

  it('use the desktop app when there is one, else the browser only with permission', async () => {
    const notifyCodeMode = vi.fn(async () => undefined)
    ;(window as { orinDesktop?: unknown }).orinDesktop = { notifyCodeMode }
    expect(notifySystem({ title: 'Orin respondeu', body: 'A resposta está pronta.' })).toBe(true)
    expect(notifyCodeMode).toHaveBeenCalledWith({ title: 'Orin respondeu', body: 'A resposta está pronta.' })
    delete (window as { orinDesktop?: unknown }).orinDesktop

    const shown: string[] = []
    class FakeNotification {
      static permission: Permission = 'denied'
      static requestPermission = vi.fn(async () => 'granted' as Permission)
      onclick: (() => void) | null = null
      constructor(title: string) { shown.push(title) }
      close() {}
    }
    vi.stubGlobal('Notification', FakeNotification)
    expect(notifySystem({ title: 'A', body: 'b' })).toBe(false)
    expect(await requestSystemNotifications()).toBe('denied')
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled() // a refusal is not asked again
    FakeNotification.permission = 'default'
    expect(notifySystem({ title: 'A', body: 'b' })).toBe(false)
    expect(await requestSystemNotifications()).toBe('granted')
    FakeNotification.permission = 'granted'
    expect(notifySystem({ title: 'T'.repeat(200), body: 'b' })).toBe(true)
    expect(shown).toEqual([`${'T'.repeat(79)}…`])
  })
})

describe('commands of the D2 surfaces', () => {
  function deps(client: ApiClient, overrides: Partial<ChatCommandDeps> = {}): ChatCommandDeps {
    return {
      client, conversationId: 'c1', messages: [], title: 'T', modelId: 'm', provider: 'p', permissionMode: 'ask_permissions', mode: 'code',
      workspacePath: null, navigate: vi.fn(), send: vi.fn(async () => undefined), reload: vi.fn(async () => undefined), notify: vi.fn(),
      openInfo: vi.fn(), openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(), ...overrides,
    }
  }

  it('/context opens the grid, or says there is no measurement yet', async () => {
    const measured = { available: true, limit_tokens: 1000, used_tokens: 400, percentage: 40, compaction_count: 0, categories: [{ key: 'messages', tokens: 400 }], measured_at: null }
    const value = deps(fakeClient({ '/context': measured }).client)
    await chatCommandHandlers(value).context('')
    expect(value.openInfo).toHaveBeenCalledWith(expect.objectContaining({ kind: 'context' }))
    const empty = deps(fakeClient({ '/context': { available: false, categories: [] } }).client)
    await chatCommandHandlers(empty).context('')
    expect(empty.openInfo).toHaveBeenCalledWith(expect.objectContaining({ kind: 'text', title: 'Uso do contexto' }))
  })

  it('/tasks is offered only with the panel; /statusline asks the model to set it up', async () => {
    const { client } = fakeClient({})
    expect('tasks' in chatCommandHandlers(deps(client))).toBe(false)
    const value = deps(client, { openTasks: vi.fn() })
    chatCommandHandlers(value).tasks('')
    expect(value.openTasks).toHaveBeenCalled()
    await chatCommandHandlers(value).statusline('o branch do git')
    expect(value.send).toHaveBeenCalledWith(expect.stringMatching(/statusLine[\s\S]*O que mostrar: o branch do git$/))
  })
})

describe('the slash picker', () => {
  it('puts the exact name first, then names that start with the text, and description matches last', () => {
    const tokens = (query: string) => slashItems(query, [], [], BUILTIN_COMMANDS).map((item) => item.token)
    expect(tokens('context')[0]).toBe('context')
    expect(tokens('context')).toContain('compact') // its description mentions "contexto": still offered, after
    expect(tokens('bashes')[0]).toBe('tasks') // an alias is an exact match too
    const c = tokens('c')
    expect(c.slice(0, 3)).toEqual(['clear', 'compact', 'context'])
    expect(c.indexOf('add-dir')).toBeGreaterThan(c.indexOf('color'))
  })
})
