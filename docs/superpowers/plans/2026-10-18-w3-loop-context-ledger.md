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
| W1-R9 decision (non-interactive ask) | `agentic/agent_tools.py` (`non_interactive_ask`); `agentic/session.py` (`_non_interactive_ask`) | `permissions/test_non_interactive_ask.py` |
| Five keys never read from a repository (`fallbackModel`, `effort`, `effortLevel`, `autoDreamEnabled`, `permissions.nonInteractiveAsk`) | `settings/loader.py` (5 added lines) | No dedicated W3 test for these five keys. `agentic/test_effort.py` covers the repository-effort case. |
| Public context telemetry keys | `agentic/models.py` (`_PUBLIC_CONTEXT_TELEMETRY_KEYS`) | No W3 test was added for this constant. The pre-existing `agentic/test_activity_contracts.py` refers to the public-context helper. |
| Schema: 4 tables, 2 columns, migration 0048 | `persistence/postgres/migrations/versions/0048_w3_loop_and_context.py`; `persistence/postgres/schema.py` | `persistence/test_w3_schema.py`, `persistence/test_postgres_schema.py` (tests ran on SQLite only) |

## Lifecycle events after W3

Status against the W2 table (`2026-10-18-w2-hooks-ledger.md`). W2: 21 of 33 events emitted. After W3: 25 emitted (the code calls `bus.emit` for them, counted by grep over `src/agentos`). Only the rows that changed are listed.

| Event | W2 | After W3 | Where |
|---|---|---|---|
| SessionStart | emitted; context and env file applied | emitted; `source` is `startup`, `resume` (first time this process serves an existing conversation) or `compact` (after a compaction) | `session.py` (`_session_start_source`); `runtime.py` (compact) |
| SessionEnd | not emitted | emitted at worker shutdown with `reason: "other"`; informational | `hooks/session_end.py` (`end_sessions`); `workers/publisher.py` |
| FileChanged | not emitted | emitted for files the turn read that changed outside it, payload `{file_path, event}`; informational plus a reminder to re-read | `agentic/runtime.py` (`_check_external_changes`); `agentic/agent_tools.py` |
| PreModelSwitch | not emitted | emitted before a fallback model is used; a block or deny keeps the current model | `agentic/runtime.py` (fallback switch) |
| PostModelSwitch | not emitted | emitted after a fallback switch; informational | `agentic/runtime.py` |
| PostToolUseFailure | emitted; context dropped by the bus | context applied: appended to the failed tool result | `agentic/lifecycle.py` (`_CONTEXT_EVENTS`); `agentic/agent_tools.py` (`invoke`) |
| SubagentStart | emitted; context not injected | context applied: appended to the subagent's task text | `agentic/session.py` (`_execute_subagent_run`) |
| TaskCreated | emitted after the change; blocking deferred | emitted before the change; a block leaves the task list unchanged. See the limitation on refused creates. | `agentic/agent_tools.py` (`_task_hook_block`) |
| TaskCompleted | emitted after the change; blocking deferred | emitted before the change; a block keeps the task open | `agentic/agent_tools.py` (`_task_hook_block`) |
| ConfigChange | emitted; blocking deferred | a block stops the turn (fail-closed, `HOOK_BLOCKED_CONFIG`) and the change is reported again next turn until a hook accepts it; managed policy changes are never blocked | `agentic/session.py` (`_emit_turn_start_events`); `settings/watcher.py` (`forget`); `agentic/runtime.py` |
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
16. W1-R9: an explicitly non-interactive Code turn denies an ordinary `ask` unless `permissions.nonInteractiveAsk` is `allow`. Chat mode and turns without the key are unchanged.
17. FileChanged watches files read during the current turn.

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

Batch F changed no existing test.

## Pending and known limitations

Known defects found in the batches and not fixed, most important first:

- Effort on Anthropic (batch E): `apply_effort` raises `max_tokens` to `budget + 4000` (up to 32,000) even when the turn's explicit output cap is smaller, so an explicit cap is overridden whenever effort is set. The raised value is not bounded by the remaining context either. Suggested fix: never raise above the explicit cap, and bound the raise by the remaining context.
- TaskCreated and TaskCompleted fire before the tool validates its input (batch F, probed). A refused `task_create` with a blank subject still emits TaskCreated, then PostToolUseFailure. A hook therefore sees creates the task list never receives. By reading the code, the same happens to `task_update` with `status: completed` for an unknown id (not probed). Suggested fix in the Task 22 notes above.
- Consolidation (batch E): `mark_run` writes the state before the background job starts, so a job interrupted by shutdown is not retried for 24 h. The state is a read-modify-write JSON file, so two worker processes can lose an increment, and the fixed temp name `auto-dream.tmp` can clash. Failures are swallowed. Best effort only; suggested fix: a unique temp name per write.
- Queue POST is not idempotent on the live-turn path (batch B): `enqueue_input` takes no `Idempotency-Key`, so a retried POST queues the text twice.
- `git_status_summary` runs synchronously in `build_runtime` for every Code turn in a trusted local folder (2 s timeout). In a large repository this adds up to 2 s before the turn starts (batch C).
- Subagent toolsets get no `memory_tracker` (batch D), so a subagent's read does not bring in subdirectory instruction files or path rules. Needs per-agent trackers (W4).
- `post_compact_enrichment` calls `stat()` on the plan file outside its `try` (batch C). If the plan is deleted between the glob and the stat, the compaction still happens but its enrichment is silently empty.
- The ignored-effort notice is recorded on every turn for an unsupported model (batch E): a conversation on Ollama with `effort` set gets one notice per turn.
- A compact boundary covers history before the compacting turn's user message, but the summary also contains that turn's earlier tool work; the next turn's replay may repeat part of it. Not tested (batch C).
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
- SessionEnd runs only on a graceful worker exit (`publisher.main`); a hard kill skips it (plan deviation 8). It is not called on conversation delete or archive, because Orin has none yet.
- Effort is not sent to Ollama (plan deviation 9).
- Consolidation uses the turn's model; Orin has no fast-model setting yet (plan deviation 10).
- ConfigChange hooks are judged with the new settings (plan deviation 13); Orin keeps no copy of the previous settings across processes.
- Linux was not run. All runs were on Windows.

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
