import { fireEvent, render, renderHook, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import type { ProviderModel } from '../../src/api/providers'
import { ModelPicker } from '../../src/components/ModelPicker'
import { BUILTIN_COMMANDS, findBuiltin, isExcludedCommand, parseSlash } from '../../src/features/commands/builtins'
import { CommandInfoDialog } from '../../src/features/commands/CommandInfoDialog'
import { chatCommandHandlers, INIT_PROMPT, type ChatCommandDeps, useChatCommands } from '../../src/features/commands/useChatCommands'
import { Composer } from '../../src/features/conversations/Composer'
import { slashItems } from '../../src/features/conversations/CommandPicker'

function fakeClient(responses: Record<string, unknown>) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  const fetchImpl = (async (url: string, init: NonNullable<Parameters<typeof fetch>[1]>) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(responses).find((item) => url.includes(item))
    return new Response(JSON.stringify(key ? responses[key] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return { client: new ApiClient({ fetchImpl, maxAttempts: 1 }), calls }
}

function deps(overrides: Partial<ChatCommandDeps> = {}): ChatCommandDeps {
  return {
    client: fakeClient({}).client, conversationId: 'chat_1',
    messages: [{ role: 'user', content: 'oi' }, { role: 'assistant', content: 'primeira' }, { role: 'assistant', content: 'segunda' }],
    title: 'Teste', modelId: 'm', provider: 'openrouter', permissionMode: 'ask_permissions', mode: 'code', workspacePath: 'C:/p',
    navigate: vi.fn(), send: vi.fn(async () => undefined), reload: vi.fn(async () => undefined), notify: vi.fn(), openInfo: vi.fn(),
    openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(), copyText: vi.fn(async () => undefined), download: vi.fn(),
    ...overrides,
  }
}

describe('built-in commands', () => {
  it('find a command by name or alias and parse its arguments', () => {
    expect(findBuiltin('reset')?.name).toBe('clear')
    expect(findBuiltin('allowed-tools')?.name).toBe('permissions')
    expect(parseSlash('/compact  focus on tests ')).toEqual({ name: 'compact', args: 'focus on tests' })
    expect(parseSlash('/caminho/arquivo')).toBeNull()
    expect(parseSlash('texto /compact')).toBeNull()
    expect(isExcludedCommand('login') && isExcludedCommand('design-review') && !isExcludedCommand('help')).toBe(true)
    expect(new Set(BUILTIN_COMMANDS.map((command) => command.name)).size).toBe(BUILTIN_COMMANDS.length)
  })

  it('run what is offered, refuse what Orin excludes and send everything else', async () => {
    const value = deps()
    const { result } = renderHook(() => useChatCommands(value))
    expect(result.current.offered.some((command) => command.name === 'help')).toBe(true)
    expect(result.current.offered.some((command) => command.name === 'rewind')).toBe(false) // its surface comes later
    expect(await result.current.run('/reset')).toEqual({ kind: 'handled' })
    expect(value.navigate).toHaveBeenCalledWith('/')
    expect(await result.current.run('/login')).toEqual({ kind: 'excluded', name: 'login' })
    expect(await result.current.run('/my-skill arg')).toEqual({ kind: 'message' })
    expect(await result.current.run('não é comando')).toEqual({ kind: 'message' })
  })

  it('handlers do what their commands say', async () => {
    const { client, calls } = fakeClient({ '/compact': { turn_id: 't1' }, '/v1/instance': { version: '0.9.4', mode: 'local', capabilities: {} } })
    const value = deps({ client })
    const handlers = chatCommandHandlers(value)
    await handlers.copy('2')
    expect(value.copyText).toHaveBeenCalledWith('primeira')
    await handlers.compact('foque nos testes')
    expect(calls.find((call) => call.url.endsWith('/compact'))?.body).toEqual({ instructions: 'foque nos testes' })
    expect(value.reload).toHaveBeenCalled()
    handlers.export('nota')
    expect(value.download).toHaveBeenCalledWith('nota.txt', expect.stringContaining('Orin:\nsegunda'))
    await handlers.plan('desenhe a migração')
    expect(value.setPermissionMode).toHaveBeenCalledWith('plan_mode')
    expect(value.send).toHaveBeenCalledWith('desenhe a migração')
    await handlers.init('')
    expect(value.send).toHaveBeenLastCalledWith(INIT_PROMPT)
    await handlers.status('')
    expect(value.openInfo).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'text', lines: expect.arrayContaining([expect.stringContaining('0.9.4')]) }))
    await handlers['add-dir']('')
    expect(value.notify).toHaveBeenLastCalledWith('Diga a pasta: /add-dir <pasta>')
  })
})

describe('picker and composer', () => {
  it('lists built-ins first and finds them by alias', () => {
    const offered = BUILTIN_COMMANDS.filter((command) => ['clear', 'help'].includes(command.name))
    const items = slashItems('reset', [], [], offered, [{ name: 'deploy', description: 'Deploy', kind: 'command', origin: 'user', argumentHint: '', context: null }])
    expect(items.map((item) => item.token)).toEqual(['clear'])
    expect(slashItems('', [], [], offered, [{ name: 'deploy', description: 'Deploy', kind: 'command', origin: 'user', argumentHint: '', context: null }])
      .map((item) => `${item.kind}:${item.token}`)).toEqual(['builtin:clear', 'builtin:help', 'command:deploy'])
  })

  it('sends now, clears and inserts a line through the keybinding engine', () => {
    const onSendNow = vi.fn()
    const onChange = vi.fn()
    const { rerender } = render(<Composer value="faça agora" onChange={onChange} onSubmit={vi.fn()} running onStop={vi.fn()} onSendNow={onSendNow} />)
    const input = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    expect(onSendNow).toHaveBeenCalledTimes(1)
    rerender(<Composer value="abc" onChange={onChange} onSubmit={vi.fn()} />)
    fireEvent.keyDown(input, { key: 'j', ctrlKey: true })
    expect(onChange).toHaveBeenLastCalledWith('abc\n')
  })
})

describe('the model picker opens on a signal and the help dialog lists keys', () => {
  it('opens the model list when the signal grows', () => {
    const models = [{ model_id: 'm1', display_name: 'Model 1', provider: 'openrouter', capabilities: [], input_modalities: [], output_modalities: [] }] as unknown as ProviderModel[]
    const props = { providers: ['openrouter' as const], provider: 'openrouter' as const, onProviderChange: vi.fn(), models, modelId: 'm1', onModelChange: vi.fn() }
    const { rerender } = render(<ModelPicker {...props} openSignal={0} />)
    expect(screen.queryByRole('listbox')).toBeNull()
    rerender(<ModelPicker {...props} openSignal={1} />)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('/help shows commands with aliases and the keys in force', () => {
    const onClose = vi.fn()
    render(<CommandInfoDialog panel={{ kind: 'help' }} commands={BUILTIN_COMMANDS.filter((command) => command.name === 'clear')} onClose={onClose} />)
    expect(screen.getByRole('dialog', { name: 'Comandos e atalhos' })).toBeInTheDocument()
    expect(screen.getByText('/reset, /new', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Ctrl+O')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})
