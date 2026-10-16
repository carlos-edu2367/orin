# Implementation Plan: Multi-Session & Multi-Agent Concurrency in Orin

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Orin to execute multiple Chat and Code sessions simultaneously across conversations and multiple agents/subagents simultaneously within a turn (both foreground parallel batches and background subagents), grounded in the reverse-engineered architecture of Claude Desktop (`LocalSessionManager`) and Claude Code CLI (`StreamingToolExecutor` / `Zo` / `yX` / `Eir`).

**Architecture:**
1. **Track 1 — Multi-Session Worker Concurrency & Thread-Safe Persistence (`publisher.py`, `workers/chat.py`, `conversations/chat.py`, `persistence/sqlite.py`)**:
   - Upgrade `publisher.py` from a blocking 1-turn loop to a bounded `ConcurrentTurnRunner` (`ThreadPoolExecutor`, default `MAX_CONCURRENT_SESSIONS = 8`).
   - Enforce per-conversation FIFO serialization in `PostgresChatStore.pending()` and `claim()` so turns in *different* conversations run concurrently while turns in the *same* conversation run strictly 1 at a time.
   - Make `ChatWorker`, `PostgresChatStore` sequence allocation (`_seq_lock`), and SQLite `QueuePool` (`pool_size=32, max_overflow=64, busy_timeout=30000`) thread-safe.
2. **Track 2 — Multi-Agent / Subagent Concurrency, Isolation & Background Execution (`runtime.py`, `session.py`, `agent_tools.py`, `conversation_agents.py`)**:
   - Move `AgentToolset._active_invocation_id` to `threading.local()` and guard `_read_file_state` / `_tool_calls_count` with `threading.RLock()`, giving each subagent its own isolated `_read_file_state` (deep copy for `fork`, empty dict for others).
   - Give each concurrent `run_subagent` / `ask_agent` invocation a unique `agent_id` instance (unless explicitly resuming via `agent_id`), make `ConversationAgentStore` atomic and thread-safe, and add `run_in_background: bool = False` + `check_subagent(agent_id, wait_seconds)` matching Claude Code's `Agent` tool.
3. **Track 3 — Frontend Parallel Subagent Timeline & Multi-Session Live Sidebar (`turnTimelineFold.ts`, `TurnTimeline.tsx`, `WorkspaceNavigation.tsx`, `ChatPage.tsx`)**:
   - Refactor `buildTurnTimeline` (`turnTimelineFold.ts`) to maintain a `Map<string, ActivityGroup>` and `Map<string, ThoughtBuffer>` keyed by `agentId` so interleaved SSE events from parallel subagents fold into one cohesive `SubagentTaskContainer` per subagent instead of shattering into dozens of 1-action fragments.
   - Add real-time multi-session status polling and project-level active indicators (`hasActive`) in `WorkspaceNavigation.tsx` and state-transition broadcasts in `ChatPage.tsx`.

---

## File Map

| Track | Files to Modify / Create | Responsibility |
| :--- | :--- | :--- |
| **Track 1 (Backend Multi-Session & Persistence)** | `src/agentos/persistence/sqlite.py`<br>`src/agentos/conversations/chat.py`<br>`src/agentos/workers/chat.py`<br>`src/agentos/workers/publisher.py`<br>`tests/unit/workers/test_multi_session_concurrency.py` *(new)* | SQLite pool expansion; `PostgresChatStore._seq_lock`; per-conversation FIFO `pending()` & `claim()`; `ChatWorker` thread-safety & per-turn `HookEngine`; `ConcurrentTurnRunner` in `publisher.py`. |
| **Track 2 (Backend Parallel & Background Subagents)** | `src/agentos/persistence/postgres/conversation_agents.py`<br>`src/agentos/agentic/agent_tools.py`<br>`src/agentos/agentic/runtime.py`<br>`src/agentos/agentic/session.py`<br>`tests/unit/agentic/test_parallel_and_background_subagents.py` *(new)* | Thread-safe `ConversationAgentStore`; `threading.local()` for `_active_invocation_id`; isolated `_read_file_state`; unique `agent_id` per concurrent subagent + `agent_id` resume; `run_in_background` + `check_subagent` tool. |
| **Track 3 (Frontend Parallel Subagent UI & Multi-Session Sidebar)** | `frontend/src/features/conversations/turnTimelineFold.ts`<br>`frontend/src/features/conversations/TurnTimeline.tsx`<br>`frontend/src/features/projects/WorkspaceNavigation.tsx`<br>`frontend/src/features/conversations/ChatPage.tsx`<br>`frontend/src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx` *(new)* | Per-`agentId` group & thought maps in `buildTurnTimeline`; parallel subagent badges in `TurnTimeline.tsx`; live multi-session polling & project active indicator in `WorkspaceNavigation.tsx`. |
| **Track 4 (End-to-End Integration & Adversarial Verification)** | `frontend/e2e/multi-session-concurrency.spec.ts` *(new)* | Full pytest + vitest + Playwright verification of concurrent sessions and parallel subagents. |

---

## Track 1: Multi-Session Worker Concurrency & Thread-Safe Persistence

### Task 1.1: Thread-Safe Sequence Allocation, Per-Conversation FIFO Queue, and SQLite Pool (`sqlite.py`, `conversations/chat.py`)

**Files:**
- Modify: [`src/agentos/persistence/sqlite.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/persistence/sqlite.py)
- Modify: [`src/agentos/conversations/chat.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/conversations/chat.py)
- Create: `tests/unit/workers/test_multi_session_concurrency.py`

- [ ] **Step 1 (RED): Write failing concurrency tests in `tests/unit/workers/test_multi_session_concurrency.py`**
  - Test 1 (`test_pending_and_claim_serialize_same_conversation_but_allow_distinct_conversations`):
    - Create Conversation A (`turn_a1`, `turn_a2`) and Conversation B (`turn_b1`).
    - Assert `store.pending()` returns `turn_a1` and `turn_b1` (the earliest turn per idle conversation), NOT `turn_a2`.
    - Claim `turn_a1` (`store.claim("turn_a1") is True`).
    - While `turn_a1` is `running`, assert `store.pending()` returns `["turn_b1"]` and `store.claim("turn_a2") is False`.
    - Complete `turn_a1`, then assert `store.pending()` now includes `"turn_a2"`.
  - Test 2 (`test_concurrent_activity_and_step_recording_never_drops_or_collides`):
    - Spawn 8 threads simultaneously calling `store.record_activity(...)`, `store.record_tool_call(...)`, and `store.record_step(...)` on the same `turn_id` (40 events total).
    - Assert all 40 events and steps are persisted with strictly contiguous, unique `sequence` / `seq` numbers and zero exceptions or dropped rows.
  - Run: `uv run pytest tests/unit/workers/test_multi_session_concurrency.py -v` (expect FAIL).

- [ ] **Step 2 (GREEN): Implement SQLite pool expansion and `PostgresChatStore` concurrency guards**
  1. In [`src/agentos/persistence/sqlite.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/persistence/sqlite.py#L23-L38):
     - Configure `create_engine` with `pool_size=32, max_overflow=64, pool_timeout=60` and `PRAGMA busy_timeout=30000`.
  2. In [`src/agentos/conversations/chat.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/conversations/chat.py):
     - Add `self._seq_lock = threading.RLock()` in `PostgresChatStore.__init__`.
     - Wrap sequence computation + insert in `_activity` (`line 402`), `record_tool_call` (`line 489`), and `record_step` (`line 518`) with `with self._seq_lock:`.
     - Update `PostgresChatStore.pending()` (`line 1429`):
       - Query `conversation_turn_dispatches` joined with `conversation_turns` where `dispatch.state == 'pending'` AND `conversation_turns.state == 'starting'`.
       - Exclude any `conversation_id` that already has a row in `conversation_turns` with `state == 'running'` or a row in `conversation_turn_dispatches` with `state == 'claimed'` (joined to an unfinished turn).
       - Deduplicate by `conversation_id` preserving the earliest `scheduled_at` (FIFO per conversation).
     - Update `PostgresChatStore.claim(turn_id)` (`line 1439`):
       - Inside `with self.engine.begin() as conn:`, look up `turn_row = conn.execute(select(conversation_turns.c.conversation_id, conversation_turns.c.state).where(conversation_turns.c.turn_id == turn_id)).first()`.
       - If another turn in `turn_row.conversation_id` already has `state == "running"`, return `False`.
       - Otherwise atomically transition `conversation_turn_dispatches.state = "claimed"` and `conversation_turns.state = "running"`.

- [ ] **Step 3 (VERIFY): Run tests**
  - Run: `uv run pytest tests/unit/workers/test_multi_session_concurrency.py -v` (expect PASS).

---

### Task 1.2: Thread-Safe `ChatWorker` & Multi-Session `ConcurrentTurnRunner` (`workers/chat.py`, `workers/publisher.py`)

**Files:**
- Modify: [`src/agentos/workers/chat.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/chat.py)
- Modify: [`src/agentos/workers/publisher.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/publisher.py)
- Modify: `tests/unit/workers/test_multi_session_concurrency.py`

- [ ] **Step 1 (RED): Add multi-session worker tests in `tests/unit/workers/test_multi_session_concurrency.py`**
  - Test 3 (`test_chat_worker_runs_two_conversations_in_parallel`):
    - Configure a provider fake whose `stream_turn` uses a `threading.Barrier(2, timeout=5.0)` so Turn A (in Conversation 1) and Turn B (in Conversation 2) can ONLY succeed if both execute simultaneously in parallel threads!
    - Run `ConcurrentTurnRunner(worker, max_workers=4).poll_once(0.0)` and wait for completion.
    - Assert both Turn A and Turn B pass the barrier simultaneously and complete with `state == "completed"`.
  - Test 4 (`test_watchdog_does_not_fail_turn_queued_behind_running_turn_in_same_conversation`):
    - Have `turn_1` running in Conversation 1 and `turn_2` queued in `starting` state in Conversation 1 for > 60 seconds.
    - Call `worker.watchdog(maximum_age=timedelta(seconds=45))`.
    - Assert `turn_2` is NOT failed with `worker_unavailable` because Conversation 1 has an active `running` turn (and/or worker heartbeat is fresh).
  - Run: `uv run pytest tests/unit/workers/test_multi_session_concurrency.py -v` (expect FAIL).

- [ ] **Step 2 (GREEN): Make `ChatWorker` thread-safe and add `ConcurrentTurnRunner` to `publisher.py`**
  1. In [`src/agentos/workers/chat.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/chat.py):
     - Add `self._state_lock = threading.RLock()` in `ChatWorker.__init__`.
     - Guard all mutations/reads of `self._reconciliation_turns`, `self._kernel_turns`, and `self._last_heartbeat_mono` with `with self._state_lock:`.
     - Ensure `self._reconciliation_turns.discard(turn_id)` is called inside the `finally:` block of `ChatWorker.run` (`line 482`).
     - In `ChatWorker._runtime_for` (`line 1118`), create a fresh per-turn `HookEngine` (`turn_hook_engine = self._hook_engine.clone()` or instantiate a new `HookEngine` with copied static hooks) before calling `rehydrate_hooks(turn_hook_engine, self.store, str(turn["conversation_id"]))` and pass `turn_hook_engine` to `TurnSession` and `AgenticTurnRuntime`.
     - In `ChatWorker.watchdog` (`line 699`), do not fail an unclaimed turn if `self.store.has_running_turn(conversation_id)` is `True` or if the `"chat-worker"` heartbeat in `worker_heartbeats` was updated within `maximum_age`.
  2. In [`src/agentos/workers/publisher.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/workers/publisher.py):
     - Define `MAX_CONCURRENT_SESSIONS = int(os.environ.get("ORIN_MAX_CONCURRENT_SESSIONS", "8"))`.
     - Implement `ConcurrentTurnRunner`:
       - Holds `self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="orin-session-worker")` and `self._in_flight: dict[str, Future] = {}`.
       - `tick(next_sweep: float) -> tuple[bool, float]`:
         - Prunes finished futures from `self._in_flight`.
         - Refreshes `worker.store.heartbeat("chat-worker")`.
         - Queries `pending = [t for t in worker.store.pending() if str(t) not in self._in_flight]`.
         - Submits up to `available_slots = max_workers - len(self._in_flight)` turns to `self._executor.submit(worker.run, str(turn_id))`.
         - Runs `recover_once(worker.store)` when `time.monotonic() >= next_sweep`.
     - Update `poll_once(worker, next_sweep)` so when multiple independent turns are pending, it runs up to `MAX_CONCURRENT_SESSIONS` of them concurrently via `ThreadPoolExecutor` and waits for the batch (keeping deterministic synchronous return semantics for callers of `poll_once`), while `main()` uses `ConcurrentTurnRunner` continuously so newly arriving sessions start immediately without waiting for an earlier batch to finish!

- [ ] **Step 3 (VERIFY): Run worker & conversation test suites**
  - Run: `uv run pytest tests/unit/workers/ tests/unit/conversations/ -v` (expect PASS).

---

## Track 2: Multi-Agent / Subagent Concurrency, Isolation & Background Execution

### Task 2.1: Thread-Local `invocation_id`, Isolated `_read_file_state`, and Atomic `ConversationAgentStore` (`conversation_agents.py`, `agent_tools.py`, `runtime.py`)

**Files:**
- Modify: [`src/agentos/persistence/postgres/conversation_agents.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/persistence/postgres/conversation_agents.py)
- Modify: [`src/agentos/agentic/agent_tools.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/agent_tools.py)
- Modify: [`src/agentos/agentic/runtime.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/runtime.py)
- Create: `tests/unit/agentic/test_parallel_and_background_subagents.py`

- [ ] **Step 1 (RED): Write failing tests for parallel tool thread-locality and `ConversationAgentStore` in `tests/unit/agentic/test_parallel_and_background_subagents.py`**
  - Test 1 (`test_agent_toolset_active_invocation_id_is_thread_local`):
    - Run 4 concurrent tool invocations on a shared `AgentToolset` via `_run_toolset` where each tool sleeps briefly and reads `toolset._active_invocation_id`.
    - Assert each tool observes its own `call_id` without ever seeing a sibling thread's `call_id`.
  - Test 2 (`test_conversation_agent_store_concurrent_create_and_usage_is_atomic`):
    - Spawn 6 threads calling `agents_store.create(...)` and `agents_store.record_usage(..., input_tokens=100, output_tokens=50, total_tokens=150)` concurrently.
    - Assert zero `IntegrityError`s and exact accumulated token totals (`input_tokens == 600`, `output_tokens == 300`, `total_tokens == 900`).
  - Run: `uv run pytest tests/unit/agentic/test_parallel_and_background_subagents.py -v` (expect FAIL).

- [ ] **Step 2 (GREEN): Implement thread-local `_active_invocation_id`, `_state_lock`, and thread-safe `ConversationAgentStore`**
  1. In [`src/agentos/persistence/postgres/conversation_agents.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/persistence/postgres/conversation_agents.py):
     - Add `self._lock = threading.RLock()` in `ConversationAgentStore.__init__`.
     - Guard `create`, `set_state`, `set_model`, and `record_usage` with `with self._lock:`.
     - Add optional `agent_id: str | None = None` parameter to `ConversationAgentStore.create(name, role, *, parent_agent_id, provider=None, model_id=None, agent_id=None)` so callers spawning multiple concurrent instances of the same subagent role can pass a unique `agent_id` (and unique display/instance name).
  2. In [`src/agentos/agentic/agent_tools.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/agent_tools.py):
     - Replace `self._active_invocation_id: str | None = None` with `self._tls = threading.local()` and `@property def _active_invocation_id(self) -> str | None` / `@_active_invocation_id.setter` backed by `self._tls.active_invocation_id`.
     - Add `self._state_lock = threading.RLock()` and guard `_read_file_state` mutations/reads and `self._tool_calls_count += 1` with `with self._state_lock:`.
     - Accept optional `initial_read_file_state: dict[str, dict[str, int | float | str | bool]] | None = None` in `AgentToolset.__init__` (deep-copied into `self._read_file_state`) and expose `snapshot_read_file_state() -> dict[...]` so `fork` subagents inherit a thread-safe clone of the parent's `read_file_state` while standard subagents start with `{}`.
  3. In [`src/agentos/agentic/runtime.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/runtime.py#L1519-L1572):
     - Update `eligible` in `_run_toolset` (`line 1523`) so `name in ("run_subagent", "ask_agent", "check_subagent")` are eligible for parallel execution alongside `is_read_only(name)`.
     - Update `mutating` (`line 1571`) to `not read_only and name not in ("run_subagent", "ask_agent", "check_subagent")` so spawning a subagent does not increment `self._writes` and invalidate the parent's read cache when the subagent is read-only.

- [ ] **Step 3 (VERIFY): Run unit tests**
  - Run: `uv run pytest tests/unit/agentic/test_parallel_and_background_subagents.py -v` (expect PASS).

---

### Task 2.2: Concurrent Same-Type Subagent Identity, `agent_id` Resume, and `run_in_background` (`session.py`, `agent_tools.py`)

**Files:**
- Modify: [`src/agentos/agentic/session.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py)
- Modify: [`src/agentos/agentic/agent_tools.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/agent_tools.py)
- Modify: [`src/agentos/agentic/phases.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/phases.py)
- Modify: `tests/unit/agentic/test_parallel_and_background_subagents.py`

- [ ] **Step 1 (RED): Add parallel subagent & background subagent tests in `tests/unit/agentic/test_parallel_and_background_subagents.py`**
  - Test 3 (`test_parallel_same_type_run_subagent_calls_get_distinct_agent_ids_and_execute_simultaneously`):
    - Have the main agent emit 3 `run_subagent(subagent_type="explore", description=..., task=...)` tool calls in a single assistant message.
    - Use a `threading.Barrier(3, timeout=5.0)` inside the child provider so the test proves all 3 `explore` subagents run concurrently in parallel threads.
    - Assert each of the 3 subagents gets a **distinct `agent_id`**, emits its own `AGENT_SPAWNED`, `TOOL_CALLED`, and `AGENT_COMPLETED` events tagged with its own `agent_id` and `subagent_type="explore"`, and returns its own result to the parent agent.
  - Test 4 (`test_run_subagent_resume_by_agent_id_preserves_subagent_identity`):
    - Call `run_subagent(subagent_type="general", task="First step")`, capture `agent_id` from outcome payload, then call `run_subagent(subagent_type="general", task="Continue step", agent_id=agent_id)`.
    - Assert the second call reuses the exact same `agent_id`.
  - Test 5 (`test_run_subagent_in_background_and_check_subagent`):
    - Call `run_subagent(subagent_type="explore", description="Audit repo", task="...", run_in_background=True)`.
    - Assert it returns immediately with `status == "async_launched"` and `agent_id`.
    - Call `check_subagent(agent_id=agent_id, wait_seconds=5.0)` and assert it returns `status == "completed"` with the background subagent's final output.
  - Run: `uv run pytest tests/unit/agentic/test_parallel_and_background_subagents.py -v` (expect FAIL).

- [ ] **Step 2 (GREEN): Implement unique concurrent subagent instances, `agent_id` resume, and background execution**
  1. In [`src/agentos/agentic/session.py`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/src/agentos/agentic/session.py):
     - Track active and background subagent runs on `TurnSession`:
       - `self._active_subagent_ids: set[str] = set()` (guarded by `self._subagent_lock`)
       - `self._background_subagents: dict[str, dict[str, Any]] = {}` (holding `future`, `description`, `subagent_type`, `status`, `outcome`, `started_at`)
       - `self._bg_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orin-bg-subagent")`
     - Update `_ensure_builtin_subagent_record(self, subagent_type: str, model_id: str | None = None, *, force_unique: bool = False, resume_agent_id: str | None = None)`:
       - If `resume_agent_id` is provided, look up that exact `agent_id` in `self.agents_store` (or construct record with that `agent_id`).
       - If `force_unique` is `True` (or if the base `key` record does not exist yet or is already in `self._active_subagent_ids`):
         - If no subagent of type `key` has been used in this turn yet and `key` is not in `self._active_subagent_ids` and only 1 is running, or better: for `run_subagent` without `resume_agent_id`, check `with self._subagent_lock:` whether `base_agent_id = self.agents_store.agent_id_for(key)` is already in `self._active_subagent_ids` or `self._used_subagent_ids`.
         - When a second/concurrent subagent of the same type is spawned (or whenever `base_agent_id` was already used in this turn), allocate a unique instance key `instance_key = f"{key}#{self._subagent_runs + 1}"` (`agent_id = self.agents_store.agent_id_for(f"{self.turn['turn_id']}:{instance_key}")`), while keeping `name=key` (or `f"{key} #{n}"`) and `builtin_type=key`!
         - *(Note: Keeping the first instance's `name = key` when not colliding preserves 100% backward compatibility with any existing single-subagent unit test expecting `name == "explore"` or `agent_id_for("explore")`, while guaranteeing that 2+ parallel or sequential `run_subagent` calls in the same turn get distinct `agent_id`s!)*
     - Extend `_run_subagent` and `AgentToolset` registration of `run_subagent`:
       - Add optional parameters `agent_id: str | None = None` (to resume a prior subagent) and `run_in_background: bool = False`.
       - When `run_in_background=True`:
         - Reserve the subagent record & `agent_id` synchronously, emit `AGENT_SPAWNED` (`"Iniciou subagente em segundo plano"`), and submit `_ask_agent(...)` to `self._bg_executor`.
         - Return `ToolOutcome("succeeded", f"Subagente {clean_type} iniciado em background ({description or clean_type})", json.dumps({"status": "async_launched", "agent_id": agent_id, "subagent_type": clean_type, "description": description}), {"tool_kind": "agent", "agent_id": agent_id, "subagent_type": clean_type, "async_launched": True})`.
     - Add `_check_subagent(self, agent_id: str, wait_seconds: float = 5.0) -> ToolOutcome` on `TurnSession` and register `check_subagent` in `AgentToolset`, `PHASE_TOOLS`, and `CODE_MODE_CORE_TOOLS`:
       - Waits up to `min(max(wait_seconds, 0.0), 60.0)` seconds on the background future if still running.
       - If finished, returns the subagent's completed `ToolOutcome` content and marks it delivered.
       - If still running after `wait_seconds`, returns `{"status": "running", "agent_id": agent_id}` so the parent agent can continue working or wait again.
     - In `TurnSession._toolset`: when `is_subagent=True` (`not subagents`), pass `initial_read_file_state=parent_toolset.snapshot_read_file_state() if inherit_context else {}`.

- [ ] **Step 3 (VERIFY): Run full agentic unit test suite**
  - Run: `uv run pytest tests/unit/agentic/ -v` (expect PASS with zero regressions).

---

## Track 3: Frontend Parallel Subagent Timeline & Multi-Session Live Sidebar

### Task 3.1: Fix Parallel Subagent Interleaving in `buildTurnTimeline` & `TurnTimeline.tsx` (`turnTimelineFold.ts`, `TurnTimeline.tsx`)

**Files:**
- Modify: [`frontend/src/features/conversations/turnTimelineFold.ts`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/turnTimelineFold.ts)
- Modify: [`frontend/src/features/conversations/TurnTimeline.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/TurnTimeline.tsx)
- Create: `frontend/src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx`

- [ ] **Step 1 (RED): Write failing Vitest tests in `frontend/src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx`**
  - Test 1 (`folds interleaved events from 3 concurrent subagents into exactly 3 SubagentTaskContainers`):
    - Construct a turn with interleaved events from `agent:1` (`explore`, `"Analisar backend"`), `agent:2` (`explore`, `"Analisar frontend"`), and `agent:3` (`general`, `"Verificar testes"`):
      - `agent:1` spawned -> `agent:2` spawned -> `agent:3` spawned -> `agent:1` tool.requested (`read_file`) -> `agent:2` tool.requested (`search_files`) -> `agent:1` thought_delta -> `agent:2` thought_delta -> `agent:3` tool.requested (`run_command`) -> `agent:1` tool.completed -> `agent:2` tool.completed -> `agent:3` tool.completed -> `agent:1` completed -> `agent:2` completed -> `agent:3` completed.
    - Call `buildTurnTimeline(events, turnId, true)` and render `<TurnTimeline ... />`.
    - Assert there are **exactly 3** `subagent-task-container` elements (NOT 12 fragmented containers!), each containing all of its own tool calls and thoughts in order.
  - Test 2 (`renders live running status on parallel active subagents while another subagent has already completed`):
    - Have `agent:1` in `working` state and `agent:2` in `completed` state in the same turn.
    - Assert `agent:1`'s container displays the active running state while `agent:2`'s container shows completed.
  - Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx` (expect FAIL).

- [ ] **Step 2 (GREEN): Refactor `buildTurnTimeline` to track subagent groups and thought buffers per `agentId`**
  1. In [`frontend/src/features/conversations/turnTimelineFold.ts`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/turnTimelineFold.ts#L249-L412):
     - Keep `group: ActivityGroup | null` for the **main** agent (`!isSubagentTaskEvent(event)`), and add `subagentGroupsByKey = new Map<string, ActivityGroup>()` for subagent task events (`key.startsWith('subagent-task:')`).
     - When a subagent event arrives (`const key = groupingKey(event)` where `key.startsWith('subagent-task:')`):
       - Look up `existingSubGroup = subagentGroupsByKey.get(key)`.
       - If found, call `applyEventToGroup(existingSubGroup, event)` in place (since `items` holds a reference to `existingSubGroup`, updating it updates the anchored timeline item in place without flushing the main agent's `group` or other subagents' groups!).
       - If not found, flush any pending main-agent `textBuffer` / `thoughtBuffer`, create `newSubGroup`, store `subagentGroupsByKey.set(key, newSubGroup)`, and push `{ id: 'activity:' + key + ':' + event.eventId, kind: 'activity', group: newSubGroup }` into `items`.
     - For `agent.thought_delta` events:
       - If `event.parentAgentId` is set (i.e. a subagent thought delta) and `subagentGroupsByKey` has an open group for `subagent-task:${event.agentId}:${event.turnId ?? ''}`, attach or fold the subagent's thought delta without flushing sibling subagent groups; or maintain `thoughtBuffersByAgent = new Map<string, ThoughtBuffer>()` keyed by `event.agentId || 'main'`.
  2. In [`frontend/src/features/conversations/TurnTimeline.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/TurnTimeline.tsx):
     - In `SubagentTaskContainer`, ensure each container's header clearly displays `subagentType`, `taskDescription`, and live action count (`{actionCount} {actionCount === 1 ? 'ação' : 'ações'}`), with a pulsing running dot when `isRunning` is true.

- [ ] **Step 3 (VERIFY): Run Vitest tests**
  - Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx` (expect PASS).

---

### Task 3.2: Multi-Session Live Sidebar Indicators & Seamless Session Switching (`WorkspaceNavigation.tsx`, `ChatPage.tsx`)

**Files:**
- Modify: [`frontend/src/features/projects/WorkspaceNavigation.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx)
- Modify: [`frontend/src/features/conversations/ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx)
- Modify: `frontend/src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx`

- [ ] **Step 1 (RED): Add sidebar multi-session live indicator tests in `ParallelSubagentsAndSessions.test.tsx`**
  - Test 3 (`WorkspaceNavigation shows live running dots for multiple concurrent sessions and project-level active indicator`):
    - Render `<WorkspaceNavigation />` with a mock API returning:
      - Standalone conversation `chat_1` (`state: 'running'`, `mode: 'code'`)
      - Standalone conversation `chat_2` (`state: 'waiting_user'`, `mode: 'chat'`)
      - Project `proj_1` containing `chat_3` (`state: 'running'`, `mode: 'code'`)
    - Assert both `chat_1` and `chat_3` render `.workspace-nav__state-dot--running`, `chat_2` renders `.workspace-nav__state-dot--waiting_user`, and `proj_1` renders `data-testid="project-active-indicator"`.
    - Trigger `window.dispatchEvent(new CustomEvent('orin:projects-updated'))` and assert the sidebar refreshes its state from the API.
  - Run: `npm --prefix frontend test -- --run src/features/conversations/__tests__/ParallelSubagentsAndSessions.test.tsx` (expect FAIL).

- [ ] **Step 2 (GREEN): Implement live sidebar polling, project active indicator, and state-transition broadcasts**
  1. In [`frontend/src/features/conversations/ChatPage.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/conversations/ChatPage.tsx):
     - Add a `useEffect` watching `conversation?.state`: whenever `conversation?.state` changes (e.g. `idle -> running`, `running -> waiting_user`, `running -> completed`), dispatch `window.dispatchEvent(new CustomEvent('orin:projects-updated'))` so `WorkspaceNavigation` updates immediately without waiting for navigation.
  2. In [`frontend/src/features/projects/WorkspaceNavigation.tsx`](file:///c:/Users/User/Documents/GitHub/Carlos/orin/frontend/src/features/projects/WorkspaceNavigation.tsx):
     - Compute `hasActiveSessions` across `projects` and standalone `conversations` (`state === 'running' || state === 'starting' || state === 'waiting_user'`).
     - Set up a background `setInterval` (`4000ms` when `hasActiveSessions` is `true`, `12000ms` when `false`) that silently refreshes `listProjectsSidebar(client)` and `listConversations(client)` so concurrent sessions running in the background update their sidebar dots in real time.
     - On each project row in `WorkspaceNavigation.tsx`, compute `const projectHasActive = project.chats.some((c) => c.state === 'running' || c.state === 'starting' || c.state === 'waiting_user')` and render `<span className="workspace-nav__state-dot workspace-nav__state-dot--running" data-testid="project-active-indicator" title="Sessão ativa neste projeto" />` when `projectHasActive` is true (matching Claude Desktop's `s1` `hasActive` indicator).

- [ ] **Step 3 (VERIFY): Run full frontend test suite and production build**
  - Run: `npm --prefix frontend test -- --run`
  - Run: `npm --prefix frontend run build`

---

## Track 4: End-to-End Playwright & Adversarial Verification

### Task 4.1: Playwright E2E Multi-Session Verification & Full Regression Suite

**Files:**
- Create: `frontend/e2e/multi-session-concurrency.spec.ts`

- [ ] **Step 1: Write and run Playwright E2E test `frontend/e2e/multi-session-concurrency.spec.ts`**
  - Verify in a real Chromium browser:
    1. Start Session A (Orin Code) and Session B (Orin Chat) in the same project or workspace.
    2. Verify both sessions appear in the sidebar with live status indicators and that switching from Session A $\rightarrow$ Session B $\rightarrow$ back to Session A preserves the full timeline and state of both sessions.
    3. Verify parallel subagent containers (`SubagentTaskContainer`) render as separate, non-fragmented collapsible cards in the timeline.
  - Run: `npx --prefix frontend playwright test e2e/multi-session-concurrency.spec.ts`

- [ ] **Step 2: Run full backend + frontend verification**
  - Run: `uv run pytest tests/unit/ -q`
  - Run: `npm --prefix frontend test -- --run`
  - Run: `npm --prefix frontend run build`
