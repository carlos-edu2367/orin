import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { parseApiError } from '../../src/api/errors'
import { rewindCode, TOO_LARGE_TO_BACK_UP } from '../../src/api/sessionTools'
import { McpScopesSection } from '../../src/features/codeSettings/McpScopesSection'
import { Composer, findInHistory } from '../../src/features/conversations/Composer'
import { RewindPicker } from '../../src/features/conversations/RewindPicker'
import { fullAutonomyDetail } from '../../src/features/conversations/SessionContextPopover'
import { ACTIONS, UNSUPPORTED_ACTIONS } from '../../src/features/keybindings/catalog'
import { PluginInstallDialog } from '../../src/features/plugins/PluginInstallDialog'

type Call = { url: string; method: string; body: unknown }

function fakeClient(answer: (call: Call) => { status?: number; body: unknown }) {
  const calls: Call[] = []
  const fetchImpl = (async (url: string, init: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = { url, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    const result = answer(call)
    return new Response(JSON.stringify(result.body), { status: result.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return { client: new ApiClient({ fetchImpl, maxAttempts: 1 }), calls }
}

afterEach(() => { vi.restoreAllMocks() })

describe('rewinding a file too large to back up', () => {
  it('reads the files a blocked rewind names, bounded', () => {
    const files = Array.from({ length: 80 }, (_, index) => ({ path: `C:/big/${index}.bin`, reason: TOO_LARGE_TO_BACK_UP }))
    const error = parseApiError(409, { error: { code: 'rewind_blocked', details: { files: [...files, { path: '' }, { reason: 'no path' }, 'junk'] } } })
    expect(error.blockedFiles).toHaveLength(50)
    expect(error.blockedFiles[0]).toEqual({ path: 'C:/big/0.bin', reason: TOO_LARGE_TO_BACK_UP })
    expect(parseApiError(409, { error: { code: 'x' } }).blockedFiles).toEqual([])
  })

  it('sends the files to rewind anyway only when there are some', async () => {
    const { client, calls } = fakeClient(() => ({ body: { message_id: 'm1', restored: [], deleted: [], unchanged: [], skipped: [], blocked: false, unbacked: ['C:/big/a.bin'] } }))
    await rewindCode(client, 'chat_1', 'm1')
    const result = await rewindCode(client, 'chat_1', 'm1', ['C:/big/a.bin'])
    expect(calls.map((call) => call.body)).toEqual([{ mode: 'code' }, { mode: 'code', force_unbacked: ['C:/big/a.bin'] }])
    expect(result.unbacked).toEqual(['C:/big/a.bin'])
  })

  it('offers to restore anyway only for the message whose rewind named the files', () => {
    const onRewind = vi.fn()
    const messages = [{ messageId: 'm1', content: 'primeira' }, { messageId: 'm2', content: 'segunda' }]
    render(<RewindPicker messages={messages} history={[]} initialMessageId="m1" unbackable={['C:/big/a.bin']} unbackableFor="m1"
      onRewind={onRewind} onClose={vi.fn()} />)
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true') // started on m1 (newest first)
    fireEvent.click(screen.getByRole('button', { name: 'Restaurar mesmo assim' }))
    expect(onRewind).toHaveBeenCalledWith('m1', 'code', ['C:/big/a.bin'])
    fireEvent.keyDown(window, { key: 'ArrowUp' }) // to m2: the offer was for m1 only
    expect(screen.queryByRole('button', { name: 'Restaurar mesmo assim' })).toBeNull()
  })
})

describe('the composer', () => {
  function composer(props: Partial<Parameters<typeof Composer>[0]> & { value: string }) {
    const onChange = vi.fn()
    const onSubmit = vi.fn()
    const view = render(<Composer onChange={onChange} onSubmit={onSubmit} {...props} />)
    return { onChange, onSubmit, view, input: screen.getByLabelText('Mensagem') as HTMLTextAreaElement }
  }

  it('recalls earlier messages with up and down, from the first line only', () => {
    const history = ['primeira', 'segunda']
    const { onChange, view, input } = composer({ value: 'rascunho', history })
    input.setSelectionRange(0, 0)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith('segunda')
    view.rerender(<Composer value="segunda" onChange={onChange} onSubmit={vi.fn()} history={history} />)
    input.setSelectionRange(0, 0)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith('primeira')
    view.rerender(<Composer value="primeira" onChange={onChange} onSubmit={vi.fn()} history={history} />)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    view.rerender(<Composer value="segunda" onChange={onChange} onSubmit={vi.fn()} history={history} />)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(onChange).toHaveBeenLastCalledWith('rascunho') // back to what was being typed
  })

  it('moves the caret instead of recalling when it is not on the first line', () => {
    const { onChange, input } = composer({ value: 'linha 1\nlinha 2', history: ['antiga'] })
    input.setSelectionRange(10, 10)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('finds earlier messages for ctrl+r, newest first', () => {
    const history = ['rode os testes', 'abra o PR', 'rode o lint']
    expect(findInHistory(history, 'rode', 0)).toBe('rode o lint')
    expect(findInHistory(history, 'RODE', 1)).toBe('rode os testes')
    expect(findInHistory(history, 'rode', 9)).toBe('rode os testes')
    expect(findInHistory(history, 'nada', 0)).toBeNull()
    expect(findInHistory(history, '  ', 0)).toBeNull()
  })

  it('hands a !command typed during a turn to the chat, which explains why it waits', () => {
    const onQueue = vi.fn()
    const { onSubmit, input } = composer({ value: '!git status', running: true, onStop: vi.fn(), onQueue, conversationMode: 'code' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onQueue).not.toHaveBeenCalled()
  })
})

describe('keys, wording and plugin details', () => {
  it('has no thinking toggle (Orin has no such switch) and keeps ctrl+t for the desktop app', () => {
    expect(ACTIONS['chat:thinkingToggle']).toBeUndefined()
    expect(UNSUPPORTED_ACTIONS.has('chat:thinkingToggle')).toBe(true)
    expect(ACTIONS['app:toggleTodos'].browser).toEqual([])
  })

  it('says what "Sem restrições" means on a local and on a shared Orin', () => {
    expect(fullAutonomyDetail(true)).toMatch(/fora da pasta da sessão/)
    expect(fullAutonomyDetail(false)).toMatch(/sempre dentro do workspace/)
  })

  it('shows what a plugin will run before it is installed', async () => {
    const { client } = fakeClient(() => ({ body: {
      plugin_id: 'kit', display_name: 'Kit', version: '1.0.0', state: 'pending_approval', warnings: [], contribution_count: 4, package_digest: 'sha256:x',
      skills: [{ name: 'revisar' }], commands: [{ command_id: 'kit:deploy', slug: 'deploy' }], agents: [], mcp_servers: [{ slug: 'docs', display_name: 'Docs' }],
      hooks: [{ event: 'PostToolUse', matcher: 'Edit|Write', command: 'npm run lint' }],
      lsp_servers: [{ name: 'pyright', command: 'pyright-langserver', args: ['--stdio'] }],
    } }))
    render(<PluginInstallDialog client={client} initialReference="acme/kit" onClose={vi.fn()} onInstalled={vi.fn()} />)
    expect(await screen.findByText('Este plugin roda comandos no seu computador:')).toBeInTheDocument()
    expect(screen.getByText('npm run lint')).toBeInTheDocument()
    expect(screen.getByText('pyright-langserver --stdio')).toBeInTheDocument()
    expect(screen.getByText('/deploy')).toBeInTheDocument()
    expect(screen.getByText('Docs')).toBeInTheDocument()
  })
})

describe('MCP servers per scope', () => {
  const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={['/settings/mcp-scopes?conversation=chat_1']}>{children}</MemoryRouter>

  it('lists the project servers waiting for approval and approves one for this folder', async () => {
    const calls: Call[] = []
    let approved = false
    globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
      const call = { url: String(input), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined }
      calls.push(call)
      if (call.method === 'PUT') approved = true
      const github = { name: 'github', scope: 'project', source: 'C:/p/.mcp.json', transport: 'stdio', shown: 'npx -y server-github --token [REDACTED]' }
      return new Response(JSON.stringify({
        folder: 'C:/p', trusted: true, managed_only: false, awaiting_trust: false, errors: [],
        servers: approved ? [{ ...github, state: 'on' }] : [], pending: approved ? [] : [{ ...github, state: 'pending' }],
        choices: approved ? { enabledMcpjsonServers: ['github'] } : {},
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as unknown as typeof fetch
    render(<McpScopesSection />, { wrapper })
    expect(await screen.findByText('npx -y server-github --token [REDACTED]')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Aprovar' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'PUT')?.body).toEqual({ conversation_id: 'chat_1', server: 'github', decision: 'approve' }))
    expect(await screen.findByText('ligado')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Esquecer a escolha sobre github' })).toBeInTheDocument()
  })
})
