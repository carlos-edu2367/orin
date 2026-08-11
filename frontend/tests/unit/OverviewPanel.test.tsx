import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { OverviewPanel } from '../../src/features/overview/OverviewPanel'

vi.mock('../../src/features/overview/OrbitalScene', () => ({
  OrbitalScene: () => <div data-testid="orbital-scene" />,
}))

describe('OverviewPanel', () => {
  it('reveals a selected subagent model and token usage while keeping the total in the overview', async () => {
    const user = userEvent.setup()
    render(<OverviewPanel conversationId="chat-1" client={client()} liveEvents={[]} onClose={vi.fn()} />)

    expect(await screen.findByText('36 tokens')).toBeInTheDocument()
    expect(screen.queryByText('anthropic/claude-sonnet-4')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Researcher/ }))

    const detail = screen.getByRole('region', { name: 'Detalhes de Researcher' })
    expect(within(detail).getByText('openrouter')).toBeInTheDocument()
    expect(within(detail).getByText('anthropic/claude-sonnet-4')).toBeInTheDocument()
    expect(within(detail).getByText('18 tokens')).toBeInTheDocument()
    expect(within(detail).getByText('11 tokens')).toBeInTheDocument()
    expect(within(detail).getByText('7 tokens')).toBeInTheDocument()
  })
})

function client(): ApiClient {
  return new ApiClient({
    fetchImpl: vi.fn<typeof fetch>(() => Promise.resolve(json({
      conversation_id: 'chat-1', title: 'Pesquisa', state: 'completed', provider: 'openai', model_id: 'gpt-5',
      agents: [
        {
          agent_id: 'agent:chat-1:main', name: 'Main', role: 'Agente principal', parent_agent_id: null,
          provider: 'openai', model_id: 'gpt-5', state: 'completed',
          token_usage: { input_tokens: 13, output_tokens: 5, total_tokens: 18, usage_reported: true },
        },
        {
          agent_id: 'agent:chat-1:researcher', name: 'Researcher', role: 'Pesquisa', parent_agent_id: 'agent:chat-1:main',
          provider: 'openrouter', model_id: 'anthropic/claude-sonnet-4', state: 'completed',
          token_usage: { input_tokens: 11, output_tokens: 7, total_tokens: 18, usage_reported: true },
        },
      ],
      tools: [], messages: [], errors: [], turns: [], activity_count: 4, duration_seconds: 2.1,
      token_usage: { input_tokens: 24, output_tokens: 12, total_tokens: 36, usage_reported: true },
    }))),
    maxAttempts: 1,
  })
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
