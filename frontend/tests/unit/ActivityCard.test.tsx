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

    // Settled, the line summarises the whole batch instead of repeating its last call.
    expect(screen.getByText('Editou 2 arquivos')).toBeInTheDocument()
    // The count is only ever stated once, inside that sentence. A visible
    // "4 ações" badge here would repeat or contradict it.
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

    fireEvent.click(screen.getByText('Editou 2 arquivos'))
    fireEvent.click(screen.getByRole('button', { name: 'Criou b.ts' }))

    expect(onPreview).toHaveBeenCalledWith({ conversationId: 'conversation-1', path: 'b.ts' })
  })

  it('renders an artifact row as plain text when there is nothing to preview with', () => {
    render(<ActivityCard group={group({})} />)

    fireEvent.click(screen.getByText('Editou 2 arquivos'))

    expect(screen.getByText('Criou b.ts')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Criou b.ts' })).not.toBeInTheDocument()
  })

  it('narrates a settled mixed batch as one sentence', () => {
    const call = (id: string, toolName: string, toolKind: string, summary: string) =>
      ({ eventId: id, cursor: id, type: 'tool.finished', kind: 'tool' as const, state: 'completed' as const, agentId: 'agent:main', toolName, toolKind, summary })
    render(<ActivityCard group={group({
      label: 'Executou os testes',
      events: [
        call('1', 'read_file', 'filesystem', 'Leu Home.jsx'),
        call('2', 'list_files', 'filesystem', 'Listou 3 itens'),
        call('3', 'run_command', 'terminal', '$ npm test'),
        call('4', 'fetch_url', 'web', 'Consultou example.com'),
      ],
    })} />)

    expect(screen.getByText('Explorou 2 arquivos, executou um comando, consultou a web')).toBeInTheDocument()
  })

  it('shows the live action, a clock and no state pill while the batch runs', () => {
    render(<ActivityCard group={group({
      state: 'waiting_tool',
      label: 'Executando python',
      events: [{ eventId: 'r1', cursor: 'r1', type: 'tool.started', kind: 'tool', state: 'waiting_tool', agentId: 'agent:main', toolName: 'run_command', toolKind: 'terminal', summary: 'Executando python', occurredAt: new Date(Date.now() - 65_000).toISOString() }],
    })} />)

    expect(screen.getByText('Executando python')).toBeInTheDocument()
    expect(screen.getByText(/^1 min \d\d s$/)).toBeInTheDocument()
    expect(screen.queryByText('Executando ferramenta')).not.toBeInTheDocument()
  })

  it('lists each call with its verb, target and a failure marker when opened', () => {
    render(<ActivityCard group={group({
      events: [
        { eventId: 'f1', cursor: 'f1', type: 'tool.finished', kind: 'tool', state: 'completed', agentId: 'agent:main', toolName: 'read_file', toolKind: 'filesystem', summary: 'Leu Home.jsx' },
        { eventId: 'f2', cursor: 'f2', type: 'tool.finished', kind: 'tool', state: 'failed', agentId: 'agent:main', toolName: 'run_command', toolKind: 'terminal', summary: '$ python build.py', errorCode: 'TOOL_FAILED' },
      ],
    })} />)

    fireEvent.click(screen.getByRole('button', { expanded: false }))

    expect(screen.getByText('Home.jsx')).toBeInTheDocument()
    expect(screen.getByText('python build.py')).toBeInTheDocument()
    expect(screen.getByText('Falhou')).toBeInTheDocument()
  })
})
