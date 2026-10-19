# W4 — Tools: ledger

Execution record for `docs/superpowers/plans/2026-10-18-w4-tools.md`.

## Batch A — Registry, split, canonical resolution (Tasks 1–7)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `c76a9ed`:

| Task | Commit | Subject |
|---|---|---|
| 1 | `f91959c` | feat(w4): ToolSpec, ToolRegistry and provider-safe schemas |
| 2 | `afecb85` | refactor(w4): move AgentToolset's module helpers to tools/_shared |
| 3 | `9fb1523` | refactor(w4): split AgentToolset into per-family mixin modules |
| 4 | `399735a` | refactor(w4): tool definitions are ToolSpecs in their family modules |
| 5 | `c3c3ab8` | feat(w4): canonical names and aliases resolve in every mode |
| 6 | `643c2ce` | feat(w4): public tool schemas come from Pydantic Input models |
| 7 | this commit | docs(w4): ledger, batch A |

### Tests added (71)

- `tests/unit/agentic/tools/test_spec.py`: 6 (Task 1).
- `tests/unit/agentic/tools/test_split.py`: 28 (Task 2: 2; Task 3: 25 family checks and 1 line-count check).
- `tests/unit/agentic/tools/test_registry.py`: 3 (Task 4).
- `tests/unit/agentic/tools/test_canonical_resolution.py`: 6 (Task 5).
- `tests/unit/agentic/tools/test_public_contract.py`: 28 (Task 6: 24 parametrized and 4 others).

### Suite results

- Baseline before W4: 3102 passed, 53 skipped.
- Full backend suite, `uv run pytest -q tests/unit` (timeout 600000 ms), run after Task 6: **3173 passed, 53 skipped, 0 failed** (252 s). 3102 + 71 new = 3173.
- Batch-level runs: `tests/unit/agentic` 927 passed, 2 skipped; `tests/unit/permissions tests/unit/hooks` 304 passed, 1 skipped.
- Frontend: Batch A does not touch `frontend/`; the frontend checks were not run.

### Deliberate expectation changes (every one)

1. **Task 5 Step 6**, `tests/unit/agentic/tools/test_public_names_runtime.py`: two W0 tests that asserted public names are unknown with the flag off were replaced by the plan's `test_flag_off_public_names_resolve_too` and `test_flag_off_public_names_with_lifecycle_resolve_too`. Reason: canonical names and aliases resolve in every mode.
2. **Task 5 Step 6**, `tests/unit/test_orin_code_phase_a.py::test_coerce_tool_arguments_maps_common_aliases`: four assertions changed. The correction notes for `file_path` (after the write and after the read), `old_string` and `new_string` are now asserted absent, because canonical fields are mapped silently since W4. The `file_text` note, the `[Note: ` assertion and the direct `coerce_tool_arguments` checks are unchanged.
   - The run brief counted Task 5 Step 6 as "three" changes. The plan lists two tests and four assertions. All six are recorded here.
3. **Task 6 Step 7**, `tests/unit/agentic/tools/test_public_names.py::test_public_schemas_use_public_field_names`: the `read_file` field set gains `force_reload` (W0-R21: the field is now exposed).
4. **Task 4 Step 6** (a bound the plan sets, not an older expectation): `tests/unit/agentic/tools/test_split.py::test_agent_tools_keeps_only_the_pipeline` bound lowered from 2600 to 1700 lines. The test was added in this batch by Task 3. `agent_tools.py` is now 1576 lines.

### Test edits that change no assertion

- **Task 3 Step 4** (monkeypatch targets moved to the module that now looks the name up; applied with `$W4/retarget.py`, which refused nothing):
  - `tests/unit/agentic/test_agent_tools.py`: import of `bash_tools` and `task_tools` added; 4 targets moved (`MAX_COMMAND_OUTPUT_CHARS` and `_terminate_process_tree` to `bash_tools`; `_process_is_running` and `_terminate_pid` to `task_tools`).
  - `tests/unit/agentic/test_view_file_tool.py`: 1 target (`extract_text`).
  - `tests/unit/agentic/test_view_file_vision.py`: 4 targets (`normalize_image` three times, `render_pdf_pages` once).
  - `$W4/scan_patches.py` then printed nothing: no other test patches a moved name.

### Deviations from the plan

Plan deviations 1 to 5, with their status in Batch A:

1. **Keys stay snake_case**: applied. No key changed; `Read` and `read` resolve to `read_file`.
2. **Split by mixins**: applied. `agent_tools.py` went from 6642 to 1576 lines. Module-level helpers stay importable from `agent_tools` through the re-export of `tools/_shared`.
3. **Two schemas per tool**: applied. Chat mode still uses `legacy_schema`; the dump of all definitions was identical before and after Task 4 (`defs_before.json` and `defs_after.json`). The public schema comes from the Input models through `public_schema()`.
4. **Input models not enforced at execution**: applied. No call site validates arguments against the Input models; `ToolInput` (`extra="forbid"`) is schema only.
5. **Output models**: not applicable yet. Batch A assigns no `output_model`; the Output models arrive with Batches B to E.

Execution differences (none changes a behaviour the plan asks for):

- **a.** Task 2 Step 4 command: `uv run pytest -q tests/unit/agentic/tools/test_split.py tests/unit/agentic` collected only the two tests of `test_split.py`, because pytest does not also collect the directory when a file inside it is passed first. The directory was run on its own instead (864 passed, 2 skipped at that point). Recorded so the narrower run is not mistaken for coverage.
- **b.** Task 3 Step 4 was applied with `retarget.py`, which the plan names as making the same replacements, rather than by hand. The resulting edits match the plan's table.
- **c.** Task 6 Step 5, item 1: `_STRING`, `_obj` and `_PUBLIC_SCHEMAS` were removed from `public_names.py` as one line-range deletion, after checking the boundary lines (129 to 342 before the deletion). Only code was removed; no file was deleted.
- **d.** The full suite was run with `-p no:cacheprovider` (no pytest cache written). No effect on results.
- **e.** Line endings: the index stores LF and the working copy is CRLF (`core.autocrlf=true`). Git normalizes on commit; the staged diffs had no whitespace errors.
- **f.** Each test and module file written from a plan code block was compared with that block (scratch check outside the repository): the test files and `spec.py` contain the plan text verbatim, with the later planned edits applied on top.

### Behaviour notes found while checking (not in the plan)

- From Task 5 on, every call passes through `arguments_in` before `coerce_tool_arguments`, including chat-mode calls with Orin's own names. A scratch check (outside the repository) bound the 30 specs that are available for the default toolset and checked their legacy schemas, and the 35 others were skipped as the builder skips them. The only key that changes is `exit_plan_mode`'s `allowedPrompts`, which its legacy schema lists next to `allowed_prompts`. It is now renamed silently, as the canonical Claude Code name should be. The handler receives the same value as before, because `coerce_tool_arguments` already renamed it. The only visible change is that the `[Note: ...]` correction no longer appears for that key.

### Open after Batch A

- Batches B to F are not written yet; this run stopped after Task 7, as instructed.
- No Output models exist yet (deviation 5 applies from Batch B).
- `frontend/` is untouched in this batch.

## Batch B — Files (Tasks 8–12)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `38ce461` (the Batch B plan):

| Task | Commit | Subject |
|---|---|---|
| 8 | `6d2b737` | fix(w4): an absolute path inside the workspace is that file |
| 9 | `935b845` | feat(w4): Read covers images, notebooks and Office files with a typed output |
| 10 | `4635caf` | feat(w4): file tools report Claude Code outputs; hooks receive them |
| 11 | `6af25d0` | feat(w4): per-message file history with a safe rewind |
| 12 | this commit | docs(w4): ledger, batch B |

### Tests added (37)

- `tests/unit/agentic/test_workspace_absolute_paths.py`: 7 (Task 8).
- `tests/unit/agentic/tools/test_read_parity.py`: 6 (Task 9).
- `tests/unit/agentic/tools/test_file_outputs.py`: 8 (Task 10).
- `tests/unit/code_mode/test_file_history.py`: 12 (Task 11; `test_a_file_replaced_by_a_link_is_refused` skips on this host).
- `tests/unit/agentic/tools/test_file_history_wiring.py`: 4 (Task 11).

### Suite results

- Start of Batch B (end of Batch A): 3173 passed, 53 skipped.
- Full backend suite, `uv run pytest -q -p no:cacheprovider tests/unit` (timeout 600000 ms, run after Task 11): **3209 passed, 54 skipped, 0 failed** (235 s). 3173 + 37 = 3210 tests; the one extra skip is the symlink test above ("this account cannot create symlinks").
- Batch-level runs, each command separate: `test_workspace_absolute_paths.py` 7 passed; `tests/unit/agentic tests/unit/permissions` 1147 passed, 3 skipped (Task 8). `tests/unit/agentic/tools` with `test_agent_tools.py`, `test_view_file_tool.py`, `test_view_file_vision.py` 247 passed, 1 skipped; `tests/unit/agentic` 940 passed, 2 skipped (Task 9). `test_file_outputs.py` 8 passed; `tests/unit/agentic tests/unit/hooks tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_b.py` 1061 passed, 2 skipped (Task 10). `test_file_history.py` 11 passed, 1 skipped; `test_file_history_wiring.py` 4 passed; `tests/unit/agentic tests/unit/code_mode tests/unit/api tests/unit/workers tests/unit/test_orin_code_phase_b.py` 1178 passed, 3 skipped (Task 11).
- Frontend: Batch B does not touch `frontend/`; the frontend checks were not run.

### Deliberate expectation changes (every one)

1. **Task 9 Step 6**, `tests/unit/agentic/tools/test_public_contract.py` (the `read_file` entry) and `tests/unit/agentic/tools/test_public_names.py` (`public_argument_names("read_file")`): the field set gains `allow_large`, the new Read input. No other existing assertion changed.

### Deviations from the plan

Plan deviations 5–7, as corrected in this plan revision:

5. **Output models**: applied from Task 9 on. Each `tool_output` is in `ToolOutcome.data`, and `payload` never carries it (asserted in `test_read_parity.py` and `test_file_outputs.py`). PostToolUse receives it merged into `{status, summary, content}` (`agent_tools._tool_response`). The Output models are checked by the tests, not at runtime.
   - Correction (plan text): the plan's Architecture paragraph (line 7 of `2026-10-18-w4-tools.md`) still says `payload["tool_output"]`; the location is `ToolOutcome.data`, as deviation 5 says. The Batch A section above does not repeat the old wording, so it was not edited.
6. **Read's `tool_output` carries no image or PDF bytes**: applied. The image output has `type` and `originalSize`; the PDF output has page numbers and sizes.
7. **Per-message rewind, code only**: applied. The per-turn `FileCheckpointStore` and `POST /v1/conversations/{id}/turns/{turn}/rewind` are unchanged. The new `POST /v1/conversations/{id}/messages/{message_id}/rewind` accepts only `mode: "code"` (422 otherwise, tested), and `GET /v1/conversations/{id}/file-history` lists the recorded messages.

Changes made where the plan text differs:

- **a. Task 10 Step 6, notebook language**: the plan's inline chained `.get` is replaced by `_notebook_language(nb)` in `files/notebook.py`. Valid metadata gives the same result. Malformed metadata (for example a `language_info` that is not an object) used to raise after the notebook was already written, so a saved edit was reported as `TOOL_FAILED`; the helper falls back to `python`.
- **b. Task 11 Step 6, request model**: `RewindMessageRequest` subclasses `_RequestModel` (`extra="forbid"`) instead of the `BaseModel` shown in the plan, to match the other request models in `gateway.py`. Unknown body fields now get a 422.
- **c. Test commands**: a file and its own directory were never passed together. Task 8's new file ran alone, then `tests/unit/agentic tests/unit/permissions`; the Task 9 command passes the `tools` directory with sibling files only. Runs used `-p no:cacheprovider`, as in Batch A.

### Behaviour notes found while checking (not in the plan)

- `Edit` now refuses old and new text that are identical (Task 10). Inside a batch (`edits`), one no-op item fails the whole batch; the error names the item.
- PostToolUse for `Write`: `tool_response.content` is the written file body (the Task 10 test asserts it), so it replaces Orin's summary text under `content` for that hook.
- A toolset built without `user_message_id` records under its turn id (`_history_message_id` falls back to `turn_id`).
- Blobs under `<ORIN_DATA_DIR>/file-history/` have no retention: every distinct pre-edit content (up to 10 MB) is kept.
- The per-message rewind does not check whether a turn is running in that conversation, so a rewind during a turn can race with its writes. The per-turn endpoint has the same gap.
- On this host the link branch of `_restore` ("it is now a link") is not exercised by any run, because the symlink test skips. The junction test runs and covers the directory-replacement branch.
- `_read_notebook` reads the whole file. `_record_file_read` already reads the whole file for its hash on every read, so this adds no new exposure.

### Open after Batch B

- Batches C–F were not started (out of scope for this run).
- The per-message rewind has no UI yet (W7); the UI still uses the per-turn store.
- `frontend/` is untouched in this batch.

## Batch C — Search and shell (Tasks 13–17)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `334f05f` (the Batch B ledger):

| Task | Commit | Subject |
|---|---|---|
| 13 | `a773052` | feat(w4): Grep uses a provided ripgrep, falls back to Python, speaks Claude Code |
| 14 | `e874c4f` | feat(w4): Glob reports a Claude Code output |
| 15 | `18459fd` | feat(w4): Bash keeps its directory, saves long output, reports Claude Code's output |
| 16 | `7056d35` | feat(w4): PowerShell tool with conservative analysis in the permission engine |
| 17 | `2f1f132` | feat(w4): TaskOutput and TaskStop take any background task id |
| 18 | this commit | docs(w4): ledger, batch C |

### Tests added (57)

- `tests/unit/agentic/tools/test_grep_parity.py`: 10 (Task 13). Four of them are the `TestWithRipgrep` class, which skips when `rg` is not on `PATH`.
- `tests/unit/agentic/tools/test_glob_output.py`: 2 (Task 14).
- `tests/unit/agentic/tools/test_bash_parity.py`: 6 (Task 15).
- `tests/unit/permissions/test_powershell_analysis.py`: 29 (Task 16).
- `tests/unit/agentic/tools/test_powershell_tool.py`: 5 (Task 16). They run on this Windows host (not skipped).
- `tests/unit/agentic/tools/test_task_output_stop.py`: 5 (Task 17).

### Suite results

- Start of Batch C (end of Batch B): 3209 passed, 54 skipped.
- Full unit suite, `uv run pytest -q -p no:cacheprovider tests/unit` (timeout 600000 ms, run for Task 18 Step 1): **3262 passed, 58 skipped, 0 failed** (269 s). This matches the scratch validation of the plan. The 57 new tests account for the difference: 53 pass here and 4 skip (the `TestWithRipgrep` class, without `rg` on `PATH`).
- Grep parity, two runs:
  - Without ripgrep on `PATH`: 6 passed, 4 skipped (`TestWithRipgrep`).
  - With `export PATH="/c/Users/carlos/AppData/Local/OpenAI/Codex/bin/9a7ba4b9ea0c44a3:$PATH"` in the same call: 10 passed (ripgrep 14.1.1).
- Per-task runs, all green: `test_grep_parity.py` with `tests/unit/agentic` (Task 13); `test_glob_output.py` with `tests/unit/agentic` (Task 14); `test_bash_parity.py` with `tests/unit/agentic tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_b.py` (Task 15: 988 passed, 6 skipped); the two PowerShell files with `tests/unit/permissions tests/unit/agentic tests/unit/code_mode tests/unit/test_orin_code_phase_a.py tests/unit/test_orin_code_phase_b.py` (Task 16: 1280 passed, 8 skipped); `test_task_output_stop.py` with `tests/unit/agentic` (Task 17: 976 passed, 6 skipped).
- Integration and eval, run as part of the full backend check (not part of the plan's gate): `uv run pytest -q tests/integration tests/eval` gives 77 passed, 65 skipped, **1 failed**. The failure is `tests/integration/api/test_profile_isolation.py::test_every_route_is_classified`. It is pre-existing: the same test fails on `334f05f`, run from a `git archive` extract of that commit with the project venv. The unclassified routes are `file-history` and `messages/{message_id}/rewind` (added in Batch B, `6af25d0`, with no classification entry), and `queue`, `context`, `compact` and `workspace-trust`. Batch C changed no file under `src/agentos/api` or `tests/integration`. Not fixed here: how each route is classified for profile isolation is the owner's decision and outside Batch C.
- Frontend: Batch C does not touch `frontend/` (`git diff 334f05f HEAD -- frontend` is empty), so the frontend checks were not run.

### Deliberate expectation changes (every one)

1. **Task 13 Step 8**, `tests/unit/agentic/tools/test_public_contract.py` (the `search_files` field set): gains `-o` and `context`.
2. **Task 13 Step 8**, `tests/unit/agentic/tools/test_public_names.py::test_grep_flags_map_to_internal_names`: the expected dict gains `"line_numbers": True`. `-n` used to be dropped; it is now honoured.
3. **Task 17 Step 5**, `tests/unit/agentic/tools/test_public_contract.py` (the `read_process_output` field set): becomes `{"task_id", "block", "timeout"}`.
4. **Task 17 Step 5**, `tests/unit/agentic/tools/test_public_names.py::test_task_output_and_stop_take_string_ids`: the first assertion becomes `{"pid": 4242, "block": True}`. A public TaskOutput call now waits by default, as Claude Code's does. The other three assertions are unchanged.

No other existing expectation changed. Tasks 15 and 16 needed none, and no test pins the exact tool names of a default toolset (the full suite passed without any such change).

### Deviations from the plan

Plan deviations 8–10, applied as written in the plan:

- **8. Grep keeps Orin's defaults.** Case-insensitive unless `-i` is false, at most 200 entries. ripgrep is used only from `ORIN_RIPGREP_PATH` or the bundled copy, never from `PATH` and never from inside the workspace (tested).
- **9. An interrupted or timed-out command is a failed call.** No `tool_output` then, and a foreground timeout is not moved to the background.
- **10. TaskStop refuses a subagent id** (`agent-…`). TaskOutput reports a background command's `status` but not its exit code.

Execution differences (none changes a behaviour the plan asks for):

- **a.** Task 16 Step 5: `engine.py` no longer imports `analyze_command` from `.shell`. After the plan's edits the import was unused; a grep confirmed no other module imports it from `engine`.
- **b.** Task 16 Step 8.8: `EXECUTION_TOOLS` in `code_mode/permissions.py` also gains `"powershell"`. The plan's global rule is that every safety list naming `run_command` gains it, and the plan's step names only two sites. The constant is exported and not used inside the module; no test references it.
- **c.** Task 16 Step 8.6 kept: `phases.py` is unchanged and PowerShell is not in the Chat-mode phase lists (the 36-tool cap is pinned by `test_phase_controller.py`). Verified with `git diff 334f05f HEAD -- src/agentos/agentic/phases.py`, which is empty.
- **d.** Task 13 Steps 8 and Task 17 Step 5 were applied as exact-text edits. Task 15 used the existing `AgentToolset._persist_truncated_output` (a method of `AgentToolset`, reached from the mixin), so no helper was added.
- **e.** Line endings: as in Batches A and B (index LF, working copy CRLF; git normalizes on commit).
- **f.** Tests were run with `-p no:cacheprovider`, as in Batch B.

### Behaviour notes found while checking (not in the plan) — open

- **A Bash deny rule does not cover PowerShell (fail-open for deny and ask).** `rule_applies_to_tool` matches the tool name exactly, so a `Bash(...)` deny does not stop the same command through `powershell`. Checked with settings containing `deny: ["Bash(rm:*)", "Bash(Remove-Item:*)"]`: `run_command "rm x"` → deny; `powershell "rm x"` → allow, and `powershell "Remove-Item x"` → allow, both under `full_autonomy`. The allow side is guarded (`test_bash_rules_do_not_apply_to_powershell`). Suggested fix: for deny and ask matching only, let `run_command` rules also apply to `powershell`, matching the specifier through `analyze_powershell` (which already resolves aliases), with a regression test. Not changed here, because the plan does not define it.
- **PowerShell error output arrives as CLIXML.** With redirected stderr, Windows PowerShell 5.1 writes progress and error records as `#< CLIXML <Objs …>`, so `Write-Error 'boom'` reaches the model as escaped XML, and a first run also shows a module-preparation progress record. Suggested fix: add `$ProgressPreference = 'SilentlyContinue'` to the UTF-8 prefix and decode the `<S S="Error">` records in `stderr`. Not changed: the prefix is the plan's text.
- **A non-zero exit is a successful call.** Bash `exit 3` and PowerShell `exit 3` both return `status="succeeded"` with `exitCode: 3`. This is existing Bash behaviour (checked), not introduced by Batch C; deviation 9 covers only interrupted and timed-out commands.
- **PowerShell in the background** was checked ad hoc, outside the repository: it starts, returns a `backgroundTaskId`, and TaskOutput reads it.

### Open after Batch C

- Batches D–F were not started (out of scope for this run).
- The integration failure above (`test_every_route_is_classified`, from Batch B).
- The two open items above (PowerShell deny matching; PowerShell CLIXML output).
- `frontend/` is untouched in this batch.

## Batch D — Hardening, web and meta tools (Tasks 19–26)

Commits (on top of `edbacb0`): `17062f6` (Task 19 fix 1, route classification), `50a3ca8` (Task 19 fix 2, Bash deny/ask rules over PowerShell), `4c75fe8` (Task 19 fix 3, CLIXML), `0966bf2` (Task 20, WebFetch), `10697c3` (Task 21, WebSearch), `d5b0e07` (Task 22, ToolSearch), `ac38ca4` (Task 23, Skill), `87a7894` (Task 24, AskUserQuestion backend and frontend), `9adf0bb` (Task 25, ExitPlanMode output and periodic reminders). Task 26 (this section) was finished by the coordinator: the executor handed back before the final suites and the ledger.

### Suites

- Unit, `uv run pytest -q tests/unit -p no:cacheprovider`: **3292 passed, 58 skipped, 0 failed** (226 s). Before Batch D: 3262 passed, 58 skipped. Same as the plan's scratch validation.
- Integration, `uv run pytest -q tests/integration -p no:cacheprovider`: **78 passed, 65 skipped, 0 failed**. Before Batch D: 1 failed (`test_every_route_is_classified`), fixed by Task 19 fix 1.
- Frontend (run by the executor after Task 24; no frontend file changed afterwards): `npm --prefix frontend test` 599 passed (101 files), `lint` clean with `--max-warnings=0`, `build` ok.

### Closed from Batch C's open list

- Bash deny/ask rules now also govern PowerShell (Task 19 fix 2): `Bash(rm:*)` stops `rm x`, `del x`, `Remove-Item x` and `Get-Date; ri x` through PowerShell; allow rules still never cross over.
- PowerShell errors arrive as text (Task 19 fix 3): progress records are off and CLIXML error/warning/verbose/information strings are decoded.
- The profile-isolation route test passes (Task 19 fix 1): the nine W3/W4 conversation routes are `OWNED`, and profile B gets 404 on each.

### Deliberate expectation changes

1. Task 20: `test_public_contract.py` — `fetch_url` becomes `({"url", "prompt", "offset"}, ["url", "prompt"])`; `test_public_names.py::test_exit_plan_mode_skill_toolsearch_and_web_tools` — `arguments_in("fetch_url", {..., "prompt": "summary"})` now keeps `prompt`.
2. Task 21: same test — `arguments_in("web_search", {..., "allowed_domains": ["a.com"]})` now keeps `allowed_domains`.
3. Task 23: same test — `arguments_in("use_skill", {"skill": "pdf", "args": "x"})` now keeps `args`.
4. Task 24: `test_public_names.py::test_ask_user_question_converts_to_orin_questions` — the converted questions now carry `header` and options keep `description` separately (before: `"label": "SQLite — local"`, `header` lost); `arguments_out` returns `"header": "Database"` and the separate `description` (before: `"header": "q1"`, merged label).
5. Task 25: none.

### Deviations

Plan deviations 11–13, applied as written: WebFetch answers with the turn's own model (11); ToolSearch defers only MCP tools and keeps a default of 10 (12); AskUserQuestion previews are cut at 480 characters to stay under the 512-character public-event limit (13).

Execution notes from the executor:

- Task 19 fix 3: the `stderr_filter` change was first written before its test; it was reverted and redone test-first. Red was confirmed for every task.
- Task 24: the executor checked a static replica of the card in a browser; the coordinator checked the real component with the real CSS on the scratch copy (desktop 1280 dark and mobile 375 light; initial, chosen-with-preview and send-error states; no horizontal overflow). A final visual check on the branch is part of the W4 verification.
- Task 24 budget: a batch of 8 questions × 12 options that all carry a description and a preview exceeds the 384-entry public budget and is refused with the existing "split it" message; a plain 8 × 12 batch still fits (344).

### Open observations (for the W4 review)

- WebFetch's page cache keeps up to 64 pages for 15 minutes with no per-page size cap (the text is the bounded page text, but a cap on stored text would bound memory).
- ExitPlanMode's `filePath` joins the model's `plan_path` to the workspace root without `workspace.resolve`; it is only reported, never read.
- A Bash deny rule without a specifier (`Bash`) now also denies every PowerShell command (intended by Task 19 fix 2; noted for its reach).
- The plan's header carried a stale copy of the old batch table; fixed in the plan source for the next revision.

## Batch E — Scheduling and notifications (Tasks 27–31)

Commits (on top of `a147839`): `2a80d64` (Task 27, five-field cron and conversation-bound schedules), `9780883` (Task 28, CronCreate, CronDelete, CronList), `a3827e0` (Task 29, ScheduleWakeup), `5ebe972` (Task 30, Monitor), `3d5f516` (Task 31, PushNotification with the in-app toast). Task 32 is this section.

### Suites

- Unit, `uv run pytest -q tests/unit` (timeout 600000 ms): **3348 passed, 58 skipped, 0 failed** (227 s). Before Batch E: 3292 passed, 58 skipped. The 56 new backend tests are the whole difference.
- Integration, `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed**. Unchanged from Batch D.
- Frontend, after the last frontend change: `npm --prefix frontend test` **607 passed (102 files)**, 8 of them new; `npm --prefix frontend run lint` clean with `--max-warnings=0`; `npm --prefix frontend run build` ok (the existing chunk-size warning remains).
- New backend tests: `tests/unit/scheduler/test_cron.py` 21, `tests/unit/scheduler/test_scheduler_cron.py` 4, `tests/unit/agentic/tools/test_cron_tools.py` 9, `test_wakeup_tools.py` 8, `test_monitor_tool.py` 8, `test_push_notification.py` 6. New frontend test: `frontend/src/features/conversations/__tests__/PushNotificationToast.test.tsx` 8.
- Red before green, per task: Task 27 `test_cron.py` failed at collection (`agentos.scheduler.cron` missing); `test_scheduler_cron.py` was not run on its own in the red state, because the collection error stopped that run. Task 28: 8 failed, 1 passed (the no-scheduler case, which is meant to pass). Task 29: 7 failed, 1 passed. Task 30: collection error (`monitor` module missing). Task 31: backend 5 failed, 1 passed; the frontend file failed to resolve `../PushNotificationToast`.

### Deliberate expectation changes

None. No existing test was edited, skipped or removed in Batch E.

### Deviations

Plan deviations 14–17 are applied as written:

14. Cron jobs are `ScheduledChatService` rows: always durable, read in the operator's `timezone` setting (else `TZ`, else the system zone, else UTC); at most 20 per conversation; the prompt is at most 16000 characters; recurring jobs expire 7 days after creation; CronList and CronDelete see only the jobs this conversation's tools made.
15. Monitor runs commands only (no `ws`; `ws` is refused). Its stdout and stderr are one stream. Between turns, new output wakes the conversation through a one-shot schedule (`source="monitor"`) whose message only names the monitor. Wakes are at least 60 s apart and at most 10 per monitor; a persistent monitor stops after 24 hours; at most 5 per conversation; every monitor is stopped at process exit (`atexit`). Bash deny and ask rules govern Monitor; allow rules never cross over (`SHELL_TOOLS` plus the `rules.py` Bash-over-PowerShell branch, extended to `monitor`).
16. ScheduleWakeup: one pending wakeup per conversation (a new one cancels the old); at most 200 created in any 24 hours (`count_created_since`); `reason` and `prompt` are required unless `stop` is true; `noop` is only recorded.
17. PushNotification: `pushSent` is always false; `localSent` is true when the `notification.pushed` event was handed to the session's recorder; messages are cut at 200 characters; at most 3 per turn; the Notification hook fires with `notification_type: "push_notification"`.

Execution notes (not in the plan):

- Commit trailer: the batch brief asked for `Co-Authored-By: Claude Opus 5.5`, and that line is used on every Batch E commit. The environment's attribution reminder named Haiku 5.5; the brief is the owner's instruction and was followed.
- Task 30: the `definitions()` read-only filter in `agent_tools.py` (the `item.name not in {...}` set) was not edited, because the plan does not list it. Monitor is not `read_only`, so that filter already excludes it. The `_invoke_unobserved` read-only check (which the plan does edit) is covered by `test_read_only_subagents_cannot_monitor`.
- Task 31: the CSS block was appended after the file's existing two trailing blank lines, so there are two blank lines before it instead of one. The rules are the plan's, verbatim.
- Task 31 visual check: a static Playwright render of the toast markup, loaded with the real `theme.css` and `agentos.css`, in light and dark themes at 900 px and 390 px widths. Both layouts fit (no horizontal overflow). The toast was not seen inside the running ChatPage, so its placement against the composer on a phone is not verified live.
- Nothing else was deviated from; every commit used the plan's code blocks as written, and no plan line needed correcting.

### Behaviour notes found while checking (not in the plan) — open

- **Tool-made jobs appear on the Schedules page.** `ScheduledChatService.list(user_id)` returns every schedule of the user. CronList and CronDelete are restricted to the conversation's tool jobs, but the Schedules page is not: cron jobs, wakeups and monitor wakes all show there, and wakeups and monitor wakes are one-shot rows that clutter it. The plan does not address this. It needs a decision (filter by `rule.source`, or show them labelled).
- Monitor wakes and wakeups fire in the conversation through `PostgresChatStore.create`; whether a run that arrives while the conversation is mid-turn queues or is refused was not checked.

### Open after Batch E (carried from the plan's Task 32 list; not verified in this batch)

(a) A scheduled run (cron, wakeup or monitor wake) that fires while the conversation waits on an AskUserQuestion closes that question, as the Schedules-page runs already do. Not exercised.
(b) A monitor's command also appears in TaskOutput's process list and can get the "background process exited" notice, because it is started as a background Bash command. Not exercised.
(c) If a turn dies before its runtime closes, the reminder queue stays attached to the conversation's monitors until the next turn attaches; lines delivered to that queue are then readable only through TaskOutput. Not exercised.
(d) Monitors live in the backend process: a restart ends them (their commands are killed at exit). By design.
(e) Anything done differently from the plan: see the execution notes above.

## Batch F — Working directories and hardening (Tasks 33–39)

Commits (on top of `6515ba1`): `e4290aa` (Task 33, tool-made schedules never authorize git push; the Schedules page lists agent jobs), `0628945` (Task 34, additional working directories in the workspace, the tools and the permission engine), `2b74edd` (Task 35, `/add-dir` folders per conversation, their API and the DirectoryAdded hook), `7e3e3f2` (Task 36, a rewind never races a running turn and restores in added folders), `cf99c02` (Task 37, remove the legacy first-token rule matcher), `e829e3a` (Task 38, the Claude Code tool ledger). Task 39 is this section.

### Suites

- Unit, `uv run pytest -q tests/unit` (timeout 600000 ms): **3385 passed, 58 skipped, 0 failed** (266 s). Before Batch F: 3348 passed, 58 skipped. The 37 new backend tests are the whole difference: Task 33 (6), Task 34 (16), Task 35 (6), Task 36 (5), Task 38 (4).
- Integration, `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (64 s). Unchanged from Batch E.
- Frontend, after the last frontend change: `npm --prefix frontend test` **608 passed (103 files)**, one file and one test more than Batch E (`frontend/tests/unit/SchedulesPageAgentJobs.test.tsx`); `npm --prefix frontend run lint` clean with `--max-warnings=0`; `npm --prefix frontend run build` ok (the existing chunk-size warning remains).
- New backend tests: `tests/unit/code_mode/test_scheduled_push_authorization.py` 5 (three parametrized), `tests/unit/scheduler/test_scheduler_cron.py` +1, `tests/unit/agentic/test_additional_directories.py` 16, `tests/unit/conversations/test_conversation_directories.py` 6, `tests/unit/api/test_rewind_while_running.py` 5, `tests/unit/agentic/tools/test_tool_ledger.py` 4. New frontend test: `SchedulesPageAgentJobs.test.tsx` 1.
- Targeted runs, each green before its commit: `tests/unit/code_mode/test_scheduled_push_authorization.py tests/unit/scheduler` 39 passed; `tests/unit/agentic tests/unit/permissions tests/unit/code_mode` 1345 passed, 8 skipped; `tests/unit/conversations tests/unit/agentic/test_additional_directories.py tests/unit/persistence` 259 passed and `tests/integration/api/test_profile_isolation.py` 4 passed; `tests/unit/api/test_rewind_while_running.py` 5 passed, `tests/unit/test_orin_code_phase_b.py` 13 passed, `tests/unit/agentic/tools/test_file_history_wiring.py tests/unit/api` 116 passed; `tests/unit/test_orin_code_phase_b.py tests/unit/code_mode tests/unit/permissions` 308 passed, 2 skipped.
- Red before green: Task 33, the three push cases failed with `True is False`, the scheduler test with `KeyError: 'source'`, the frontend test on the page's load error. Task 34, `ModuleNotFoundError: agentos.agentic.directories`. Task 35, `ModuleNotFoundError: agentos.conversations.directories`. Task 36, the four busy cases failed with `200 == 409` and the message-rewind test did not restore the file. Task 37, no red step: the plan's Step 1 is the removal together with its test rewrite, so the new engine assertions were not seen failing first. Task 38, `ModuleNotFoundError: agentos.agentic.tools.ledger`.
- Visual check of Task 33 (the `pelo agente` badge, the `cron …` label, `Concluída`): a static Playwright page reproducing the schedule-row markup, loading the real `theme.css`, `index.css` and `agentos.css`. Light and dark at 900 px, dark at 390 px: no horizontal overflow, the badge sits inside the row and stays legible. This is not the live SchedulesPage with backend data.

### Deliberate expectation changes

- Task 33: none. One test was appended to `tests/unit/scheduler/test_scheduler_cron.py`; no existing assertion changed.
- Task 35: `tests/unit/persistence/test_postgres_schema.py` — the expected table set gains `conversation_directories`. `tests/unit/persistence/test_w3_schema.py` — both head-revision assertions now expect `0049_w4_tools` (replace-all, 2 occurrences). `tests/integration/api/test_profile_isolation.py` — the three `/directories` routes are added to `OWNED` (classification of new routes; no assertion changed).
- Task 36: `tests/unit/test_orin_code_phase_b.py::test_gateway_tool_permission_rewind_and_four_mode_settings_endpoints` — the stub's conversation state is now a class attribute. The test first asserts the 409 `turn_running` and that the file is untouched, then sets the state to `completed` and asserts the 200 rewind as before.
- Task 37: the same file's `test_permission_rules_parsing_and_matching` — the three `matches_permission_rule` assertions became four `PermissionEngine.decide` checks, including the compound `npm test && rm -rf build`, which must ask.
- Tool ledger counts: 31 included, 6 deferred, 12 excluded (`tests/unit/agentic/tools/test_tool_ledger.py`).

### Deviations from the plan

18. **`DirectoryAdded` fires at the start of the next turn**, once per folder added with `/add-dir` since the last turn (as planned). See open item (c).
19. **Unsafe additional directories are refused**, at most 20 per conversation; `/add-dir` is closed on a server (404) and has no composer command until W7 (as planned).

Execution deviations (not in the plan):

- Task 34: after the plan's edits 12–13, `root = self.workspace.root.resolve()` in `search/grep.py` had no use left (its only use was the replaced `relative_to(root)`). The dead assignment was removed.
- Task 37: the plan's "replace with an empty block" edits were applied as whole-line removals, including each line's newline, so no blank line is left in the import and export lists (`code_mode/permissions.py`, `code_mode/__init__.py`, the phase-B test import).

Execution notes:

- Commit trailer: the batch brief asked for `Co-Authored-By: Claude Opus 5.5`, and that line is used on every Batch F commit, as on Batch E. The environment's attribution reminder named Haiku 5.5; the brief is the owner's instruction and was followed.
- Python lint was not run: `ruff` is not installed in this environment and `pyproject.toml` has no ruff or flake8 config. The one dead variable was found by reading the diff.

### Behaviour notes and open items after Batch F

(a) **The rewind guard is check-then-act.** `_turn_in_flight` reads the conversation state and the restore runs afterwards. A turn that starts between the read and the restore is not blocked. Closing that gap needs the turn start and the rewind to share a lock, or a conditional state transition. Not done.

(b) **The per-turn rewind does not reach added folders.** `POST …/turns/{turn_id}/rewind` (`FileCheckpointStore`) restores only the workspace. Files a turn edited in a folder added with `/add-dir` can be restored only through the message rewind. The plan's deviation 19 describes the message rewind; the per-turn endpoint was left as it was.

(c) **A refused folder is never announced.** `ConversationDirectoryStore.take_unannounced` marks each new folder announced before the turn checks that it is usable. A folder that is refused in that turn (for example one that was deleted meanwhile) is never announced. Accepted for now.

(d) **A store is built per call.** `TurnSession._directory_store()` constructs a `ConversationDirectoryStore` each time it is called, and each construction runs `create(checkfirst=True)`. It is called a few times per turn. Correct but wasteful; not changed.

(e) **Closed from Batch E:** the Schedules page now shows tool-made jobs, labelled `pelo agente`, shows `cron <expression>` for cron jobs, and hides finished wakeups and monitor wakes (Task 33).

(f) Still open from Batch E, not touched in this batch: whether a monitor or schedule run that arrives mid-turn queues or is refused was not checked.

## Review fixes

This section covers both passes over `docs/superpowers/reviews/2026-10-18-w4-review.md`. Part 1 is the first pass (commits `fbf4a71`..`eae7cc2`). Part 2 is the second pass, one commit per finding, from `92a143d` to `e6861e2`, with the test-module rename `c0bc57f` between them.

### Part 1: first pass (R1–R6, R14, R17–R19)

- **fbf4a71 R1.** A closed host folder (`host_folders=False`) applies no `permissions.additionalDirectories`: the settings and the directory store are not read, and nothing is announced.
- **38ea3a6 R2.** Protected paths behind `-Name:value` parameters and wildcard paths ask a person (`requires_human`), in Bash and in PowerShell.
- **92ee97d R3.** Monitor and PowerShell get the Bash host guard and the auto-mode hard-deny list.
- **ce8101b R4.** Escaped and nested spellings cannot bypass a deny rule (PowerShell backtick, Bash backslash, `sh -c`, `powershell -Command`, `cmd /c`).
- **73e8c80 R5.** The Code-mode `git push` and `gh pr` guards decide from the shell analysis. The helper lives in `tools/shell/publish.py`, because `agent_tools.py` hit its 1700-line guard.
- **17a0be7 R6.** A Bash `cwd` inside an added folder is recorded through `workspace.key()` and restored through `workspace.resolve()`.
- **4611033 R14.** PushNotification counts per turn in the session, one counter for the main agent and its subagents, and cuts at 180 UTF-16 units. Expectation change: `test_push_notification` cut 200 → 180; the frontend parser limit is 180.
- **8d58ddd R17.** The Python Grep and Glob walks skip junctions. Helper `linked_or_outside` in `tools/_shared.py`. The ripgrep variant of the test is skipped when no `rg` is configured.
- **8f8fef2 R18.** ToolSearch indexes every MCP tool (`McpToolSearchEngine.listing()` in `src/agentos/mcp/search.py`). `select:` and `+term` no longer stop at 200.
- **eae7cc2 R19.** The directory store is built once per session, inside the `try`. An unavailable store is logged and the turn continues. Setup change: `test_conversation_directories` sets `session.host_folders = True`.

### Part 2: second pass (R7–R12, R15, R16, R20)

Tests are named per finding. Each finding was run red first (the failing test was seen to fail for the reason given) unless noted under "Test-first notes".

- **92a143d R7.** A due run whose conversation has a `waiting_user` or `running` turn is deferred: nothing is claimed, `next_fire_at` is kept, and no occurrence row is written. A scheduled `create` no longer closes a waiting turn. Tests: `tests/unit/scheduler/test_scheduler_review_fixes.py`.
  - Deviation: `queued` does not defer. Existing expectations rely on a scheduled run firing beside a queued first turn, and a queued turn holds no prompt. Deviation: a deferred run is retried at every poll (the schedule is only read and not advanced), not at most once a minute.
- **196180b R8.** The next run is the first one strictly after `max(logical_at, now)`. Hourly keeps its grid anchored to the logical time. Cron, daily and weekly are computed from the same base. A missed run coalesces into one. Tests: 3-hour gaps for `*/5` cron and for hourly. No expectation changed.
- **a03da9b R9.** A monitor keeps one pending reminder, rewritten in place with its newest lines, bounded by `MAX_EVENT_CHARS`, with the omitted count. Monitor reminders take at most half the queue (16) and 12,000 characters together; a refused line stays pending. `ReminderQueue.replace()` is new. The subagent toolset receives the turn's reminder queue (`session.py`). Tests: `tests/unit/agentic/tools/test_monitor_review_fixes.py`. No expectation changed.
- **dc5fcf8 R10.** The rewind is two-phase. Phase 1 checks every file (backup present and matching, directory identity, not too large to copy) and copies each current file into the undo snapshot; if any check fails nothing is written. Phase 2 applies. A blocked rewind returns `blocked: true`, `undo_id: null`, nothing applied, and each blocker in `skipped`. The message route answers `409 rewind_blocked` with `error.details.files` (`_error()` gained `details`). A Write or Edit over a file too large to keep a copy says "this file cannot be rewound". Tests: `tests/unit/code_mode/test_file_history_review_fixes.py`, `tests/unit/api/test_rewind_review_fixes.py`. No existing expectation changed: the old skip reasons are now reported as blockers.
  - Not done: NotebookEdit does not add the note (the finding names Write and Edit). An I/O error in phase 2 still skips that one file; there is no rollback.
- **30bfa22 R12.** A working directory that is a link, or that resolves to a path other than the one it was given, refuses its files with `its working directory was replaced after the snapshot`. A link between the directory and the file refuses that file. Tests: Windows junctions through `_winapi.CreateJunction`, skipped elsewhere. The link-inside-the-root case is refused by the directory-identity check first, so its test does not isolate the new chain check.
- **68b5fb6 R11, bb713b2 across processes.** `src/agentos/code_mode/workspace_lock.py` has two layers. Inside one process, per normalized folder, a count of running turns and an exclusive rewind under a Condition. Across processes (the API and the chat worker run as separate processes), marker files in `orin_paths().run / "workspace-locks"`: a turn writes `<hash>.turn.<pid>.<uuid>` and then checks for a live `<hash>.rewind`; a rewind writes `<hash>.rewind` with `O_EXCL` and then checks for live turn markers. A marker whose pid is not running is stale: it is ignored and removed when seen. Both rewind routes take the folder exclusively around the busy check and the rewind, so a turn of any chat on that folder, in any process, answers `409 turn_running`. The chat worker counts a turn for its whole run (`run` → `_run_claimed`) and waits while a rewind holds the folder. Tests: `tests/unit/code_mode/test_workspace_lock.py` (including five cross-process tests, one with a real child process), `tests/unit/api/test_rewind_review_fixes.py`. `tests/conftest.py` isolates `ORIN_RUN_DIR` for every test.
  - Deviation: sibling chats are found by folder key, not by listing conversations in the store (the store lists only non-project chats).
  - Limit: a pid reused by an unrelated process after a crash makes its stale marker look live until that process exits. The effect is a refused rewind or a waiting turn, never a lost write. On POSIX, `kill(pid, 0)` reports an unreaped zombie as alive.
- **3a97b4a R20.** The snapshot is taken after validation and immediately before the write (`_recorded_write`). A write that then fails drops the entry it added and keeps the entries the message already had. NotebookEdit snapshots at its write. A sharing violation is retried for up to one second. A manifest that exists but cannot be parsed is never overwritten: a snapshot goes to `<message>.recovered-<hash>.json`, which `messages()` and `rewind` merge. A write whose copy cannot be kept is refused with "nothing was changed". Tests: `tests/unit/code_mode/test_file_history_review_fixes.py`.
  - Deviation: the review's reproduction holds the manifest open for the whole edit. Such an edit is now refused, not recorded. `agent_tools.py` is at 1694 lines against the 1700-line guard.
- **016070d R15.** `_pinned_public_hop` (`tools/_shared.py`): the name is normalized with UTS 46 once, looked up once, and every answer must be global. `.localhost`, `.local`, `.internal`, `.lan` and single-label names are refused without a lookup. Literal addresses must be global, so 100.64/10 is refused. WebFetch connects to the checked address with `Host` and `sni_hostname` when it uses the production client. An injected transport keeps its URLs, and every hop is still validated. Each redirect hop is checked against the WebFetch deny rules. Tests: `tests/unit/agentic/tools/test_web_fetch_review_fixes.py`.
  - Not done: `_public_url` keeps its check-then-connect shape for MCP HTTP transport, OAuth netpolicy, web search endpoints and plugin sources. The same DNS gap stays open there. Redirect hops refuse only on a deny rule; an `ask` on a hop is not re-asked. `idna` is imported directly; it is installed as an `httpx` dependency and is not declared in `pyproject.toml`.
- **e6861e2 R16.** The page cache keeps at most 512 KB per page and 32 MB in total, evicting least recently used by bytes. The body is read in chunks, with a 60-second total deadline and a cancel check between chunks. Added beyond the finding: the body is capped at 8 MB. The page sits in a delimiter drawn for each call (`frame_page`), and the question names that tag. An unanswered page carries the untrusted-content notice. Expectation change: `test_the_session_answers_with_the_turn_model_and_bills_the_turn` asserted the fixed `<page>` tag; it now asserts the per-call tag named in the question. Tests: `tests/unit/agentic/tools/test_web_fetch_review_fixes.py`.

Test module rename, `c0bc57f`: `tests/unit/scheduler/test_w4_review_fixes.py` became `test_scheduler_review_fixes.py`. Its basename collided with `tests/unit/agentic/test_w4_review_fixes.py`, and pytest could not collect the tree.

Test-first notes. R11's in-process lock module was written before its unit tests, so those were not seen red; the route test (200 instead of 409) and the worker test (the turn ran at once) were seen red. The cross-process tests were written first and seen red: the refused rewind did not raise, the stale markers were kept, the waiting turn returned at once, and the child's turn was not seen. R10's API test was checked red against the pre-fix routes. R15's tests and R16's tests were red in the first run, and their failures matched the findings.

Verification at the end of this pass: `uv run pytest -q tests/unit` 3665 passed, 59 skipped; `uv run pytest -q tests/integration` 78 passed, 65 skipped; `npm --prefix frontend test` 609 passed; `npm --prefix frontend run lint` clean; `npm --prefix frontend run build` succeeds.

### Can defer

The review's "Can defer" table, with the timing the review gives. Where the review names no wave, the entry says so.

| Id | Severity | Timing from the review |
|---|---|---|
| W4-R13 | low | Media tools write files without snapshots. W7: the rewind UI should list "not rewindable" writers. |
| W4-R21 | low | ripgrep reads parent `.gitignore`/`.ignore` files (`--no-ignore-parent`). Do it with the next Grep change. |
| W4-R22 | low | Grep parity (CRLF, type mapping, binary detection, timed-out ripgrep). Correctness polish; mark a timed-out search `truncated=True` first. No wave given. |
| W4-R23 | low | Grep passes `type` and `glob` as separate argv words; hardening. No wave given. |
| W4-R24 | low | WebSearch host parsing and an unclamped `max_results` (clamp to 20). No wave given. |
| W4-R25 | low | Skill arguments can contain the wrapper's closing tag. W5 reworks skills on disk. |
| W4-R26 | low | `READ_ONLY_TOOLS` mixes read-only and never-prompts tools. W6 (permissions UI). |
| W4-R27 | low | A rewind drops a restored file's hidden attribute and alternate data streams. Cosmetic; no wave given. |
| W4-R28 | low | File-history storage path is derived from the message id. Fix with the next history change; no wave given. |
| W4-R29 | low | A NUL character in a path raises `ValueError`. Make `workspace.resolve` reject NUL with `WorkspaceError`; no wave given. |

Of the review's "known limits" rows, these are closed by the fixes above: the check-then-act rewind guard (R11), a scheduled run that closes a pending question (R7), and the WebFetch page cache without a cap (R16). The other rows are unchanged.

## Coordinator verification (W4 close)

- Each batch's plan was prototyped on a scratch worktree and re-applied to a clean tree from the plan text before execution (Batches E and F byte-identical to their prototypes). Each batch's commits were diffed against that application. The only differences were the ones the executors reported: a removed dead line in `grep.py` and blank lines.
- Two defects were found while verifying Batch E and fixed in Batch F Task 33:
  - a tool-made schedule's model-written message counted as the person's `git push` authorization;
  - the Schedules page parser threw on the `cron` recurrence.
- Review: the first reviewer and its web/search helper were both stopped part-way by an automated content filter. The coordinator re-ran every reproduction script on the branch and finished the review (`6028a4a`): 19 must-fix findings and 10 deferred.
- R11 was first fixed with an in-process lock. The desktop app runs the API and the chat worker as separate processes, so it was sent back and finished with marker files (`bb713b2`).
- Final suites at `2fd3c4e`:
  - `uv run pytest -q tests/unit` gave **3670 passed, 59 skipped, 0 failed**.
  - `uv run pytest -q tests/integration` gave **78 passed, 65 skipped**.
  - `npm --prefix frontend test` gave **609 passed** in 5 of 6 runs. One run had one failure that did not reproduce in 5 re-runs; the failing test could not be identified. This is recorded as a flaky frontend test to watch.
  - Lint and build were clean.
- Visual check on the branch code (worktree at `bc9305a`, temporary harness outside the repository, Playwright), in desktop dark and 375 px light. No horizontal overflow and no page errors in any state:
  - AskUserQuestion card: initial, preview and error states;
  - PushNotification toast: short and long messages, keyboard focus, dismissed;
  - Schedules page: list, empty and error states, with the agent-made badge.
- Residuals carried forward:
  - R15's single-resolution check covers WebFetch only. MCP HTTP, OAuth, web-search endpoints and plugin sources keep check-then-connect; their URLs are configured by the person, not chosen by the model.
  - The R11 markers rely on both processes sharing `ORIN_RUN_DIR`.
  - A failure during phase 2 of a rewind is not rolled back.
  - NotebookEdit has no "cannot be rewound" note.
  - R7 defers scheduled runs only while a turn waits or runs, not while one is queued.
  - The deferred items (R13, R21–R29) and the known limits listed in the review.
- Not verified here: PostgreSQL, Linux/POSIX paths, a live provider, the Electron runtime.
