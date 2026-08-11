import { createElement } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ClientEvent } from '../../src/api/types'
import { deriveToolActivityDetail, normalizeActivities } from '../../src/features/activities/activityNormalizer'
import { ActivityGroup } from '../../src/features/activities/ActivityGroup'
import { ToolActivityGroup } from '../../src/features/activities/ToolActivityGroup'
import type { Activity } from '../../src/features/activities/activityTypes'

function event(overrides: Partial<ClientEvent> = {}): ClientEvent {
  return {
    event_id: 'evt-1',
    event_type: 'ExecutionStarted',
    execution_id: 'exec-1',
    sequence: 1,
    occurred_at: '2026-08-07T10:00:00.000Z',
    payload: {},
    ...overrides,
  }
}

function toolEvent(overrides: Partial<ClientEvent> & { payload?: Record<string, unknown> } = {}): ClientEvent {
  return event({
    event_type: 'ToolStarted',
    payload: { invocation_id: 'inv-1', tool_kind: 'code_exec', state: 'running' },
    ...overrides,
  })
}

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

describe('normalizeActivities', () => {
  it('groups three events sharing an invocation_id into a single tool activity with all event ids', () => {
    const events = [
      toolEvent({ event_id: 'evt-a', sequence: 1, payload: { invocation_id: 'inv-1', tool_kind: 'code_exec', state: 'running' } }),
      toolEvent({ event_id: 'evt-b', event_type: 'ToolProgressed', sequence: 2, payload: { invocation_id: 'inv-1', progress_kind: 'chunk' } }),
      toolEvent({ event_id: 'evt-c', event_type: 'ToolFinished', sequence: 3, payload: { invocation_id: 'inv-1', state: 'succeeded' } }),
    ]

    const activities = normalizeActivities(events)

    expect(activities).toHaveLength(1)
    expect(activities[0]).toMatchObject({ category: 'tool', executionId: 'exec-1', state: 'succeeded' })
    expect(activities[0].eventIds).toEqual(['evt-a', 'evt-b', 'evt-c'])
  })

  it('never groups events lacking invocation_id as tool activity', () => {
    const events = [
      event({ event_id: 'evt-a', event_type: 'ToolStarted', sequence: 1, payload: { tool_kind: 'code_exec' } }),
      event({ event_id: 'evt-b', event_type: 'ExecutionStarted', sequence: 2, payload: { to_state: 'RUNNING', state_version: 2 } }),
    ]

    const activities = normalizeActivities(events)

    expect(activities.some((activity) => activity.category === 'tool')).toBe(false)
  })

  it('does not infer a tool call from Execution lifecycle transitions alone', () => {
    const events = [
      event({ event_id: 'evt-a', event_type: 'ExecutionWaitingForTool', sequence: 1, payload: { to_state: 'WAITING_TOOL', state_version: 2 } }),
      event({ event_id: 'evt-b', event_type: 'ExecutionStarted', sequence: 2, payload: { to_state: 'RUNNING', state_version: 3 } }),
    ]

    const activities = normalizeActivities(events)

    expect(activities.every((activity) => activity.category === 'lifecycle')).toBe(true)
  })

  it('does not duplicate an activity when the same event_id is redelivered on replay', () => {
    const first = toolEvent({ event_id: 'evt-a', sequence: 1 })
    const replayed = toolEvent({ event_id: 'evt-a', sequence: 1 })

    const activities = normalizeActivities([first, replayed])

    expect(activities).toHaveLength(1)
    expect(activities[0].eventIds).toEqual(['evt-a'])
  })

  it('keeps activities from different executions from mixing into one group', () => {
    const events = [
      toolEvent({ event_id: 'evt-a', execution_id: 'exec-1', sequence: 1, payload: { invocation_id: 'inv-shared', state: 'running' } }),
      toolEvent({ event_id: 'evt-b', execution_id: 'exec-2', sequence: 1, payload: { invocation_id: 'inv-shared', state: 'running' } }),
    ]

    const activities = normalizeActivities(events)

    expect(activities).toHaveLength(2)
    expect(activities.map((activity) => activity.executionId).sort()).toEqual(['exec-1', 'exec-2'])
  })

  it('is pure: same input produces the same output and the input is never mutated', () => {
    const events = Object.freeze([
      Object.freeze(toolEvent({ event_id: 'evt-a', sequence: 1 })),
      Object.freeze(event({ event_id: 'evt-b', event_type: 'ExecutionStarted', sequence: 2, payload: Object.freeze({ to_state: 'RUNNING', state_version: 2 }) })),
    ])

    const first = normalizeActivities(events as unknown as ClientEvent[])
    const second = normalizeActivities(events as unknown as ClientEvent[])

    expect(first).toEqual(second)
    expect(events[0].event_id).toBe('evt-a')
  })

  it('orders activities deterministically regardless of input order', () => {
    const events = [
      event({ event_id: 'evt-c', event_type: 'ExecutionFinished', sequence: 3, payload: { to_state: 'COMPLETED', state_version: 3 } }),
      toolEvent({ event_id: 'evt-a', sequence: 1 }),
      toolEvent({ event_id: 'evt-b', event_type: 'ToolFinished', sequence: 2, payload: { invocation_id: 'inv-2', state: 'succeeded' } }),
    ]

    const forward = normalizeActivities(events)
    const shuffled = normalizeActivities([...events].reverse())

    expect(forward.map((activity) => activity.id)).toEqual(shuffled.map((activity) => activity.id))
  })

  it('represents a failed tool activity using the authorized state field', () => {
    const activities = normalizeActivities([toolEvent({ payload: { invocation_id: 'inv-1', state: 'failed' } })])

    expect(activities[0].state).toBe('failed')
  })

  it('represents a cancelled tool activity using the authorized state field', () => {
    const activities = normalizeActivities([toolEvent({ payload: { invocation_id: 'inv-1', state: 'cancelled' } })])

    expect(activities[0].state).toBe('cancelled')
  })

  it('represents a timed-out tool activity via the documented error_code, without inventing a new state', () => {
    const activities = normalizeActivities([toolEvent({ payload: { invocation_id: 'inv-1', state: 'failed', error_code: 'timeout' } })])

    expect(activities[0].state).toBe('failed')
    const detail = deriveToolActivityDetail([toolEvent({ payload: { invocation_id: 'inv-1', state: 'failed', error_code: 'timeout' } })])
    expect(detail.errorCode).toBe('timeout')
  })
})

describe('deriveToolActivityDetail', () => {
  it('only reads the authorized ToolActivityView fields and drops everything else', () => {
    const events = [
      toolEvent({
        payload: {
          invocation_id: 'inv-1',
          tool_kind: 'code_exec',
          state: 'succeeded',
          progress_kind: 'chunk',
          error_code: 'timeout',
          summary: 'A leitura sanitizada do resultado.',
          arguments: { secret: 'do-not-leak' },
          output: 'raw tool output',
          token: 'pat-secret-value',
          result_ref: 'opaque-result-secret',
        },
      }),
    ]

    const detail = deriveToolActivityDetail(events)

    expect(detail).toEqual({
      toolKind: 'code_exec',
      progressKind: 'chunk',
      errorCode: 'timeout',
      summary: 'A leitura sanitizada do resultado.',
    })
    expect(JSON.stringify(detail)).not.toMatch(/secret|raw tool output|pat-secret-value|opaque-result-secret/)
  })
})

const toolActivity = (overrides: Partial<Activity> = {}): Activity => ({
  id: 'exec-1::tool::inv-1',
  executionId: 'exec-1',
  category: 'tool',
  state: 'running',
  eventIds: ['evt-a'],
  ...overrides,
})

describe('ActivityGroup', () => {
  it('exposes an accessible, keyboard-operable disclosure with aria-expanded', async () => {
    const user = userEvent.setup()
    render(createElement(ActivityGroup, { activity: toolActivity(), summary: 'Em execução · code_exec' }))

    const trigger = screen.getByRole('button', { name: /Ferramenta/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Em execução · code_exec')).not.toBeInTheDocument()

    await user.tab()
    expect(trigger).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Em execução · code_exec')).toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('shows the observed action count for progressive disclosure level 1', () => {
    render(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a', 'b', 'c'] }), summary: 'detalhe' }))

    expect(screen.getByText('3 ações observadas')).toBeInTheDocument()
  })

  it('pulses at most once for a newly observed event_id and never on initial mount', () => {
    const { container, rerender } = render(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a'] }), summary: 'detalhe' }))
    expect(container.querySelector('.activity-group--pulse')).toBeNull()

    rerender(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a', 'b'] }), summary: 'detalhe' }))
    expect(container.querySelector('.activity-group--pulse')).not.toBeNull()
  })

  it('does not pulse again when the same activity is redelivered by replay or reconnection', () => {
    const { container, rerender } = render(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a', 'b'] }), summary: 'detalhe' }))
    rerender(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a', 'b'] }), summary: 'detalhe' }))

    expect(container.querySelector('.activity-group--pulse')).toBeNull()
  })

  it('keeps the same semantic information in text under prefers-reduced-motion, without pulsing', () => {
    mockReducedMotion(true)
    const { container, rerender } = render(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a'] }), summary: 'detalhe' }))

    rerender(createElement(ActivityGroup, { activity: toolActivity({ eventIds: ['a', 'b'] }), summary: 'detalhe' }))

    expect(container.querySelector('.activity-group--pulse')).toBeNull()
    expect(screen.getByText('2 ações observadas')).toBeInTheDocument()
  })
})

describe('ToolActivityGroup', () => {
  it('shows type, count and sanitized result at disclosure level 2', async () => {
    const user = userEvent.setup()
    const events = [
      toolEvent({ event_id: 'evt-a', sequence: 1 }),
      toolEvent({ event_id: 'evt-b', event_type: 'ToolFinished', sequence: 2, payload: { invocation_id: 'inv-1', tool_kind: 'code_exec', state: 'succeeded', summary: 'Concluído sem intercorrências.' } }),
    ]
    const activity = normalizeActivities(events)[0]

    render(createElement(ToolActivityGroup, { activity, events }))

    await user.click(screen.getByRole('button', { name: /Ferramenta/ }))

    expect(screen.getByText('2 ações observadas')).toBeInTheDocument()
    expect(screen.getByText('code_exec')).toBeInTheDocument()
    expect(screen.getByText('Concluído sem intercorrências.')).toBeInTheDocument()
  })

  it('shows a sanitized failure code without exposing stack or raw payload', async () => {
    const user = userEvent.setup()
    const events = [toolEvent({ payload: { invocation_id: 'inv-1', tool_kind: 'code_exec', state: 'failed', error_code: 'timeout', arguments: { path: '/etc/secret' } } })]
    const activity = normalizeActivities(events)[0]

    const { container } = render(createElement(ToolActivityGroup, { activity, events }))
    await user.click(screen.getByRole('button', { name: /Ferramenta/ }))

    expect(screen.getByText('timeout')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/etc\/secret/)
  })

  it('never renders raw arguments, output, tokens or secrets from the underlying events', async () => {
    const user = userEvent.setup()
    const events = [
      toolEvent({
        payload: {
          invocation_id: 'inv-1',
          tool_kind: 'code_exec',
          state: 'succeeded',
          arguments: { query: 'SELECT * FROM secrets' },
          output: 'raw-stdout-content',
          token: 'pat-live-token',
          result_ref: 'opaque-ref-abc',
        },
      }),
    ]
    const activity = normalizeActivities(events)[0]

    const { container } = render(createElement(ToolActivityGroup, { activity, events }))
    await user.click(screen.getByRole('button', { name: /Ferramenta/ }))

    expect(container.textContent).not.toMatch(/SELECT \* FROM secrets|raw-stdout-content|pat-live-token|opaque-ref-abc/)
  })

  it('shows observed progress as ticks, never as an invented percentage', async () => {
    const user = userEvent.setup()
    const events = [
      toolEvent({ event_id: 'evt-a', sequence: 1 }),
      toolEvent({ event_id: 'evt-b', event_type: 'ToolProgressed', sequence: 2, payload: { invocation_id: 'inv-1', progress_kind: 'chunk' } }),
      toolEvent({ event_id: 'evt-c', event_type: 'ToolProgressed', sequence: 3, payload: { invocation_id: 'inv-1', progress_kind: 'chunk' } }),
    ]
    const activity = normalizeActivities(events)[0]

    const { container } = render(createElement(ToolActivityGroup, { activity, events }))
    await user.click(screen.getByRole('button', { name: /Ferramenta/ }))

    expect(screen.getByText('2 atualizações')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/%/)
  })
})
