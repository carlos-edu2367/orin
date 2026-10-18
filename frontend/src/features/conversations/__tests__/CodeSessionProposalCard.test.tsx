import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { groupingKey } from '../activitySummary'
import { ChatPage } from '../ChatPage'
import { CodeSessionProposalCard } from '../CodeSessionProposalCard'
import { UserQuestionCard } from '../UserQuestionCard'
import type { CodeSessionProposalRequest } from '../activityTypes'

function LocationDisplay() {
  return <output data-testid="current-location">{useLocation().pathname}</output>
}

describe('Task 2.2: CodeSessionProposalCard & Chat -> Orin Code Handoff', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('parses code_session_proposal in parseConversationActivityEvent and isolates it in groupingKey', () => {
    const event = parseConversationActivityEvent(
      {
        event_id: 'evt-prop-1',
        event_type: 'tool.finished',
        turn_id: 'turn-prop-1',
        agent_id: 'agent:main',
        summary: 'Sugestão para iniciar no Orin Code: Refatorar Auth',
        payload: {
          tool_name: 'propose_code_session',
          tool_kind: 'code_handoff',
          status: 'succeeded',
          wait_for_user: true,
          code_session_proposal: {
            proposal_id: 'codeprop_123',
            title: 'Refatorar Auth JWT',
            prompt: 'Extrair validação de token para middleware dedicado e adicionar testes.',
            workspace_path: '/workspace/orin',
            provider: 'anthropic',
            model_id: 'claude-sonnet-4-5',
            permission_mode: 'accept_edits',
            project_id: 'proj_1',
          },
        },
      },
      'a.1',
    )

    expect(event.codeSessionProposal).toEqual({
      proposal_id: 'codeprop_123',
      title: 'Refatorar Auth JWT',
      prompt: 'Extrair validação de token para middleware dedicado e adicionar testes.',
      workspace_path: '/workspace/orin',
      provider: 'anthropic',
      model_id: 'claude-sonnet-4-5',
      permission_mode: 'accept_edits',
      project_id: 'proj_1',
    })

    expect(groupingKey(event)).toBe('approval:code-session:agent:main:turn-prop-1:codeprop_123')
  })

  it('renders editable fields and calls onAccept with edited values or onDecline when declined', async () => {
    const proposal: CodeSessionProposalRequest = {
      proposal_id: 'codeprop_456',
      title: 'Criar endpoint de relatórios',
      prompt: 'Implementar GET /v1/reports com paginação.',
      workspace_path: '/srv/app',
      provider: 'openai',
      model_id: 'gpt-5',
      permission_mode: 'accept_edits',
      project_id: null,
    }

    const onAccept = vi.fn().mockResolvedValue(undefined)
    const onDecline = vi.fn().mockResolvedValue(undefined)

    render(
      <MemoryRouter>
        <CodeSessionProposalCard
          proposal={proposal}
          active={true}
          onAccept={onAccept}
          onDecline={onDecline}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('Criar endpoint de relatórios')).toBeInTheDocument()

    const workspaceInput = screen.getByTestId('code-proposal-workspace-input') as HTMLInputElement
    const providerSelect = screen.getByTestId('code-proposal-provider-select') as HTMLSelectElement
    const modelSelect = screen.getByTestId('code-proposal-model-select') as HTMLSelectElement
    const permissionSelect = screen.getByTestId('code-proposal-permission-select') as HTMLSelectElement
    const promptInput = screen.getByTestId('code-proposal-prompt-input') as HTMLTextAreaElement

    expect(workspaceInput.value).toBe('/srv/app')
    expect(providerSelect.value).toBe('openai')
    expect(modelSelect.value).toBe('gpt-5')
    expect(permissionSelect.value).toBe('accept_edits')
    expect(promptInput.value).toBe('Implementar GET /v1/reports com paginação.')

    fireEvent.change(workspaceInput, { target: { value: '/srv/app-v2' } })
    fireEvent.change(providerSelect, { target: { value: 'anthropic' } })
    fireEvent.change(modelSelect, { target: { value: 'claude-sonnet-4-5' } })
    fireEvent.change(permissionSelect, { target: { value: 'plan_mode' } })
    fireEvent.change(promptInput, {
      target: { value: 'Implementar GET /v1/reports com filtros e testes.' },
    })

    fireEvent.click(screen.getByTestId('code-proposal-accept-button'))

    await waitFor(() => {
      expect(onAccept).toHaveBeenCalledWith({
        title: 'Criar endpoint de relatórios',
        prompt: 'Implementar GET /v1/reports com filtros e testes.',
        workspace_path: '/srv/app-v2',
        provider: 'anthropic',
        model_id: 'claude-sonnet-4-5',
        permission_mode: 'plan_mode',
        project_id: null,
      })
    })

    fireEvent.click(screen.getByTestId('code-proposal-decline-button'))
    await waitFor(() => {
      expect(onDecline).toHaveBeenCalledTimes(1)
    })
  })

  it('creates a new code session and navigates when accepted in ChatPage, and shows settled link when already launched', async () => {
    let createdPayload: Record<string, unknown> | null = null

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.2"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.endsWith('/v1/conversations') && method === 'POST') {
        createdPayload = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_new_code_99',
            title: 'Refatorar Auth JWT',
            turn_id: 'turn-code-1',
            message_id: 'msg-code-1',
            state: 'queued',
            mode: 'code',
            permission_mode: 'accept_edits',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/v1/conversations/conv_chat_1')) {
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_chat_1',
            title: 'Conversa inicial no Chat',
            state: 'waiting_user',
            mode: 'chat',
            permission_mode: 'ask_permissions',
            provider: 'openai',
            model_id: 'gpt-5',
            messages: [
              {
                message_id: 'msg-1',
                role: 'user',
                content: 'Vamos implementar isso no Orin Code',
                status: 'completed',
                retryable: false,
              },
              {
                message_id: 'msg-2',
                role: 'assistant',
                content: 'Preparei a proposta para abrir no Orin Code.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-1',
                state: 'waiting_user',
                created_at: '2026-10-16T12:00:00Z',
                started_at: '2026-10-16T12:00:01Z',
                finished_at: null,
                code_mode: null,
              },
            ],
            activities: [
              {
                event_id: 'evt-prop-99',
                event_type: 'tool.finished',
                sequence: 1,
                turn_id: 'turn-1',
                agent_id: 'agent:main',
                summary: 'Sugestão para iniciar no Orin Code',
                cursor: 'a.1',
                payload: {
                  tool_name: 'propose_code_session',
                  tool_kind: 'code_handoff',
                  status: 'succeeded',
                  code_session_proposal: {
                    proposal_id: 'codeprop_launch_99',
                    title: 'Refatorar Auth JWT',
                    prompt: 'Implemente a refatoração completa do módulo JWT.',
                    workspace_path: '/repo/backend',
                    provider: 'openai',
                    model_id: 'gpt-5',
                    permission_mode: 'accept_edits',
                    project_id: null,
                  },
                },
              },
            ],
            activity_cursor: 'a.1',
            workspace: {
              kind: 'local',
              path: '/repo/backend',
              folder_name: 'backend',
              scope: 'chat',
              project_name: null,
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={['/chats/conv_chat_1']}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
        <LocationDisplay />
      </MemoryRouter>,
    )

    const acceptBtn = await screen.findByTestId('code-proposal-accept-button')
    fireEvent.click(acceptBtn)

    await waitFor(() => {
      expect(createdPayload).toEqual(
        expect.objectContaining({
          message: 'Implemente a refatoração completa do módulo JWT.',
          selection: { provider: 'openai', model_id: 'gpt-5' },
          workspace_path: '/repo/backend',
          workspace_acknowledged_risk: true,
          code_mode: 'code',
          permission_mode: 'accept_edits',
        }),
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent('/chats/conv_new_code_99')
    })

    const stored = JSON.parse(
      localStorage.getItem('orin.launched-code-session.codeprop_launch_99') ?? '{}',
    ) as { conversation_id?: string }
    expect(stored.conversation_id).toBe('conv_new_code_99')
  })

  it('ignores placeholder model_id="default", inherits conversation provider/model, and shows folder picker in ChatPage', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.2"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/v1/providers/openrouter/models')) {
        return new Response(
          JSON.stringify({
            items: [
              {
                provider: 'openrouter',
                model_id: 'qwen/qwen3.7-flash',
                display_name: 'Qwen3.7 Flash',
                context_window: 128000,
                capabilities: ['tools'],
                input_modalities: ['text'],
                output_modalities: ['text'],
                pricing: null,
                is_favorite: true,
                is_custom: false,
                refreshed_at: null,
                route_kind: 'model',
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes('/v1/conversations/conv_openrouter_1')) {
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_openrouter_1',
            title: 'Landing Page',
            state: 'waiting_user',
            mode: 'chat',
            permission_mode: 'ask_permissions',
            provider: 'openrouter',
            model_id: 'qwen/qwen3.7-flash',
            messages: [
              {
                message_id: 'msg-1',
                role: 'user',
                content: 'Crie uma landing page',
                status: 'completed',
                retryable: false,
              },
              {
                message_id: 'msg-2',
                role: 'assistant',
                content: 'Preparei a proposta.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-1',
                state: 'waiting_user',
                created_at: '2026-10-16T12:00:00Z',
                started_at: '2026-10-16T12:00:01Z',
                finished_at: null,
                code_mode: null,
              },
            ],
            activities: [
              {
                event_id: 'evt-prop-default',
                event_type: 'tool.finished',
                sequence: 1,
                turn_id: 'turn-1',
                agent_id: 'agent:main',
                summary: 'Sugestão para iniciar no Orin Code',
                cursor: 'a.1',
                payload: {
                  tool_name: 'propose_code_session',
                  tool_kind: 'code_handoff',
                  status: 'succeeded',
                  code_session_proposal: {
                    proposal_id: 'codeprop_default_1',
                    title: 'Landing Page — Curso de IA',
                    prompt: 'Construir landing page completa em React + Tailwind.',
                    workspace_path: null,
                    provider: 'openai',
                    model_id: 'default',
                    permission_mode: 'accept_edits',
                    project_id: null,
                  },
                },
              },
            ],
            activity_cursor: 'a.1',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={['/chats/conv_openrouter_1']}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const providerSelect = (await screen.findByTestId('code-proposal-provider-select')) as HTMLSelectElement
    const modelSelect = screen.getByTestId('code-proposal-model-select') as HTMLSelectElement

    await waitFor(() => {
      expect(providerSelect.value).toBe('openrouter')
      expect(modelSelect.value).toBe('qwen/qwen3.7-flash')
    })

    // Friendly provider option label and folder picker button are present
    expect(screen.getByRole('option', { name: 'OpenRouter' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Escolher pasta/i })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'default' })).toBeNull()
  })

  it('collapses the optional note textarea by default on choice questions in UserQuestionCard and expands on click', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(
      <UserQuestionCard
        active={true}
        onSubmit={onSubmit}
        questions={[
          {
            id: 'q1',
            question: 'Qual estética visual você prefere?',
            mode: 'single_choice',
            options: [
              { id: 'dark', label: 'Dark Tech Editorial' },
              { id: 'light', label: 'Minimalista Claro' },
            ],
            placeholder: 'Alguma referência extra?',
          },
        ]}
      />,
    )

    // Note textarea is hidden by default for single_choice questions
    expect(screen.queryByPlaceholderText('Alguma referência extra?')).toBeNull()

    const toggleBtn = screen.getByTestId('user-question-note-toggle')
    expect(toggleBtn).toHaveTextContent(/\+ Adicionar observação \(opcional\)/i)

    fireEvent.click(toggleBtn)
    const noteTextarea = screen.getByPlaceholderText('Alguma referência extra?') as HTMLTextAreaElement
    expect(noteTextarea).toBeInTheDocument()

    fireEvent.change(noteTextarea, { target: { value: 'Usar tipografia mono nos dados' } })
    fireEvent.click(screen.getByLabelText('Dark Tech Editorial'))
    fireEvent.click(screen.getByRole('button', { name: /Enviar respostas/i }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith([
        { id: 'q1', selected: ['dark'], note: 'Usar tipografia mono nos dados' },
      ])
    })
  })
})

