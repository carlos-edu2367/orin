import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { activeCompaction, AgentPulse, modeFromEvents, pulseDetailFromEvents } from '../AgentPulse'
import type { ConversationActivityEvent } from '../activityTypes'

function raw(id: string, eventType: string, summary: string, payload: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return parseConversationActivityEvent(
    { event_id: id, event_type: eventType, turn_id: 't1', agent_id: 'agent:main', summary, payload, ...extra },
    id,
  )
}

const started = raw('e1', 'turn.started', 'Turn started')
const compacting = raw('e2', 'context.updated', 'Compactando contexto', { compacting: true, removed_messages: 184, compaction_count: 1 })
const usage = raw('e3', 'context.updated', 'Contexto atualizado', { used_tokens: 25008, limit_tokens: 1000000 })
const compacted = raw('e4', 'context.compacted', 'Contexto compactado automaticamente', { compacting: false, removed_messages: 184 })
const toolStarted = raw('e5', 'tool.started', 'Executando read_file', { tool_name: 'read_file', invocation_id: 'i1' })

describe('context compaction pulse', () => {
  it('parses the compacting flag and message count from the activity payload', () => {
    expect(compacting.compacting).toBe(true)
    expect(compacting.compactingMessages).toBe(184)
    expect(usage.compacting).toBeUndefined()
    expect(compacted.compacting).toBeUndefined()
  })

  it('shows the compacting mode while the summary is being written', () => {
    expect(modeFromEvents([started, compacting], 'running')).toBe('compacting')
    expect(pulseDetailFromEvents([started, compacting])).toBe('Compactando contexto · resumindo 184 mensagens')
  })

  it('keeps compacting through plain context usage updates and subagent events', () => {
    const childTool: ConversationActivityEvent = { ...toolStarted, eventId: 'c1', parentAgentId: 'agent:main', agentId: 'agent:child' }
    expect(modeFromEvents([started, compacting, usage, childTool], 'running')).toBe('compacting')
  })

  it('ends on context.compacted', () => {
    const events = [started, compacting, compacted, usage]
    expect(activeCompaction(events)).toBeNull()
    expect(modeFromEvents(events, 'running')).toBe('thinking')
    expect(pulseDetailFromEvents(events)).toBeUndefined()
  })

  it('ends when the root agent resumes work even without context.compacted', () => {
    expect(modeFromEvents([started, compacting, toolStarted], 'running')).toBe('reading')
  })

  it('does not leak a previous turn compaction into a new turn', () => {
    const nextTurn = raw('n1', 'turn.started', 'Turn started', {}, { turn_id: 't2' })
    expect(activeCompaction([started, compacting, nextTurn])).toBeNull()
  })

  it('omits the count when the payload has none', () => {
    const bare = raw('b1', 'context.updated', 'Compactando contexto', { compacting: true })
    expect(pulseDetailFromEvents([started, bare])).toBe('Compactando contexto')
  })

  it('renders the animated press with a shimmering label', () => {
    const events = [started, compacting]
    render(
      <AgentPulse
        mode={modeFromEvents(events, 'running')}
        state="working"
        detail={pulseDetailFromEvents(events)}
        activeTurnId="t1"
      />,
    )
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('data-mode', 'compacting')
    const label = screen.getByText('Compactando contexto · resumindo 184 mensagens')
    expect(label).toHaveClass('agent-pulse__label--shimmer')
    expect(screen.getByTestId('agent-pulse-compacting')).toBeInTheDocument()
  })
})
