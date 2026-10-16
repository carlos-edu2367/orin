import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  EVENT_LIMIT,
  applyActivityEvent,
  createActivityState,
  trimActivityEvents,
} from '../activityReducer'
import { buildTurnTimeline } from '../turnTimelineFold'
import { TurnTimeline } from '../TurnTimeline'
import { parseConversationActivityEvent, streamConversationEvents } from '../../../api/conversations'
import { ApiError } from '../../../api/errors'
import type { ApiClient } from '../../../api/client'
import type { ConversationActivityEvent } from '../activityTypes'

describe('TurnTimeline Collapsible Batch & Text Retention (TDD)', () => {
  it('never drops consolidated assistant.delta or agent.thought_delta paragraphs before tool calls when trimming hundreds of events', () => {
    expect(EVENT_LIMIT).toBe(1500)

    let state = createActivityState()
    let cursorSeq = 1

    // Paragraph 1 streamed in 5 chunks before a tool call
    const p1Chunks = ['Vou ', 'analisar ', 'a ', 'arquitetura ', 'do projeto.']
    for (let i = 0; i < p1Chunks.length; i += 1) {
      state = applyActivityEvent(state, {
        eventId: `p1-delta-${i}`,
        cursor: String(cursorSeq++),
        turnId: 'turn-long',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        agentId: 'agent:main',
        messageId: 'msg-long',
        content: p1Chunks[i],
        summary: p1Chunks[i],
      })
    }

    // Tool call 1 between Paragraph 1 and Paragraph 2
    state = applyActivityEvent(state, {
      eventId: 'tool-1-fin',
      cursor: String(cursorSeq++),
      turnId: 'turn-long',
      type: 'tool.finished',
      kind: 'tool',
      state: 'completed',
      agentId: 'agent:main',
      toolName: 'read_file',
      path: 'README.md',
      summary: 'Leu README.md',
    })

    // Paragraph 2 streamed in 4 chunks before another tool call
    const p2Chunks = ['Encontrei ', 'a configuração ', 'principal ', 'no README.']
    for (let i = 0; i < p2Chunks.length; i += 1) {
      state = applyActivityEvent(state, {
        eventId: `p2-delta-${i}`,
        cursor: String(cursorSeq++),
        turnId: 'turn-long',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        agentId: 'agent:main',
        messageId: 'msg-long',
        content: p2Chunks[i],
        summary: p2Chunks[i],
      })
    }

    // Tool call 2
    state = applyActivityEvent(state, {
      eventId: 'tool-2-fin',
      cursor: String(cursorSeq++),
      turnId: 'turn-long',
      type: 'tool.finished',
      kind: 'tool',
      state: 'completed',
      agentId: 'agent:main',
      toolName: 'edit_file',
      path: 'src/index.ts',
      summary: 'Editou src/index.ts',
    })

    // Flood with 200 more deltas for Paragraph 3 and trim with an explicit small limit (e.g. 20)
    const events: ConversationActivityEvent[] = [...state.events]
    for (let i = 0; i < 200; i += 1) {
      events.push({
        eventId: `p3-delta-${i}`,
        cursor: String(cursorSeq++),
        turnId: 'turn-long',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        agentId: 'agent:main',
        messageId: 'msg-long',
        content: `palavra-${i} `,
        summary: `palavra-${i} `,
      })
    }

    const trimmed = trimActivityEvents(events, 10)
    const timeline = buildTurnTimeline(trimmed, 'turn-long', 'msg-long')

    expect(timeline.map((item) => item.kind)).toEqual(['text', 'activity', 'text', 'activity', 'text'])
    expect(timeline[0]).toMatchObject({
      kind: 'text',
      content: 'Vou analisar a arquitetura do projeto.',
    })
    expect(timeline[2]).toMatchObject({
      kind: 'text',
      content: 'Encontrei a configuração principal no README.',
    })
    if (timeline[4]?.kind === 'text') {
      expect(timeline[4].content).toContain('palavra-0 ')
      expect(timeline[4].content).toContain('palavra-199 ')
    }
  })

  it('places completeContent before activity items in reconcileCompleteText when no text delta survived', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-tool-only',
        cursor: 'a.1',
        turnId: 'turn-reconcile',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'read_file',
        path: 'src/main.ts',
        summary: 'Leu src/main.ts',
      },
    ]

    const timeline = buildTurnTimeline(
      events,
      'turn-reconcile',
      'msg-reconcile',
      'Texto completo da resposta inicial.',
    )

    expect(timeline.map((item) => item.kind)).toEqual(['text', 'activity'])
    expect(timeline[0]).toMatchObject({
      kind: 'text',
      content: 'Texto completo da resposta inicial.',
    })
  })

  it('preserves signed data.cursor in parseConversationActivityEvent and throws cursor_invalid on SSE resync event', async () => {
    const parsed = parseConversationActivityEvent(
      {
        event_id: 'activity:turn_1:4',
        event_type: 'tool.finished',
        cursor: 'a.eyJwIjo0fQ.signed_hmac_token',
        agent_id: 'agent:main',
        summary: 'Leu arquivo',
        payload: { tool_name: 'read_file', status: 'succeeded' },
      },
      'activity:turn_1:4',
    )

    expect(parsed.cursor).toBe('a.eyJwIjo0fQ.signed_hmac_token')

    const mockClient = {
      request: async () => {
        throw new Error('not used')
      },
      stream: async () =>
        new Response('event: resync\ndata: {"reason":"cursor_invalid"}\n\n', {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    } as unknown as ApiClient

    await expect(
      streamConversationEvents(mockClient, 'conv-1', 'bad-cursor', {
        onEvent: () => {},
        onCursor: () => {},
      }),
    ).rejects.toSatisfy((err: unknown) => {
      return err instanceof ApiError && err.code === 'cursor_invalid' && err.status === 409
    })
  })

  it('renders CodeToolBatch collapsed by default for >= 2 tool actions with Portuguese summaries and expands on click', () => {
    const readEvents: ConversationActivityEvent[] = [
      {
        eventId: 'read-1',
        cursor: '1',
        turnId: 'turn-b1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'read_file',
        path: 'src/a.ts',
        summary: 'Leu src/a.ts',
      },
      {
        eventId: 'read-2',
        cursor: '2',
        turnId: 'turn-b1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'read_file',
        path: 'src/b.ts',
        summary: 'Leu src/b.ts',
      },
    ]

    const { rerender } = render(<TurnTimeline events={readEvents} mode="code" />)

    const toggle = screen.getByTestId('code-tool-batch-toggle')
    expect(toggle).toHaveTextContent('Leu 2 arquivos')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryAllByTestId('code-tool-call-block')).toHaveLength(0)

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByTestId('code-tool-call-block')).toHaveLength(2)

    // Homogeneous write_file batch
    const writeEvents: ConversationActivityEvent[] = [
      {
        eventId: 'write-1',
        cursor: '3',
        turnId: 'turn-b2',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'write_file',
        path: 'src/new1.ts',
        summary: 'Criou src/new1.ts',
      },
      {
        eventId: 'write-2',
        cursor: '4',
        turnId: 'turn-b2',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'write_file',
        path: 'src/new2.ts',
        summary: 'Criou src/new2.ts',
      },
    ]
    rerender(<TurnTimeline events={writeEvents} mode="code" />)
    expect(screen.getByTestId('code-tool-batch-toggle')).toHaveTextContent('Criou 2 arquivos')

    // Homogeneous edit_file batch with diff stats
    const editEvents: ConversationActivityEvent[] = [
      {
        eventId: 'edit-1',
        cursor: '5',
        turnId: 'turn-b3',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'edit_file',
        path: 'src/one.ts',
        addedLines: 10,
        removedLines: 2,
        summary: 'Editou src/one.ts',
      },
      {
        eventId: 'edit-2',
        cursor: '6',
        turnId: 'turn-b3',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'edit_file',
        path: 'src/two.ts',
        addedLines: 5,
        removedLines: 3,
        summary: 'Editou src/two.ts',
      },
    ]
    rerender(<TurnTimeline events={editEvents} mode="code" />)
    const editToggle = screen.getByTestId('code-tool-batch-toggle')
    expect(editToggle).toHaveTextContent('Editou 2 arquivos')
    expect(editToggle).toHaveTextContent('+15')
    expect(editToggle).toHaveTextContent('-5')

    // Heterogeneous / mixed batch with a running action
    const mixedEvents: ConversationActivityEvent[] = [
      ...readEvents,
      {
        eventId: 'cmd-1',
        cursor: '7',
        turnId: 'turn-b1',
        type: 'tool.started',
        kind: 'tool',
        state: 'waiting_tool',
        status: 'running',
        agentId: 'agent:main',
        toolName: 'run_command',
        command: 'npm test',
        summary: '$ npm test',
      },
    ]
    rerender(<TurnTimeline events={mixedEvents} mode="code" />)
    const mixedToggle = screen.getByTestId('code-tool-batch-toggle')
    expect(mixedToggle).toHaveTextContent('Executou 3 ações')
    expect(mixedToggle).toHaveTextContent('Bash(npm test)')

    // Same file read multiple times + search + bash summaries
    expect(
      /* same file read twice */
      readEvents.map((e) => ({ ...e, path: 'src/same.ts', filePath: 'src/same.ts' })),
    ).toHaveLength(2)
    rerender(
      <TurnTimeline
        events={readEvents.map((e, idx) => ({
          ...e,
          eventId: `same-read-${idx}`,
          path: 'src/same.ts',
          filePath: 'src/same.ts',
        }))}
        mode="code"
      />,
    )
    expect(screen.getByTestId('code-tool-batch-toggle')).toHaveTextContent(
      'Leu src/same.ts (2 trechos)',
    )

    rerender(
      <TurnTimeline
        events={[
          {
            eventId: 'search-1',
            cursor: '8',
            turnId: 'turn-s',
            type: 'tool.finished',
            kind: 'tool',
            state: 'completed',
            agentId: 'agent:main',
            toolName: 'search_files',
            label: 'useTimeline',
            summary: 'Buscou useTimeline',
          },
          {
            eventId: 'search-2',
            cursor: '9',
            turnId: 'turn-s',
            type: 'tool.finished',
            kind: 'tool',
            state: 'completed',
            agentId: 'agent:main',
            toolName: 'glob_files',
            path: 'src/**/*.tsx',
            summary: 'Glob src/**/*.tsx',
          },
        ]}
        mode="code"
      />,
    )
    expect(screen.getByTestId('code-tool-batch-toggle')).toHaveTextContent('Buscou em 2 padrões')

    rerender(
      <TurnTimeline
        events={[
          {
            eventId: 'bash-1',
            cursor: '10',
            turnId: 'turn-cmd',
            type: 'tool.finished',
            kind: 'tool',
            state: 'completed',
            agentId: 'agent:main',
            toolName: 'run_command',
            command: 'npm run lint',
            summary: '$ npm run lint',
          },
          {
            eventId: 'bash-2',
            cursor: '11',
            turnId: 'turn-cmd',
            type: 'tool.finished',
            kind: 'tool',
            state: 'completed',
            agentId: 'agent:main',
            toolName: 'run_command',
            command: 'npm test',
            summary: '$ npm test',
          },
        ]}
        mode="code"
      />,
    )
    expect(screen.getByTestId('code-tool-batch-toggle')).toHaveTextContent('Executou 2 comandos')
  })

  it('renders single tool action compactly with diff collapsed by default in TurnTimeline', () => {
    const singleEdit: ConversationActivityEvent[] = [
      {
        eventId: 'single-edit-1',
        cursor: '1',
        turnId: 'turn-single',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'edit_file',
        path: 'README.md',
        addedLines: 2,
        removedLines: 1,
        summary: 'Editou README.md',
        structuredPatch: [
          {
            oldStart: 1,
            oldLines: 1,
            newStart: 1,
            newLines: 2,
            lines: ['-# Old Title', '+# New Title', '+Subtitle'],
          },
        ],
      },
    ]

    render(<TurnTimeline events={singleEdit} mode="code" />)

    const block = screen.getByTestId('code-tool-call-block')
    expect(block).toHaveTextContent('Update(README.md)')
    expect(screen.queryByTestId('code-tool-diff-view')).not.toBeInTheDocument()

    // Clicking header expands the diff
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByTestId('code-tool-diff-view')).toBeInTheDocument()
    expect(screen.getByText('+# New Title')).toBeInTheDocument()
  })

  it('defaults SubagentTaskContainer to collapsed once completed and expands when clicking Expandir', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'main-tool-1',
        cursor: '1',
        turnId: 'turn-sub',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'read_file',
        path: 'README.md',
        summary: 'Leu README.md',
      },
      {
        eventId: 'sub-tool-1',
        cursor: '2',
        turnId: 'turn-sub',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:sub:1',
        parentAgentId: 'agent:main',
        agentName: 'Explorador',
        toolName: 'read_file',
        path: 'src/deep.ts',
        summary: 'Leu src/deep.ts',
      },
    ]

    render(<TurnTimeline events={events} mode="code" turnCompleted={true} />)

    const subToggle = screen.getByTestId('subagent-task-toggle')
    expect(subToggle).toHaveAttribute('aria-expanded', 'false')
    expect(subToggle).toHaveTextContent(/Expandir/i)
    expect(screen.queryByText('src/deep.ts')).not.toBeInTheDocument()

    fireEvent.click(subToggle)
    expect(subToggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('src/deep.ts')).toBeInTheDocument()
  })
})
