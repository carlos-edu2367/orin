import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActivityCard } from '../../src/features/conversations/ActivityCard'
import type { ActivityGroup } from '../../src/features/conversations/activityTypes'

function group(overrides: Partial<ActivityGroup>): ActivityGroup {
  return {
    id: 'tool:agent:turn-1', kind: 'tool', state: 'completed', label: 'Escreveu b.ts', count: 4,
    agentId: 'agent:main', failed: false,
    events: [
      { eventId: 'e1', cursor: 'a.1', type: 'tool.finished', kind: 'tool', state: 'completed', agentId: 'agent:main', toolName: 'write_file', toolKind: 'filesystem', summary: 'Escreveu a.tsx' },
      { eventId: 'e2', cursor: 'a.2', type: 'artifact.created', kind: 'artifact', state: 'completed', agentId: 'agent:main', label: 'a.tsx', path: 'a.tsx', summary: 'Criou a.tsx' },
      { eventId: 'e3', cursor: 'a.3', type: 'tool.finished', kind: 'tool', state: 'completed', agentId: 'agent:main', toolName: 'write_file', toolKind: 'filesystem', summary: 'Escreveu b.ts' },
      { eventId: 'e4', cursor: 'a.4', type: 'artifact.created', kind: 'artifact', state: 'completed', agentId: 'agent:main', label: 'b.ts', path: 'b.ts', summary: 'Criou b.ts' },
    ],
    ...overrides,
  }
}

describe('ActivityCard', () => {
  it('shows the batch label without a redundant count badge for a tool group', () => {
    render(<ActivityCard group={group({})} />)

    expect(screen.getByText('Escreveu b.ts')).toBeInTheDocument()
    // The count is only ever stated once: either in the label ("N ações", see
    // activitySummary) or, for a single real action, not at all. A visible
    // "4 ações" badge here would repeat or contradict that.
    expect(screen.queryByText(/ações$/)).not.toBeInTheDocument()
  })

  it('still shows the count badge for a non-tool group', () => {
    render(<ActivityCard group={group({
      kind: 'agent',
      events: [
        { eventId: 'e1', cursor: 'a.1', type: 'agent.message_sent', kind: 'agent', state: 'waiting_agent', agentId: 'agent:main', summary: 'Enviou uma tarefa' },
        { eventId: 'e2', cursor: 'a.2', type: 'agent.message_sent', kind: 'agent', state: 'waiting_agent', agentId: 'agent:main', summary: 'Enviou outra tarefa' },
      ],
      count: 2,
    })} />)

    expect(screen.getByText('2 ações')).toBeInTheDocument()
  })

  it('expanding the batch lets a produced file be opened for preview', () => {
    const onPreview = vi.fn()
    render(<ActivityCard group={group({})} conversationId="conversation-1" onPreview={onPreview} />)

    fireEvent.click(screen.getByText('Escreveu b.ts'))
    fireEvent.click(screen.getByRole('button', { name: 'Criou b.ts' }))

    expect(onPreview).toHaveBeenCalledWith({ conversationId: 'conversation-1', path: 'b.ts' })
  })

  it('renders an artifact row as plain text when there is nothing to preview with', () => {
    render(<ActivityCard group={group({})} />)

    fireEvent.click(screen.getByText('Escreveu b.ts'))

    expect(screen.getByText('Criou b.ts')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Criou b.ts' })).not.toBeInTheDocument()
  })
})
