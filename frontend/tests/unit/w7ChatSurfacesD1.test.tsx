import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { forkConversation, renameConversation, rewindMessage, runShell } from '../../src/api/sessionTools'
import { chatCommandHandlers, type ChatCommandDeps } from '../../src/features/commands/useChatCommands'
import { CodePermissionDock, isEditRequest } from '../../src/features/conversations/CodePermissionDock'
import { Composer } from '../../src/features/conversations/Composer'
import { QueuePanel } from '../../src/features/conversations/QueuePanel'
import { RewindPicker } from '../../src/features/conversations/RewindPicker'

function fakeClient(responses: Record<string, unknown>) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  const fetchImpl = (async (url: string, init: NonNullable<Parameters<typeof fetch>[1]>) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(responses).find((item) => url.includes(item))
    return new Response(JSON.stringify(key ? responses[key] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return { client: new ApiClient({ fetchImpl, maxAttempts: 1 }), calls }
}

describe('W7 D1 client calls', () => {
  it('runs a shell turn, forks, renames and rewinds the conversation', async () => {
    const fork = { conversation_id: 'chat_2', title: 'T (ramo)', prompt: 'segunda', messages: 2 }
    const { client, calls } = fakeClient({ '/shell': { turn_id: 't9' }, '/fork': fork, '/title': { title: 'Novo' }, '/rewind': { fork, restored: ['a.py'] } })
    expect(await runShell(client, 'chat_1', 'git status')).toEqual({ turnId: 't9' })
    expect((await forkConversation(client, 'chat_1', { beforeMessageId: 'm1' })).prompt).toBe('segunda')
    expect(await renameConversation(client, 'chat_1', 'Novo')).toEqual({ title: 'Novo' })
    const rewound = await rewindMessage(client, 'chat_1', 'm1', 'both')
    expect(rewound.fork.conversationId).toBe('chat_2')
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /v1/conversations/chat_1/shell', 'POST /v1/conversations/chat_1/fork', 'PATCH /v1/conversations/chat_1/title',
      'POST /v1/conversations/chat_1/messages/m1/rewind',
    ])
    expect(calls[1].body).toEqual({ before_message_id: 'm1' })
    expect(calls[3].body).toEqual({ mode: 'both' })
  })
})

describe('queue panel', () => {
  it('lists the queued messages and acts on one', () => {
    const onRemove = vi.fn()
    const onSendNow = vi.fn()
    const { rerender } = render(<QueuePanel items={[{ queueId: 'q1', content: 'rode os testes', createdAt: null }]} onRemove={onRemove} onSendNow={onSendNow} />)
    expect(screen.getByRole('region', { name: 'Mensagens na fila' })).toHaveTextContent('Na fila (1)')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar agora' }))
    fireEvent.click(screen.getByRole('button', { name: /Remover da fila/ }))
    expect(onSendNow).toHaveBeenCalledWith(expect.objectContaining({ queueId: 'q1' }))
    expect(onRemove).toHaveBeenCalledWith('q1')
    rerender(<QueuePanel items={[]} onRemove={onRemove} onSendNow={onSendNow} />)
    expect(screen.queryByRole('region', { name: 'Mensagens na fila' })).toBeNull()
  })
})

describe('rewind picker', () => {
  const messages = [{ messageId: 'm1', content: 'primeira' }, { messageId: 'm2', content: 'segunda' }]
  const history = [{ messageId: 'm2', turnId: 't2', kind: 'message', createdAt: null, rewoundAt: null, files: ['src/a.py', 'src/b.py'] }]

  it('shows the newest message first with the files a code rewind restores', () => {
    const onRewind = vi.fn()
    render(<RewindPicker messages={messages} history={history} onRewind={onRewind} onClose={vi.fn()} />)
    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveTextContent('segunda')
    expect(screen.getByText(/2 arquivos mudaram/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Os dois' }))
    expect(onRewind).toHaveBeenLastCalledWith('m2', 'both')
    // Going back to the older message restores what the newer one changed too.
    fireEvent.keyDown(window, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(/2 arquivos mudaram/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Voltar a conversa' }))
    expect(onRewind).toHaveBeenLastCalledWith('m1', 'conversation')
  })

  it('offers no code rewind when no file changed', () => {
    render(<RewindPicker messages={messages} history={[]} onRewind={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('Nenhum arquivo mudou depois desta mensagem.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Restaurar o código' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Os dois' })).toBeDisabled()
  })

  it('closes on Escape and says when there is nothing to go back to', () => {
    const onClose = vi.fn()
    render(<RewindPicker messages={[]} history={[]} onRewind={vi.fn()} onClose={onClose} />)
    expect(screen.getByText('Ainda não há mensagens para voltar.')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})

describe('composer', () => {
  it('queues on Enter while running, opens the rewind picker on Esc Esc, and shows the shell mode', () => {
    const onQueue = vi.fn()
    const onOpenRewind = vi.fn()
    const { rerender } = render(<Composer value="e os testes?" onChange={vi.fn()} onSubmit={vi.fn()} running onStop={vi.fn()} onQueue={onQueue} />)
    const input = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onQueue).toHaveBeenCalledTimes(1)
    rerender(<Composer value="" onChange={vi.fn()} onSubmit={vi.fn()} onOpenRewind={onOpenRewind} />)
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onOpenRewind).toHaveBeenCalledTimes(1)
    rerender(<Composer value="!git status" onChange={vi.fn()} onSubmit={vi.fn()} conversationMode="code" />)
    expect(screen.getByRole('status')).toHaveTextContent('Modo shell')
    rerender(<Composer value="!git status" onChange={vi.fn()} onSubmit={vi.fn()} conversationMode="chat" />)
    expect(screen.queryByText(/Modo shell/)).toBeNull()
  })
})

describe('permission card', () => {
  it('says Yes / Yes and don’t ask again / No, and an edit’s second option is for the session', () => {
    const onResolve = vi.fn()
    const request = { requestId: 'r1', turnId: 't1', toolName: 'edit_file', path: 'src/a.py', state: 'pending' as const }
    expect(isEditRequest(request)).toBe(true)
    render(<CodePermissionDock requests={[request]} onResolve={onResolve} />)
    expect(screen.getByTestId('code-perm-allow-once')).toHaveTextContent('Sim')
    expect(screen.getByTestId('code-perm-allow-always')).toHaveTextContent('Sim, e permitir edições nesta sessão')
    expect(screen.getByTestId('code-perm-deny')).toHaveTextContent('Não')
    fireEvent.keyDown(window, { key: '2' })
    expect(onResolve).toHaveBeenCalledWith('r1', { behavior: 'allow', scope: 'session', kind: 'edits' })
  })

  it('names the rule for a command', () => {
    render(<CodePermissionDock requests={[{ requestId: 'r2', turnId: 't1', toolName: 'run_command', command: 'npm test', ruleSuggestion: 'Bash(npm test:*)', state: 'pending' }]} onResolve={vi.fn()} />)
    expect(screen.getByTestId('code-perm-allow-always')).toHaveTextContent('Sim, e não perguntar de novo para:Bash(npm test:*)(nesta pasta)')
  })
})

describe('commands of the D1 surfaces', () => {
  function deps(overrides: Partial<ChatCommandDeps> = {}): ChatCommandDeps {
    return {
      client: fakeClient({ '/overview': {
        conversation_id: 'chat_1', title: 'T', state: 'completed', provider: 'p', model_id: 'm',
        token_usage: { input_tokens: 1200, output_tokens: 300, total_tokens: 1500, estimated_cost_usd: 0.0123, usage_reported: true },
        agents: [], tools: [], messages: [], errors: [], turns: [], activity_count: 0, duration_seconds: null,
      } }).client,
      conversationId: 'chat_1', messages: [], title: 'T', modelId: 'm', provider: 'p', permissionMode: 'ask_permissions', mode: 'code',
      workspacePath: null, navigate: vi.fn(), send: vi.fn(async () => undefined), reload: vi.fn(async () => undefined), notify: vi.fn(),
      openInfo: vi.fn(), openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(), ...overrides,
    }
  }

  it('offers /rewind, /rename, /branch and /fork only when the chat provides them', async () => {
    expect('rewind' in chatCommandHandlers(deps())).toBe(false)
    const value = deps({ openRewind: vi.fn(), rename: vi.fn(async () => undefined), branch: vi.fn(async () => undefined), fork: vi.fn(async () => undefined) })
    const handlers = chatCommandHandlers(value)
    handlers.rewind('')
    expect(value.openRewind).toHaveBeenCalled()
    await handlers.rename('')
    expect(value.notify).toHaveBeenCalledWith('Diga o novo nome: /rename <nome>')
    await handlers.rename('Pagamentos')
    expect(value.rename).toHaveBeenCalledWith('Pagamentos')
    await handlers.fork('rode os testes')
    expect(value.fork).toHaveBeenCalledWith('rode os testes')
  })

  it('/usage reads the session’s tokens and cost', async () => {
    const value = deps()
    await chatCommandHandlers(value).usage('')
    expect(value.openInfo).toHaveBeenCalledWith(expect.objectContaining({ title: 'Uso da sessão', lines: expect.arrayContaining([expect.stringContaining('1.200')]) }))
  })
})
