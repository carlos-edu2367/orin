# Chat Interleaved Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ChatPage` show an assistant turn's narration text and its tool/agent action cards in the order they actually happened, instead of the full text followed by a replayed action log.

**Architecture:** For each assistant message, resolve its `turn_id` from the `assistant.delta`/`assistant.completed` events that already name it, then fold that turn's `activity.events` (already cursor-ordered) into an alternating list of text segments and activity groups, reusing the exact grouping rules and rendering components `ActivityStream` already uses today. Any turn a message can't claim (no resolvable events, e.g. very old data past the 500-event ring buffer, or a turn still running before its first delta lands) keeps rendering through the existing flat `ActivityStream`, so nothing is ever dropped.

**Tech Stack:** React 18, TypeScript, Vitest + Testing Library (`frontend/tests/unit`), `motion/react` for animation (unchanged, reused as-is).

## Global Constraints

- No backend changes. The API, SSE payload shape, and `Conversation`/`ConversationActivityEvent` types are untouched.
- No new visual components. `MarkdownMessage`, `ActivityCard`, `AgentBirth`, `AgentExchange` render exactly as they do today — only their position in the DOM changes.
- Every existing test in `frontend/tests/unit/activitySummary.test.ts`, `ChatPage.test.tsx`, `activityReducer.test.ts` must still pass unmodified except where this plan explicitly extends `ChatPage.test.tsx`.
- Design reference: `docs/superpowers/specs/2026-08-10-chat-interleaved-timeline-design.md`.

---

### Task 1: Turn timeline algorithm

**Files:**
- Modify: `frontend/src/features/conversations/activitySummary.ts:47,59,66` (add `export` to `isRenderable`, `groupingKey`, `groupLabel` — no other change)
- Create: `frontend/src/features/conversations/turnTimelineFold.ts`
- Test: `frontend/tests/unit/turnTimelineFold.test.ts`

**Interfaces:**
- Consumes: `ActivityGroup`, `ConversationActivityEvent` from `./activityTypes`; `isRenderable(event, settled)`, `groupingKey(event)`, `groupLabel(group)` from `./activitySummary` (all three currently private — this task exports them, changing nothing else).
- Produces (used by Task 2 and Task 3):
  - `type TimelineTextItem = { id: string; kind: 'text'; content: string }`
  - `type TimelineActivityItem = { id: string; kind: 'activity'; group: ActivityGroup }`
  - `type TimelineItem = TimelineTextItem | TimelineActivityItem`
  - `resolveTurnId(events: ConversationActivityEvent[], messageId: string): string | null`
  - `buildTurnTimeline(events: ConversationActivityEvent[], turnId: string, messageId: string): TimelineItem[]`
  - `buildMessageTimelines(messages: { message_id: string; role: string }[], events: ConversationActivityEvent[]): { timelines: Map<string, TimelineItem[]>; claimedTurnIds: Set<string> }` — `timelines` only ever holds non-empty arrays.

- [ ] **Step 1: Export the three grouping helpers `activitySummary.ts` already has**

In `frontend/src/features/conversations/activitySummary.ts`, add `export` to the three function declarations (signatures and bodies unchanged):

```ts
export function isRenderable(event: ConversationActivityEvent, settled: Set<string>): boolean {
```
```ts
export function groupingKey(event: ConversationActivityEvent): string {
```
```ts
export function groupLabel(group: ActivityGroup): string {
```

- [ ] **Step 2: Run the existing suite to confirm this export-only change is a no-op**

Run: `cd frontend && npx vitest run tests/unit/activitySummary.test.ts`
Expected: all existing tests still PASS (this step only adds visibility, no behavior changed).

- [ ] **Step 3: Write the failing tests for the new module**

Create `frontend/tests/unit/turnTimelineFold.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { buildMessageTimelines, buildTurnTimeline, resolveTurnId } from '../../src/features/conversations/turnTimelineFold'
import { kindFor, stateFor, type ConversationActivityEvent } from '../../src/features/conversations/activityTypes'

let sequence = 0

function event(partial: Partial<ConversationActivityEvent> & { type: string }): ConversationActivityEvent {
  sequence += 1
  const toolKind = partial.toolKind
  return {
    eventId: partial.eventId ?? `activity:turn-1:${sequence}`,
    cursor: `a.${sequence}`,
    kind: partial.kind ?? kindFor(partial.type, toolKind),
    state: partial.state ?? stateFor(partial.type, partial.status, partial.errorCode),
    agentId: partial.agentId ?? 'agent:c:main',
    summary: partial.summary ?? partial.type,
    turnId: partial.turnId ?? 'turn-1',
    ...partial,
  }
}

describe('buildTurnTimeline', () => {
  it('interleaves narration text with the action that happened in between', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Vou criar o subagente. ' }),
      event({ type: 'agent.created', label: 'Analista', summary: 'Criou o agente Analista', agentId: 'agent:c:sub' }),
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Pronto, agora vou consultar o site.' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline.map((item) => item.kind)).toEqual(['text', 'activity', 'text'])
    expect(timeline[0]).toMatchObject({ kind: 'text', content: 'Vou criar o subagente. ' })
    if (timeline[1].kind === 'activity') expect(timeline[1].group.label).toBe('Criou o agente Analista')
    expect(timeline[2]).toMatchObject({ kind: 'text', content: 'Pronto, agora vou consultar o site.' })
  })

  it('does not merge two tool cards separated by narration text', () => {
    const events = [
      event({ type: 'tool.started', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1', status: 'succeeded', summary: 'Leu a.txt' }),
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Agora o outro arquivo.' }),
      event({ type: 'tool.started', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-2' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-2', status: 'succeeded', summary: 'Leu b.txt' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline.map((item) => item.kind)).toEqual(['activity', 'text', 'activity'])
    if (timeline[0].kind === 'activity') expect(timeline[0].group.count).toBe(1)
    if (timeline[2].kind === 'activity') expect(timeline[2].group.count).toBe(1)
  })

  it('ignores events from other turns', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Deste turno.' }),
      event({ type: 'assistant.delta', messageId: 'msg-9', content: 'De outro turno.', turnId: 'turn-9' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline).toEqual([{ id: expect.any(String), kind: 'text', content: 'Deste turno.' }])
  })
})

describe('resolveTurnId', () => {
  it("finds the turn from the message's own delta event", () => {
    const events = [event({ type: 'assistant.delta', messageId: 'msg-1', content: 'oi', turnId: 'turn-7' })]
    expect(resolveTurnId(events, 'msg-1')).toBe('turn-7')
  })

  it('returns null when no event names this message', () => {
    const events = [event({ type: 'assistant.delta', messageId: 'msg-2', content: 'oi' })]
    expect(resolveTurnId(events, 'msg-1')).toBeNull()
  })
})

describe('buildMessageTimelines', () => {
  it('claims a turn only for the message whose timeline is non-empty', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Oi' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1', status: 'succeeded', summary: 'Leu a.txt', turnId: 'turn-2' }),
    ]
    const messages = [
      { message_id: 'msg-1', role: 'assistant' as const },
      { message_id: 'msg-0', role: 'user' as const },
    ]

    const { timelines, claimedTurnIds } = buildMessageTimelines(messages, events)

    expect(timelines.get('msg-1')?.map((item) => item.kind)).toEqual(['text'])
    expect(claimedTurnIds).toEqual(new Set(['turn-1']))
  })
})
```

- [ ] **Step 4: Run the new test file to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/turnTimelineFold.test.ts`
Expected: FAIL — `Cannot find module '../../src/features/conversations/turnTimelineFold'`

- [ ] **Step 5: Implement `turnTimelineFold.ts`**

Create `frontend/src/features/conversations/turnTimelineFold.ts`:

```ts
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { groupLabel, groupingKey, isRenderable } from './activitySummary'

export type TimelineTextItem = { id: string; kind: 'text'; content: string }
export type TimelineActivityItem = { id: string; kind: 'activity'; group: ActivityGroup }
export type TimelineItem = TimelineTextItem | TimelineActivityItem

/**
 * The turn a message belongs to, found through the message's own delta or
 * completed event — the public API has no direct message→turn field.
 */
export function resolveTurnId(events: ConversationActivityEvent[], messageId: string): string | null {
  const owner = events.find((event) => event.kind === 'message' && event.messageId === messageId && event.turnId)
  return owner?.turnId ?? null
}

/**
 * One assistant turn folded into the order it actually happened: narration
 * text broken wherever a tool or agent event landed, instead of prose
 * followed by a replayed report. Reuses the same grouping rules as
 * `summarizeActivities` so a run of same-family tool calls still collapses
 * into one card — just interrupted by whatever text fell between them.
 */
export function buildTurnTimeline(events: ConversationActivityEvent[], turnId: string, messageId: string): TimelineItem[] {
  const turnEvents = events.filter((event) => event.turnId === turnId)
  const settled = new Set(
    turnEvents.filter((event) => event.type === 'tool.finished' && event.invocationId).map((event) => event.invocationId as string),
  )

  const items: TimelineItem[] = []
  let textBuffer = ''
  let textId = ''
  let group: ActivityGroup | null = null

  const flushGroup = () => {
    if (!group) return
    items.push({ id: `activity:${group.id}:${group.events[0].eventId}`, kind: 'activity', group })
    group = null
  }
  const flushText = () => {
    if (!textBuffer) return
    items.push({ id: textId, kind: 'text', content: textBuffer })
    textBuffer = ''
    textId = ''
  }

  for (const event of turnEvents) {
    if (event.kind === 'message') {
      if (event.messageId !== messageId) continue
      flushGroup()
      if (!event.content) continue
      if (!textBuffer) textId = `text:${event.eventId}`
      textBuffer += event.content
      continue
    }
    if (!isRenderable(event, settled)) continue
    flushText()
    const key = groupingKey(event)
    if (group && group.id === key && group.kind === event.kind) {
      group.count += 1
      group.events.push(event)
      group.state = event.state
      group.failed = group.failed || event.state === 'failed'
      group.label = groupLabel(group)
      continue
    }
    flushGroup()
    group = { id: key, kind: event.kind, state: event.state, label: '', count: 1, events: [event], agentId: event.agentId, agentName: event.agentName, failed: event.state === 'failed' }
    group.label = groupLabel(group)
  }
  flushGroup()
  flushText()
  return items
}

/**
 * Every assistant message paired with its own interleaved timeline, plus the
 * set of turn ids those timelines account for. A turn that no message could
 * claim (unresolvable, or still running before its first delta lands) is
 * left out of `claimedTurnIds` on purpose — the caller renders it through the
 * existing flat activity stream instead of dropping it.
 */
export function buildMessageTimelines(
  messages: { message_id: string; role: string }[],
  events: ConversationActivityEvent[],
): { timelines: Map<string, TimelineItem[]>; claimedTurnIds: Set<string> } {
  const timelines = new Map<string, TimelineItem[]>()
  const claimedTurnIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const turnId = resolveTurnId(events, message.message_id)
    if (!turnId) continue
    const timeline = buildTurnTimeline(events, turnId, message.message_id)
    if (timeline.length === 0) continue
    timelines.set(message.message_id, timeline)
    claimedTurnIds.add(turnId)
  }
  return { timelines, claimedTurnIds }
}
```

- [ ] **Step 6: Run the test file to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/turnTimelineFold.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/conversations/activitySummary.ts frontend/src/features/conversations/turnTimelineFold.ts frontend/tests/unit/turnTimelineFold.test.ts
git commit -m "feat(chat): fold a turn's events into an interleaved text/activity timeline"
```

---

### Task 2: `TurnTimeline` presentational component

**Files:**
- Modify: `frontend/src/features/conversations/ActivityStream.tsx:41` (rename private `renderGroup` to exported `renderActivityGroup`, update its one call site in the same file)
- Create: `frontend/src/features/conversations/TurnTimeline.tsx`
- Modify: `frontend/src/styles/agentos.css:268` (add one rule after `.bubble__retry`)
- Test: `frontend/tests/unit/TurnTimeline.test.tsx`

**Interfaces:**
- Consumes: `TimelineItem` from `./turnTimelineFold` (Task 1); `MarkdownMessage` from `./MarkdownMessage`; `renderActivityGroup(group: ActivityGroup): ReactNode` from `./ActivityStream`.
- Produces (used by Task 3): `TurnTimeline({ items }: { items: TimelineItem[] })` component, CSS class `.turn-timeline`.

- [ ] **Step 1: Export the group renderer `ActivityStream.tsx` already has**

In `frontend/src/features/conversations/ActivityStream.tsx`, rename the function and its one internal call:

```ts
export function renderActivityGroup(group: ActivityGroup) {
```

And in the component body, change:

```ts
{groups.map((group) => <li key={group.id + group.events[0].eventId}>{renderGroup(group)}</li>)}
```

to:

```ts
{groups.map((group) => <li key={group.id + group.events[0].eventId}>{renderActivityGroup(group)}</li>)}
```

- [ ] **Step 2: Run the existing ActivityStream-adjacent tests to confirm no regression**

Run: `cd frontend && npx vitest run tests/unit/activitySummary.test.ts tests/unit/ChatPage.test.tsx`
Expected: all PASS (this step only renames a private function to an exported one).

- [ ] **Step 3: Write the failing component test**

Create `frontend/tests/unit/TurnTimeline.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TurnTimeline } from '../../src/features/conversations/TurnTimeline'
import type { TimelineItem } from '../../src/features/conversations/turnTimelineFold'

describe('TurnTimeline', () => {
  it('renders text and activity items in the order given', () => {
    const items: TimelineItem[] = [
      { id: 'text:1', kind: 'text', content: 'Vou criar o subagente.' },
      {
        id: 'activity:agent:c:sub:agent.created:2',
        kind: 'activity',
        group: {
          id: 'agent:c:sub:agent.created:',
          kind: 'agent',
          state: 'completed',
          label: 'Criou o agente Analista',
          count: 1,
          agentId: 'agent:c:sub',
          failed: false,
          events: [{
            eventId: '2', cursor: 'a.2', type: 'agent.created', kind: 'agent', state: 'completed',
            agentId: 'agent:c:sub', summary: 'Criou o agente Analista', label: 'Analista',
          }],
        },
      },
    ]

    const { container } = render(<TurnTimeline items={items} />)

    expect(screen.getByText('Vou criar o subagente.')).toBeInTheDocument()
    expect(screen.getByText(/Criou o agente/)).toBeInTheDocument()
    const order = container.querySelector('.turn-timeline')!.children
    expect(order[0].className).toBe('turn-timeline__text')
    expect(order[1].className).toBe('turn-timeline__activity')
  })
})
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/TurnTimeline.test.tsx`
Expected: FAIL — `Cannot find module '../../src/features/conversations/TurnTimeline'`

- [ ] **Step 5: Implement `TurnTimeline.tsx`**

Create `frontend/src/features/conversations/TurnTimeline.tsx`:

```tsx
import { AnimatePresence } from 'motion/react'
import { MarkdownMessage } from './MarkdownMessage'
import { renderActivityGroup } from './ActivityStream'
import type { TimelineItem } from './turnTimelineFold'

type TurnTimelineProps = {
  items: TimelineItem[]
}

/**
 * One assistant turn, told in order: narration and the action it triggered,
 * back to back, instead of the finished text followed by a replayed log.
 */
export function TurnTimeline({ items }: TurnTimelineProps) {
  return (
    <div className="turn-timeline">
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <div key={item.id} className={`turn-timeline__${item.kind}`}>
            {item.kind === 'text' ? <MarkdownMessage content={item.content} /> : renderActivityGroup(item.group)}
          </div>
        ))}
      </AnimatePresence>
    </div>
  )
}
```

Add to `frontend/src/styles/agentos.css` right after the `.bubble__retry` rule (line 268):

```css
.turn-timeline { display: flex; flex-direction: column; gap: 8px; }
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run tests/unit/TurnTimeline.test.tsx`
Expected: PASS, 1 test.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/conversations/ActivityStream.tsx frontend/src/features/conversations/TurnTimeline.tsx frontend/src/styles/agentos.css frontend/tests/unit/TurnTimeline.test.tsx
git commit -m "feat(chat): add TurnTimeline component for interleaved turn rendering"
```

---

### Task 3: Wire the timeline into `ChatPage`

**Files:**
- Modify: `frontend/src/features/conversations/ChatPage.tsx:1-17` (imports), `:144` (new `useMemo`s after the `messages` memo), `:221-236` (render swap)
- Test: `frontend/tests/unit/ChatPage.test.tsx`

**Interfaces:**
- Consumes: `buildMessageTimelines` from `./turnTimelineFold` (Task 1); `TurnTimeline` from `./TurnTimeline` (Task 2).

- [ ] **Step 1: Write the failing order-assertion test**

In `frontend/tests/unit/ChatPage.test.tsx`, add this test inside the `describe('ChatPage', ...)` block (after the existing `'rebuilds a finished conversation...'` test):

```tsx
  it('shows the action that happened between two pieces of narration in the order it happened', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Crie o resumo', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Vou consultar o site. Pronto, terminei.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'assistant.delta', 'Vou consultar o site. ', { message_id: 'msg-2', content: 'Vou consultar o site. ' }),
        activity(2, 'tool.finished', 'Consultou example.com', { tool_name: 'fetch_url', tool_kind: 'web', status: 'succeeded', invocation_id: 'call-1', label: 'example.com' }),
        activity(3, 'assistant.delta', 'Pronto, terminei.', { message_id: 'msg-2', content: 'Pronto, terminei.' }),
        activity(4, 'turn.completed', 'Resposta concluída', {}),
      ],
    }))

    renderChat()

    const first = await screen.findByText('Vou consultar o site.', { exact: false })
    const action = await screen.findByText('Consultou example.com')
    const second = await screen.findByText('Pronto, terminei.', { exact: false })

    expect(first.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(action.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run tests/unit/ChatPage.test.tsx -t "order it happened"`
Expected: FAIL — the three texts render, but `action` currently comes after both `first` and `second` (it's appended by the trailing `<ActivityStream>`, not between them), so the second assertion fails.

- [ ] **Step 3: Add the timeline imports and memos to `ChatPage.tsx`**

Change the import block (lines 12-17) from:

```ts
import { ActivityStream } from './ActivityStream'
import { AgentPulse, modeFromEvents } from './AgentPulse'
import { Composer } from './Composer'
import { MarkdownMessage } from './MarkdownMessage'
import { activityReducer, createActivityState } from './activityReducer'
import type { ConversationActivityEvent } from './activityTypes'
```

to:

```ts
import { ActivityStream } from './ActivityStream'
import { AgentPulse, modeFromEvents } from './AgentPulse'
import { Composer } from './Composer'
import { MarkdownMessage } from './MarkdownMessage'
import { TurnTimeline } from './TurnTimeline'
import { activityReducer, createActivityState } from './activityReducer'
import type { ConversationActivityEvent } from './activityTypes'
import { buildMessageTimelines } from './turnTimelineFold'
```

Right after the `messages` memo (ends at line 144, `}, [conversation?.messages, streamedByMessage, pendingUserMessage])`), add:

```ts
  // Each assistant message gets its own text/action timeline, folded from the
  // turn it belongs to; a turn no message could claim (still running before
  // its first delta, or older than the 500-event window) stays visible
  // through the flat stream below instead of disappearing.
  const { timelines: timelinesByMessage, claimedTurnIds } = useMemo(
    () => buildMessageTimelines(messages, activity.events),
    [messages, activity.events],
  )
  const unclaimedEvents = useMemo(
    () => activity.events.filter((event) => !claimedTurnIds.has(event.turnId ?? '')),
    [activity.events, claimedTurnIds],
  )
```

- [ ] **Step 4: Swap the render block**

Change (lines 221-236):

```tsx
            {messages.map((item) => (
              <motion.article
                key={item.message_id}
                className={`bubble bubble--${item.role}`}
                initial={reduced ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
              >
                {item.role === 'assistant' && item.content
                  ? <MarkdownMessage content={item.content} />
                  : <p>{item.content || placeholderFor(item)}</p>}
                {item.retryable && <span className="bubble__retry">Você pode reenviar esta mensagem.</span>}
              </motion.article>
            ))}

            <ActivityStream events={activity.events} />
```

to:

```tsx
            {messages.map((item) => {
              const timeline = item.role === 'assistant' ? timelinesByMessage.get(item.message_id) : undefined
              return (
                <motion.article
                  key={item.message_id}
                  className={`bubble bubble--${item.role}`}
                  initial={reduced ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
                >
                  {timeline
                    ? <TurnTimeline items={timeline} />
                    : item.role === 'assistant' && item.content
                      ? <MarkdownMessage content={item.content} />
                      : <p>{item.content || placeholderFor(item)}</p>}
                  {item.retryable && <span className="bubble__retry">Você pode reenviar esta mensagem.</span>}
                </motion.article>
              )
            })}

            <ActivityStream events={unclaimedEvents} />
```

- [ ] **Step 5: Run the full ChatPage suite to verify everything passes**

Run: `cd frontend && npx vitest run tests/unit/ChatPage.test.tsx`
Expected: PASS, including the new test and every pre-existing one (the `messages: []` and single-tool-running fixtures still render through `unclaimedEvents`, since no assistant message exists there to claim any turn).

- [ ] **Step 6: Run the entire frontend unit suite for a final regression check**

Run: `cd frontend && npx vitest run`
Expected: PASS, no failures anywhere.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/conversations/ChatPage.tsx frontend/tests/unit/ChatPage.test.tsx
git commit -m "feat(chat): interleave a turn's narration and actions in ChatPage"
```

---

### Task 4: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Try a live browser pass**

Check whether a runnable dev target exists (`frontend/.claude/launch.json` or repo root `.claude/launch.json`, plus whether the API (`src/agentos`) can be started locally with seed data). If a full stack is reasonably available, start it, open a conversation that used at least one tool mid-answer, and confirm visually: narration paragraph → action card → next paragraph, in that order, with the same colors/icons/animations as before. Capture a screenshot for the user.

- [ ] **Step 2: If no full stack is available, say so explicitly**

The existing Playwright visual spec (`frontend/tests/visual/live-surface.spec.ts`) needs a running API at `AGENTOS_BASE_URL` (default `http://127.0.0.1:8000`) with real conversation history — it takes plain screenshots for manual review, not pixel-diff assertions, so there is no baseline to update. If that stack isn't available in this environment, report that unit/component tests (Tasks 1-3) are the verification evidence, and browser confirmation is still outstanding.
