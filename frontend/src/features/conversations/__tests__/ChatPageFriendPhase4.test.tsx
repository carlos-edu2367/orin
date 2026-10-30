import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../ChatPage'
import { MicCapture } from '../live/liveAudio'

const CONVERSATION_ID = 'friend_conv_phase4'

const SAMPLE_PERSONA = {
  traits: {
    warmth: 0.72,
    humor: 0.58,
    sarcasm: 0.25,
    formality: 0.3,
    verbosity: 0.4,
    candor: 0.75,
    energy: 0.6,
  },
  affinity_score: 68.5,
  bond_stage: 'close_friend',
  style_notes: ['Prefere respostas diretas'],
  current_mood: 'focado',
  soul_markdown_path: 'C:/Users/User/.orin/friend/SOUL.md',
  updated_at: '2026-10-27T15:00:00Z',
  display_name: 'Orin',
  user_nickname: 'Carlos',
  orb_theme: 'aurora',
}

function activity(sequence: number, type: string, summary: string, payload: Record<string, unknown> = {}) {
  return {
    event_id: `activity:turn-1:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-08-10T17:26:00+00:00',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: `agent:${CONVERSATION_ID}:main`,
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

function stubFetch(mode: 'friend' | 'chat' = 'friend') {
  return vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : String(input)

    if (url.includes('/events?')) {
      return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/v1/friend/persona')) {
      return new Response(JSON.stringify(SAMPLE_PERSONA), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/friend/memories')) {
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`)) {
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/projects/sidebar')) {
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/conversations')) {
      return new Response(
        JSON.stringify({
          items: [{ conversation_id: CONVERSATION_ID, title: 'Orin', state: 'completed', mode }],
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
      return new Response(
        JSON.stringify({
          conversation_id: CONVERSATION_ID,
          title: 'Orin',
          state: 'completed',
          mode,
          provider: 'openrouter',
          model_id: 'model-a',
          messages: [
            {
              message_id: 'msg-user-1',
              role: 'user',
              content: 'Como está o redesign do checkout?',
              status: 'completed',
              retryable: false,
            },
            {
              message_id: 'msg-assistant-1',
              role: 'assistant',
              content: '<speak>O fluxo de pagamento já foi validado.</speak> Quer abrir no Code?',
              status: 'completed',
              retryable: false,
              turn_id: 'turn-1',
            },
          ],
          turns: [
            {
              turn_id: 'turn-1',
              state: 'completed',
              created_at: '2026-08-10T17:26:00+00:00',
              started_at: '2026-08-10T17:26:01+00:00',
              finished_at: '2026-08-10T17:26:30+00:00',
            },
          ],
          activities: [
            activity(1, 'tool.completed', 'Progresso atualizado', {
              tool_name: 'TodoWrite',
              tool_kind: 'task',
              status: 'succeeded',
              todos: [
                { id: 'step-1', content: 'Mapear jornada de compra', status: 'completed' },
                { id: 'step-2', content: 'Refinar tela de cartão', status: 'completed' },
                { id: 'step-3', content: 'Publicar pacote final', status: 'pending' },
              ],
              delegated_code_session: {
                code_conversation_id: 'code-checkout-88',
                goal: 'Redesign do Checkout',
                status: 'running',
                completed_todo_count: 2,
                total_todo_count: 3,
              },
            }),
          ],
          activity_cursor: 'a.9',
          workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    }
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
}

function CurrentPath() {
  return <output data-testid="current-path">{useLocation().pathname}</output>
}

function renderChat(initialEntry = `/chats/${CONVERSATION_ID}`) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/chats/:conversationId" element={<ChatPage />} />
      </Routes>
      <CurrentPath />
    </MemoryRouter>,
  )
}

describe('ChatPage — Fase 4 (Integração LiveVoiceStage ref-04 + FriendSoulDrawer ref-06)', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
    vi.spyOn(MicCapture, 'open').mockResolvedValue({
      level: 0,
      deviceLabel: 'Mic Teste',
      close: vi.fn(),
    } as unknown as MicCapture)
  })

  afterEach(() => {
    cleanup()
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('passa transcriptItems, todos, delegatedCodeSession, onOpenCodeSession e userNickname para LiveVoiceStage ao abrir o modo de voz', async () => {
    const user = userEvent.setup()
    globalThis.fetch = stubFetch('chat')

    renderChat()

    expect(await screen.findByText('Como está o redesign do checkout?')).toBeInTheDocument()

    await user.click(screen.getByTestId('chat-voice-button'))

    const stage = await screen.findByTestId('live-voice-stage')
    expect(stage).toBeInTheDocument()

    // Mesmo em conversa de modo 'chat', userNickname de friendPersona é exibido quando carregado
    await waitFor(() => {
      expect(within(stage).getByTestId('live-voice-state-label')).toHaveTextContent('Estou ouvindo, Carlos.')
    })

    // Mensagens reais da conversa aparecem sanitizadas na coluna "Nesta conversa"
    const transcriptList = within(stage).getByTestId('live-voice-transcript-list')
    expect(transcriptList).toHaveTextContent('Como está o redesign do checkout?')
    expect(transcriptList).toHaveTextContent('O fluxo de pagamento já foi validado. Quer abrir no Code?')
    expect(transcriptList).not.toHaveTextContent('<speak>')

    // Card de tarefa ativa mostra dados reais de delegatedCodeSession e todos
    const taskCard = within(stage).getByTestId('live-voice-task-card')
    expect(taskCard).toHaveTextContent('Redesign do Checkout')
    expect(taskCard).toHaveTextContent('2 de 3 etapas concluídas')
    expect(taskCard).toHaveTextContent('Mapear jornada de compra')
    expect(taskCard).toHaveTextContent('Refinar tela de cartão')
    expect(taskCard).toHaveTextContent('Publicar pacote final')

    // Clicar em "Abrir no Code" fecha o palco de voz e navega para a sessão do Code
    await user.click(within(taskCard).getByTestId('live-voice-open-in-code'))
    expect(screen.queryByTestId('live-voice-stage')).not.toBeInTheDocument()
    expect(screen.getByTestId('current-path')).toHaveTextContent('/chats/code-checkout-88')
  })

  it('ao abrir FriendSoulDrawer e clicar em "Conversar por voz", fecha a personalização e abre LiveVoiceStage', async () => {
    const user = userEvent.setup()
    globalThis.fetch = stubFetch('friend')

    renderChat()

    expect(await screen.findByText('Como está o redesign do checkout?')).toBeInTheDocument()

    const personalizeBtn = screen.getByTestId('open-friend-soul-drawer-button')
    await user.click(personalizeBtn)

    const drawer = await screen.findByTestId('friend-soul-drawer')
    expect(drawer).toBeInTheDocument()

    const startVoiceBtn = within(drawer).getByTestId('friend-start-voice-button')
    await user.click(startVoiceBtn)

    expect(screen.queryByTestId('friend-soul-drawer')).not.toBeInTheDocument()
    expect(await screen.findByTestId('live-voice-stage')).toBeInTheDocument()
  })
})
