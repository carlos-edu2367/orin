import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { moreCommandHandlers, NOT_IN_ORIN, type EffortCommandController } from '../moreCommands'
import { chatCommandHandlers } from '../useChatCommands'

function makeDeps(effort?: EffortCommandController) {
  const notify = vi.fn()
  const openInfo = vi.fn()
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: vi.fn() })
  return {
    client,
    notify,
    openInfo,
    handlers: moreCommandHandlers({
      client,
      navigate: vi.fn(),
      send: vi.fn().mockResolvedValue(undefined),
      notify,
      openInfo,
      effort,
    }),
  }
}

describe('/effort command', () => {
  it('removes effort from NOT_IN_ORIN and registers a real handler', () => {
    expect('effort' in NOT_IN_ORIN).toBe(false)
    const { handlers } = makeDeps()
    expect(typeof handlers.effort).toBe('function')
  })

  it('shows current effort (explicit or default) and accepted levels when called without arguments', async () => {
    const { handlers, notify } = makeDeps({
      current: null,
      defaultEffort: 'medium',
      supportedEfforts: ['low', 'medium', 'high', 'xhigh'],
      modelLabel: 'claude-sonnet-5-5',
      modelLoaded: true,
      setEffort: vi.fn(),
    })

    await handlers.effort('')

    expect(notify).toHaveBeenCalledTimes(1)
    const message = String(notify.mock.calls[0][0])
    expect(message).toMatch(/padrão/i)
    expect(message).toMatch(/medium/i)
    expect(message).toMatch(/low, medium, high, xhigh/i)
  })

  it('sets effort for next turn and remembers it for the current model on valid level', async () => {
    const setEffort = vi.fn()
    const { handlers, notify } = makeDeps({
      current: null,
      defaultEffort: 'medium',
      supportedEfforts: ['low', 'medium', 'high', 'xhigh', 'max'],
      modelLabel: 'claude-opus-5-5',
      modelLoaded: true,
      setEffort,
    })

    await handlers.effort('high')

    expect(setEffort).toHaveBeenCalledWith('high')
    expect(notify).toHaveBeenCalledTimes(1)
    expect(String(notify.mock.calls[0][0])).toMatch(/high/i)
  })

  it('rejects a level not supported by the current model and lists accepted levels', async () => {
    const setEffort = vi.fn()
    const { handlers, notify } = makeDeps({
      current: 'medium',
      defaultEffort: 'medium',
      supportedEfforts: ['low', 'medium', 'high'],
      modelLabel: 'o3-mini',
      modelLoaded: true,
      setEffort,
    })

    await handlers.effort('ultra')

    expect(setEffort).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledTimes(1)
    const message = String(notify.mock.calls[0][0])
    expect(message).toMatch(/ultra/i)
    expect(message).toMatch(/low, medium, high/i)
  })

  it('clears explicit effort on /effort default and /effort auto', async () => {
    const setEffort = vi.fn()
    const { handlers, notify } = makeDeps({
      current: 'high',
      defaultEffort: 'medium',
      supportedEfforts: ['low', 'medium', 'high'],
      modelLabel: 'claude-sonnet-5-5',
      modelLoaded: true,
      setEffort,
    })

    await handlers.effort('default')
    await handlers.effort('auto')

    expect(setEffort).toHaveBeenNthCalledWith(1, null)
    expect(setEffort).toHaveBeenNthCalledWith(2, null)
    expect(notify).toHaveBeenCalledTimes(2)
    expect(String(notify.mock.calls[0][0])).toMatch(/padrão/i)
  })

  it('validates against known levels when no model is loaded yet and wires through chatCommandHandlers', async () => {
    const setEffort = vi.fn()
    const notify = vi.fn()
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: vi.fn() })
    const handlers = chatCommandHandlers({
      client,
      conversationId: 'conv-1',
      messages: [],
      title: 'Teste',
      modelId: '',
      provider: '',
      permissionMode: 'ask_permissions',
      mode: 'chat',
      workspacePath: null,
      navigate: vi.fn(),
      send: vi.fn().mockResolvedValue(undefined),
      reload: vi.fn().mockResolvedValue(undefined),
      notify,
      openInfo: vi.fn(),
      openPreferences: vi.fn(),
      openModelPicker: vi.fn(),
      setPermissionMode: vi.fn(),
      effort: {
        current: null,
        defaultEffort: null,
        supportedEfforts: [],
        modelLoaded: false,
        setEffort,
      },
    })

    await handlers.effort('xhigh')
    expect(setEffort).toHaveBeenCalledWith('xhigh')

    await handlers.effort('absurd')
    expect(setEffort).toHaveBeenCalledTimes(1)
    expect(String(notify.mock.calls[1][0])).toMatch(/low, medium, high, xhigh, max, ultra/i)
  })
})
