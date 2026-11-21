import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { MergeConflictCard } from '../MergeConflictCard'
import { buildTurnTimeline } from '../turnTimelineFold'
import type { ActivityGroup, ConversationActivityEvent } from '../activityTypes'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function makeMergeEvent(
  type: 'merge.conflict' | 'merge.applied' | 'merge.check_failed',
  overrides: Partial<ConversationActivityEvent> = {},
): ConversationActivityEvent {
  return {
    ...overrides,
    eventId: overrides.eventId ?? `ev-${type}`,
    cursor: overrides.cursor ?? '1',
    turnId: overrides.turnId ?? 'turn-1',
    type,
    kind: 'lifecycle',
    state: type === 'merge.applied' ? 'completed' : 'failed',
    summary:
      overrides.summary ??
      (type === 'merge.applied'
        ? 'Alterações da worktree integradas (2 arquivo(s))'
        : type === 'merge.check_failed'
          ? 'Checagem de integração falhou; alterações revertidas'
          : 'Conflito ao integrar worktree (src/auth.ts, src/routes.ts)'),
    agentId: overrides.agentId ?? 'sub-wt-1',
    agentName: overrides.agentName ?? 'Refatorador',
    parentAgentId: overrides.parentAgentId ?? 'main',
    occurredAt: overrides.occurredAt ?? '2026-11-21T19:10:00Z',
    payload: overrides.payload ?? {
      agent_id: 'sub-wt-1',
      files: ['src/auth.ts', 'src/routes.ts'],
      worktree_path: 'C:/repo/.orin/worktrees/agent-refatorador',
      branch: 'orin/agent-refatorador',
      conflict_detail: 'CONFLICT (content): Merge conflict in src/auth.ts',
    },
  }
}

function makeGroup(events: ConversationActivityEvent[]): ActivityGroup {
  const latest = events[events.length - 1]
  return {
    id: `merge:${latest.agentId}:${latest.turnId ?? ''}`,
    kind: 'lifecycle',
    state: latest.state,
    label: 'Integração da worktree',
    count: events.length,
    events,
    agentId: latest.agentId,
    agentName: latest.agentName,
    failed: latest.state === 'failed',
  }
}

describe('MergeConflictCard e fold de eventos merge.* (WS-B9)', () => {
  it('renders merge.conflict with conflicting files, worktree path, conflict detail and all three required actions', async () => {
    const user = userEvent.setup()
    const onOpenWorktree = vi.fn()
    const onAskAgentToResolve = vi.fn()
    const discardUrls: string[] = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/worktrees/sub-wt-1/discard') && init?.method === 'POST') {
        discardUrls.push(url)
        return jsonResponse({
          discarded: true,
          conversation_id: 'conv-7',
          agent_id: 'sub-wt-1',
          merge_state: 'discarded',
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    const event = makeMergeEvent('merge.conflict')
    render(
      <MergeConflictCard
        group={makeGroup([event])}
        conversationId="conv-7"
        client={client}
        onOpenWorktree={onOpenWorktree}
        onAskAgentToResolve={onAskAgentToResolve}
      />,
    )

    const card = screen.getByTestId('merge-conflict-card')
    expect(card).toHaveAttribute('data-merge-kind', 'conflict')
    expect(card).toHaveTextContent('C:/repo/.orin/worktrees/agent-refatorador')
    expect(card).toHaveTextContent('orin/agent-refatorador')
    expect(card).toHaveTextContent('Merge conflict in src/auth.ts')

    const filesList = within(card).getByTestId('merge-conflict-files')
    expect(filesList).toHaveTextContent('src/auth.ts')
    expect(filesList).toHaveTextContent('src/routes.ts')

    // Action 1: Abrir worktree
    await user.click(within(card).getByRole('button', { name: /Abrir worktree/i }))
    expect(onOpenWorktree).toHaveBeenCalledWith('C:/repo/.orin/worktrees/agent-refatorador', 'sub-wt-1')

    // Action 3: Pedir ao agente para resolver
    await user.click(within(card).getByRole('button', { name: /Pedir ao agente para resolver/i }))
    expect(onAskAgentToResolve).toHaveBeenCalledTimes(1)
    const [prompt, details] = onAskAgentToResolve.mock.calls[0]
    expect(prompt).toContain('src/auth.ts')
    expect(prompt).toContain('src/routes.ts')
    expect(prompt).toContain('C:/repo/.orin/worktrees/agent-refatorador')
    expect(details).toEqual({
      agentId: 'sub-wt-1',
      files: ['src/auth.ts', 'src/routes.ts'],
      worktreePath: 'C:/repo/.orin/worktrees/agent-refatorador',
      kind: 'conflict',
    })

    // Action 2: Descartar -> POST /v1/conversations/{id}/worktrees/{agent_id}/discard
    await user.click(within(card).getByRole('button', { name: /Descartar/i }))
    await waitFor(() => {
      expect(discardUrls).toHaveLength(1)
      expect(discardUrls[0]).toContain('/v1/conversations/conv-7/worktrees/sub-wt-1/discard')
    })
    await waitFor(() => {
      expect(card).toHaveAttribute('data-merge-kind', 'discarded')
      expect(card).toHaveTextContent(/descartad/i)
    })
  })

  it('renders merge.applied as a compact success card without conflict actions, and merge.check_failed with failure detail and actions', () => {
    const appliedEvent = makeMergeEvent('merge.applied', {
      payload: {
        agent_id: 'sub-wt-2',
        files: ['src/utils.ts'],
        branch: 'orin/agent-utils',
      },
    })
    const { rerender } = render(
      <MergeConflictCard group={makeGroup([appliedEvent])} conversationId="conv-7" />,
    )

    const appliedCard = screen.getByTestId('merge-conflict-card')
    expect(appliedCard).toHaveAttribute('data-merge-kind', 'applied')
    expect(appliedCard).toHaveTextContent('src/utils.ts')
    expect(within(appliedCard).queryByRole('button', { name: /Descartar/i })).not.toBeInTheDocument()

    const checkFailedEvent = makeMergeEvent('merge.check_failed', {
      payload: {
        agent_id: 'sub-wt-3',
        files: ['src/calc.ts'],
        worktree_path: 'C:/repo/.orin/worktrees/agent-calc',
        branch: 'orin/agent-calc',
        detail: 'pytest exited with code 1',
      },
    })
    rerender(<MergeConflictCard group={makeGroup([checkFailedEvent])} conversationId="conv-7" />)

    const failedCard = screen.getByTestId('merge-conflict-card')
    expect(failedCard).toHaveAttribute('data-merge-kind', 'check_failed')
    expect(failedCard).toHaveTextContent('pytest exited with code 1')
    expect(within(failedCard).getByRole('button', { name: /Abrir worktree/i })).toBeInTheDocument()
    expect(within(failedCard).getByRole('button', { name: /Descartar/i })).toBeInTheDocument()
    expect(within(failedCard).getByRole('button', { name: /Pedir ao agente para resolver/i })).toBeInTheDocument()
  })

  it('folds merge.* events into dedicated timeline items in buildTurnTimeline', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'ev-msg',
        cursor: '1',
        turnId: 'turn-1',
        messageId: 'msg-1',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        summary: 'Integrando alterações...',
        agentId: 'main',
        payload: { delta: 'Integrando alterações...' },
      },
      makeMergeEvent('merge.conflict', { eventId: 'ev-merge-1', cursor: '2', turnId: 'turn-1' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', 'Integrando alterações...', true)
    const mergeItem = timeline.find(
      (item) => item.kind === 'activity' && item.group.id.startsWith('merge:'),
    )
    expect(mergeItem).toBeDefined()
    if (mergeItem && mergeItem.kind === 'activity') {
      expect(mergeItem.group.events).toHaveLength(1)
      expect(mergeItem.group.events[0].type).toBe('merge.conflict')
      expect(mergeItem.group.label).toBe('Integração da worktree')
    }
  })
})
