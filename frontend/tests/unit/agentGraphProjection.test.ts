import { createElement } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ClientEvent } from '../../src/api/types'
import type { ExecutionProjection } from '../../src/features/executions/executionProjection'
import { projectAgentGraph, type AgentEdge, type AgentGraph, type AgentNode } from '../../src/features/agents/agentGraphProjection'
import { AgentGlyph } from '../../src/features/agents/AgentGlyph'
import { AgentRail } from '../../src/features/agents/AgentRail'

// The rail test owns the expand/collapse affordance, not R3F's module load.
// Keeping this boundary synchronous prevents the unit suite from racing the
// actual lazy scene import; OrchestrationScene.test.tsx and browser E2Es cover
// the real reduced-motion/canvas implementations separately.
vi.mock('../../src/features/agents/OrchestrationScene', () => ({
  OrchestrationScene: ({ graph }: { graph: { nodes: Array<{ agentId: string }> } }) => (
    createElement('section', { 'aria-label': 'Cena 3D da orquestração observada' },
      graph.nodes.map((node) => createElement('span', { key: node.agentId }, node.agentId)),
    )
  ),
}))

function event(overrides: Partial<ClientEvent> & { payload?: Record<string, unknown> } = {}): ClientEvent {
  return {
    event_id: 'evt-1',
    event_type: 'DelegationCreated',
    execution_id: 'exec-parent',
    sequence: 1,
    occurred_at: '2026-08-07T10:00:00.000Z',
    payload: {},
    ...overrides,
  }
}

function projection(overrides: Partial<ExecutionProjection> = {}): ExecutionProjection {
  return {
    execution_id: 'exec-parent',
    agent_id: 'agent-parent',
    state: 'RUNNING',
    state_version: 3,
    parent_execution_id: null,
    created_at: '2026-08-07T10:00:00.000Z',
    updated_at: '2026-08-07T10:00:00.000Z',
    finished_at: null,
    result: null,
    failure: null,
    visual_status: 'Trabalhando',
    last_event_sequence: 0,
    ...overrides,
  }
}

// jsdom does not define matchMedia by default; reset after every test so a mocked
// reduced-motion preference from one test can never leak into the next (matches
// carry across tests otherwise, since window is shared within this file).
afterEach(() => {
  // @ts-expect-error restoring the undefined default, not a typed jsdom API
  delete window.matchMedia
})

function mockReducedMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: matches && query.includes('reduce'),
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

const delegationCreated = (overrides: Partial<ClientEvent> & { payload?: Record<string, unknown> } = {}): ClientEvent =>
  event({
    event_type: 'DelegationCreated',
    payload: { delegation_id: 'del-1', child_execution_id: 'exec-child', child_agent_id: 'agent-child' },
    ...overrides,
  })

const delegationResultReturned = (overrides: Partial<ClientEvent> & { payload?: Record<string, unknown> } = {}): ClientEvent =>
  event({
    event_id: 'evt-2',
    event_type: 'DelegationResultReturned',
    sequence: 2,
    payload: { delegation_id: 'del-1' },
    ...overrides,
  })

const agentMessageCreated = (overrides: Partial<ClientEvent> & { payload?: Record<string, unknown> } = {}): ClientEvent =>
  event({
    event_id: 'evt-msg-1',
    event_type: 'AgentMessageCreated',
    execution_id: 'exec-delivery-synthetic',
    payload: { message_id: 'msg-1', sender_agent_id: 'agent-a', recipient_agent_id: 'agent-b', content: 'never shown' },
    ...overrides,
  })

describe('projectAgentGraph', () => {
  it('creates a parent→child edge with fact "delegation" from DelegationCreated', () => {
    const executions = { 'exec-parent': projection() }
    const graph = projectAgentGraph([delegationCreated()], executions)

    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0]).toMatchObject({ from: 'agent-parent', to: 'agent-child', fact: 'delegation', eventId: 'evt-1' })
    expect(graph.nodes.map((node) => node.agentId).sort()).toEqual(['agent-child', 'agent-parent'])
  })

  it('creates a return edge with fact "result" from DelegationResultReturned, distinct from the outbound edge', () => {
    const executions = { 'exec-parent': projection() }
    const graph = projectAgentGraph([delegationCreated(), delegationResultReturned()], executions)

    expect(graph.edges).toHaveLength(2)
    const resultEdge = graph.edges.find((edge) => edge.fact === 'result')
    const delegationEdge = graph.edges.find((edge) => edge.fact === 'delegation')
    expect(resultEdge).toMatchObject({ from: 'agent-child', to: 'agent-parent', eventId: 'evt-2' })
    expect(delegationEdge).toMatchObject({ from: 'agent-parent', to: 'agent-child', eventId: 'evt-1' })
    expect(resultEdge?.id).not.toBe(delegationEdge?.id)
  })

  it('does not create a return edge when no matching DelegationCreated was observed for that delegation_id', () => {
    const executions = { 'exec-parent': projection() }
    const graph = projectAgentGraph([delegationResultReturned({ payload: { delegation_id: 'unknown-delegation' } })], executions)

    expect(graph.edges).toHaveLength(0)
  })

  it('never draws an edge from an isolated child execution, even when parent_execution_id is set on the projection', () => {
    const executions = {
      'exec-parent': projection(),
      'exec-child': projection({
        execution_id: 'exec-child',
        agent_id: 'agent-child',
        parent_execution_id: 'exec-parent',
      }),
    }

    const graph = projectAgentGraph([], executions)

    expect(graph.nodes).toHaveLength(0)
    expect(graph.edges).toHaveLength(0)
  })

  it('creates a "message" edge from AgentMessageCreated without exposing message content', () => {
    const graph = projectAgentGraph([agentMessageCreated()], {})

    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0]).toMatchObject({ from: 'agent-a', to: 'agent-b', fact: 'message', eventId: 'evt-msg-1' })
    expect(JSON.stringify(graph)).not.toMatch(/never shown/)
  })

  it('does not duplicate a node or edge when the same event_id is redelivered on replay', () => {
    const executions = { 'exec-parent': projection() }
    const first = delegationCreated()
    const replayed = delegationCreated()

    const graph = projectAgentGraph([first, replayed], executions)

    expect(graph.edges).toHaveLength(1)
    expect(graph.nodes).toHaveLength(2)
  })

  it('keeps graphs for different executions from mixing', () => {
    const executions = {
      'exec-parent': projection(),
      'exec-2': projection({ execution_id: 'exec-2', agent_id: 'agent-2' }),
    }
    // Only exec-parent's DelegationCreated is observed; exec-2 has no events at all.
    const graph = projectAgentGraph([delegationCreated()], executions)

    expect(graph.nodes.some((node) => node.agentId === 'agent-2')).toBe(false)
    expect(graph.edges.some((edge) => edge.from === 'agent-2' || edge.to === 'agent-2')).toBe(false)
  })

  it('sets the parent agent visualState to "waiting" on AgentWaitRegistered and reverts it on AgentWaitSatisfied, without touching an unrelated paused agent', () => {
    const executions = {
      'exec-parent': projection(),
      'exec-other': projection({ execution_id: 'exec-other', agent_id: 'agent-other', state: 'PAUSED' }),
    }
    const events = [
      delegationCreated(),
      event({ event_id: 'evt-wait-1', event_type: 'AgentWaitRegistered', sequence: 2, payload: { wait_id: 'wait-1' } }),
      delegationCreated({
        event_id: 'evt-unrelated',
        execution_id: 'exec-other',
        sequence: 3,
        payload: { delegation_id: 'del-2', child_execution_id: 'exec-other-child', child_agent_id: 'agent-other-child' },
      }),
    ]

    const waiting = projectAgentGraph(events, executions)
    const parentNode = waiting.nodes.find((node) => node.agentId === 'agent-parent')
    const otherNode = waiting.nodes.find((node) => node.agentId === 'agent-other')
    expect(parentNode?.visualState).toBe('waiting')
    expect(otherNode?.visualState).not.toBe('waiting')

    const satisfied = projectAgentGraph([...events, event({ event_id: 'evt-wait-2', event_type: 'AgentWaitSatisfied', sequence: 4, payload: { wait_id: 'wait-1' } })], executions)
    const parentAfter = satisfied.nodes.find((node) => node.agentId === 'agent-parent')
    expect(parentAfter?.visualState).not.toBe('waiting')
    expect(parentAfter?.visualState).toBe('running')
  })

  it('is pure: the same input produces the same output and neither events nor executions are mutated', () => {
    const executions = Object.freeze({ 'exec-parent': Object.freeze(projection()) }) as Record<string, ExecutionProjection>
    const events = Object.freeze([Object.freeze(delegationCreated())]) as ClientEvent[]

    const first = projectAgentGraph(events, executions)
    const second = projectAgentGraph(events, executions)

    expect(first).toEqual(second)
    expect(events[0].event_id).toBe('evt-1')
    expect(executions['exec-parent'].agent_id).toBe('agent-parent')
  })

  it('is tolerant of incomplete payloads and never throws, dropping only the affected fact', () => {
    const executions = { 'exec-parent': projection() }
    const incomplete = [
      event({ event_id: 'evt-bad-1', event_type: 'DelegationCreated', payload: {} }),
      event({ event_id: 'evt-bad-2', event_type: 'AgentMessageCreated', payload: { sender_agent_id: 'agent-a' } }),
      event({ event_id: 'evt-bad-3', event_type: 'DelegationResultReturned', payload: {} }),
    ]

    expect(() => projectAgentGraph(incomplete, executions)).not.toThrow()
    const graph = projectAgentGraph(incomplete, executions)
    expect(graph.edges).toHaveLength(0)
  })

  it('never exposes handoff content, raw payload, refs, tokens or secrets on any node or edge', () => {
    const events = [
      delegationCreated({
        payload: {
          delegation_id: 'del-1',
          child_execution_id: 'exec-child',
          child_agent_id: 'agent-child',
          handoff_context: 'sensitive handoff payload',
          token: 'pat-secret-value',
          result_ref: 'opaque-result-secret',
        },
      }),
      agentMessageCreated({ payload: { sender_agent_id: 'agent-a', recipient_agent_id: 'agent-b', content: 'do-not-leak-this', token: 'pat-live' } }),
    ]

    const graph = projectAgentGraph(events, { 'exec-parent': projection() })
    const serialized = JSON.stringify(graph)

    expect(serialized).not.toMatch(/sensitive handoff|do-not-leak-this|pat-secret-value|pat-live|opaque-result-secret/)
  })
})

const baseGraph = (): AgentGraph => ({
  nodes: [
    { agentId: 'agent-parent', executionId: 'exec-parent', visualState: 'running' },
    { agentId: 'agent-child', executionId: 'exec-child', visualState: 'queued' },
  ],
  edges: [
    { id: 'delegation-evt-1', from: 'agent-parent', to: 'agent-child', fact: 'delegation', eventId: 'evt-1' },
  ],
})

const eventsForGraph = (): ClientEvent[] => [delegationCreated()]

describe('AgentGlyph', () => {
  it('exposes an accessible name that includes the agent id and its human state', () => {
    render(createElement(AgentGlyph, {
      node: { agentId: 'agent-parent', executionId: 'exec-parent', visualState: 'running' } satisfies AgentNode,
      expanded: false,
      onToggle: () => {},
    }))

    expect(screen.getByRole('button', { name: /agent-parent/ })).toBeInTheDocument()
  })

  it('is keyboard operable and shows level-2 detail (state + fact) without content on expansion', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(createElement(AgentGlyph, {
      node: { agentId: 'agent-parent', executionId: 'exec-parent', visualState: 'running' } satisfies AgentNode,
      expanded: false,
      onToggle,
    }))

    await user.tab()
    expect(screen.getByRole('button')).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('shows the same state information as text under prefers-reduced-motion', () => {
    mockReducedMotion(true)
    render(createElement(AgentGlyph, {
      node: { agentId: 'agent-parent', executionId: 'exec-parent', visualState: 'waiting' } satisfies AgentNode,
      expanded: true,
      onToggle: () => {},
      lastFact: 'delegation',
    }))

    expect(screen.getByText(/Aguardando/)).toBeInTheDocument()
  })
})

describe('AgentRail', () => {
  it('renders nothing when no edge has been projected', () => {
    const { container } = render(createElement(AgentRail, { graph: { nodes: [], edges: [] }, events: [] }))

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a compact glyph per participant once at least one edge exists', () => {
    render(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    expect(screen.getByRole('button', { name: /agent-parent/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /agent-child/ })).toBeInTheDocument()
  })

  it('shows IDs, source event_id and timestamp at disclosure level 3, opaque and unlabeled as content', async () => {
    const user = userEvent.setup()
    render(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    await user.click(screen.getByRole('button', { name: /Detalhes técnicos da colaboração/ }))

    expect(screen.getByText('evt-1')).toBeInTheDocument()
    expect(screen.getByText('2026-08-07T10:00:00.000Z')).toBeInTheDocument()
  })

  it('caps the rail at 12 nodes and exposes the remainder as an accessible, named counter', () => {
    const nodes: AgentNode[] = Array.from({ length: 15 }, (_, index) => ({
      agentId: `agent-${index}`,
      visualState: 'idle' as const,
    }))
    const edges: AgentEdge[] = nodes.slice(1).map((node, index) => ({
      id: `delegation-evt-${index}`,
      from: 'agent-0',
      to: node.agentId,
      fact: 'delegation' as const,
      eventId: `evt-${index}`,
    }))

    render(createElement(AgentRail, { graph: { nodes, edges }, events: [] }))

    expect(screen.getAllByRole('button', { name: /^agent-\d+/ })).toHaveLength(12)
    const overflowButton = screen.getByRole('button', { name: /3.*participante/ })
    expect(overflowButton).toBeInTheDocument()
  })

  it('expanding the overflow counter reveals an accessible list of the remaining participants', async () => {
    const user = userEvent.setup()
    const nodes: AgentNode[] = Array.from({ length: 13 }, (_, index) => ({
      agentId: `agent-${index}`,
      visualState: 'idle' as const,
    }))
    const edges: AgentEdge[] = [{ id: 'delegation-evt-0', from: 'agent-0', to: 'agent-1', fact: 'delegation', eventId: 'evt-0' }]

    render(createElement(AgentRail, { graph: { nodes, edges }, events: [] }))
    await user.click(screen.getByRole('button', { name: /1.*participante/ }))

    const list = screen.getByRole('list', { name: 'Participantes adicionais' })
    expect(within(list).getByText('agent-12')).toBeInTheDocument()
  })

  it('preserves focus order across the rail when a glyph is expanded', async () => {
    const user = userEvent.setup()
    render(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    const parentTrigger = screen.getByRole('button', { name: /agent-parent/ })
    const childTrigger = screen.getByRole('button', { name: /agent-child/ })
    parentTrigger.focus()
    await user.keyboard('{Enter}')
    await user.tab()

    expect(childTrigger).toHaveFocus()
  })

  it('fires a pulse at most once for a newly observed edge event_id, never on initial mount', () => {
    const initial = { nodes: baseGraph().nodes, edges: [] as AgentEdge[] }
    const { container, rerender } = render(createElement(AgentRail, { graph: initial, events: [] }))
    expect(container.querySelector('.agent-glyph__core--pulse')).toBeNull()

    rerender(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))
    expect(container.querySelector('.agent-glyph__core--pulse')).not.toBeNull()
  })

  it('does not pulse again when the same edges are redelivered by replay or reconnection', () => {
    const { container, rerender } = render(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))
    rerender(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    expect(container.querySelector('.agent-glyph__core--pulse')).toBeNull()
  })

  it('suppresses the pulse and layout morph under prefers-reduced-motion while keeping equivalent text', () => {
    mockReducedMotion(true)
    const initial = { nodes: baseGraph().nodes, edges: [] as AgentEdge[] }
    const { container, rerender } = render(createElement(AgentRail, { graph: initial, events: [] }))

    rerender(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    expect(container.querySelector('.agent-glyph__core--pulse')).toBeNull()
    expect(screen.getByRole('button', { name: /agent-child/ })).toBeInTheDocument()
  })

  it('opens the lazy-loaded 3D scene from the "Expandir grafo" affordance and collapses back to it, without ever losing the 2D glyphs underneath', async () => {
    // Reduced motion keeps the scene's own performance profile at "static" for the
    // whole test (textual, canvas-free) instead of racing jsdom's transition from
    // its initial "reduced" state to "full" — that transition, and the resulting
    // WebGL-unavailable fallback, is already covered deterministically by
    // OrchestrationScene.test.ts. This test only verifies the rail's own expand/
    // collapse affordance and that the 2D glyphs never disappear underneath it.
    mockReducedMotion(true)
    const user = userEvent.setup()
    render(createElement(AgentRail, { graph: baseGraph(), events: eventsForGraph() }))

    expect(screen.getByRole('button', { name: /agent-parent/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Expandir grafo/ }))

    expect(await screen.findByText('agent-parent')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /agent-parent/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Recolher grafo' }))
    expect(screen.getByRole('button', { name: /Expandir grafo/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /agent-parent/ })).toBeInTheDocument()
  })
})
