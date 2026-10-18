# Collapsible Thinking UI, Bottom Work Duration & On-Demand Parallel Subagent Scaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `ThoughtBlock` collapsed by default while streaming (`▸ Pensando...`) and expandable on click, move `"Trabalhou por Xs"` to the bottom of the assistant response, and enable running many subagents in parallel with on-demand worker scaling and elastic queueing without hitting `"The subagent budget for this turn is exhausted"`.

**Architecture:** On the frontend (`TurnTimeline.tsx`, `ChatPage.tsx`), unify `ThoughtBlock` so both `streaming` and `completed` states render a collapsible toggle button (`expanded = false` by default) that preserves user expansion across state transitions, keep `ParallelSubagentsGroup` waves intact when subagents emit `thought_delta`, and render `turn-work-duration` at the end of the timeline/response. On the backend (`session.py`, `runtime.py`, `conversation_worker.py`, `chat.py`), remove the hardcoded 4-subagent-per-turn cutoff by default (`max_subagents_per_turn = None`), scale worker pools on demand up to `MAX_CONCURRENT_SUBAGENTS = 16` (`ORIN_MAX_CONCURRENT_SUBAGENTS`), guard concurrent subagent execution with a `BoundedSemaphore` so bursts larger than 16 queue and complete cleanly without `SUBAGENT_LIMIT`, and abort queued subagents immediately if the turn is cancelled.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, Playwright (Frontend); Python 3.12, SQLAlchemy/SQLite, `concurrent.futures.ThreadPoolExecutor`, `threading.BoundedSemaphore`, Pytest (Backend).

**Spec:** `docs/superpowers/specs/2026-10-17-collapsible-thinking-and-elastic-subagent-scaling-design.md`

## Global Constraints

- `ThoughtBlock` MUST start collapsed (`expanded = false`) during `streaming` (`▸ Pensando...`) and only render `.thought-block__body` when `expanded && thought.content`.
- If the user expands `ThoughtBlock` while `streaming`, it MUST stay expanded when transitioning to `completed` (`▾ Pensou por Xs`).
- `"Trabalhou por Xs"` (`data-testid="turn-work-duration"`) MUST be rendered at the bottom of `TurnTimeline` (after `</AnimatePresence>`) and after `<MarkdownMessage>` in `ChatPage.tsx`.
- By default (`ORIN_MAX_SUBAGENTS_PER_TURN` unset and `max_subagents_per_turn=None`), turns MUST NOT fail subagents with `SUBAGENT_LIMIT` (`"The subagent budget for this turn is exhausted"`).
- Concurrent subagent workers MUST scale on demand (`min(batch_size, max_concurrent_subagents)`) up to `MAX_CONCURRENT_SUBAGENTS = 16`, queueing excess subagents in the pool until a slot opens.

---

### Task 1: Frontend — Collapsible Streaming `ThoughtBlock`, Bottom `"Trabalhou por Xs"`, and Subagent Wave Preservation

**Files:**
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx:211-269`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx:1081-1259`
- Modify: `frontend/src/features/conversations/ChatPage.tsx:1700-1714`
- Test: `frontend/tests/unit/TurnTimeline.test.tsx`
- Test: `frontend/src/features/conversations/mediaAndMetricsUx.test.tsx`

**Interfaces:**
- Consumes: `TimelineThoughtData` (`id`, `turnId`, `content`, `status: 'streaming' | 'completed'`, `durationMs?: number`), `formatTurnWorkDuration(turnStartedAt, turnFinishedAt)`
- Produces: Collapsible `ThoughtBlock` (`data-testid="thought-block-streaming"` / `data-testid="thought-block-completed"` with `data-testid="thought-block-toggle"` in both states), bottom-positioned `data-testid="turn-work-duration"`.

- [ ] **Step 1: Write the failing unit tests in `frontend/tests/unit/TurnTimeline.test.tsx` and `frontend/src/features/conversations/mediaAndMetricsUx.test.tsx`**

Update `frontend/tests/unit/TurnTimeline.test.tsx` around line 327 to test:
1. `ThoughtBlock` while streaming is collapsed by default (`aria-expanded="false"`, `thought.content` not in DOM), expands when clicked to show live `thought.content`, and stays expanded when transitioning to `completed` (`Pensou por 4s`).
2. `ThoughtBlock` when not clicked during streaming stays collapsed when transitioning to `completed`, and expands on click.
3. Parallel subagents emitting `agent.thought_delta` do not shatter `ParallelSubagentsGroup`.

```tsx
  it('renders ThoughtBlock collapsed by default while streaming, expands on click, and preserves expansion when completed', () => {
    const streamingItems: TimelineItem[] = [
      {
        id: 'thought:1',
        kind: 'thought',
        thought: {
          id: 'thought:1',
          turnId: 'turn-t1',
          content: 'Analisando a causa raiz do problema...',
          status: 'streaming',
        },
      } as unknown as TimelineItem,
    ]

    const { rerender } = render(<TurnTimeline items={streamingItems} mode="code" />)

    const streamingBlock = screen.getByTestId('thought-block-streaming')
    expect(streamingBlock).toBeInTheDocument()
    const toggleBtn = screen.getByTestId('thought-block-toggle')
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Pensando...')).toHaveClass('agent-pulse__label--shimmer')
    expect(screen.queryByText('Analisando a causa raiz do problema...')).not.toBeInTheDocument()

    // Click to expand while still thinking
    fireEvent.click(toggleBtn)
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Analisando a causa raiz do problema...')).toBeInTheDocument()

    const completedItems: TimelineItem[] = [
      {
        id: 'thought:1',
        kind: 'thought',
        thought: {
          id: 'thought:1',
          turnId: 'turn-t1',
          content: 'Analisando a causa raiz do problema...',
          status: 'completed',
          durationMs: 4200,
        },
      } as unknown as TimelineItem,
    ]

    rerender(<TurnTimeline items={completedItems} mode="code" />)

    const completedBtn = screen.getByRole('button', { name: /Pensou por 4s/i })
    expect(completedBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Analisando a causa raiz do problema...')).toBeInTheDocument()

    // Click to collapse
    fireEvent.click(completedBtn)
    expect(completedBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Analisando a causa raiz do problema...')).not.toBeInTheDocument()
  })
```

And in `frontend/src/features/conversations/mediaAndMetricsUx.test.tsx`, update the `turn-work-duration` test to assert that `turn-work-duration` appears **after** the response text in DOM order:

```tsx
    const textEl = screen.getByText('Resposta final pronta.')
    const durationEl = screen.getByTestId('turn-work-duration')
    expect(durationEl).toHaveTextContent('Trabalhou por 19s')
    expect(
      Boolean(textEl.compareDocumentPosition(durationEl) & Node.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true)
```

- [ ] **Step 2: Run the frontend tests to verify they fail (RED)**

Run: `npm --prefix frontend test -- --run tests/unit/TurnTimeline.test.tsx src/features/conversations/mediaAndMetricsUx.test.tsx`
Expected: FAIL (`Analisando a causa raiz do problema...` is visible while streaming before clicking; `turn-work-duration` precedes `Resposta final pronta.`).

- [ ] **Step 3: Implement collapsible streaming `ThoughtBlock`, subagent thought wave handling, and bottom `"Trabalhou por Xs"` in `TurnTimeline.tsx` and `ChatPage.tsx`**

In `frontend/src/features/conversations/TurnTimeline.tsx`:
1. Update `ThoughtBlock` (`lines 211-269`) so both `streaming` and `completed` use the toggle button (`expanded` defaults to `false`):
```tsx
function ThoughtBlock({
  thought,
  turnCompleted = false,
}: {
  thought: TimelineThoughtData
  turnCompleted?: boolean
}) {
  const isStreaming = thought.status === 'streaming' && !turnCompleted
  const [expanded, setExpanded] = useState(false)

  const durationSeconds =
    typeof thought.durationMs === 'number' && Number.isFinite(thought.durationMs) && thought.durationMs >= 500
      ? Math.max(1, Math.round(thought.durationMs / 1000))
      : null

  if (isStreaming) {
    return (
      <div
        className="thought-block thought-block--streaming"
        data-testid="thought-block-streaming"
      >
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          aria-expanded={expanded}
          data-testid="thought-block-toggle"
          className="thought-block__toggle"
        >
          <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          <span className="agent-pulse__dot" aria-hidden="true" />
          <span className="agent-pulse__label--shimmer">Pensando...</span>
        </button>
        {expanded && thought.content && (
          <div className="thought-block__body">
            {thought.content}
          </div>
        )}
      </div>
    )
  }

  const summaryLabel = durationSeconds !== null ? `Pensou por ${durationSeconds}s` : 'Pensou'

  return (
    <div
      className="thought-block thought-block--completed"
      data-testid="thought-block-completed"
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid="thought-block-toggle"
        className="thought-block__toggle"
      >
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <span>{summaryLabel}</span>
      </button>
      {expanded && thought.content && (
        <div className="thought-block__body">
          {thought.content}
        </div>
      )}
    </div>
  )
}
```
2. In `TurnTimeline.tsx` (`lines 1169-1190`), allow subagent `thought` items inside a `subagentWave` lookahead so a subagent emitting `agent.thought_delta` does not split a `ParallelSubagentsGroup`.
3. In `TurnTimeline.tsx` (`lines 1085-1092` -> move after `</AnimatePresence>` at `line 1257`), move `workDurationLabel` to the bottom of `.turn-timeline` with `pt-1`.
4. In `frontend/src/features/conversations/ChatPage.tsx` (`lines 1702-1713`), render `<MarkdownMessage ... />` first and `{turnWorkDurationLabel && ...}` right below it with `pt-1`.

- [ ] **Step 4: Run the frontend unit tests to verify they pass (GREEN)**

Run: `npm --prefix frontend test -- --run tests/unit/TurnTimeline.test.tsx src/features/conversations/mediaAndMetricsUx.test.tsx`
Expected: PASS.

---

### Task 2: Backend — On-Demand Subagent Worker Scaling, Unlimited Per-Turn Default, Semaphore Queueing & Adversarial Guards

**Files:**
- Modify: `src/agentos/agentic/session.py:107-130, 736-850, 892-910, 1400-1475, 1784-1827, 2350-2353`
- Modify: `src/agentos/agentic/runtime.py:25-30, 1640-1660`
- Modify: `src/agentos/browser/conversation_worker.py:42-45`
- Modify: `src/agentos/conversations/chat.py:431-572`
- Test: `tests/unit/agentic/test_turn_session.py`
- Test: `tests/unit/agentic/test_agentic_runtime_loop.py`

**Interfaces:**
- Produces:
  - `MAX_CONCURRENT_SUBAGENTS: int` (default `16`, configurable via `ORIN_MAX_CONCURRENT_SUBAGENTS`) in `src/agentos/agentic/session.py`
  - `MAX_SUBAGENTS_PER_TURN: int | None` (default `None` unless `ORIN_MAX_SUBAGENTS_PER_TURN` is set to a positive integer)
  - `TurnSession(..., max_subagents_per_turn: int | None = ..., max_concurrent_subagents: int = ...)`
  - `MAX_PARALLEL_TOOLS = 8` and `MAX_PARALLEL_SUBAGENT_TOOLS = 16` in `src/agentos/agentic/runtime.py`
  - `MAX_AGENT_PAGES = 20` in `src/agentos/browser/conversation_worker.py`

- [ ] **Step 1: Write failing backend unit and adversarial tests in `tests/unit/agentic/test_turn_session.py` and `tests/unit/agentic/test_agentic_runtime_loop.py`**

Add tests to `tests/unit/agentic/test_turn_session.py`:
1. `test_many_subagents_run_in_parallel_without_exhausting_turn_budget`: Spawns 10 subagents in parallel in a single `_ask_agents` batch and via `_run_subagent`, plus a second wave of 6 subagents in the same turn (16 total), verifying all 16 succeed and none return `SUBAGENT_LIMIT`.
2. `test_subagent_workers_scale_on_demand_and_queue_when_exceeding_max_concurrent_workers`: Configures `max_concurrent_subagents=3` and dispatches 9 subagents in parallel; tracks active concurrency with a lock/counter and proves peak active workers == 3 while all 9 subagents complete with `status == "succeeded"`.
3. `test_cancelled_turn_aborts_queued_subagents_without_calling_provider`: Configures `max_concurrent_subagents=2`, dispatches 6 subagents where the first 2 flip `cancelled=True`, and verifies the remaining 4 queued subagents abort immediately with `TURN_CANCELLED` without invoking the provider stream.
4. `test_subagent_thinking_delta_carries_parent_agent_id`: Verifies `emit_lifecycle(turn, "thinking_delta", agent_id=sub_id, delta="...")` records `parent_agent_id=session.main_agent_id`.
5. Update existing `test_the_per_turn_subagent_budget_still_applies_to_a_batch` and `test_the_subagent_budget_is_bounded` to test explicit `max_subagents_per_turn=4` when configured.

- [ ] **Step 2: Run pytest to verify the new tests fail (RED)**

Run: `pytest tests/unit/agentic/test_turn_session.py -v`
Expected: FAIL (`SUBAGENT_LIMIT` after 4 subagents; missing `max_concurrent_subagents` / `parent_agent_id`).

- [ ] **Step 3: Implement on-demand worker scaling, semaphore queueing, cancellation guard, and sequence retry expansion**

1. In `src/agentos/agentic/session.py`:
   - Define:
     ```python
     def _env_optional_positive_int(name: str) -> int | None:
         raw = (os.environ.get(name) or "").strip()
         if not raw:
             return None
         try:
             val = int(raw)
             return val if val > 0 else None
         except ValueError:
             return None

     MAX_CONCURRENT_SUBAGENTS = max(1, int(os.environ.get("ORIN_MAX_CONCURRENT_SUBAGENTS", "16") or "16"))
     MAX_SUBAGENTS_PER_TURN: int | None = _env_optional_positive_int("ORIN_MAX_SUBAGENTS_PER_TURN")
     ```
   - In `TurnSession.__init__`:
     - Accept `max_subagents_per_turn: int | None = MAX_SUBAGENTS_PER_TURN` and `max_concurrent_subagents: int = MAX_CONCURRENT_SUBAGENTS`.
     - Store `self._max_subagents_per_turn = max_subagents_per_turn` and `self._max_concurrent_subagents = max(1, int(max_concurrent_subagents))`.
     - Create `self._subagent_semaphore = BoundedSemaphore(self._max_concurrent_subagents)`.
   - In `TurnSession.emit_lifecycle` (`state == "thinking_delta"`):
     - Pass `parent_agent_id=self.main_agent_id if is_subagent else None` to `self._record(...)`.
   - In `TurnSession._ask_agent`:
     - Check budget only if `self._max_subagents_per_turn is not None and self._subagent_runs >= self._max_subagents_per_turn`.
     - For `run_in_background=True`, initialize `self._bg_subagent_executor` with `max_workers=self._max_concurrent_subagents`.
   - In `TurnSession._execute_subagent_run`:
     - Acquire `self._subagent_semaphore` around execution.
     - Right after acquiring `self._subagent_semaphore`, check `if self.cancelled(self.turn):` and if cancelled, mark agent state `"cancelled"` and return `ToolOutcome("failed", f"{clean} cancelado", f"Subagent '{clean}' was cancelled before starting.", {"agent_id": agent_id, "agent_name": clean, "tool_kind": "agent"}, "TURN_CANCELLED")` without calling `self._child_provider_factory` or `runtime.run`.
   - In `TurnSession._ask_agents`:
     - Scale worker pool on demand: `worker_count = min(len(pending), self._max_concurrent_subagents)` and use `ThreadPoolExecutor(max_workers=worker_count)`.
2. In `src/agentos/agentic/runtime.py`:
   - Set `MAX_PARALLEL_TOOLS = 8` and `MAX_PARALLEL_SUBAGENT_TOOLS = max(8, int(os.environ.get("ORIN_MAX_CONCURRENT_SUBAGENTS", "16") or "16"))`.
   - In `_run_toolset` (`line 1645`), compute:
     ```python
     has_subagent_call = any(prepared[i][1] in {"run_subagent", "ask_agent", "check_subagent"} for i in run_indexes)
     pool_cap = MAX_PARALLEL_SUBAGENT_TOOLS if has_subagent_call else MAX_PARALLEL_TOOLS
     with ThreadPoolExecutor(max_workers=min(len(run_indexes), pool_cap)) as pool:
     ```
3. In `src/agentos/browser/conversation_worker.py`:
   - Increase `MAX_AGENT_PAGES = 20`.
4. In `src/agentos/conversations/chat.py`:
   - Increase the sequence allocation retry loop in `_activity`, `record_tool_call`, and `record_step` from `range(16)` to `range(64)`.

- [ ] **Step 4: Run pytest to verify all backend tests pass (GREEN)**

Run: `pytest tests/unit/agentic/test_turn_session.py tests/unit/agentic/test_agentic_runtime_loop.py tests/unit/agentic/test_subagent_and_stream_resilience.py -v`
Expected: PASS.

---

### Task 3: Visual Verification in Real Browser (Playwright) & Full Test Suite Verification

**Files:**
- Modify: `frontend/tests/e2e/code-mode-ux.spec.ts`

- [ ] **Step 1: Update E2E test & inspect visual rendering in real Chromium browser (Desktop & Mobile)**

Run Playwright E2E test verifying:
1. Streaming `ThoughtBlock` renders collapsed (`▸ Pensando...`) and expands on click.
2. Completed `ThoughtBlock` renders `▸ Pensou` / `▸ Pensou por Xs` and expands on click.
3. `"Trabalhou por Xs"` (`turn-work-duration`) renders at the bottom of the assistant response.

Run: `npx --prefix frontend playwright test tests/e2e/code-mode-ux.spec.ts`
Expected: PASS with zero accessibility violations.
