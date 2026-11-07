# CLI Context, Contract Resumption, Context Meter & Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate multi-turn context truncation/repetition in CLI providers (`agy`, `claude`, `codex`), prevent completed turns' `write_contract` from hijacking subsequent Chat turns, keep user requests visible at the top of `/skill` expansions and compaction summaries, fix the Antigravity context usage meter (`2.69M / 1M`), and eliminate stuck `"Preparando..."` tool cards.

**Architecture:** We address six interlocking seams: (1) `NativeTurnRunner` ages prior turns' tool results (`keep_recent=0`), preserves compaction/omission system summaries, caps serialized `prior_messages` below `agy`'s 190 KB `USER_INPUT` limit (`MAX_NATIVE_PRIOR_HISTORY_CHARS = 120_000`), and explicitly prefixes the current request with `[User]` when preceded by history or reminders; (2) `ChatStore.latest_contract` only resumes a contract when the turn that wrote it did not finish with `state == "completed"`; (3) `_skill_body` places `Pedido da pessoa:` at the top and `_compact_source` compresses tool results and fills its 48 KB budget from newest to oldest; (4) `AntigravityStreamTranslator` stops emitting retroactive `TOOL_USE_START`/`TOOL_USE_END` for already-completed Orin MCP tools and tracks `context_window_used_tokens` from the latest step rather than the cumulative sum; (5) `buildTurnTimeline` passes `isTurnCompleted` into `computeSettledEventIds`.

**Tech Stack:** Python 3.13, SQLAlchemy Core, Pytest, TypeScript / React, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-07-cli-context-contract-and-timeline-design.md`

## Global Constraints

- Work strictly inside the isolated worktree: `c:\Users\carlos\Documents\projetos\orin\.orin\worktrees\fix-cli-context-and-timeline` (branch `fix/cli-context-and-timeline`).
- Preserve security guards in `AntigravityStreamTranslator` and `NativeTurnHandle`: any non-Orin/forbidden built-in tool in `step_update` MUST still emit `TOOL_USE_START` and trigger `CliSecurityAbort`.
- Maintain backward compatibility of `CliUsage` across `claude_cli`, `codex_cli`, and `antigravity_cli`.

---

### Task 1: Age Prior Tool Results, Preserve Compaction Summaries, Cap Prior History & Label `[User]` in `NativeTurnRunner`

**Files:**
- Modify: `src/agentos/agentic/native_turn.py:1466-1645`
- Test: `tests/unit/agentic/test_native_turn.py`

**Interfaces:**
- Consumes: `AgenticTurnRuntime._age_tool_results(messages, keep_recent=0)`
- Produces: `MAX_NATIVE_PRIOR_HISTORY_CHARS = 120_000` and updated `NativeTurnRunner._build_initial_user_content` / `NativeTurnRunner._run`

- [ ] **Step 1: Write the failing tests in `tests/unit/agentic/test_native_turn.py`**

```python
def test_native_turn_ages_prior_tool_results_and_caps_prior_history_preserving_current_user():
    # Verifies:
    # 1. Prior turn tool results > 1600 chars are compressed before building initial user content
    # 2. Compaction summary (role="system") is preserved in prior blocks
    # 3. Massive prior history (> 120k chars) drops oldest tool blocks from the front, never the tail
    # 4. Current user message is labeled with "[User]\n" after <system-reminder>
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/unit/agentic/test_native_turn.py -k "ages_prior_tool_results" -v -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: Implement in `src/agentos/agentic/native_turn.py`**

1. Define `MAX_NATIVE_PRIOR_HISTORY_CHARS: int = 120_000`.
2. In `NativeTurnRunner._run`, call `self.runtime._age_tool_results(self.messages, keep_recent=0)` before `window = self.runtime._request_messages(self.messages)`.
3. In `NativeTurnRunner._build_initial_user_content`:
   - Filter out only the runtime preamble system messages (`self.runtime.system_prompt`, `self.runtime.volatile_prompt`, and `self.runtime.contract.render()`) from `prior_messages`, keeping compaction and omission system messages.
   - Cap serialized `prior_blocks` to `MAX_NATIVE_PRIOR_HISTORY_CHARS` by dropping oldest `[Tool Use]` / `[Tool Result]` blocks from the front while preserving `[User]`, `[Assistant]`, and `[System]` summary blocks.
   - Prefix `cur_text` with `[User]\n` whenever `blocks` is non-empty (i.e. preceded by prior history or `<system-reminder>`).

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/unit/agentic/test_native_turn.py -v -p no:cacheprovider`
Expected: PASS

---

### Task 2: Only Auto-Resume `write_contract` When the Writing Turn Did Not Complete

**Files:**
- Modify: `src/agentos/conversations/chat.py:704-740`
- Test: `tests/unit/agentic/test_task_contract.py`

**Interfaces:**
- Produces: `ChatStore.latest_contract(conversation_id: str) -> dict[str, object] | None` returning `None` if the turn that wrote the latest `write_contract` has `state == "completed"`.

- [ ] **Step 1: Write the failing test in `tests/unit/agentic/test_task_contract.py`**

```python
def test_latest_contract_ignores_completed_turn_and_resumes_incomplete_turn(chat_store):
    # Turn 1 writes contract and completes -> latest_contract returns None on Turn 2
    # Turn 2 writes contract and is cancelled/waiting_user -> latest_contract returns contract on Turn 3
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/unit/agentic/test_task_contract.py -v -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: Implement in `src/agentos/conversations/chat.py`**

Select `conversation_turn_steps.c.turn_id, conversation_turn_steps.c.payload` and check `conversation_turns.c.state` for the matching `turn_id`. If `state == "completed"`, return `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/unit/agentic/test_task_contract.py -v -p no:cacheprovider`
Expected: PASS

---

### Task 3: Put User Request First in `_skill_body` and Preserve Recent Tail in `_compact_source`

**Files:**
- Modify: `src/agentos/conversations/chat.py:156-168`
- Modify: `src/agentos/agentic/runtime.py:1768-1785`
- Test: `tests/unit/conversations/test_commands.py`, `tests/unit/agentic/test_structured_compaction.py`

- [ ] **Step 1: Write failing tests for `_skill_body` and `_compact_source`**

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement in `src/agentos/conversations/chat.py` and `src/agentos/agentic/runtime.py`**

1. In `_skill_body`, prepend `f"Pedido da pessoa: {arguments}\n\n"` before `<agentos-skill-instructions authority="subordinate">` when `arguments` is non-empty, so `runtime._first_request_text` (`[:2_000]`) and the model always see the user's request first.
2. In `AgenticTurnRuntime._compact_source`, compress tool-result content using `self._compress` and iterate `reversed(sorted(indices))` within the `48_000` char budget (reversing collected entries back to chronological order), so recent user/assistant turns are never truncated by early tool output.

- [ ] **Step 4: Run tests to verify they pass**

---

### Task 4: Fix Antigravity CLI Stuck `"Preparando..."` Cards, Correlation Shift, and Context Meter (`2.69M / 1M`)

**Files:**
- Modify: `src/agentos/providers/claude_cli/stream.py`
- Modify: `src/agentos/providers/antigravity_cli/stream.py`
- Modify: `src/agentos/agentic/native_turn.py:1331-1418`
- Test: `tests/unit/providers/test_cli_registry.py`, `tests/unit/agentic/test_native_turn.py`

- [ ] **Step 1: Write failing tests for `AntigravityStreamTranslator` and `_RunnerSink.on_assistant_usage`**

1. Verify `AntigravityStreamTranslator.feed_line` returns `[]` for `step_update` of allowed `mcp_orin_*` tools (`canonical_name is not None`), while still returning `TOOL_USE_START` for forbidden built-in tools (`canonical_name is None`).
2. Verify `AntigravityStreamTranslator` sets `usage.context_window_used_tokens` to the latest step's prompt size (`input_tokens` of the highest `step_idx`), while keeping cumulative `input_tokens` + `cache_read_input_tokens` for total turn token accounting.
3. Verify `_RunnerSink.on_assistant_usage` reports `used_tokens = usage.context_window_used_tokens` in `context_updated` when present.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement in `stream.py` and `native_turn.py`**

- [ ] **Step 4: Run tests to verify they pass**

---

### Task 5: Pass `isTurnCompleted` to `computeSettledEventIds` in `buildTurnTimeline`

**Files:**
- Modify: `frontend/src/features/conversations/turnTimelineFold.ts:306-314`
- Test: `frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.ts`

- [ ] **Step 1: Write failing test in `frontend/src/features/conversations/__tests__/longTaskAndPowershellFixes.test.ts`**

- [ ] **Step 2: Run Vitest to verify it fails**

- [ ] **Step 3: Implement in `frontend/src/features/conversations/turnTimelineFold.ts`**

Compute `hasTerminalTurnEvent` and `isTurnCompleted` before `computeSettledEventIds`, then call `const settled = computeSettledEventIds(turnEvents, isTurnCompleted)`.

- [ ] **Step 4: Run Vitest to verify it passes**
