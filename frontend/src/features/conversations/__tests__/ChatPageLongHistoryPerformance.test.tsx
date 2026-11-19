import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'chat_friend_long'

function makeTurn(index: number) {
  return {
    turn_id: `turn-${index}`,
    state: 'completed',
    created_at: `2026-11-18T10:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
    started_at: `2026-11-18T10:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
    finished_at: `2026-11-18T10:${String(Math.floor(index / 60)).padStart(2, '0')}:${String((index % 60) + 1 > 59 ? 59 : (index % 60) + 1).padStart(2, '0')}Z`,
    scheduled_by_schedule_id: null,
    code_mode: null,
  }
}

function makeMessagesForTurn(index: number) {
  const userSeq = index * 2 + 1
  const assistantSeq = index * 2 + 2
  return [
    {
      message_id: `u-${index}`,
      sequence: userSeq,
      role: 'user',
      content: `Mensagem Discord #${index}`,
      status: 'completed',
      retryable: false,
      attachments: [],
      command: null,
      turn_id: `turn-${index}`,
    },
    {
      message_id: `a-${index}`,
      sequence: assistantSeq,
      role: 'assistant',
      content: `Resposta do Orin Friend #${index} com **markdown** e \`código\`.`,
      status: 'completed',
      retryable: false,
      attachments: [],
      command: null,
      turn_id: `turn-${index}`,
    },
  ]
}

describe('ChatPage long history windowing & on-demand pagination', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('windows initial DOM nodes to 40 messages, paginates older messages on demand, and avoids runaway older-activity loops', async () => {
    const user = userEvent.setup()
    // 60 turns = 120 messages total; initial snapshot returns latest 50 (seq 71..120 -> turns 35..59)
    const allTurns = Array.from({ length: 60 }, (_, idx) => makeTurn(idx))
    const allMessages = Array.from({ length: 60 }, (_, idx) => makeMessagesForTurn(idx)).flat()

    let activityPageCalls = 0
    const messagePageQueries: string[] = []

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()

      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.120"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/activities`)) {
        activityPageCalls += 1
        return new Response(
          JSON.stringify({
            activities: [],
            has_older_activities: true,
            oldest_activity_id: Math.max(1, 500 - activityPageCalls),
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/messages?`) && method === 'GET') {
        messagePageQueries.push(url)
        const parsed = new URL(url, 'http://localhost')
        const beforeSeq = Number(parsed.searchParams.get('before_sequence') ?? '0')
        const limit = Number(parsed.searchParams.get('limit') ?? '40')
        const matching = allMessages.filter((m) => m.sequence < beforeSeq)
        const slice = matching.slice(-limit)
        const hasOlder = matching.length > limit
        const oldestSeq = hasOlder && slice.length > 0 ? slice[0].sequence : null
        const turnIds = new Set(slice.map((m) => m.turn_id))
        return new Response(
          JSON.stringify({
            messages: slice,
            turns: allTurns.filter((t) => turnIds.has(t.turn_id)),
            has_older_messages: hasOlder,
            oldest_message_sequence: oldestSeq,
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/queue`)) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/v1/friend/persona')) {
        return new Response(
          JSON.stringify({
            display_name: 'Orin',
            traits: { warmth: 0.8, humor: 0.6, directness: 0.7, curiosity: 0.7, expressiveness: 0.6 },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        const initialSlice = allMessages.slice(-50)
        return new Response(
          JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: 'Orin Friend',
            state: 'completed',
            mode: 'friend',
            provider: 'openrouter',
            model_id: 'model-friend',
            messages: initialSlice,
            has_older_messages: true,
            oldest_message_sequence: initialSlice[0].sequence,
            turns: allTurns.slice(-25),
            activities: [],
            activity_cursor: 'a.120',
            has_older_activities: true,
            oldest_activity_id: 500,
            workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    render(
      <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Latest message is rendered, while older messages outside the 40-message window are NOT mounted
    expect(await screen.findByText('Mensagem Discord #59')).toBeInTheDocument()
    expect(document.querySelectorAll('.bubble').length).toBe(40)
    expect(screen.queryByText('Mensagem Discord #35')).not.toBeInTheDocument()
    expect(screen.queryByText('Mensagem Discord #10')).not.toBeInTheDocument()

    // Must never auto-loop older activities on mount
    expect(activityPageCalls).toBe(0)

    // First click reveals the remaining 10 in-memory messages from the 50-message snapshot without network fetch
    const loadOlderBtn = screen.getByTestId('load-older-messages')
    await user.click(loadOlderBtn)
    expect(await screen.findByText('Mensagem Discord #35')).toBeInTheDocument()
    expect(document.querySelectorAll('.bubble').length).toBe(50)
    expect(messagePageQueries.length).toBe(0)

    // Second click fetches a 40-message older page from /v1/conversations/{id}/messages?before_sequence=71
    await user.click(screen.getByTestId('load-older-messages'))
    await waitFor(() => {
      expect(messagePageQueries.length).toBe(1)
    })
    expect(messagePageQueries[0]).toContain('before_sequence=71')
    expect(await screen.findByText('Mensagem Discord #15')).toBeInTheDocument()
    expect(document.querySelectorAll('.bubble').length).toBe(90)
    // Latest message remains intact
    expect(screen.getByText('Mensagem Discord #59')).toBeInTheDocument()
  })
})
