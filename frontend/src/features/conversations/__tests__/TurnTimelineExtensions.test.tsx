import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../ChatPage'
import { TurnTimeline } from '../TurnTimeline'
import type { ConversationActivityEvent } from '../activityTypes'
import type { TimelineRenderItem } from '../TurnTimeline'

describe('Task 1.2: TurnTimeline & ChatPage Extension Approval Cards in Orin Code', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('renders interactive McpApprovalCard and PluginApprovalCard inside a code-mode timeline group alongside CodeToolCallBlock', async () => {
    const onMcpApprove = vi.fn().mockResolvedValue(undefined)
    const onMcpDecline = vi.fn().mockResolvedValue(undefined)
    const onPluginApprove = vi.fn().mockResolvedValue(undefined)
    const onPluginDecline = vi.fn().mockResolvedValue(undefined)

    const readEvent: ConversationActivityEvent = {
      eventId: 'evt-read-1',
      cursor: '1',
      turnId: 'turn-code-ext',
      type: 'tool.finished',
      kind: 'tool',
      state: 'completed',
      status: 'succeeded',
      agentId: 'agent:main',
      toolName: 'read_file',
      path: 'package.json',
      summary: 'Leu package.json',
    }

    const mcpEvent: ConversationActivityEvent = {
      eventId: 'evt-mcp-1',
      cursor: '2',
      turnId: 'turn-code-ext',
      type: 'tool.finished',
      kind: 'tool',
      state: 'waiting_user',
      status: 'succeeded',
      agentId: 'agent:main',
      toolName: 'configure_mcp',
      summary: 'Configurar MCP GitHub',
      mcpApproval: {
        server_id: 'srv_github',
        display_name: 'GitHub MCP',
        transport: 'stdio',
        secret_names: ['GITHUB_TOKEN'],
        catalog_id: 'github',
      },
    }

    const pluginEvent: ConversationActivityEvent = {
      eventId: 'evt-plugin-1',
      cursor: '3',
      turnId: 'turn-code-ext',
      type: 'tool.finished',
      kind: 'tool',
      state: 'waiting_user',
      status: 'succeeded',
      agentId: 'agent:main',
      toolName: 'install_plugin',
      summary: 'Instalar plugin Superpowers',
      pluginApproval: {
        plugin_id: 'plg_superpowers',
        version: '1.2.0',
        display_name: 'Superpowers',
        description: 'Workflows TDD',
        author: 'Orin',
        warnings: [],
        skills: [{ skill_id: 'sk_tdd', name: 'TDD' }],
        mcp_servers: [],
        agents: [],
        commands: [],
        hooks: [],
        contribution_count: 1,
      },
    }

    const items: TimelineRenderItem[] = [
      {
        id: 'code-mode:turn-code-ext',
        kind: 'activity',
        group: {
          id: 'code-mode:turn-code-ext',
          kind: 'lifecycle',
          state: 'waiting_user',
          label: 'Modo Code',
          count: 3,
          agentId: 'agent:main',
          failed: false,
          events: [readEvent, mcpEvent, pluginEvent],
        },
      },
    ]

    render(
      <TurnTimeline
        items={items}
        mode="code"
        openQuestionTurnIds={new Set(['turn-code-ext'])}
        onMcpApprove={onMcpApprove}
        onMcpDecline={onMcpDecline}
        onPluginApprove={onPluginApprove}
        onPluginDecline={onPluginDecline}
      />,
    )

    // CodeToolCallBlock is rendered for read_file
    expect(screen.getByTestId('code-tool-call-block')).toBeInTheDocument()

    // McpApprovalCard is rendered and interactive
    expect(screen.getByRole('region', { name: 'Conectar GitHub MCP' })).toBeInTheDocument()
    const tokenInput = screen.getByLabelText('GITHUB_TOKEN')
    fireEvent.change(tokenInput, { target: { value: 'ghp_secret123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))

    await waitFor(() => {
      expect(onMcpApprove).toHaveBeenCalledWith(mcpEvent, { GITHUB_TOKEN: 'ghp_secret123' })
    })

    // PluginApprovalCard is rendered and interactive
    expect(screen.getByRole('region', { name: 'Instalar Superpowers' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Instalar' }))

    await waitFor(() => {
      expect(onPluginApprove).toHaveBeenCalledWith(pluginEvent)
    })
  })

  it('keeps McpApprovalCard active via openQuestionTurnIds in ChatPage when a code turn emits mcpApproval', async () => {
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
      if (url.includes('/v1/conversations/conv_code_mcp')) {
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_code_mcp',
            title: 'Sessão Code MCP',
            state: 'running',
            mode: 'code',
            permission_mode: 'accept_edits',
            provider: 'openai',
            model_id: 'gpt-5',
            messages: [
              {
                message_id: 'msg-user-1',
                role: 'user',
                content: 'Configure o MCP do Postgres',
                status: 'completed',
                retryable: false,
              },
              {
                message_id: 'msg-asst-1',
                role: 'assistant',
                content: 'Vou configurar o servidor MCP do Postgres.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-mcp-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-mcp-1',
                state: 'running',
                created_at: '2026-10-16T12:00:00Z',
                started_at: '2026-10-16T12:00:01Z',
                finished_at: null,
                code_mode: 'code',
              },
            ],
            activities: [
              {
                event_id: 'evt-mcp-code-1',
                event_type: 'tool.finished',
                sequence: 1,
                turn_id: 'turn-mcp-1',
                agent_id: 'agent:main',
                summary: 'Configurar Postgres MCP',
                cursor: 'a.1',
                payload: {
                  tool_name: 'configure_mcp',
                  tool_kind: 'mcp',
                  status: 'succeeded',
                  mcp_approval: true,
                  server: {
                    server_id: 'srv_pg',
                    display_name: 'Postgres MCP',
                    transport: 'stdio',
                    secret_names: [],
                    catalog_id: 'postgres',
                  },
                },
              },
            ],
            activity_cursor: 'a.1',
            workspace: {
              kind: 'local',
              path: '/repo/app',
              folder_name: 'app',
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
      <MemoryRouter initialEntries={['/chats/conv_code_mcp']}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const card = await screen.findByRole('region', { name: 'Conectar Postgres MCP' })
    expect(card).toHaveAttribute('data-active', 'true')
    expect(screen.getByRole('button', { name: 'Conectar' })).not.toBeDisabled()
  })
})
