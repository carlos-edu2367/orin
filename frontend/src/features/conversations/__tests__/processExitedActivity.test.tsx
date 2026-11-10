import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { ActivityStream } from '../ActivityStream'
import { CodeToolCallBlock } from '../CodeToolCallBlock'
import { processExitedLabel, summarizeActivities } from '../activitySummary'

describe('process.exited activity summary and rendering', () => {
  it('parses process.exited events and formats Portuguese labels with and without exit code', () => {
    const failedEvent = parseConversationActivityEvent(
      {
        event_id: 'evt_proc_1',
        event_type: 'process.exited',
        sequence: 10,
        summary: 'Processo 3144 (TTS) terminou com erro (exit 1)',
        turn_id: 'turn_1',
        agent_id: 'agent:conv_1:main',
        payload: {
          pid: 3144,
          description: 'TTS',
          command: 'python tts.py',
          exit_code: 1,
          log_path: '.orin/logs/proc-3144.log',
          output_tail: 'Traceback (most recent call last):\nValueError: invalid voice sample',
          status: 'failed',
          failed: true,
        },
      },
      'evt_proc_1',
    )

    expect(failedEvent.type).toBe('process.exited')
    expect(failedEvent.kind).toBe('lifecycle')
    expect(failedEvent.state).toBe('failed')
    expect(failedEvent.pid).toBe(3144)
    expect(failedEvent.exitCode).toBe(1)
    expect(failedEvent.path).toBe('.orin/logs/proc-3144.log')
    expect(failedEvent.outputTail).toContain('ValueError: invalid voice sample')
    expect(processExitedLabel(failedEvent)).toBe('Processo 3144 (TTS) terminou com erro (exit 1)')

    const unknownExitEvent = parseConversationActivityEvent(
      {
        event_id: 'evt_proc_2',
        event_type: 'process.exited',
        sequence: 11,
        summary: '',
        turn_id: 'turn_1',
        agent_id: 'agent:conv_1:main',
        payload: {
          pid: 4200,
          description: 'Indexador',
          exit_code: null,
          log_path: '.orin/logs/proc-4200.log',
          output_tail: 'Done indexing.',
          status: 'completed',
          failed: false,
        },
      },
      'evt_proc_2',
    )

    expect(unknownExitEvent.state).toBe('completed')
    expect(unknownExitEvent.exitCode).toBeNull()
    expect(processExitedLabel(unknownExitEvent)).toBe('Processo 4200 (Indexador) terminou')

    const groups = summarizeActivities([failedEvent, unknownExitEvent])
    expect(groups).toHaveLength(2)
    expect(groups[0].label).toBe('Processo 3144 (TTS) terminou com erro (exit 1)')
    expect(groups[0].state).toBe('failed')
    expect(groups[1].label).toBe('Processo 4200 (Indexador) terminou')
    expect(groups[1].state).toBe('completed')
  })

  it('renders process.exited in ActivityStream with collapsible log tail', () => {
    const failedEvent = parseConversationActivityEvent(
      {
        event_id: 'evt_proc_ui',
        event_type: 'process.exited',
        sequence: 15,
        summary: 'Processo 3144 (TTS) terminou com erro (exit 1)',
        turn_id: 'turn_2',
        agent_id: 'agent:conv_1:main',
        payload: {
          pid: 3144,
          description: 'TTS',
          command: 'python tts.py',
          exit_code: 1,
          log_path: '.orin/logs/proc-3144.log',
          output_tail: 'ValueError: invalid voice sample',
          status: 'failed',
          failed: true,
        },
      },
      'evt_proc_ui',
    )

    render(<ActivityStream events={[failedEvent]} conversationId="conv_1" />)

    const trigger = screen.getByRole('button', {
      name: /Processo 3144 \(TTS\) terminou com erro \(exit 1\)/i,
    })
    expect(trigger).toBeTruthy()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText(/ValueError: invalid voice sample/)).toBeNull()

    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/ValueError: invalid voice sample/)).toBeTruthy()
    expect(screen.getByText('.orin/logs/proc-3144.log')).toBeTruthy()
  })

  it('labels the Code-mode block as a process, not a generic tool', () => {
    const event = parseConversationActivityEvent(
      {
        event_id: 'evt_proc_code',
        event_type: 'process.exited',
        sequence: 16,
        summary: 'Processo 3144 (TTS) terminou com erro (exit 1)',
        turn_id: 'turn_2',
        agent_id: 'agent:conv_1:main',
        payload: { pid: 3144, description: 'TTS', exit_code: 1, failed: true },
      },
      'evt_proc_code',
    )

    const { container } = render(<CodeToolCallBlock event={event} />)

    expect(container.textContent).toContain('Processo')
    expect(container.textContent).not.toMatch(/\btool\(/)
  })
})
