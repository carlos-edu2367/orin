import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_bancada_preview'

function activity(sequence: number, type: string, summary: string, payload: Record<string, unknown> = {}) {
  return {
    event_id: `ev:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-11-07T10:00:00.000Z',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: 'agent:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

type Scenario = { activities: ReturnType<typeof activity>[]; live: ReturnType<typeof activity>[]; idle?: boolean }

function snapshot(scenario: Scenario) {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Landing page',
    state: scenario.idle ? 'completed' : 'running',
    mode: 'code',
    permission_mode: 'ask_permissions',
    provider: 'openai',
    model_id: 'gpt-5',
    messages: [
      { message_id: 'msg-1', role: 'user', content: 'Crie a landing page e me mostre.', status: 'completed', retryable: false, turn_id: 'turn-1' },
      { message_id: 'msg-2', role: 'assistant', content: '', status: 'streaming', retryable: false, turn_id: 'turn-1' },
    ],
    turns: [
      { turn_id: 'turn-1', state: scenario.idle ? 'completed' : 'running', code_mode: 'code', created_at: '2026-11-07T10:00:00.000Z', started_at: '2026-11-07T10:00:00.000Z', finished_at: scenario.idle ? '2026-11-07T10:05:00.000Z' : null },
    ],
    activities: scenario.activities,
    activity_cursor: scenario.activities.at(-1)?.cursor ?? 'a.0',
    workspace: { kind: 'local', path: 'C:/Projetos/landing', folder_name: 'landing', scope: 'chat', project_name: null },
  }
}

function installFetch(scenario: Scenario) {
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : String(input)
    if (url.includes('/events?')) {
      const live = scenario.live
      scenario.activities = [...scenario.activities, ...live]
      scenario.live = []
      const records = live.map((item) => `id: ${item.cursor}\ndata: ${JSON.stringify(item)}\n\n`).join('')
      return new Response(`${records}event: heartbeat\ndata: {"cursor":"${scenario.activities.at(-1)?.cursor ?? 'a.0'}"}\n\n`, {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
      return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`) && !url.includes('/v1/conversations/' + CONVERSATION_ID + '/')) {
      return new Response(JSON.stringify(snapshot(scenario)), { headers: { 'Content-Type': 'application/json' } })
    }
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
}

function renderChat() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('ChatPage: the agent opens the preview in the Bancada', () => {
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

  it('opens the closed Bancada on the Preview tab when a live PreviewOpen result arrives', async () => {
    const scenario: Scenario = {
      activities: [
        activity(1, 'tool.finished', 'Editou src/App.tsx', {
          tool_name: 'write_file', tool_kind: 'filesystem', status: 'succeeded', path: 'C:\\Projetos\\landing\\src\\App.tsx', added_lines: 4, removed_lines: 0,
        }),
      ],
      live: [
        activity(2, 'tool.finished', 'Prévia aberta na Bancada: http://localhost:5173/', {
          tool_name: 'preview_open', tool_kind: 'terminal', status: 'succeeded',
          preview: { url: 'http://localhost:5173/', name: 'localhost:5173', status: 'running', ready: true },
        }),
      ],
    }
    installFetch(scenario)
    renderChat()

    const panel = await screen.findByTestId('code-workbench-panel', {}, { timeout: 4000 })
    expect(panel).toBeInTheDocument()
    expect(screen.getByTestId('workbench-tab-preview')).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => {
      expect(screen.getByTestId('workbench-preview-iframe')).toHaveAttribute('src', 'http://localhost:5173/')
    })
    // The absolute Windows path was made workspace-relative (one file, not two).
    expect(screen.getByTestId('code-workbench-toggle')).toHaveTextContent('1')
  })

  it('does not pop the Bancada open for a preview that is already in the loaded history of an idle turn', async () => {
    const scenario: Scenario = {
      activities: [
        activity(1, 'tool.finished', 'Prévia aberta', {
          tool_name: 'preview_open', tool_kind: 'terminal', status: 'succeeded',
          preview: { url: 'http://localhost:5173/', name: 'localhost:5173', status: 'running' },
        }),
      ],
      live: [],
      idle: true,
    }
    installFetch(scenario)
    renderChat()
    expect(await screen.findByTestId('code-workbench-toggle')).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(screen.queryByTestId('code-workbench-panel')).toBeNull()
  })
})
