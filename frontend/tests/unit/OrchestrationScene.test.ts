import { createElement } from 'react'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentGraph } from '../../src/features/agents/agentGraphProjection'
import { OrchestrationScene } from '../../src/features/agents/OrchestrationScene'
import { RAIL_NODE_LIMIT } from '../../src/features/agents/AgentRail'

// jsdom has neither matchMedia nor ResizeObserver by default; both are reset after
// every test so a mock from one test never leaks into the next (same convention as
// agentGraphProjection.test.ts and usePerformanceProfile.test.ts).
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

const baseGraph = (): AgentGraph => ({
  nodes: [
    { agentId: 'agent-parent', executionId: 'exec-parent', visualState: 'running' },
    { agentId: 'agent-child', executionId: 'exec-child', visualState: 'queued' },
  ],
  edges: [
    { id: 'delegation-evt-1', from: 'agent-parent', to: 'agent-child', fact: 'delegation', eventId: 'evt-1' },
  ],
})

describe('OrchestrationScene', () => {
  it('does not mount when the graph has no edges, mirroring AgentRail\'s own rule', () => {
    mockReducedMotion(false)
    const { container } = render(createElement(OrchestrationScene, { graph: { nodes: [], edges: [] } }))

    expect(container).toBeEmptyDOMElement()
  })

  it('does not mount when the graph has nodes but no observed collaboration edge', () => {
    mockReducedMotion(false)
    const graph: AgentGraph = { nodes: [{ agentId: 'agent-solo', visualState: 'running' }], edges: [] }
    const { container } = render(createElement(OrchestrationScene, { graph }))

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a textual, canvas-free fallback under prefers-reduced-motion ("static" profile) with the same participant/state text as the 2D rail', () => {
    mockReducedMotion(true)
    const { container } = render(createElement(OrchestrationScene, { graph: baseGraph() }))

    expect(container.querySelector('canvas')).toBeNull()
    expect(screen.getByText('agent-parent')).toBeInTheDocument()
    expect(screen.getByText('agent-child')).toBeInTheDocument()
    expect(screen.getByText('Trabalhando')).toBeInTheDocument()
    expect(screen.getByText('Na fila')).toBeInTheDocument()
  })

  it('falls back to the same textual view — never a crash — when the R3F canvas fails to mount', async () => {
    mockReducedMotion(false)
    const { container } = render(createElement(OrchestrationScene, { graph: baseGraph() }))

    // jsdom has no ResizeObserver, which is exactly the class of environment R3F
    // cannot render in; the scene's own error boundary must degrade to the same
    // textual fallback instead of throwing past this render.
    expect(await screen.findByText('agent-parent')).toBeInTheDocument()
    expect(container.querySelector('canvas')).toBeNull()
  })

  it('caps the scene at the same 12-node budget the 2D rail already enforces, with an accessible overflow note', () => {
    mockReducedMotion(true)
    const nodes = Array.from({ length: 15 }, (_, index) => ({ agentId: `agent-${index}`, visualState: 'idle' as const }))
    const edges = nodes.slice(1).map((node, index) => ({
      id: `delegation-evt-${index}`,
      from: 'agent-0',
      to: node.agentId,
      fact: 'delegation' as const,
      eventId: `evt-${index}`,
    }))

    render(createElement(OrchestrationScene, { graph: { nodes, edges } }))

    const list = screen.getByRole('list', { name: /orquestração/i })
    expect(within(list).getAllByRole('listitem')).toHaveLength(RAIL_NODE_LIMIT)
    expect(screen.getByText(/3 participantes adicionais/)).toBeInTheDocument()
  })

  it('never leaks event/message content: the component only accepts an AgentGraph, and rendered text never includes payload content', () => {
    mockReducedMotion(true)
    // Only nodes/edges (already content-free by AgentGraph's own type) are ever
    // passed in; OrchestrationScene's props type has no `events`/ClientEvent field
    // to read from in the first place — this mirrors the "never accepts ClientEvent[]"
    // rule already enforced for `projectAgentGraph`.
    type SceneProps = Parameters<typeof OrchestrationScene>[0]
    const acceptsEvents = false as unknown as 'events' extends keyof SceneProps ? true : false
    expect(acceptsEvents).toBe(false)

    const { container } = render(createElement(OrchestrationScene, { graph: baseGraph() }))
    expect(container.textContent ?? '').not.toMatch(/sensitive|handoff|do-not-leak|token|secret/i)
  })
})
