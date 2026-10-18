# W3 — Loop and context: ledger

Execution record for `docs/superpowers/plans/2026-10-18-w3-loop-context.md`.

## Execution notes — batch A (Tasks 1-4)

### Task 1 — One `<system-reminder>` mechanism

- Commit: `36e7235` `feat(reminders): one system-reminder mechanism for hooks, tools and the runtime`
- Tests added: `tests/unit/agentic/test_reminders.py` (8).
- Deviations: none in behaviour. The `from .reminders import ...` line in `session.py` sits between the `lifecycle` and `phases` imports (import order only).
- Existing test expectations changed: none.

### Task 2 — W3 persistence schema and migration

- Commit: `b94c8a4` `feat(persistence): W3 tables for queue, prompt snapshots, compaction boundaries and sessions`
- Tests added: `tests/unit/persistence/test_w3_schema.py` (3).
- Deviation: `tests/unit/persistence/test_postgres_schema.py::test_persistence_schema_contains_only_the_durable_boundary_tables` asserts the exact set of metadata tables, so the four new W3 tables made it fail. The plan does not mention this test. Added `conversation_input_queue`, `conversation_prompt_snapshots`, `conversation_compact_boundaries` and `conversation_sessions` to the expected set. Additive only, nothing removed or weakened.
- Existing test expectations changed: yes, the one above.

### Task 3 — Code-mode limits, budget, safety cap and durable leases

- Commit: `ff8d430` `feat(loop): Code turns end on maxTurns, maxBudgetUsd or the safety cap, with durable leases`
- Tests added: `tests/unit/agentic/test_loop_settings.py` (8), `tests/unit/agentic/test_code_loop_limits.py` (11, including 6 parametrized cases), `tests/unit/conversations/test_dispatch_leases.py` (5). Total 24.
- Deviation: anchor only. In `session.py` the plan says to store `catalog_pricing` next to `self.lifecycle = lifecycle`, but the line is annotated (`self.lifecycle: LifecycleBus | None = lifecycle`). The new line sits right after it.
- Existing test expectations changed: none.

### Task 4 — Retry with backoff, retry-after and the fallback model chain

- Commit: `9c5b3e4` `feat(loop): retry with backoff and retry-after, then a same-provider fallback model chain`
- Tests added: `tests/unit/agentic/test_retry.py` (5), `tests/unit/agentic/test_retry_and_fallback.py` (9). Total 14.
- Deviations: none.
- Existing test expectations changed: none.

### Full backend suite after batch A

`uv run pytest -q tests/unit` → **2906 passed, 53 skipped, 0 failed** (about 189 s).
Baseline before W3 was 2857 passed, 53 skipped. The difference is the 49 tests added in this batch.

### Not verified in batch A

- PostgreSQL: every persistence and lease test ran on SQLite. Migration `0048` was applied only on SQLite. The Postgres branch of `_ensure_conversation_mode_columns` (`TIMESTAMP WITH TIME ZONE`) and the `func.coalesce` lease predicate were not run against Postgres.
- Retry timing was tested with a fake `sleep`. The real `time.sleep` path and cancellation during a real wait were not timed.
- A refused PreModelSwitch still builds the fallback provider object before the hook runs (the plan's order). The object is discarded and no network call is made, but it is a construction the hook could have prevented.
- No live provider run, no lint or type check, no frontend change (W3 does not touch it).

## Execution notes — batch B (Tasks 5-8)

### Task 5 — `max_tokens` recovery

- Commit: `6e19595` `feat(loop): recover from max_tokens: never run a cut tool call, continue a cut answer`
- Tests added: `tests/unit/agentic/test_output_limit_recovery.py` (7).
- Red: 4 failed, 3 passed before the change. The plan expected the first test and "the others" to fail. The three that passed (`test_without_a_provider_cap_nothing_is_raised`, `test_an_explicit_cap_is_never_raised`, `test_a_complete_call_with_a_length_finish_still_runs`) are regression guards for behaviour the runtime already had. They were kept as written.
- Deviations: none in code.
- Existing test expectations changed: none.

### Task 6 — Interruption keeps partial text and closes the tool batch

- Commit: `6a48808` `feat(loop): a cancel closes the tool batch with interrupted results and keeps partial text`
- Tests added: `tests/unit/agentic/test_interruption.py` (2).
- Red: ImportError (`INTERRUPTED_TOOL_RESULT`), as planned. Extra check: the late-delta test was also run against the HEAD version of `chat.py` and failed (`partial late text`, `streaming`), so the guard is what makes it pass.
- Deviations: none.
- Existing test expectations changed: none.

### Task 7 — Input queue

- Commit: `17a66d3` `feat(loop): input queue injected between tool rounds, promoted to a turn when left over`
- Tests added: `tests/unit/conversations/test_input_queue.py` (5), `tests/unit/agentic/test_queue_injection.py` (3), `tests/unit/api/test_queue_api.py` (2). Total 10.
- Red: 5 failed (ImportError, AttributeError, 404), as planned.
- Deviations: none in code.
- Existing test expectations changed: none.
- Not covered: `ChatWorker._promote_queued_inputs` has no direct test. `_inject_queued_inputs` is tested with a fake store only. The queue endpoints are tested with a stub application only.
- Known gap (not fixed, from the plan's design): the queue POST is not idempotent on the live-turn path. `enqueue_input` takes no `Idempotency-Key`, so a retried POST queues the text twice.

### Task 8 — Clean-room Code system prompt; Claude Code tool names on by default in Code mode

- Commit: `cce830f` `feat(prompt): Orin's own Code system prompt and Claude Code tool names by default in Code mode`
- Tests added: `tests/unit/agentic/test_code_prompt.py` (11).
- Red: ImportError (`public_text`), as planned.
- Deviations:
  1. `arguments_in` (`src/agentos/agentic/tools/public_names.py`) set `subagent_type` to `general` before resolving the aliases `agent_type`, `type` and `agent`. This was latent: the translation only ran when Claude Code names were turned on by hand. Task 8 turns them on by default in Code mode, so it reached every Code turn. It showed up as a failure of `tests/unit/agentic/test_subagents_and_permissions_v076.py::test_run_subagent_accepts_fuzzy_model_and_aliases_in_toolset` (the second child run was missing). Fix: in the `run_subagent` branch of `arguments_in`, resolve the alias keys before the default is applied. Not in the plan.
  2. Step 8 rule (snake_case tool names in provider requests): `tests/unit/test_orin_code_phase_c.py::test_builtin_subagents_explore_plan_general_and_fork` also asserted snake_case names in `plan_tools` and `general_tools`. Those checks were converted as well (see below). The plan's Step 7 item covers only the explore block (lines 207-211 of that function).
  3. `tests/unit/agentic/tools/test_public_names.py::test_agent_maps_types_and_prompt` expected `model` to be dropped. The plan's own Step 3 maps `model` to `model_id`, and the plan's test `test_the_agent_model_reaches_the_subagent` requires that. The expectation was updated (see below). This test is not in the plan's list.
- Existing test expectations changed (old → new):
  - `tests/unit/agentic/test_turn_session.py::test_new_managed_code_workspace_skips_plan_approval`: `"autonomia de código" in managed_runtime.system_prompt` → `"code autonomy" in managed_runtime.volatile_prompt` (plan Step 7).
  - `tests/unit/agentic/test_subagent_and_stream_resilience.py::test_fix6_subagent_runs_in_code_mode_loop_and_recovers_partial_output_like_claude_code`: `"glob_files" in tools_sent` → `"Glob" in tools_sent`; `"propose_code_session" not in tools_sent` → `"ProposeCodeSession" not in tools_sent` (plan Step 7).
  - `tests/unit/test_orin_code_phase_c.py::test_builtin_subagents_explore_plan_general_and_fork`: explore block `read_file` → `Read`, `search_files` → `Grep`, `write_file` → `Write` (not in), `edit_file` → `Edit` (not in), `run_command` → `Bash` (not in) (plan Step 7). Plan block: `write_file` → `Write`, `edit_file` → `Edit`, `run_command` → `Bash` (not in). General block: `write_file` → `Write`, `run_command` → `Bash` (in) (Step 8 rule).
  - `tests/unit/agentic/test_project_session_tools.py::test_project_tools_exposed_in_phases_code_mode_and_turn_session_prompt`: `"read_project_conversation" in runtime.volatile_prompt` → `"ReadProjectConversation" in runtime.volatile_prompt` (plan Step 7).
  - `tests/unit/agentic/tools/test_public_names.py::test_agent_maps_types_and_prompt`: the expected dict for `arguments_in("run_subagent", {... "resume": "agent-1", "model": "opus"})` gained `"model_id": "opus"` (deviation 3, not in the plan's list).
- Left in place: `src/agentos/code_mode/prompt.py` (`build_code_mode_system_prompt`) is no longer called by the session. It still has a test caller (`tests/unit/agentic/test_extension_tools_exposure.py`), so it was not removed. `TurnSession._code_prompt_parts` accepts a `trusted` argument it does not use.

### Verification notes (batch B)

- Pytest quirk: when a test file is passed on the command line together with its containing directory (`pytest tests/unit/agentic/test_x.py tests/unit/agentic ...`), pytest collects only the file. The run of `tests/unit/agentic/test_output_limit_recovery.py tests/unit/agentic tests/unit/test_orin_code_phase_b.py` reported 20 tests against 772 collected by the directory form. The suites were therefore run with the directory alone (the new file is inside it). Deviation in the command form only.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the task specified.

### Full backend suite after batch B

`uv run pytest -q tests/unit` → **2936 passed, 53 skipped, 0 failed** (about 189 s).
That is the batch A total (2906) plus the 30 tests added here (7 + 2 + 10 + 11).

### Not verified in batch B

- PostgreSQL: all tests ran on SQLite. The queue query (`func.count`, `notin_`, `in_`) and the `UPDATE ... rowcount` checks were not run against Postgres.
- No lint or type check was run (ruff, ty).
- No live provider run. The prompt is checked for section headings and public names, not for quality.
- Frontend untouched (W3 does not touch it). Backend only, so no visual check.
- Queue promotion at the end of a turn (`ChatWorker._promote_queued_inputs`) is exercised only through the store and application tests, not through a worker run.

## Execution notes — batch C (Tasks 9-12)

### Task 9 — Prompt snapshot per conversation, `excludeDynamicSystemPromptSections`, safe git status

- Commit: `a9149f3` `feat(prompt): per-conversation prompt snapshot, dynamic sections in the first message, safe git status`
- Tests added: `tests/unit/code_mode/test_git_context.py` (4), `tests/unit/agentic/test_prompt_snapshot.py` (8). Total 12.
- Red: `test_git_context.py` failed at collection (`ModuleNotFoundError: agentos.code_mode.git_context`), as planned. That run stopped at the collection error, so `test_prompt_snapshot.py` did not run in the red phase. It was checked afterwards: with the pre-task `session.py`, `runtime.py`, `conversations/chat.py` and `workers/chat.py` (from `153b535`) temporarily restored, that file gave 6 failed, 2 passed. The two passing tests are guards (`test_the_snapshot_can_be_turned_off`, `test_chat_mode_has_no_snapshot`) that hold on the old code too. HEAD was then restored and re-verified.
- Deviations: none in code.
- Existing test expectations changed: none.

### Task 10 — Standard compaction sections and a persisted `compact_boundary`

- Commit: `0b52f9e` `feat(compaction): one set of nine sections and a persisted compact_boundary the next turn starts from`
- Tests added: `tests/unit/agentic/test_compact_boundary.py` (5).
- Red: `ModuleNotFoundError: agentos.agentic.compaction`, as planned. The Step 7 expectations were run before they were changed: 7 failed (the ones listed below), then they were changed.
- Deviations:
  1. The old Portuguese four-section fallback (`_fallback_compaction_summary`) was deleted. The nine-section fallback (formerly `_fallback_code_compaction_summary`) took its name, and the old name stays as an alias. The plan describes this in prose; the deletion is written out here.
  2. The comments on the removed old header ("The old header ended with ...") went with the definitions they described. The comment on why summaries have fixed headings was kept.
  3. `COMPACTION_HEADER` stays imported (re-exported) in `runtime.py`, though `runtime.py` no longer uses it.
- Existing test expectations changed (file::test, old → new):
  - `tests/unit/agentic/test_structured_compaction.py`: `_SummarizingProvider.stream` returned the four Portuguese sections → returns the nine sections (`### {section}\n- detail`). `COMPACTION_HEADING` added to the import.
  - Same file, `test_the_compacted_replacement_keeps_the_structured_sections`, `test_the_compaction_header_does_not_invite_rework`, `test_the_fallback_still_produces_every_section`, `test_the_fallback_names_the_tools_whose_results_were_folded_away`: `startswith("## Contexto compactado")` → `startswith(COMPACTION_HEADING)`.
  - `tests/unit/agentic/test_context_window.py::test_auto_compaction_keeps_the_current_request_and_reduces_old_units`: `"## Contexto compactado" in ...` → `COMPACTION_HEADING in ...`.
  - `tests/unit/agentic/test_prefix_stability.py::test_compaction_is_the_only_thing_that_rewrites_the_prefix`: `"Contexto compactado" in ...` → `COMPACTION_HEADING in ...`.
  - `tests/unit/test_orin_code_phase_c.py::test_three_tier_compaction_and_top_5_file_rehydration`: `startswith("## Contexto compactado")` → `startswith(COMPACTION_HEADING)`.
  - Not changed: the `_SummarizingProvider` docstring in `test_structured_compaction.py` still says "four required sections" (stale wording only).

### Task 11 — Microcompact keeps the 8 newest results; `microcompact_boundary`; autocompact threshold and `autoCompact`

- Commit: `41830f8` `feat(compaction): microcompact clears all but the 8 newest results, autocompact follows the window`
- Tests added: `tests/unit/agentic/test_microcompact.py` (7).
- Red: 5 failed, 2 passed. The two that pass are guards (`test_small_results_are_not_worth_clearing`, `test_the_pinned_request_is_never_touched`).
- Deviations: none.
- Existing test expectations changed: none.

### Task 12 — After a compaction: reattach by read order, SessionStart `compact`, new snapshot

- Commit: `36fa43e` `feat(compaction): reattach files by read order, fire SessionStart compact and re-render the snapshot`
- Tests added: `tests/unit/agentic/test_post_compact.py` (2).
- Red: 2 failed, as planned (the read-order test listed `a.py`; `AgenticTurnRuntime` had no `prompt_renderer`).
- Deviations:
  1. `TurnSession._code_prompt_parts` still imports `render_code_system_prompt` but no longer calls it (the plan changes only the call). Cosmetic. `pyproject.toml` has no ruff configuration and CI does not lint, so it stays as the plan has it.
- Existing test expectations changed (file::test, old → new):
  - `tests/unit/agentic/test_lifecycle_runtime.py::test_compaction_emits_pre_and_post`: `names == [PRE_COMPACT, POST_COMPACT]` → `names == [PRE_COMPACT, POST_COMPACT, SESSION_START]`, plus `dict(events[2].payload)["source"] == "compact"` (plan Step 6).

### Verification notes (batch C)

- Pytest was run with directories only (`tests/unit/agentic`, not a file together with its own directory).
- Line endings: the index is LF for every file. The working copies of `runtime.py`, `session.py`, `agent_tools.py` and `workers/chat.py` are CRLF; `chat.py` is LF. `git diff --stat` shows only the intended changes.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### Full backend suite after batch C

`uv run pytest -q tests/unit` → **2962 passed, 53 skipped, 0 failed** (191 s). That is batch B's 2936 plus the 26 new tests (12 + 5 + 7 + 2). No existing test failed in batch C.

### Not verified in batch C

- PostgreSQL: everything ran on SQLite. The new queries (the snapshot `delete`/`insert`, the boundary `order_by(id desc).limit(1)`, `is_not(None)`, the `through_sequence` comparison) were not run on Postgres.
- A real `git status` inside a trusted workspace, end to end: `test_git_context.py` uses a temporary repository; `test_prompt_snapshot.py` checks the trust gate with `git_status_summary` stubbed. The fsmonitor test was checked to be meaningful: the control `git status` created the marker file, the Orin call did not.
- No live provider run. Summary quality with a real model is not measured, and nothing was measured against prompt-cache hits.
- No lint or type check (ruff, ty) was run.
- Backend only, so no visual check.
- Worker-level flows for the snapshot and the boundary (a `ChatWorker` turn against the real chat store) are not exercised end to end.

### Limitations found while testing (not fixed)

1. `git_status_summary` runs synchronously in `build_runtime` for every Code turn in a trusted local folder (2 s timeout). In a large repository that adds up to 2 s before the turn starts.
2. A `compact` boundary covers the history before the compacting turn's user message, but the summary also contains that turn's earlier tool work. On the next turn, the replay of that turn's stored steps (inside the rehydration budget) may repeat part of it. Not tested.
3. `_microcompact` rewrites old results on each check above 65% of the budget; every rewrite changes the cached prefix. Not measured.
4. `post_compact_enrichment` calls `stat()` on the plan file outside its `try`. If the plan is deleted between the glob and the stat, `_run_compaction`'s enricher guard catches the exception: the compaction still happens, but its enrichment is silently empty.
5. Microcompact boundaries are persisted but do not replace history, as the plan specifies. The next turn's replay of stored steps is unchanged, so a microcompact does not shrink the next turn's prompt. This is by design, noted so it is not mistaken for a bug.
6. `tests/unit/agentic/test_lifecycle_runtime.py` and `tests/unit/hooks/test_runtime_decisions.py` still stub the summarizer with the old Portuguese four-heading text. The runtime now rejects that shape and uses the fallback. Both tests pass because they assert nothing about the summary text. They are not in the plan's list and were not changed.
