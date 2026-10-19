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
