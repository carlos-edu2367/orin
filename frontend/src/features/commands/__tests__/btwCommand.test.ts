import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { findBuiltin } from '../builtins'
import { NOT_IN_ORIN } from '../moreCommands'
import { chatCommandHandlers, useChatCommands } from '../useChatCommands'

describe('/btw command registration and running-turn safety', () => {
  it('removes btw from NOT_IN_ORIN and exposes argumentHint on builtin', () => {
    expect('btw' in NOT_IN_ORIN).toBe(false)
    const builtin = findBuiltin('btw')
    expect(builtin).not.toBeNull()
    expect(builtin?.argumentHint).toBe('<pergunta>')
  })

  it('registers handler only when askBtw dep is provided and forwards trimmed args', async () => {
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: vi.fn() })
    const askBtw = vi.fn().mockResolvedValue(undefined)
    const baseDeps = {
      client,
      conversationId: 'chat_1',
      messages: [],
      title: 'Teste',
      modelId: 'claude-sonnet-5-5',
      provider: 'anthropic',
      permissionMode: 'ask_permissions',
      mode: 'code',
      workspacePath: null,
      navigate: vi.fn(),
      send: vi.fn().mockResolvedValue(undefined),
      reload: vi.fn().mockResolvedValue(undefined),
      notify: vi.fn(),
      openInfo: vi.fn(),
      openPreferences: vi.fn(),
      openModelPicker: vi.fn(),
      setPermissionMode: vi.fn(),
    }

    expect('btw' in chatCommandHandlers(baseDeps)).toBe(false)

    const handlers = chatCommandHandlers({ ...baseDeps, askBtw })
    expect(typeof handlers.btw).toBe('function')
    await handlers.btw('   Qual arquivo define essa rota?   ')
    expect(askBtw).toHaveBeenCalledWith('Qual arquivo define essa rota?')
  })

  it('blocks /compact, /review, /diff, /subtask, /loop and /plan <args> while turnBusy without clearing input or toggling plan mode', async () => {
    const fetchMock = vi.fn()
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchMock as unknown as typeof fetch })
    const notify = vi.fn()
    const setPermissionMode = vi.fn()
    const send = vi.fn().mockImplementation(async () => {
      notify('Espere o turno atual terminar para enviar.')
      return false
    })

    const { result } = renderHook(() =>
      useChatCommands({
        client,
        conversationId: 'chat_1',
        messages: [],
        title: 'Teste',
        modelId: 'claude-sonnet-5-5',
        provider: 'anthropic',
        permissionMode: 'ask_permissions',
        mode: 'code',
        workspacePath: null,
        turnBusy: true,
        navigate: vi.fn(),
        send,
        reload: vi.fn().mockResolvedValue(undefined),
        notify,
        openInfo: vi.fn(),
        openPreferences: vi.fn(),
        openModelPicker: vi.fn(),
        setPermissionMode,
      }),
    )

    expect(await result.current.run('/compact resuma')).toEqual({ kind: 'blocked' })
    expect(fetchMock).not.toHaveBeenCalled()

    expect(await result.current.run('/plan refatore pagamentos')).toEqual({ kind: 'blocked' })
    expect(setPermissionMode).not.toHaveBeenCalled()
    expect(send).not.toHaveBeenCalled()

    expect(await result.current.run('/review pagamentos')).toEqual({ kind: 'blocked' })
    expect(await result.current.run('/diff')).toEqual({ kind: 'blocked' })
    expect(await result.current.run('/subtask rode os testes')).toEqual({ kind: 'blocked' })
    expect(await result.current.run('/loop 10m rode pytest')).toEqual({ kind: 'blocked' })

    // Custom skills fall through as message so ChatPage can warn without clearing
    expect(await result.current.run('/minha-skill arg')).toEqual({ kind: 'message' })

    // Plain /plan without args can still toggle plan mode while running
    expect(await result.current.run('/plan')).toEqual({ kind: 'handled' })
    expect(setPermissionMode).toHaveBeenCalledWith('plan_mode')
  })

  it('treats HTTP 409 from /compact as blocked instead of unhandled error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'CONFLICT', message: 'turn_running', retryable: true } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchMock as unknown as typeof fetch })
    const notify = vi.fn()
    const { result } = renderHook(() =>
      useChatCommands({
        client,
        conversationId: 'chat_1',
        messages: [],
        title: 'Teste',
        modelId: 'claude-sonnet-5-5',
        provider: 'anthropic',
        permissionMode: 'ask_permissions',
        mode: 'code',
        workspacePath: null,
        turnBusy: false,
        navigate: vi.fn(),
        send: vi.fn().mockResolvedValue(undefined),
        reload: vi.fn().mockResolvedValue(undefined),
        notify,
        openInfo: vi.fn(),
        openPreferences: vi.fn(),
        openModelPicker: vi.fn(),
        setPermissionMode: vi.fn(),
      }),
    )

    expect(await result.current.run('/compact')).toEqual({ kind: 'blocked' })
    expect(notify).toHaveBeenCalledWith('Espere o turno atual terminar para compactar a conversa.')
  })
})
