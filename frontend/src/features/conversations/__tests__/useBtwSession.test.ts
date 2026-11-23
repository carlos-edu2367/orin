import { act, fireEvent, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { KeybindingsProvider } from '../../keybindings/engine'
import { useBtwChatControls, useBtwSession } from '../useBtwSession'

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('useBtwSession', () => {
  it('streams start, waiting, thinking, delta and done events into state and sends CSRF on POST', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: start\ndata: {"btw_id":"btw_1","provider":"anthropic","model_id":"claude-sonnet-5-5","effort":"low","active_turn_included":true,"context_messages":4}\n\n',
        'event: waiting\ndata: {"position":1,"limit":2,"reason":"limit"}\n\n',
        'event: thinking\ndata: {"delta":"analisando"}\n\n',
        'event: delta\ndata: {"delta":"Fica em "}\n\n',
        'event: delta\ndata: {"delta":"`routes.py`."}\n\n',
        'event: done\ndata: {"btw_id":"btw_1","text":"Fica em `routes.py`.","duration_ms":850,"provider":"anthropic","model_id":"claude-sonnet-5-5","effort":"low","usage":{"input_tokens":100,"output_tokens":12,"cached_input_tokens":50}}\n\n',
      ]),
    )
    const client = new ApiClient({
      baseUrl: 'http://localhost',
      csrfToken: 'csrf-token-123',
      fetchImpl: fetchMock as unknown as typeof fetch,
    })

    const { result } = renderHook(() => useBtwSession(client, 'chat_abc'))
    expect(result.current.state).toBeNull()

    await act(async () => {
      await result.current.ask('Onde está a rota?', { provider: 'anthropic', modelId: 'claude-sonnet-5-5' })
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, Parameters<typeof fetch>[1]]
    expect(url).toBe('http://localhost/v1/conversations/chat_abc/btw')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Headers).get('X-CSRF-Token')).toBe('csrf-token-123')
    expect(JSON.parse(String(init?.body))).toEqual({
      question: 'Onde está a rota?',
      provider: 'anthropic',
      model_id: 'claude-sonnet-5-5',
    })

    expect(result.current.state?.status).toBe('completed')
    expect(result.current.state?.answer).toBe('Fica em `routes.py`.')
    expect(result.current.state?.thinking).toBe('analisando')
    expect(result.current.state?.activeTurnIncluded).toBe(true)
    expect(result.current.state?.durationMs).toBe(850)
    expect(result.current.state?.usage?.outputTokens).toBe(12)
  })

  it('aborts cleanly on stop, resets on conversation change, and supports minimize/expand', async () => {
    let capturedSignal: AbortSignal | undefined
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: Parameters<typeof fetch>[1]) => {
      capturedSignal = init?.signal ?? undefined
      return new Promise<Response>((_resolve, reject) => {
        capturedSignal?.addEventListener('abort', () => {
          reject(new DOMException('The request was aborted', 'AbortError'))
        })
      })
    })
    const client = new ApiClient({
      baseUrl: 'http://localhost',
      fetchImpl: fetchMock as unknown as typeof fetch,
    })

    const { result, rerender } = renderHook(
      ({ convId }) => useBtwSession(client, convId),
      { initialProps: { convId: 'chat_1' } },
    )

    act(() => {
      void result.current.ask('Pergunta longa')
    })
    expect(result.current.state?.status).toBe('connecting')
    expect(result.current.isStreaming).toBe(true)

    act(() => {
      result.current.stop()
    })
    expect(capturedSignal?.aborted).toBe(true)
    expect(result.current.state?.status).toBe('cancelled')
    expect(result.current.isStreaming).toBe(false)

    act(() => {
      result.current.toggleMinimized()
    })
    expect(result.current.state?.minimized).toBe(true)
    expect(result.current.isOpen).toBe(false)

    act(() => {
      result.current.toggleExpanded()
    })
    expect(result.current.state?.expanded).toBe(true)
    expect(result.current.state?.minimized).toBe(false)
    expect(result.current.isOpen).toBe(true)

    // Switching conversation dismisses the ephemeral card
    rerender({ convId: 'chat_2' })
    expect(result.current.state).toBeNull()
  })

  it('marks truncated SSE stream with partial text as retryable error instead of completed', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: start\ndata: {"btw_id":"btw_cut","provider":"anthropic","model_id":"claude-sonnet-5-5","effort":"low","active_turn_included":false,"context_messages":2}\n\n',
        'event: delta\ndata: {"delta":"Resposta incompleta..."}\n\n',
      ]),
    )
    const client = new ApiClient({
      baseUrl: 'http://localhost',
      fetchImpl: fetchMock as unknown as typeof fetch,
    })

    const { result } = renderHook(() => useBtwSession(client, 'chat_cut'))
    await act(async () => {
      await result.current.ask('Teste corte')
    })

    expect(result.current.state?.status).toBe('error')
    expect(result.current.state?.answer).toBe('Resposta incompleta...')
    expect(result.current.state?.error?.retryable).toBe(true)
  })

  it('triggers onUnauthenticated on 401 and surfaces 401/403/429 retryability accurately', async () => {
    const onUnauthenticated = vi.fn()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'expired', retryable: false } }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: 'FORBIDDEN', message: 'csrf', retryable: false } }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: 'RATE_LIMITED', message: 'busy', retryable: true } }), {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const client = new ApiClient({
      baseUrl: 'http://localhost',
      onUnauthenticated,
      fetchImpl: fetchMock as unknown as typeof fetch,
    })

    const { result } = renderHook(() => useBtwSession(client, 'chat_auth'))

    await act(async () => {
      await result.current.ask('Teste 401')
    })
    expect(onUnauthenticated).toHaveBeenCalledTimes(1)
    expect(result.current.state?.status).toBe('error')
    expect(result.current.state?.error?.retryable).toBe(false)
    expect(result.current.state?.error?.message).toMatch(/sessão expirou/i)

    await act(async () => {
      await result.current.ask('Teste 403')
    })
    expect(result.current.state?.error?.retryable).toBe(false)
    expect(result.current.state?.error?.message).toMatch(/Acesso recusado/i)

    await act(async () => {
      await result.current.ask('Teste 429')
    })
    expect(result.current.state?.error?.retryable).toBe(true)
    expect(result.current.state?.error?.message).toMatch(/Muitas perguntas/i)
  })

  it('handles macOS Option+B (key="∫", code="KeyB"), ignores other inputs, respects escapeBlocked, and preserves projectId in /fork', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          conversation_id: 'chat_forked',
          title: 'Projeto · btw',
          prompt: null,
          messages: 4,
          project_id: 'proj_99',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = new ApiClient({
      baseUrl: 'http://localhost',
      fetchImpl: fetchMock as unknown as typeof fetch,
    })
    const dismiss = vi.fn()
    const setMinimized = vi.fn()
    const setMessage = vi.fn()
    const focusComposer = vi.fn()
    const navigate = vi.fn()
    const notify = vi.fn()

    const wrapper = ({ children }: { children: ReactNode }) => createElement(KeybindingsProvider, null, children)

    const { result, rerender } = renderHook(
      ({ escapeBlocked }: { escapeBlocked: boolean }) =>
        useBtwChatControls({
          btw: {
            state: null,
            isOpen: true,
            isStreaming: false,
            ask: vi.fn(),
            stop: vi.fn(),
            retry: vi.fn(),
            dismiss,
            toggleMinimized: vi.fn(),
            setMinimized,
            toggleExpanded: vi.fn(),
          },
          client,
          conversationId: 'chat_1',
          conversationTitle: 'Projeto',
          projectId: 'proj_99',
          escapeBlocked,
          setMessage,
          focusComposer,
          notify,
          navigate,
        }),
      { initialProps: { escapeBlocked: true }, wrapper },
    )

    // macOS Option+B produces key='∫' with code='KeyB'
    fireEvent.keyDown(window, { key: '∫', code: 'KeyB', altKey: true })
    expect(setMessage).toHaveBeenCalledTimes(1)
    expect(focusComposer).toHaveBeenCalledTimes(1)

    // Alt+B inside another input must not hijack focus
    const otherInput = document.createElement('input')
    document.body.appendChild(otherInput)
    try {
      fireEvent.keyDown(otherInput, { key: 'b', code: 'KeyB', altKey: true })
      expect(setMessage).toHaveBeenCalledTimes(1)
    } finally {
      document.body.removeChild(otherInput)
    }

    // Escape while escapeBlocked=true must not dismiss /btw
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(dismiss).not.toHaveBeenCalled()

    // Escape once overlay clears dismisses /btw
    rerender({ escapeBlocked: false })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(dismiss).toHaveBeenCalledTimes(1)

    // continueBtwInFork keeps /projects/:projectId/chats/:conversationId route
    await act(async () => {
      await result.current.continueBtwInFork('Qual rota?', 'Fica em `/v1/btw`.')
    })
    expect(navigate).toHaveBeenCalledWith('/projects/proj_99/chats/chat_forked')
  })
})
