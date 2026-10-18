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

## Execution notes — batch D (Tasks 13-16)

### Task 13 — `/compact [instructions]` as a compaction-only turn behind `POST /compact`

- Commit: `68cf1cd` `feat(compaction): /compact [instructions] as a compaction-only turn behind POST /compact`
- Tests added: `tests/unit/conversations/test_manual_compact.py` (3), `tests/unit/agentic/test_manual_compact_runtime.py` (4). Total 7.
- Red: all 7 failed before implementation (`request_compaction`, `compact_request`, `turn_kind` missing). The route test's failure message was not inspected separately.
- Deviations: none.
- Existing test expectations changed: none.

### Task 14 — Context usage by category (memory and free space), `GET /context`

- Commit: `694351e` `feat(context): context usage by category with memory and free space, served at GET /context`
- Tests added: `tests/unit/conversations/test_context_usage.py` (3).
- Red: 3 failed before implementation (`memory_prompt_tokens` unknown keyword, `context_usage` missing, route missing). The route test's failure message was not inspected separately.
- Deviations: none.
- Existing test expectations changed: none.

### Task 15 — ORIN.md/CLAUDE.md hierarchy (pure loader)

- Commit: `3500500` `feat(memory): ORIN.md/CLAUDE.md hierarchy with rules, local files, bounded @imports and lazy subdirectories`
- Tests added: `tests/unit/memory_files/test_loader.py` (15 from the plan, plus 1 added: `test_a_hostile_glob_cannot_stall_a_read`), and the empty `tests/unit/memory_files/__init__.py`. Total 16.
- Red: `ModuleNotFoundError: agentos.memory_files` before implementation.
- Deviation (security fix, found by trying to break the code): the plan's `glob_match` compiled `paths:` and `claudeMdExcludes` patterns into a backtracking `re` regex. Match time grows about 6x per extra `*` (0.46 s at 6 stars, about 8 s at 8 stars on a 41-char path). Repository rule files supply `paths:` patterns, so a hostile repository could stall every read that matches them. `_glob_regex` was replaced by `_glob_tokens` (pattern to tokens) and `_glob_accepts` (set-of-positions matcher: each token is applied once, no backtracking). Worst measured case: 0.35 s for a 500-char pattern on a 4000-char path. The added regression test failed (8.3 s) before the change and passes after it.
- Equivalence check: a scratch differential run of the old regex against the new matcher on 60,000 random pattern/path pairs (alphabet `ab/*?.A`, with and without `**/`) found 0 mismatches. The script lives in the session scratchpad and is not committed.
- Existing test expectations changed: none (`test_glob_semantics` and all other tests unchanged).

### Task 16 — Memory files in the session, the toolset and subagents

- Commit: `5bbe120` `feat(memory): load the instruction hierarchy per turn, report each file, bring rules in on read`
- Tests added: `tests/unit/agentic/test_memory_integration.py` (4).
- Red: verified after the implementation, not before (process deviation). The implementation was written before the new test was first run. Afterwards `session.py` and `agent_tools.py` were stashed and the test was run: 4 failed (`.claude/rules`, `CLAUDE.local.md`, user files, managed `claudeMd` and `memory_tracker` all missing). The stash was restored and the 4 tests passed with the implementation.
- Deviations: none in code. The `_memory_files` settings reads sit outside its `try`, as in the plan; the helpers they call are defensive (see limitations).
- Existing test expectations changed: none. `test_root_workspace_conventions_are_injected_into_the_volatile_prompt` and `test_first_turn_emits_session_start_prompt_and_instructions` pass unchanged.

### Verification notes (batch D)

- Pytest was run with files or directories only, never a file together with its own directory.
- Line endings: the modified files are CRLF in the working copy, as before. `git diff --stat` shows only the intended changes. The "LF will be replaced by CRLF" warnings on new files are normal with `core.autocrlf`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the plan specifies (the attribution reminder names Haiku 5.5; the plan and the launching instruction were followed).

### Full backend suite after batch D

`uv run pytest -q tests/unit` → **2992 passed, 53 skipped, 0 failed** (193 s). That is batch C's 2962 plus the 30 new tests (7 + 3 + 16 + 4). No existing test failed in batch D.

### Not verified in batch D

- PostgreSQL: everything ran on SQLite. Not run on Postgres: the `mark_command_turn` update, `status.in_((...))` in `history_for_turn`, the `turn_kind` insert, and the `context_usage` query (`order_by(id desc).limit(50)`).
- No lint or type check (ruff, ty).
- No live provider run. The quality of a manual compaction summary with a real model is not measured.
- Backend only, so no visual check. The frontend is untouched.
- End-to-end `ChatWorker` flows for `/compact` (queue promotion, PreCompact/PostCompact through the plugin hook engine) are not exercised. The tests use stubs around the real store, runtime, session and route.

### Limitations found while testing (not fixed)

1. Subagent toolsets (`TurnSession._toolset`, built for `_subagent_prompt` runs) get no `memory_tracker`, so a subagent's `read_file` does not bring in subdirectory instruction files or path rules. The subagent prompt does include the project memory. Sharing the turn's tracker would let a subagent's read consume the once-only load before the main agent sees its reminder, so this needs per-agent trackers (W4 territory).
2. Only `POST /compact` is a command. A message typed as `/compact` through `POST /messages` is an ordinary prompt (slash commands are W7).
3. Glob matching is bounded but not free: about 0.35 s per match for a 500-char pattern on a 4000-char path. With up to 64 rule files per directory, a repository full of such patterns slows each matching read. A tighter path or pattern cap would reduce this.
4. A manual compaction summarizes the whole history (`keep_units=0`), so nothing stays verbatim after `/compact`. This is by design.
5. In `history_for_turn`, the local name `blocked_user_ids` now also holds command-turn ids (`COMMAND_STATUS`). The behaviour is right; the name is misleading (cosmetic).
6. In `_memory_files`, the settings reads (`effective_settings.get`, `layer_values`) sit outside the `try`, as the plan has them. Both helpers are defensive, so the risk is low, but the global rule "a turn never breaks because of an enrichment" would prefer them inside.

## Execution notes — batch E (Tasks 17-20)

### Task 17 — Output styles (Default, Explanatory, Learning, custom files)

- Commit: `eb412da` `feat(prompt): output styles (Default, Explanatory, Learning and custom files)`. Follow-up fix: `867f67d` `fix(prompt): a repository's output styles count only for a trusted workspace` (deviation 2).
- Tests added: `tests/unit/agentic/test_output_styles.py` (5 from the plan, plus 1 regression test added with the fix). Total 6.
- Red: `ModuleNotFoundError: agentos.agentic.output_styles` before implementation. The session test, run as written, failed with `assert '# Output style: Learning' in ''` (deviation 1).
- Deviations:
  1. Plan test defect. `test_the_session_uses_the_style_and_it_changes_the_snapshot_key` built its turn without `code_mode`. The Code prompt and `_code_prompt_options` exist only in Code mode (Chat keeps its prompt, plan deviation 11), so `_code_stable_prompt()` returned an empty string and both snapshot keys were equal. Fix: `"code_mode": "code"` added to that test's Store turn. Implementation unchanged.
  2. Security gap in plan code, found by trying to break it. `load_output_styles` read `.claude/output-styles` and `.orin/output-styles` even for an untrusted workspace, and project files override user and built-in names. An untrusted repository's `.orin/output-styles/explanatory.md` replaced the user's chosen `Explanatory` style and went into the system prompt, dropping the coding guidance (file styles default to `keep-coding-instructions: false`). Reproduced by script before the fix. Why it matters: the plan's Global Constraints gate project-level memory content on trust. Fix (TDD: the new test failed with `source == 'project'` for an untrusted workspace): `load_output_styles(workspace_root, *, trusted=True)` skips project directories when `trusted` is false, and `resolve_output_style` passes `settings.trusted`. The default stays `True`, so the plan's `test_custom_styles_from_user_and_project_dirs` is unchanged. After the fix the same script resolves to the built-in style.
- Existing test expectations changed: none.

### Task 18 — Effort (thinking budget, reasoning effort) and Anthropic thinking signatures

- Commit: `bf88cc7` `feat(loop): effort as thinking budget or reasoning effort, with signed thinking kept across tool calls`
- Tests added: `tests/unit/agentic/test_effort.py` (9).
- Red: `ModuleNotFoundError: agentos.agentic.effort` before implementation.
- Deviations: none.
- Existing test expectations changed: none.

### Task 19 — `autoMemoryEnabled` and opt-in consolidation (`autoDreamEnabled`)

- Commit: `2c616b8` `feat(memory): autoMemoryEnabled pause and opt-in daily consolidation (autoDreamEnabled)`. Follow-up fix: `44d8c37` `fix(memory): a state-file timestamp without an offset is read as UTC` (deviation 1).
- Tests added: `tests/unit/memory/test_consolidation.py` (7 from the plan, plus 1 regression test added with the fix). Total 8.
- Red: `ModuleNotFoundError: agentos.memory.consolidation` before implementation.
- Deviations:
  1. Found by probing, not in the plan. `ConsolidationState.due` raised `TypeError` (offset-naive vs offset-aware datetimes) when the state file held a timestamp without an offset, for example after a hand edit. The runtime swallowed it (`_settle_quality` wraps `on_completed`), so no turn broke, but consolidation stopped for that user. Fix: a naive timestamp is read as UTC. The regression test failed with that `TypeError` before the fix.
- Existing test expectations changed: none.

### Task 20 — SessionStart source from the store and SessionEnd at worker shutdown

- Commit: `2571e5e` `feat(hooks): SessionStart source from the store (startup/resume) and SessionEnd at worker shutdown`
- Tests added: `tests/unit/agentic/test_session_source.py` (3), `tests/unit/hooks/test_session_end.py` (3). Total 6.
- Red: `ImportError: cannot import name 'reset_process_sessions'` and `agentos.hooks.session_end` missing, before implementation.
- Deviations: none. The SessionEnd tests start real subprocesses through the command runner (the shell on this machine is cmd.exe) with the timeouts as written (30 s hook timeout, 1.0 s shutdown budget).
- Existing test expectations changed: none.

### Verification notes (batch E)

- Pytest was run with directories only, never a file together with its own directory.
- Commits: every task commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the batch instruction specifies. The attribution reminder in that session named Haiku 5.5; the batch instruction was followed.
- The two follow-up fixes are extra commits, outside the one-commit-per-task rule. Each is logged above with its own RED step.
- Line endings: the ledger is LF; code files are CRLF in the working copy. The "LF will be replaced by CRLF" warnings on new files are normal with `core.autocrlf`.
- No frontend or visual change in this batch.

### Full backend suite after batch E

`uv run pytest -q tests/unit` → **3021 passed, 53 skipped, 0 failed** (197 s). That is batch D's 2992 plus 29 new tests: 6 (Task 17), 9 (Task 18), 8 (Task 19), 6 (Task 20). A full run before the two follow-up fixes gave 3019 passed, 53 skipped, 0 failed.

### Not verified in batch E

- No lint or type check. `ruff`, `ty` and `pyflakes` are not installed in the project environment, and no dependency was added for this. `py_compile` passed on the new modules.
- PostgreSQL. Everything ran on SQLite. The `IntegrityError` retry in `record_session_start` (two workers creating the same session row at once) is not exercised by any test.
- No live provider call. The Anthropic thinking round-trip (signature, redacted block, tool loop) is verified only against mocked SSE and request payloads. A real API run is still needed.
- No cross-process test of the consolidation state file. The module lock is per process.
- No browser or app run. The batch is backend only.

### Limitations found while testing (not fixed)

1. Effort on Anthropic: `apply_effort` raises `max_tokens` to `budget + 4000` (up to 32,000) even when the turn's explicit output cap is smaller, so an explicit cap is overridden whenever effort is set. The raised value is also not bounded by the remaining context (`remaining_tokens`). Plan deviation 4 covers escalation only. Suggested fix: never raise above the explicit cap, and bound the raise by the remaining context.
2. Consolidation: `mark_run` writes the state before the background job starts, so a job interrupted by worker shutdown is not retried for 24 h. `note_completed_turn` and `mark_run` are read-modify-write on one JSON file, so two worker processes can lose an increment, and the fixed temp name `auto-dream.tmp` can clash. Failures are swallowed. Best effort only; suggested fix if it matters: a unique temp name per write.
3. SessionEnd runs only on a graceful worker exit (the `finally` in `publisher.main`). A hard kill (SIGKILL, crash) skips it. This follows plan deviation 8.
4. The ignored-effort notice is recorded on every turn for an unsupported model, so a conversation on Ollama with `effort` set gets one notice per turn.
5. `load_output_styles` defaults to `trusted=True` (kept so the plan's test is unchanged). Production code reaches it only through the trust-gated `resolve_output_style`. Any new caller that serves an untrusted workspace must pass `trusted=False`.
6. `_PROCESS_SESSIONS` is process-global. A test in the same process that uses a store with `session_state` must call `reset_process_sessions()`; the new session tests do.

## Execution notes — batch F (Tasks 21-24)

### Task 21 — FileChanged for files changed outside the session

- Commit: `7cea7b9` `feat(hooks): FileChanged for files changed outside the session, with a reminder to re-read`
- Tests added: `tests/unit/agentic/test_file_changed.py` (4).
- Red: 4 failed before the change (`AttributeError` for `detect_external_changes`; `IndexError` in the runtime test because no FILE_CHANGED event was emitted).
- Anchors found as planned: `self._inject_reminders(messages)` in `AgenticTurnRuntime._run`, `_read_file_lock` and `_read_file_state` in `AgentToolset`.
- Deviations: none in code.
- Existing test expectations changed: none.
- Limitations (by reading the code, not measured or tested): the check runs before every model request and stats up to 200 files the turn read. A change made by a shell command the model runs (for example `sed -i` through `run_command`) is also reported as an external change, because only the toolset's own writes update the read state.

### Task 22 — TaskCreated/TaskCompleted and ConfigChange blocks; PostToolUseFailure and SubagentStart context

- Commit: `933644a` `feat(hooks): task and config blocks take effect; PostToolUseFailure and SubagentStart context reach the model`
- Tests added: `tests/unit/hooks/test_w3_hook_decisions.py` (5).
- Red: 5 failed before the change, each for the expected reason (task blocks ignored, failure context dropped, `config_block_reason` missing, SubagentStart context missing).
- Deviations: none in code.
- Existing test expectations changed: none. `test_task_events` (W2) passes unchanged.
- Limitation found by probing (plan design, not fixed): TaskCreated now fires before the tool validates its input. A `task_create` with a blank subject emitted `TaskCreated` (subject `"   "`) and was then refused with `TOOL_REFUSED`, and `PostToolUseFailure` followed. A hook therefore sees creates the task list never receives. By reading the code, the same happens to `task_update` with `status: completed` for an id that does not exist (`_task_hook_block` runs before the tool's own checks; not probed). Suggested fix: apply the tool's own validation in `_task_hook_block` and skip the event when it would fail; or emit TaskCreated after a successful create and keep only a pre-check for blocking.

### Task 23 — W1-R9: an explicitly non-interactive Code turn refuses an ordinary ask

- Commit: `703d629` `fix(permissions): W1-R9 a non-interactive Code turn refuses an ordinary ask unless the user opts in`
- Tests added: `tests/unit/permissions/test_non_interactive_ask.py` (2).
- Red: 2 failed before the change (`TypeError` for the new `non_interactive_ask` keyword; `AttributeError` for `_non_interactive_ask_allowed`).
- Deviations: none in code.
- Existing test expectations changed: none.
- The second test writes `permissions.nonInteractiveAsk` into the user settings directory. `tests/conftest.py` points `ORIN_USER_DIR` at a temporary folder for every test, so the developer's `~/.orin` is not touched.

### Task 24 — Regression, warnings and ledger

- Warning fix: `src/agentos/permissions/engine.py` (`_token_paths` docstring, line 166) and `src/agentos/permissions/paths.py` (`workspace_path` docstring, line 86): `\.` became `\.` in the docstrings. No runtime text changed.
- Verification method (deviation): the plan's Step 1 command (`python -W error::SyntaxWarning -c "import ..."`) does not recompile a module whose bytecode cache is still valid, so it can pass without reading the source. I ran it only after the fix (it printed nothing and exited 0), so my own runs do not show that it would have caught the bug. The check that confirmed the bug and the fix compiles each source file with `compile()` under `-W error::SyntaxWarning`: it failed before the fix (`SyntaxError: invalid escape sequence '\.'` in both files) and passes after it. The same check over every `src/agentos/**/*.py` file found no other file with the warning.
- Ledger commit: this commit (see Verification).

## Spec §6 delivered

Paths are relative to `src/agentos/` (code) and `tests/unit/` (tests). "Delivered" means the code and the listed tests exist and pass in the full backend suite. What is still unverified is listed under Pending and known limitations.

| Item | Where | Tests |
|---|---|---|
| Code-mode turns without caps (maxTurns, maxBudgetUsd, safetyMaxToolRounds=500), durable leases, 24 h execution deadline | `agentic/loop_settings.py`; `agentic/runtime.py` (MAX_TURNS, MAX_BUDGET_USD, safety pause, TURN_DEADLINE_EXCEEDED); `agentic/session.py` (`CODE_TURN_DEADLINE` = 24 h); `conversations/chat.py` (lease predicate, `renew_dispatch_leases`); `workers/publisher.py` (lease renewal) | `agentic/test_loop_settings.py`, `agentic/test_code_loop_limits.py`, `conversations/test_dispatch_leases.py` |
| Retry with backoff/jitter/retry-after; fallback chain with Pre/PostModelSwitch | `agentic/retry.py`; `agentic/runtime.py` (fallback switch; PreModelSwitch can refuse the candidate); `agentic/provider_stream.py` (retry-after on rejection) | `agentic/test_retry.py`, `agentic/test_retry_and_fallback.py` |
| max_tokens recovery | `agentic/runtime.py` (`_output_cap`, `_max_tokens_recoveries`); `agentic/provider_stream.py` (`max_tokens` finish) | `agentic/test_output_limit_recovery.py` |
| Input queue (API only) | `conversations/chat.py` (`enqueue_input`, `claim_queued_inputs`, `cancel_queued_input`); `agentic/runtime.py` (`_inject_queued_inputs`); `workers/chat.py` (`_promote_queued_inputs`); `agentic/transcript.py` (queued-input step); `api/gateway.py` (queue endpoints) | `conversations/test_input_queue.py`, `agentic/test_queue_injection.py`, `api/test_queue_api.py` |
| Interruption keeps partial text; interrupted tool results | `agentic/runtime.py` (`INTERRUPTED_TOOL_RESULT`, `_cancel`) | `agentic/test_interruption.py` |
| One `<system-reminder>` mechanism | `agentic/reminders.py`, used by `agentic/runtime.py`, `agentic/agent_tools.py`, `agentic/session.py` | `agentic/test_reminders.py` |
| Clean-room Code prompt, public tool names by default in Code mode (W0-R20) | `code_mode/system_prompt.py`; `agentic/tools/public_names.py`; `agentic/session.py` | `agentic/test_code_prompt.py` (plus the expectation changes listed below) |
| Prompt snapshot, excludeDynamicSystemPromptSections, safe git status | `conversations/chat.py` (`prompt_snapshot`, `save_prompt_snapshot`, `clear_prompt_snapshot`); `agentic/session.py`; `code_mode/git_context.py` | `agentic/test_prompt_snapshot.py`, `code_mode/test_git_context.py` |
| compact_boundary, microcompact_boundary, /compact, /context, reattach, nine sections | `agentic/compaction.py`; `agentic/runtime.py` (`_run_compaction`, `_run_manual_compaction`, `_microcompact`); `conversations/chat.py` (`record_compact_boundary`, `context_usage`); `api/gateway.py` (`POST /v1/conversations/{id}/compact`, `GET /v1/conversations/{id}/context`) | `agentic/test_compact_boundary.py`, `agentic/test_microcompact.py`, `agentic/test_post_compact.py`, `agentic/test_manual_compact_runtime.py`, `conversations/test_manual_compact.py`, `conversations/test_context_usage.py` |
| Memory hierarchy (ORIN.md/CLAUDE.md, rules, local files, @imports, lazy subdirectories, claudeMdExcludes, InstructionsLoaded per file) | `memory_files/__init__.py`, `memory_files/loader.py`; `agentic/session.py` (`_memory_files`); `agentic/agent_tools.py` (`_note_memory_for`, lazy load on `read_file`) | `memory_files/test_loader.py`, `agentic/test_memory_integration.py` |
| Output styles, effort, autoMemoryEnabled, autoDreamEnabled | `agentic/output_styles.py`; `agentic/effort.py`; `agentic/provider_stream.py` (thinking budget, reasoning effort, signed thinking); `memory/consolidation.py`; `agentic/session.py` | `agentic/test_output_styles.py`, `agentic/test_effort.py`, `memory/test_consolidation.py` |
| SessionStart source (startup/resume/compact) from the store, SessionEnd, FileChanged (W0-R14 / W2-R6 residual) | `agentic/session.py` (`_session_start_source`); `conversations/chat.py` (`record_session_start`); `hooks/session_end.py` and `workers/publisher.py` (SessionEnd at shutdown); `agentic/agent_tools.py` (`detect_external_changes`); `agentic/runtime.py` (`_check_external_changes`) | `agentic/test_session_source.py`, `hooks/test_session_end.py`, `agentic/test_file_changed.py` |
| TaskCreated/TaskCompleted/ConfigChange blocks; PostToolUseFailure and SubagentStart context | `agentic/agent_tools.py` (`_task_hook_block`); `agentic/session.py` (`_emit_turn_start_events`, `_execute_subagent_run`); `agentic/runtime.py` (`config_block_reason`); `settings/watcher.py` (`forget`); `agentic/lifecycle.py` (`_CONTEXT_EVENTS`) | `hooks/test_w3_hook_decisions.py` |
| W1-R9 decision (non-interactive ask) | `agentic/agent_tools.py` (`non_interactive_ask`); `agentic/session.py` (`_non_interactive_ask`) | `permissions/test_non_interactive_ask.py`. No production path makes a turn non-interactive, so the rule never applies today (W3-R24, deferred to W6). |
| Five keys never read from a repository (`fallbackModel`, `effort`, `effortLevel`, `autoDreamEnabled`, `permissions.nonInteractiveAsk`) | `settings/loader.py` (5 added lines) | No dedicated W3 test for these five keys. `agentic/test_effort.py` covers the repository-effort case. |
| Public context telemetry keys | `agentic/models.py` (`_PUBLIC_CONTEXT_TELEMETRY_KEYS`) | No W3 test was added for this constant. The pre-existing `agentic/test_activity_contracts.py` refers to the public-context helper. |
| Schema: 4 tables, 2 columns, migration 0048 | `persistence/postgres/migrations/versions/0048_w3_loop_and_context.py`; `persistence/postgres/schema.py` | `persistence/test_w3_schema.py`, `persistence/test_postgres_schema.py` (tests ran on SQLite only) |

## Lifecycle events after W3

Status against the W2 table (`2026-10-18-w2-hooks-ledger.md`). W2: 21 of 33 events emitted. After W3: 25 emitted (the code calls `bus.emit` for them, counted by grep over `src/agentos`). Only the rows that changed are listed.

| Event | W2 | After W3 | Where |
|---|---|---|---|
| SessionStart | emitted; context and env file applied | emitted; `source` is `startup`, `resume` (first time this process serves an existing conversation) or `compact` (after a compaction) | `session.py` (`_session_start_source`); `runtime.py` (compact) |
| SessionEnd | not emitted | emitted at worker shutdown with `reason: "other"`; informational. A supervised stop runs it since W3-R8 (on Windows only when the supervisor has a console; see Pending) | `hooks/session_end.py` (`end_sessions`); `workers/publisher.py` |
| FileChanged | not emitted | emitted for files the turn read that changed outside it, payload `{file_path, event}`; informational plus a reminder to re-read | `agentic/runtime.py` (`_check_external_changes`); `agentic/agent_tools.py` |
| PreModelSwitch | not emitted | emitted before a fallback model is used; a block or deny keeps the current model | `agentic/runtime.py` (fallback switch) |
| PostModelSwitch | not emitted | emitted after a fallback switch; informational | `agentic/runtime.py` |
| PostToolUseFailure | emitted; context dropped by the bus | context applied: appended to the failed tool result | `agentic/lifecycle.py` (`_CONTEXT_EVENTS`); `agentic/agent_tools.py` (`invoke`) |
| SubagentStart | emitted; context not injected | context applied: appended to the subagent's task text | `agentic/session.py` (`_execute_subagent_run`) |
| TaskCreated | emitted after the change; blocking deferred | emitted before the change; a block leaves the task list unchanged. A call the tool refuses (blank subject, unknown id) emits no event (W3-R21). | `agentic/agent_tools.py` (`_task_hook_block`) |
| TaskCompleted | emitted after the change; blocking deferred | emitted before the change; a block keeps the task open | `agentic/agent_tools.py` (`_task_hook_block`) |
| ConfigChange | emitted; blocking deferred | a block stops the turn (fail-closed, `HOOK_BLOCKED_CONFIG`): no hook of the refused settings runs in that turn or in the later blocked ones (W3-R3). The refused file's own ConfigChange hooks still run before the refusal is known, which is inherent. The change is reported again next turn until a hook accepts it; managed policy changes are never blocked | `agentic/session.py` (`_emit_turn_start_events`); `settings/watcher.py` (`forget`); `agentic/runtime.py` |
| InstructionsLoaded | emitted once per turn for the project files | emitted per loaded memory file with `memory_type` and `load_reason`; also emitted when a read brings in subdirectory files | `agentic/session.py`; `agentic/agent_tools.py` (`_note_memory_for`); `memory_files/` |
| PreCompact / PostCompact | emitted | payloads now carry `trigger` and `custom_instructions` (PreCompact), and `trigger`, `compaction_count`, `pre_tokens`, `post_tokens` (PostCompact) | `agentic/runtime.py` (`_run_compaction`) |

Not listed above: PreToolUse, PostToolUse, PostToolBatch, UserPromptSubmit, Stop, StopFailure, SubagentStop, PermissionRequest, PermissionDenied, WorktreeCreate, WorktreeRemove and CwdChanged. The batch notes record no change to them, but I did not re-audit them. Not emitted (as in W2): Notification, UserPromptExpansion, Setup, Elicitation, ElicitationResult, TeammateIdle, DirectoryAdded, MessageDisplay.

## Deviations

### Plan deviations (from the plan header, same numbering)

1. Settings key names chosen by Orin: `maxTurns`, `maxBudgetUsd`, `safetyMaxToolRounds` (500), `fallbackModel`, `systemPromptSnapshot`, `excludeDynamicSystemPromptSections`, `autoCompact`, `outputStyle`, `effort` (alias `effortLevel`), `autoMemoryEnabled`, `autoDreamEnabled`, `claudeMdExcludes`, `permissions.nonInteractiveAsk`.
2. Hook payload fields not in the notes are Orin's choice (PreModelSwitch/PostModelSwitch, FileChanged, InstructionsLoaded, SessionEnd, PreCompact, PostCompact).
3. `maxTurns` counts completed model responses; `maxBudgetUsd` is checked before each model call.
4. Output-cap escalation goes up to 32,000 tokens, only when the turn has no explicit `max_output_tokens` and the provider sent a cap. Batch E found that effort can still raise the cap above an explicit value (see Pending).
5. Fallback models stay on the turn's provider; the primary is used again on the next turn.
6. Input queue is API only; queued text is injected between tool rounds and promoted to a follow-up turn if left over.
7. `/compact` and `/context` are API endpoints in W3; slash commands and the grid are W7.
8. SessionEnd fires at worker shutdown (`reason: "other"`) only.
9. Effort is sent only to models that reason (OpenRouter/OmniRoute `reasoning`, Anthropic `thinking`, OpenAI o-series/gpt-5); otherwise ignored with a visible note. Ollama is always ignored.
10. Consolidation runs after a completed turn, at most once per 24 h per user and after 5 completed turns since the last run, with the turn's own model.
11. Chat mode keeps its caps, its single immediate retry and its prompt.
12. PostToolUseFailure and SubagentStart `additionalContext` are honoured; TaskCreated and TaskCompleted blocks prevent the change.
13. ConfigChange block: the turn does not run and the change is reported again until a hook accepts it.
14. Subdirectory memory loads on `Read`/`read_file` only.
15. Prompt snapshot is keyed by `PROMPT_VERSION`, the published tool names, the names flag and the output style; compaction deletes it.
16. W1-R9: an explicitly non-interactive Code turn denies an ordinary `ask` unless `permissions.nonInteractiveAsk` is `allow`. Chat mode and turns without the key are unchanged. No production path makes a turn non-interactive yet, so the rule does not run in production (W3-R24, W6).
17. FileChanged watches files read during the current turn.
18. No gate: W3 behaviour is on by default in Code mode. There is no `experimental.w3` gate, although spec §2 and §16 put each wave behind `experimental.<wave>` until it stabilises (W3-R25). This is a deviation, not reverted by batch 3.

### Execution deviations (batches A-F)

- E1 (A, Task 2): `tests/unit/persistence/test_postgres_schema.py` asserts the exact table set and was not in the plan's list; the four W3 tables were added to the expected set (additive).
- E2 (A, Task 3): anchor placement: the `catalog_pricing` line sits after the annotated `self.lifecycle: LifecycleBus | None = lifecycle` line.
- E3 (A, Task 1): import order only in `session.py`.
- E4 (B, Task 5): 3 of the 7 new tests passed before the change; they were kept as regression guards.
- E5 (B, Task 8): `arguments_in` in `agentic/tools/public_names.py` set `subagent_type` to `general` before resolving the aliases `agent_type`, `type` and `agent`. This was latent until Claude Code names became the default in Code mode. Fixed in the `run_subagent` branch; not in the plan.
- E6 (B, Task 8): the snake_case rule (plan Step 8) was also applied to the plan and general blocks of `test_orin_code_phase_c.py::test_builtin_subagents_explore_plan_general_and_fork`, beyond the explore block in Step 7.
- E7 (B, Task 8): `model` maps to `model_id` for `Agent`/`run_subagent` (plan Step 3), so `test_public_names.py::test_agent_maps_types_and_prompt` changed; not in the plan's list.
- E8 (B through F): test command form. When a file is passed together with its own directory, pytest collects only the file. Batches used directories or files, never both.
- E9 (C, Task 9): `test_git_context.py` failed at collection, so the snapshot test did not run in the red phase. It was checked afterwards by temporarily restoring the pre-task files (6 failed, 2 passed), then HEAD was restored.
- E10 (C, Task 10): the old Portuguese four-section fallback was deleted; the nine-section fallback took its name (the old name stays as an alias); comments on the removed header were removed.
- E11 (C, Task 10): `COMPACTION_HEADER` is still re-exported from `runtime.py` though unused there.
- E12 (C, Task 12): `TurnSession._code_prompt_parts` still imports `render_code_system_prompt` but no longer calls it (cosmetic).
- E13 (D, Task 15, security): the plan's regex glob matcher was replaced by a token matcher (`_glob_tokens`, `_glob_accepts`): match time grew about 6x per `*` (about 8 s at 8 stars). Regression test `test_a_hostile_glob_cannot_stall_a_read` added.
- E14 (D, Task 16): process: the implementation was written before the new test was first run; it was checked afterwards by stashing the implementation (4 failed, then pass with it restored).
- E15 (E, Task 17, plan test defect): `test_the_session_uses_the_style_and_it_changes_the_snapshot_key` built a non-Code turn. `"code_mode": "code"` was added to that test's turn. Implementation unchanged.
- E16 (E, Task 17, security): follow-up commit `867f67d`. Project and local output styles were read for untrusted workspaces and overrode user and built-in names. `load_output_styles(..., trusted=...)` now skips project directories when untrusted; regression test added.
- E17 (E, Task 19): follow-up commit `44d8c37`. A state-file timestamp without an offset raised `TypeError` in `ConsolidationState.due`; it is now read as UTC. Regression test added.
- E18 (E): the two follow-up fixes (E16, E17) are extra commits outside the one-commit-per-task rule.
- E19 (F, Task 24): the plan's Step 1 import command does not recompile a module with a valid bytecode cache, so it can pass without reading the source. A source-level compile check was used to confirm the bug and the fix (see the Task 24 notes).

## Existing tests whose expectations changed (and why)

Each line is an existing test whose assertion was edited. No existing test was deleted, skipped or weakened.

- `tests/unit/persistence/test_postgres_schema.py::test_persistence_schema_contains_only_the_durable_boundary_tables`: expected table set gained the four W3 tables (`conversation_input_queue`, `conversation_prompt_snapshots`, `conversation_compact_boundaries`, `conversation_sessions`). Reason: the test asserts the exact set; the tables are new by design.
- `tests/unit/agentic/test_structured_compaction.py::_SummarizingProvider.stream` (test double): returned the old four Portuguese sections; now returns the nine sections. `COMPACTION_HEADING` imported. Reason: one set of nine sections.
- `tests/unit/agentic/test_structured_compaction.py`: `test_the_compacted_replacement_keeps_the_structured_sections`, `test_the_compaction_header_does_not_invite_rework`, `test_the_fallback_still_produces_every_section`, `test_the_fallback_names_the_tools_whose_results_were_folded_away`: `startswith("## Contexto compactado")` now `startswith(COMPACTION_HEADING)`. Reason: the header is a constant in `compaction.py`.
- `tests/unit/agentic/test_context_window.py::test_auto_compaction_keeps_the_current_request_and_reduces_old_units`: `"## Contexto compactado" in ...` now `COMPACTION_HEADING in ...`. Same reason.
- `tests/unit/agentic/test_prefix_stability.py::test_compaction_is_the_only_thing_that_rewrites_the_prefix`: `"Contexto compactado" in ...` now `COMPACTION_HEADING in ...`. Same reason.
- `tests/unit/test_orin_code_phase_c.py::test_three_tier_compaction_and_top_5_file_rehydration`: `startswith("## Contexto compactado")` now `startswith(COMPACTION_HEADING)`. Same reason.
- `tests/unit/agentic/test_turn_session.py::test_new_managed_code_workspace_skips_plan_approval`: `"autonomia de código" in managed_runtime.system_prompt` now `"code autonomy" in managed_runtime.volatile_prompt`. Reason: the Code prompt is Orin's own clean-room text and the dynamic part moved to the first message.
- `tests/unit/agentic/test_subagent_and_stream_resilience.py::test_fix6_subagent_runs_in_code_mode_loop_and_recovers_partial_output_like_claude_code`: `"glob_files" in tools_sent` now `"Glob" in tools_sent`; `"propose_code_session" not in tools_sent` now `"ProposeCodeSession" not in tools_sent`. Reason: public tool names are on by default in Code mode.
- `tests/unit/test_orin_code_phase_c.py::test_builtin_subagents_explore_plan_general_and_fork`: explore block `read_file` now `Read`, `search_files` now `Grep`, and `write_file`, `edit_file`, `run_command` are checked as `Write`, `Edit`, `Bash` (absent). Plan block `write_file` now `Write`, `edit_file` now `Edit`, `run_command` now `Bash` (absent). General block `write_file` now `Write`, `run_command` now `Bash` (present). Reason: plan Step 7 and the Step 8 rule.
- `tests/unit/agentic/test_project_session_tools.py::test_project_tools_exposed_in_phases_code_mode_and_turn_session_prompt`: `"read_project_conversation" in runtime.volatile_prompt` now `"ReadProjectConversation" in ...`. Reason: public tool names by default in Code mode.
- `tests/unit/agentic/tools/test_public_names.py::test_agent_maps_types_and_prompt`: expected dict for `arguments_in("run_subagent", {... "model": "opus"})` gained `"model_id": "opus"`. Reason: E7; not in the plan's list.
- `tests/unit/agentic/test_lifecycle_runtime.py::test_compaction_emits_pre_and_post`: `names == [PRE_COMPACT, POST_COMPACT]` now `[PRE_COMPACT, POST_COMPACT, SESSION_START]`, plus `source == "compact"` on the third event. Reason: a compaction now also emits a session-start with source `compact`.
- `tests/unit/agentic/test_code_loop_limits.py::test_the_safety_cap_pauses_and_asks_to_continue` (batch 3, W3-R14): `store.finished` expectation changed from `("SAFETY_TOOL_ROUNDS", False)` to `("WAITING_USER", False)`. Reason: the safety pause now finishes the stored turn as `waiting_user`.

Batch F changed no existing test.

## Pending and known limitations

Known defects found in the batches and not fixed, most important first:

- Effort on Anthropic (batch E), explicit cap: fixed in batch 2 (W3-R12). The cap is never raised, and a cap below 2,048 sends no thinking. Still open: without an explicit cap, effort raises `max_tokens` to budget + 4000, and that raise is not bounded by the remaining context.
- TaskCreated and TaskCompleted fired for calls the tool then refused: fixed in batch 3 (W3-R21). No event fires for a refused create or for an unknown task id.
- Consolidation (batch E): `mark_run` writes the state before the background job starts, so a job interrupted by shutdown is not retried for 24 h. The state is a read-modify-write JSON file, so two worker processes can lose an increment, and the fixed temp name `auto-dream.tmp` can clash. Failures are swallowed. Best effort only; suggested fix: a unique temp name per write.
- Queue POST idempotency: fixed in batch 3 (W3-R20). A repeated Idempotency-Key in a conversation returns the first queue row. Still open: a key reused with a different body is not refused. It returns the first row, so the second text is dropped without an error. The row did not ask for a refusal.
- `git_status_summary` runs synchronously in `build_runtime` for every Code turn in a trusted local folder (2 s timeout). In a large repository this adds up to 2 s before the turn starts (batch C).
- Subagent toolsets get no `memory_tracker` (batch D), so a subagent's read does not bring in subdirectory instruction files or path rules. Needs per-agent trackers (W4).
- `post_compact_enrichment` calls `stat()` on the plan file outside its `try` (batch C). If the plan is deleted between the glob and the stat, the compaction still happens but its enrichment is silently empty.
- The ignored-effort notice is recorded on every turn for an unsupported model (batch E): a conversation on Ollama with `effort` set gets one notice per turn.
- Compact boundary (corrected by W3-R1, batch 1): a `compact` boundary now covers exactly the stored messages that were folded (`through_sequence`), so no retained message is lost. Still open: the summary also contains the compacting turn's earlier tool work, and the next turn's replay may repeat part of it (no content is lost). The W3-R1 tests cover the loss, not the repeat.
- `_PROCESS_SESSIONS` is process-global (batch E). A test in the same process that uses a store with `session_state` must call `reset_process_sessions()`; the new session tests do.
- `load_output_styles` defaults to `trusted=True` (batch E). Production code reaches it only through the trust-gated `resolve_output_style`; any new caller serving an untrusted workspace must pass `trusted=False`.
- FileChanged detection cost is not measured, and shell-driven edits to read files are reported as external changes (batch F, from reading the code, not tested).
- Glob matching is bounded but not free: about 0.35 s per match for a 500-char pattern on a 4000-char path (batch D).
- Stale tests not in the plan's list (batch C): `tests/unit/agentic/test_lifecycle_runtime.py` and `tests/unit/hooks/test_runtime_decisions.py` still stub the summarizer with the old Portuguese four-heading text. The runtime now rejects that shape and uses the fallback. Both pass because they assert nothing about the summary text.
- `ChatWorker._promote_queued_inputs` has no direct test (batch B); it is covered only through the store and application tests.
- Cosmetic: `history_for_turn`'s local `blocked_user_ids` also holds command-turn ids (batch D); `_memory_files` settings reads sit outside their `try` (batch D; the helpers they call are defensive).
- Manual compaction summarises the whole history, so nothing stays verbatim after `/compact` (by design, batch D).
- Subagent prompts still name some tools in snake_case inside repository text (carried from the plan's checklist; not re-verified in batch F).
- Deferred to W7 by the plan: the queue, `/compact` and `/context` UIs, and slash commands (a `/compact` typed in a message is an ordinary prompt).
- SessionEnd runs only on a graceful worker exit (`publisher.main`). A supervised stop runs it since W3-R8, but on Windows only when the supervisor has a console: a supervisor with no console cannot deliver Ctrl+Break, so its children keep `CREATE_NO_WINDOW` and a stop there is terminate-after-grace without SessionEnd (needs a decision: a stop-request file or a hidden console). A hard kill skips it (plan deviation 8). It is not called on conversation delete or archive, because Orin has none yet.
- Effort is not sent to Ollama (plan deviation 9).
- Consolidation uses the turn's model; Orin has no fast-model setting yet (plan deviation 10).
- ConfigChange hooks are judged with the new settings (plan deviation 13); Orin keeps no copy of the previous settings across processes.
- Linux was not run. All runs were on Windows.
- W1-R9 rule has no production caller (W3-R24): no path sets `interactive_permissions` to False, so a non-interactive ask never reaches the rule. Deferred to W6 (headless `--permission-prompts none`).
- Not delivered from spec §6 (W3-R25), by wave. `--json-schema` structured output with retries (§6.1): W6. `/memory` (edit) and `/init` (generates ORIN.md) (§6.4): W7. `/pause-memory` (pauses learning and consolidation) (§6.4): W7. Next-prompt suggestion, opt-in with the fast model (§6.5): W7. "Send now" (interrupt and send, §6.1): W7. Todo reminders in the `<system-reminder>` mechanism (§6.1): W4. Plan-mode reminder (§6.1): the review gives no wave; this ledger places it in W4 with the todo reminder.
- No `experimental.w3` gate (deviation 18): no setting turns the W3 loop off as a wave. The per-feature settings (`maxTurns`, `autoCompact`, `systemPromptSnapshot`, and the others) still apply.

Verification gaps (from the batch notes, still true):

- PostgreSQL: every test ran on SQLite, and migration 0048 was applied only on SQLite. Not run on Postgres: the Postgres branch of `_ensure_conversation_mode_columns` (`TIMESTAMP WITH TIME ZONE`), the `func.coalesce` lease predicate, the queue `func.count`/`notin_`/`in_` queries, the snapshot delete/insert, the boundary `order_by(id desc).limit(1)`, `mark_command_turn`, `status.in_(...)`, and the `context_usage` query. The `IntegrityError` retry in `record_session_start` is not exercised by any test.
- No live provider run in any batch. The Anthropic thinking round trip (signature, redacted block, tool loop) is verified only against mocked SSE and request payloads. Compaction summary quality with a real model and prompt-cache behaviour were not measured.
- No lint or type check of the W3 Python code: `ruff`, `ty` and `pyflakes` are not installed in the project environment, and no dependency was added. `py_compile` passed on the new modules.
- Retry timing was tested with a fake `sleep`; the real `time.sleep` path and cancellation during a real wait were not timed.
- A refused PreModelSwitch still builds the fallback provider object before the hook runs (the plan's order). The object is discarded and no network call is made.
- No browser or app run. W3 is backend only; the frontend was not changed (zero frontend files in the range).
- Consolidation state file: no cross-process test (the lock is per process).
- The SessionEnd tests start real subprocesses through the command runner (the shell on this machine is cmd.exe).
- Worker-level flows (ChatWorker turns for the snapshot and the boundary, `/compact`, queue promotion, PreCompact/PostCompact through the plugin hook engine) are not exercised end to end; the tests use stubs around the real store, runtime and route.

## Verification

Full backend suite after batch F (`uv run pytest -q tests/unit`, run once, 200.5 s):

- **3032 passed, 53 skipped, 0 failed.** Batch E ended at 3021 passed, 53 skipped; batch F adds 11 tests (Task 21: 4, Task 22: 5, Task 23: 2) and nothing else changed the count.
- 31 warnings, all DeprecationWarnings or Starlette notices from existing code (`bootstrap/production.py`, SQLAlchemy's datetime adapter, FastAPI's test client). None was traced to W3 code.

Targeted runs during batch F (directory form):

- Task 21: `tests/unit/agentic tests/unit/test_orin_code_phase_b.py` → 838 passed, 2 skipped.
- Task 22: `tests/unit/hooks tests/unit/agentic tests/unit/settings` → 965 passed, 5 skipped.
- Task 23: `tests/unit/permissions tests/unit/agentic tests/unit/code_mode tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_d.py` → 1087 passed, 3 skipped.

Frontend (W3 changed no file under `frontend/`):

- `npm --prefix frontend test` (vitest): 100 test files, 596 tests passed, exit 0. No rerun was needed.
- `npm --prefix frontend run lint` (`eslint . --max-warnings=0`): exit 0, no output.
- `npm --prefix frontend run build` (`tsc -b && vite build`): exit 0. Vite warns that some chunks exceed 500 kB; that warning comes from existing frontend code, not from W3.

Other checks:

- `compile()` with `-W error::SyntaxWarning` over every `src/agentos/**/*.py` file: 0 files with a warning or error (after the Task 24 fix).
- Not run: a visual or browser check (backend only); lint or type checks of the Python code (tools not installed); PostgreSQL; a live provider. See Verification gaps and Pending above.

## Review fixes (2026-10-18-w3-review.md) — batch 1 (R1-R7)

Each finding is one commit, `fix(w3): W3-Rn ...`, with its regression test written first and seen failing. The reproduction scripts live in the review session's scratchpad (`review_w3/`); "re-run" below is the output after the fix.

| Finding | Status | What changed | Tests | Residual risk |
|---|---|---|---|---|
| W3-R1 (compaction drops the newest units) | Fixed (`941d57b`; guard `95ca7b3`) | `_run_compaction` (and so `_force_compact`) persists the last stored sequence it folded as `through_sequence`; no `compact` boundary is written when no stored message was folded. `history_for_turn` tags each conversation message with `_sequence` (underscore keys never reach the provider). `record_compact_boundary(through_sequence=...)`; the old `user_sequence - 1` rule remains only for callers that omit the argument. | `tests/unit/agentic/test_compact_boundary.py`: `test_auto_compaction_never_drops_retained_turns`, `test_reactive_compaction_never_drops_retained_turns`, `test_a_compaction_that_folds_no_stored_message_persists_no_boundary`, `test_a_manual_compaction_still_covers_the_whole_history` (the last one is a guard, green before and after). Re-run r01: `LOST between turns: []`. | The duplication limitation (batch C, item 2) is NOT fixed: steps of a retained turn that were folded inside that turn are still replayed next turn. No content is lost. Fixing it needs step-level sequencing, which the row's "record which turns' steps were folded" does not provide. |
| W3-R2 (UNC `@import` opens the SMB share) | Fixed (`cc98f92`) | `_resolve_import` returns None for UNC and device targets, for every layer, before any I/O. `_inside_project` decides lexically first and resolves only a lexically inside path. `_key` and `_excluded` never resolve a remote path. | `tests/unit/memory_files/test_loader.py`: `test_an_untrusted_unc_import_never_touches_the_network` (2 trust values x 3 spellings; spy on `Path.resolve` and `Path.stat`), `test_an_absolute_local_path_outside_an_untrusted_project_is_refused_without_resolving`. Re-run r04: no UNC path opened, load 0.0 s (was 22.1 s). | A link inside the project (junction or symlink) whose target is a UNC path is still resolved by the containment check, so the share can still be opened through that link. Git on Windows writes links as plain text unless `core.symlinks` is on. Not fixed; the next step is to read the link target before resolving. |
| W3-R3 (refused ConfigChange still runs hooks) | Fixed (`017feef`) | A refused change removes the settings hooks from the bus at once (`_remove_settings_hooks`); `_emit_turn_start_events` returns before InstructionsLoaded, SessionStart and UserPromptSubmit for that turn. | `tests/unit/hooks/test_w3_hook_decisions.py::test_a_refused_config_change_runs_none_of_its_hooks` (two blocked turns: zero runs; the accepted turn runs the hook once). Re-run r02: refused executions 0 in turns 2 and 3 (was 1 and 2). | The refused file's own ConfigChange hooks run before the refusal is known; that is inherent to the event. While a settings change is refused, every settings hook of the turn is off, including unchanged ones (fail closed). |
| W3-R4 (repository excludes the user's instructions) | Fixed (`2bfb5ed`) | `MemoryFiles(repository_excludes=...)`. Operator excludes (user, CLI, managed layers) apply to every non-managed file; repository excludes only to the repository's own project and local files. Managed text stays non-excludable. `_memory_files` splits `claudeMdExcludes` by layer. | `tests/unit/memory_files/test_loader.py`: `test_a_repository_cannot_exclude_the_users_own_instructions`, `test_an_operator_exclude_hides_a_project_file_and_a_repository_exclude_hides_its_own`, `test_a_repository_exclude_never_hides_managed_text`; `tests/unit/agentic/test_memory_integration.py::test_a_repository_cannot_exclude_the_users_own_instructions[False|True]`. Re-run r03: user rules present with the repository exclude set. | Not changed: rules with `paths:` are added without any exclude check, so `claudeMdExcludes` never hides them, from any layer. |
| W3-R5 (queued text answers a question or a safety pause) | Fixed (`cb1e475`) | `ChatWorker._promote_queued_inputs` promotes only after `completed` or `completed_with_caveats`. A `waiting_user` end (question or safety pause), a failed one and a cancelled one keep the queue pending. | `tests/unit/conversations/test_input_queue.py`: `test_queued_text_never_answers_a_question`, `test_queued_text_waits_out_a_safety_pause`, `test_queued_text_is_promoted_after_a_finished_turn[completed|completed_with_caveats]`. Re-run: the original r08 part (a) still promotes, because it calls the application method directly; the adapted copy `r08a_adapted_worker_gate.py` calls the worker and shows the queue pending, one turn, turn states `['waiting_user']`. | `ChatApplication.enqueue` promotes without a state check when a turn ends between its liveness check and its insert (a narrow window). The safety pause is still stored as `completed` (W3-R14, not in this batch). |
| W3-R6 (fallback keeps the primary's effort and prices) | Fixed (`d2943fb`), with a deviation | Runtime `effort_supported` (a fallback that rejects the effort drops it; a check that raises counts as rejected) and `model_switched` (the pricing learns the new model). Session pricing is `_TurnPricing`, which replaces `_cost_estimator`: catalog rates only while the turn's model is the one it started with. Deviation: the row checked `self.turn["model_id"]`, which never reflects a switch in production (the runtime updates its own copy), so the switch is reported to the pricing instead. | `tests/unit/agentic/test_retry_and_fallback.py`: `test_a_fallback_keeps_or_drops_the_effort_by_what_the_model_accepts[True|False]`, `test_a_fallback_is_priced_as_the_fallback_model` (spies `estimate_cost_usd` through a real `TurnSession.build_runtime`). Re-run: the original r07 cannot show the fix (it does not pass the new parameters, and the runtime swallows its now-missing estimator call); adapted `r07a_adapted_fallback_wiring.py`: effort `None`, thinking `None`, fallback cost 0.00416. | A fallback model with no catalog or fallback-table price is not counted, as before. Not run against a live Anthropic API. |
| W3-R7 (Code subagents outside the budget and the Code limits) | Fixed (`81153c5`), with deviations | `BudgetMeter` (thread-safe) in `agentic/loop_settings.py`, one per `TurnSession` (one per turn) rather than one per `build_runtime` call. The main and every subagent runtime add each call's cost to it, and `max_budget_usd` is checked against its total. A Code-mode subagent inherits the turn's deadline, `max_budget_usd`, `max_provider_retries` and `retry_base_delay_seconds`, and prices its calls for its own model. `max_turns` is not inherited (the row does not list it). Chat-mode subagents are unchanged. | `tests/unit/agentic/test_subagent_and_stream_resilience.py`: `test_a_code_subagent_spends_from_the_turn_budget`, `test_a_code_subagent_stops_when_the_turn_budget_is_already_spent`, `test_a_code_subagent_inherits_the_turn_deadline_retries_and_budget`; `tests/unit/agentic/test_loop_settings.py::test_a_budget_meter_adds_from_many_threads_and_ignores_what_is_not_money`. Re-run r14: subagent deadline 1 day, budget 0.5, retries 8, backoff 0.5 (was 1 h, none, 1, 0). | The budget is checked before each model call, so a single call can overshoot it. A subagent's `max_turns` is still unlimited. Chat-mode subagents remain outside any budget, as Chat has none. The verification subagent of a hook shares the meter in Code mode. |

Existing tests changed (full list):

- `tests/unit/agentic/test_compact_boundary.py::_bulky()` (fixture): each message gained a `_sequence`, because the runtime now persists a boundary only for stored messages. No expectation changed.
- `tests/unit/agentic/test_manual_compact_runtime.py::HISTORY` (fixture): the four messages gained `_sequence` 1 to 4, for the same reason. No expectation changed.
- `tests/unit/agentic/test_memory_integration.py` and `tests/unit/agentic/test_subagent_and_stream_resilience.py`: import lines extended. No test body changed.

No assertion of an existing test was removed or changed. New tests were added to the files named above.

Full backend suite after batch 1 (`uv run pytest -q tests/unit`, run once, 239.7 s):

- **3060 passed, 53 skipped, 0 failed**, 31 warnings (the same kinds as before). Before this batch: 3032 passed, 53 skipped. The 28 new tests are the regression tests above.

Not verified in this batch: PostgreSQL, Linux, a live provider, a real UNC host (R2 was checked with a spy on path resolution and stat, not with network capture), and the frontend (no file under `frontend/` changed).

## Review fixes — batch 2 (R8-R13)

Each finding is one commit, `fix(w3): W3-Rn ...`, with its regression tests written first and seen failing. The reproduction scripts live in the review session's scratchpad (`review_w3/`). "Re-run" below is the output after the fix; where a script calls an API that changed or indexes a value the fix removes, the re-run used an adapted copy written next to it in the scratchpad (never in the repository).

| Finding | Status | What changed | Tests | Residual risk |
|---|---|---|---|---|
| W3-R8 (SessionEnd never runs on a supervised stop) | Fixed (`9f9950c`), with a deviation | `install_shutdown_handlers()` (`workers/publisher.py`): SIGTERM, and SIGBREAK on Windows, raise `SystemExit(0)` on the first signal only, from the main thread only. `main()` runs `end_sessions("other", timeout_seconds=5.0)` in `finally` before `runner.shutdown(wait=True)`. Deviation: on Windows the graceful stop never reached a child, because `SupervisedProcess.start` created it with `CREATE_NO_WINDOW`, which gives the child a console the supervisor's Ctrl+Break does not reach (probe: the handler ran with `CREATE_NEW_PROCESS_GROUP` alone, never with `CREATE_NO_WINDOW`). The child now keeps `CREATE_NO_WINDOW` only when the supervisor has no console (`_attached_to_console`, `GetConsoleProcessList`); a child of an attached supervisor inherits its console and opens no window. | `tests/unit/workers/test_worker_shutdown.py`: `test_a_supervised_stop_runs_session_end` (real `publisher.main()` in a subprocess, stopped by `SupervisedProcess.stop(grace=8)`, exit 0, SessionEnd marker written), `test_session_end_runs_before_the_turns_in_flight_are_drained`, `test_a_repeated_stop_signal_does_not_cut_the_shutdown_short`, `test_handler_installation_is_a_no_op_off_the_main_thread`; `tests/unit/launcher/test_windows_no_console_leak.py::test_a_child_of_an_attached_supervisor_shares_its_console_so_ctrl_break_reaches_it`. Red: subprocess exit 1 and no marker. Re-run r09 (its child has no installer, unchanged): stop 0.1 s, exit 0xC000013A, finally False (the break now arrives, and the default handling ends the process at once). Adapted `r09a_adapted_installer.py` (child installs the real handler and waits for a ready file): stop 0.8 s, exit 0, finally True. | A supervisor with no console at all (a GUI interpreter) still cannot deliver Ctrl+Break: its children keep `CREATE_NO_WINDOW`, so a stop there is terminate-after-grace and SessionEnd does not run. Needs a decision (a stop-request file the worker polls, or a hidden console). A second signal is ignored, so the launcher's terminate escalation no longer unwinds `finally`. A SessionEnd hook slower than 5 s is abandoned (pre-existing). Not verified: POSIX (SIGTERM path), a real desktop launch, and a console-less supervisor on this machine (this environment always has a console, so the attached branch is what was exercised). |
| W3-R9 (a repository lowers the autocompact threshold; summaries are not billed) | Fixed (`cfc40a9`) | `_autocompact_setting` (session): only operator layers (user, CLI, managed) set a token threshold; a repository may set `false` (turns autocompaction off) and nothing else; a repository `"auto"` is ignored. `_autocompact_threshold` (runtime): an integer threshold is floored at 25% of `max_context_tokens` (was 1,000 tokens). `_request_compaction_summary` bills the summarizer's call: the provider's reported cost, else the cost estimator on its input and output tokens; into the turn's `BudgetMeter`, or the runtime's own counter when there is none (`_spent_usd`). | `tests/unit/agentic/test_microcompact.py`: `test_a_repository_cannot_lower_the_autocompact_threshold` (untrusted repository file in the session's workspace root), `test_an_operator_threshold_has_a_floor`, `test_the_summary_call_counts_toward_the_budget`. Existing expectations unchanged (`test_the_autocompact_threshold`, `test_the_session_reads_autocompact`). Red: a repository `autoCompact: 1` came back as `1`; the floor was 1,000; the meter total was 0. Re-run r06: (a) `runtime.autocompact='auto'` (was 1); (b) with an operator value of 1 passed to the runtime, 0 summary calls (was 8). | Behaviour change: an operator who sets a small threshold gets at least 25% of the context. A summary whose provider reports no usage and no estimator is billed 0. The summary cost counts toward `max_budget_usd` only, not `max_cost` (PROVIDER_COST_LIMIT). The budget is checked before each model call, so a summary can overshoot by one call. |
| W3-R10 (a cancel does not stop a running command) | Fixed (`1a783c3`) | `AgentToolset(cancel_check=...)`. The foreground `run_command` wait loop checks it at most every `CANCEL_CHECK_SECONDS` (0.5 s); when it is true the loop terminates the process tree, joins the readers, reaps the process with `wait()`, and raises `AgentToolError("Interrupted by user: the command was stopped.", error_code="INTERRUPTED_BY_USER")`. The session passes `lambda: bool(self.cancelled(self.turn))`, the signal the runtime uses; subagent toolsets come from the same builder, so they see the parent turn's cancel. A check that raises never stops a command. The timeout path is unchanged (an existing test pins its `communicate` call sequence). | `tests/unit/agentic/test_interruption.py::test_a_cancel_stops_the_running_command` (a real 30 s `python -c` sleep stopped within 3 s, `INTERRUPTED_BY_USER`, its pid no longer alive, checks at least 0.45 s apart). Red: `TypeError` (no `cancel_check` parameter), a missing feature rather than the behaviour. Re-run r16: unchanged at 4.0 s, because its toolset is a fake `time.sleep` that never polls the cancel (the runtime cannot interrupt a call that does not poll). Adapted `r16a_adapted_real_toolset.py` (real `AgentToolset` with `run_command`, runtime `cancelled`): the turn returned `cancelled` after 0.8-0.9 s; result "Interrupted by user: the command was stopped." | An interrupted mutating command is recorded NOT_APPLIED, although it may have changed files before it was stopped (fail-closed would be UNKNOWN; the row did not ask for it, not changed). Up to 0.5 s plus the kill time before the stop. Partial output of the stopped command is not returned. Only foreground commands (a background process is not stopped by a turn cancel, by design). Not verified on POSIX. |
| W3-R11 (a queued message the model received is not durable) | Fixed (`0802932`) | `history_for_turn` always replays each earlier turn's `queued_input` steps as user messages, after that turn's replayed trajectory and before its answer, on every budget, bounded to 16,000 characters each and 20 per turn. `_rehydrated_steps` leaves queued steps out of the budgeted trajectory, so they are never repeated. `_ConversationProviderPort.queued_turn_count` carries the runtime's count into the kernel-path `AgenticRunResult`. | `tests/unit/conversations/test_input_queue.py::test_a_delivered_queued_message_stays_in_the_conversation` (real runtime and store; the instruction appears exactly once with budgets 0, 1,500 and 100,000); `tests/unit/workers/test_chat.py::test_conversation_provider_port_carries_the_queued_count`. Red: budget 0 showed 0 occurrences; the port had no `queued_turn_count`. Re-run r15: the instruction is visible on all three budgets (was not visible on 0 and 1,500). | Its position is after that turn's trajectory, not between its tool calls. `queued_turn_count` now reaches the worker's result, but nothing consumes it yet. The conversation view still shows only user and assistant messages (r15's "conversation shows" line is unchanged): the fix covers the model's history only. Each history call now reads the earlier turns' rows even at budget zero (a small extra query). |
| W3-R12 (effort raises an explicit output cap) | Fixed (`f14637c`) | `apply_effort(..., *, explicit_cap=None)`. With an explicit cap (the request's `max_output_tokens`), the Anthropic thinking budget is `min(level budget, cap - 1024)`; no thinking is sent when that is below 1,024; the cap is never raised. Without an explicit cap the existing raise is unchanged. `_request_for` passes `explicit_cap=requested`. | `tests/unit/agentic/test_effort.py::test_effort_never_raises_an_explicit_cap` (through the transport, the r12 item 1 request), `test_a_cap_below_2048_sends_no_thinking`. Red: `max_tokens 32000 == 1000`. Re-run: adapted `r12a_adapted_item1.py` (the original script indexes `payload["thinking"]`, which the fix removes): `max_tokens 1000`, no thinking (was 32,000 with a 28,000 budget). | A small explicit cap with an effort level now gets no thinking (intended). Without an explicit cap, effort still raises the cap to budget + 4,000, not bounded by the remaining context (not in the row). Not verified against the live API (W3-R31, as before). |
| W3-R13 (an edited instruction file never reaches the model; hooks hear every turn) | Fixed (`00b328c`), deliberate behaviour change | `_snapshot_key` includes the digest of the rendered memory text, so an edit re-renders the stable prompt snapshot on the next turn (one cache miss). InstructionsLoaded is emitted only for files whose digest differs from the one last reported for that conversation (`_REPORTED_INSTRUCTIONS`, in process memory; every file on the first turn after a restart). The fallback path (no memory object) digests each file's bytes; an unreadable file is reported every time. `reset_process_sessions` clears the digests. | `tests/unit/agentic/test_memory_integration.py::test_an_edited_instruction_file_reaches_the_model_next_turn` (two later builds see the edit), `test_unchanged_instruction_files_are_reported_only_when_they_change`; `test_code_mode_snapshots_memory_with_the_stable_prompt` (expectation changed, see below). Red: the old expectation held ("first version" still in the snapshot); a second, unchanged build reported the file again. Re-run r20: turn 1 reports and sees V1, turn 2 reports and sees V2, turn 3 reports nothing and sees V2 (was: reported every turn, kept V1). | Every instruction edit is one cache miss of the stable prompt (intended). A file whose load reason changes without a content change is not reported again. The digest map holds an entry set per conversation for the life of the process (small; not capped). |

Existing tests changed in batch 2 (each listed; no test deleted or skipped):

- `tests/unit/agentic/test_memory_integration.py::test_code_mode_snapshots_memory_with_the_stable_prompt`: the final expectation changed from "the first version is still in the second build's system prompt" to "the second version is in it, and the first is not". Reason: W3-R13, a deliberate behaviour change (the row asks for it).
- `tests/unit/launcher/test_windows_no_console_leak.py::test_supervised_process_start_sets_create_no_window_on_windows`: added `monkeypatch.setattr(processes, "_attached_to_console", lambda: False)`, so the test keeps its documented scenario (a supervisor without a console) whichever console the test runner has. Its assertions are unchanged.
- `tests/unit/agentic/test_microcompact.py::_session_autocompact`: gained an optional `repository` argument (used by the new test only). No expectation changed.
- `tests/unit/agentic/test_interruption.py`, `tests/unit/conversations/test_input_queue.py`, `tests/unit/workers/test_chat.py`, `tests/unit/agentic/test_effort.py`: imports extended, or new tests appended. No existing test body changed.
- New file: `tests/unit/workers/test_worker_shutdown.py` (4 tests).

Full backend suite after batch 2 (`uv run pytest -q tests/unit`, run once, 260.9 s):

- **3075 passed, 53 skipped, 0 failed**, 31 warnings (the same kinds as before). Before this batch: 3060 passed, 53 skipped. The 15 new tests are the regression tests above (R8: 5, R9: 3, R10: 1, R11: 2, R12: 2, R13: 2).

Not verified in this batch: PostgreSQL, Linux or any POSIX signal path, a live provider, a desktop launch of the supervisor, and the frontend (no file under `frontend/` changed).

## Review fixes — batch 3 (R14-R22, R25, R27, R28, R30)

Each finding is one commit, `fix(w3): W3-Rn ...`, with its regression test written first and seen failing. The reproduction scripts live in the review session's scratchpad (`review_w3/`). "Re-run" below is the output after the fix. Where a script calls an API that changed (R20, R21, and R12 item 1 from batch 2), the re-run used an adapted copy written next to it in the scratchpad (never in the repository). R27, R28 and R30 are code-reading findings: the test is the check.

| Finding | Status | What changed | Tests | Residual risk |
|---|---|---|---|---|
| W3-R14 (safety pause stored as completed) | Fixed (`ba54d78`) | `_pause_for_safety` finishes the stored turn with `WAITING_USER`; the result keeps `SAFETY_TOOL_ROUNDS`. | `tests/unit/conversations/test_input_queue.py::test_a_safety_pause_is_stored_as_waiting_for_the_person` (real store). Red: `'completed' == 'waiting_user'`. Re-run r08 (b): stored state `waiting_user` (was `completed`). Expectation changed: see the list below. | r08 (a) still promotes, because that script calls the application method directly; the worker gate (W3-R5) covers the real path, as in batch 1. The batch-1 test `test_queued_text_waits_out_a_safety_pause` still writes the code `SAFETY_TOOL_ROUNDS` by hand; it tests the gate, not the store mapping. |
| W3-R15 (unbounded rule-file patterns) | Fixed (`b8b7cdd`) | `_load_rules`: a pattern over 200 characters is ignored; at most 32 patterns per rule and 256 in total; a rule left with no usable pattern is skipped (never loaded unconditionally). | `tests/unit/memory_files/test_loader.py`: `test_a_hostile_rule_set_cannot_stall_a_read` (the r05 shape, the read timed), `test_rule_patterns_are_bounded_per_rule_and_in_total`, `test_rule_patterns_longer_than_200_characters_are_ignored`. Red: the read took 8.3 s on the old loader. The first draft also timed the load, which on this machine is dominated by file opens (about 11 ms per freshly written file); the test was corrected to time the read only. Re-run r05: read 0.0 s (was 11.1 s). | The load still takes about 0.9 s for 64 rule files on this machine (file opens, not matching). Worst case the caps allow: 256 patterns of 200 characters read in 0.25 s on a 128-character path; the cost grows with the path length (up to 4000 characters). |
| W3-R16 (migration 0048 after the store's self-patch) | Fixed (`11ceb36`) | 0048 inspects the bind and creates each table and index, and adds each column, only when it is missing. Store first and migration first both reach head. | `tests/unit/persistence/test_w3_schema.py::test_the_migration_reaches_head_after_the_store_patched_the_database` (red: `table conversation_input_queue already exists`), `test_the_store_starts_on_a_database_that_is_already_at_head`. Re-run r10: `upgrade head: OK`, revision 0048 (was FAILED at 0047). | SQLite only. PostgreSQL not run (the inspector calls and the batch add on Postgres are unverified). Downgrade unchanged. |
| W3-R17 (settings source by the resolved path) | Fixed (`46d5ed7`) | `_settings_source` decides lexically first (under the workspace root, as the memory loader does since W2-R2); only a path outside the root is resolved. | `tests/unit/hooks/test_w3_hook_decisions.py::test_a_settings_file_reached_through_a_link_is_still_a_project_file` (a junction made with `mklink /J`, a symlink on POSIX, skipped if the machine cannot make a link; also checks that a real outside file stays `policy_settings`). Red: `'policy_settings' == 'project_settings'`. Re-run r17: `project_settings`, blockable True (was `policy_settings`, not blockable). | The POSIX symlink branch is not run here. A link inside the root that points into a user directory is now a project file (blockable): it fails closed, but it no longer shows as a user file. |
| W3-R18 (microcompact clears the current batch) | Fixed (`8e349fb`) | `_microcompact` never clears a tool result that comes after the last tool-call message. | `tests/unit/agentic/test_microcompact.py::test_results_after_the_last_tool_call_are_never_cleared` (red: 4 cleared). The existing microcompact tests pass unchanged. Re-run r18: 0 cleared (was 4). | A batch larger than 8 is kept whole until the next request. The reactive `_force_compact` path also keeps the current batch, so one very large batch can still overflow until the summary runs. |
| W3-R19 (external-change check hashes on every request) | Fixed (`7eb0d52`) | `AgentToolset._external_stamps` remembers `(mtime_ns, size)` per path; an unchanged stamp is not hashed again; a file over 8 MiB compares the stamp only (a moved stamp counts as the change). The record's sha stays the read-time hash. | `tests/unit/agentic/test_file_changed.py::test_an_unchanged_stamp_is_not_hashed_again` (red: 3 reads), `test_a_growing_large_log_is_not_read_in_full_on_every_request` (red: 3 full reads of 12.5 MB). Re-run r11: 0 full reads in three requests, the change reported once (was a 100 MB read per request). | The row said "one full read"; the 8 MiB rule gives zero reads for a file over the limit (deviation). A `touch` of a file over 8 MiB is reported once as a change (no content comparison). A file under 8 MiB that keeps changing is still hashed on each request (up to 8 MiB). |
| W3-R20 (queue POST not idempotent) | Fixed (`46e8be5`) | `conversation_input_queue.idempotency_key` (nullable) with a unique index per conversation: in the schema, in migration 0048, and in the store's self-patch (which adds the column and the index to an existing queue table). `enqueue_input(..., idempotency_key=)` and `ChatApplication.enqueue` return the first row for a repeated key; a retry after the turn ended returns the first row and does not send again; an insert race is settled by the unique index. | `tests/unit/conversations/test_input_queue.py`: `test_a_repeated_idempotency_key_returns_the_first_queue_row`, `test_a_retry_after_the_turn_ended_neither_queues_nor_sends_again`, `test_the_store_returns_the_row_of_a_repeated_key`; `tests/unit/persistence/test_w3_schema.py::test_the_store_adds_the_idempotency_key_to_an_older_queue_table`. Red: all four failed (the queue ids differed, the keyword was unknown, the column was missing). Re-run r12 item 3 (adapted copy `r12b_adapted_items_2_3.py`; the original stops at item 1): `['also do X']` (was two rows). | A key reused with a different body returns the first row and the second text is dropped without an error. The row asks for a refusal only "if the row says so"; it does not, so it is not implemented. The insert-race branch is not exercised by a test. PostgreSQL not run. |
| W3-R21 (task hooks fire for refused calls) | Fixed (`d745d43`) | `_task_hook_block` returns no event when the tool would refuse: a blank subject or an invalid status for a create, an unknown id for a completion. | `tests/unit/hooks/test_w3_hook_decisions.py::test_a_task_create_the_tool_refuses_fires_no_hook`, `test_a_completion_of_an_unknown_task_fires_no_hook`. Red: TaskCreated / TaskCompleted in the events. Re-run r12 item 2 (adapted copy): events `['PreToolUse', 'PostToolUseFailure']` (were with TaskCreated / TaskCompleted). | The refusal checks copy the tool's own checks: if the tool's validation changes, the hook gate must change with it. |
| W3-R22 (hook-refused queued text kept) | Fixed (`0f31d03`) | `mark_queued_input_blocked` writes the blocked placeholder over the content. | `tests/unit/conversations/test_input_queue.py::test_a_queued_text_a_hook_refused_is_not_kept_in_the_database`. Red: the password text was kept. Re-run r19: `[('blocked', '[mensagem bloqueada por hook]')]` (was the password text). | The row stays (state `blocked`, id and timestamps kept); only the text is erased. |
| W3-R27 (consolidation reads assistant text) | Fixed (`ad37af2`) | `transcript_excerpt` keeps only the person's own (`user`) messages. | `tests/unit/memory/test_consolidation.py::test_the_excerpt_is_only_the_persons_own_messages` (exact excerpt; red: the assistant line was present). Expectation changed: `test_the_excerpt_keeps_the_newest_text` (see the list below). Code-reading finding: no script. | Consolidation sees less context: a decision made only in assistant text is no longer learned (opt-in feature). The person's own text is still read. |
| W3-R28 (reminder tags, case and spacing) | Fixed (`a1a9f64`) | `_neutralize` rewrites every spelling of the reminder tag, opening or closing (any case, inner spaces, an attribute list of at most 200 characters), to `system_reminder`. | `tests/unit/agentic/test_reminders.py::test_every_spelling_of_the_reminder_tags_is_neutralized` (5 cases; all 5 fail on the previous code, checked by restoring that file from git and then restoring HEAD). Check: `system_reminder('a </SYSTEM-REMINDER> b')` gives `a </system_reminder> b` inside one block. | Unicode look-alikes of the hyphen and zero-width characters inside the tag are not neutralized. An attribute list longer than 200 characters is not matched (deviation: the row asked for case and spacing only). |
| W3-R30 (leftovers) | Fixed (`071880a`), one commit | The unused `render_code_system_prompt` import in `_code_prompt_parts` is removed; the `_INTERNAL_WORD` look-behind excludes a backslash again; the memory settings reads in `_memory_files` are inside the guard. | `tests/unit/agentic/test_code_prompt.py::test_public_text_leaves_a_windows_path_alone` (red: `C:\tools\Read`), `tests/unit/agentic/test_memory_integration.py::test_a_settings_read_failure_never_stops_the_memory_load` (red: RuntimeError). The import removal has no test; an AST check confirmed the name was unused in that function. | A backslash directly before a tool id is no longer rewritten (intended). |
| W3-R25 (ledger gaps) | Fixed (`0630aa0`), documentation only | Pending gained the spec §6 items with their waves: `--json-schema` W6; `/memory`, `/init`, `/pause-memory`, suggestions and send-now W7; todo reminders W4; plan-mode reminder W4 (the review named no wave; this is the ledger's choice). The missing `experimental.<wave>` gate is deviation 18. Claims corrected for R1 (boundary), R3 (ConfigChange), R8 (SessionEnd) and R24 (W1-R9). Three Pending entries fixed in batches 2 and 3 (R12, R20, R21) are marked. | None (documentation). | The plan-mode reminder's wave is this ledger's choice. |

Deviations in batch 3 (each is also in its row above):

- R15: a rule whose patterns are all over the limit is skipped, never loaded unconditionally.
- R19: over 8 MiB the moved stamp is the change, so the row's "one full read" is zero reads.
- R20: a repeated key with a different body returns the first row; there is no refusal.
- R28: the tag match allows an attribute list of at most 200 characters.
- Deviation 18 (the plan's `experimental.<wave>` gate): W3 is on by default in Code mode. Batch 3 did not change this.

Existing tests whose expectations changed in batch 3 (each listed; no test deleted, skipped or weakened):

- `tests/unit/agentic/test_code_loop_limits.py::test_the_safety_cap_pauses_and_asks_to_continue`: `store.finished` expectation changed from `[("SAFETY_TOOL_ROUNDS", False)]` to `[("WAITING_USER", False)]`. Reason: W3-R14, the stored turn is now `waiting_user`.
- `tests/unit/memory/test_consolidation.py::test_the_excerpt_keeps_the_newest_text`: `"new answer" in excerpt` changed to `"new answer" not in excerpt`. Reason: W3-R27, consolidation reads only the person's own messages.

New tests were added to the files named in the table, and imports were extended in `tests/unit/conversations/test_input_queue.py`, `tests/unit/agentic/test_reminders.py`, `tests/unit/agentic/test_code_prompt.py` and `tests/unit/agentic/test_memory_integration.py`. No other existing test body changed.

Deferred findings (not fixed in batch 3):

- W3-R23 (consolidation state race) → W6, with background jobs.
- W3-R24 (the W1-R9 rule has no production caller) → W6, headless `--permission-prompts none`.
- W3-R26 (subagents have no memory tracker) → W5, agents in files.
- W3-R29 (fallback provider built before PreModelSwitch) → accepted: no network call happens at construction.
- W3-R31 (thinking replay against the real API) → W6, live conformance.

Full backend suite after batch 3 (`uv run pytest -q tests/unit`, run once, 247.2 s):

- **3100 passed, 53 skipped, 0 failed**, 31 warnings (the same kinds as before). Before this batch: 3075 passed, 53 skipped. The 25 new tests are the regression tests above (R14: 1, R15: 3, R16: 2, R17: 1, R18: 1, R19: 2, R20: 4, R21: 2, R22: 1, R27: 1, R28: 5, R30: 2).

Not verified in this batch: PostgreSQL (the migration's inspector calls, the new unique index, its self-patch, and the insert-race branch), POSIX (the R17 symlink branch), a live provider, a console-less supervisor, lint and type checks (not run, as before), and the frontend (no file under `frontend/` changed).

## Coordinator verification (after batch 3)

- Follow-up to W3-R20 (`9eb8ce9`), fail closed: the same queue Idempotency-Key with a different text now returns 422 (`ApplicationValidationError`) instead of silently dropping the second text. A retry of a request whose first call found no live turn and sent the text as a turn now returns that turn instead of queueing the text into it (before, the retry queued the same instruction into its own turn). A hook-refused row (text erased by W3-R22) or an erased turn prompt (W2-R6) cannot be compared and is returned as it is. Tests: `test_input_queue.py::test_a_reused_key_with_a_different_message_is_refused`, `::test_a_retry_of_a_sent_message_is_not_queued_into_its_own_turn` (both red before the fix).
- Full backend suite: `uv run pytest -q tests/unit` → **3102 passed, 53 skipped, 0 failed** (255.96 s).
- Frontend: W3 changed nothing under `frontend/`; the checks run after batch F stay valid. No visual change in W3, so no visual check was needed.
- Not verified here: PostgreSQL, Linux/POSIX paths, a live provider, a console-less supervisor (W3-R8 residual).
