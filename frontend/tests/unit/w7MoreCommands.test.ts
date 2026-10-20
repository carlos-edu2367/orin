import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { BUILTIN_COMMANDS } from '../../src/features/commands/builtins'
import { LOOP_PROMPT, moreCommandHandlers, SUBTASK_PROMPT } from '../../src/features/commands/moreCommands'
import { chatCommandHandlers, type ChatCommandDeps } from '../../src/features/commands/useChatCommands'

function client(routes: Record<string, unknown>) {
  const fetchImpl = (async (url: string) => {
    const key = Object.keys(routes).find((item) => url.includes(item))
    return new Response(JSON.stringify(key ? routes[key] : {}), { status: key ? 200 : 404, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return new ApiClient({ fetchImpl, maxAttempts: 1 })
}

function deps(overrides: Partial<ChatCommandDeps> = {}): ChatCommandDeps {
  return {
    client: client({}), conversationId: 'chat_1', messages: [], title: 'T', modelId: 'm', provider: 'p', permissionMode: 'ask_permissions', mode: 'code',
    workspacePath: null, navigate: vi.fn(), send: vi.fn(async () => undefined), reload: vi.fn(async () => undefined), notify: vi.fn(),
    openInfo: vi.fn(), openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(),
    openRewind: vi.fn(), rename: vi.fn(async () => undefined), branch: vi.fn(async () => undefined), fork: vi.fn(async () => undefined),
    openTasks: vi.fn(), setAccent: vi.fn(), ...overrides,
  }
}

describe('every built-in command has a handler in the chat', () => {
  it('so none is sent to the model by mistake', () => {
    const handlers = chatCommandHandlers(deps())
    expect(BUILTIN_COMMANDS.map((command) => command.name).filter((name) => !(name in handlers))).toEqual([])
  })
})

describe('the remaining commands', () => {
  it('/subtask and /loop ask the model in Orin’s words; without arguments they say what is missing', async () => {
    const value = deps()
    const handlers = moreCommandHandlers(value)
    await handlers.subtask('')
    expect(value.notify).toHaveBeenCalledWith('Diga a tarefa: /subtask <tarefa>')
    await handlers.subtask('revise os testes')
    expect(value.send).toHaveBeenLastCalledWith(`${SUBTASK_PROMPT}\nrevise os testes`)
    await handlers.loop('10m rode os testes')
    expect(value.send).toHaveBeenLastCalledWith(`${LOOP_PROMPT} a cada 10m, este pedido: rode os testes`)
  })

  it('/color sets the accent by name and refuses an unknown one', () => {
    const value = deps()
    const handlers = moreCommandHandlers(value)
    handlers.color('azul')
    expect(value.setAccent).toHaveBeenCalledWith('sapphire')
    handlers.color('')
    expect(value.setAccent).toHaveBeenLastCalledWith('violet')
    handlers.color('magenta')
    expect(value.setAccent).toHaveBeenCalledTimes(2)
    expect(value.notify).toHaveBeenLastCalledWith('Cores: roxo, azul, verde, âmbar, mono ou default.')
  })

  it('/doctor reports the instance and the installation, and says when it could not read one', async () => {
    const value = deps({ client: client({ '/v1/instance': { version: '0.9.5', mode: 'local', capabilities: { host_folders: true, shell: false } } }) })
    await moreCommandHandlers(value).doctor('')
    const panel = (value.openInfo as ReturnType<typeof vi.fn>).mock.calls[0][0]
    expect(panel.title).toBe('Diagnóstico')
    expect(panel.lines).toEqual(['Orin 0.9.5 (local)', 'Desligado nesta instância: shell', 'Não foi possível ler o estado da instalação.'])
  })

  it('a command Orin does not have answers plainly instead of reaching the model', () => {
    const value = deps()
    moreCommandHandlers(value).fast('')
    expect(value.send).not.toHaveBeenCalled()
    expect(value.openInfo).toHaveBeenCalledWith(expect.objectContaining({ title: 'Modo rápido', lines: expect.arrayContaining(['O Orin não tem um modo rápido.']) }))
  })
})

describe('the slash picker with every built-in offered', () => {
  it('still lists the folder’s own commands after them', async () => {
    const { slashItems } = await import('../../src/features/conversations/CommandPicker')
    const catalog = [{ name: 'deploy', description: 'Publica', kind: 'command', origin: 'project', argumentHint: '', context: null }]
    const tokens = slashItems('', [], [], BUILTIN_COMMANDS, catalog).map((item) => item.token)
    expect(tokens.length).toBeGreaterThan(BUILTIN_COMMANDS.length)
    expect(tokens).toContain('deploy')
  })
})
