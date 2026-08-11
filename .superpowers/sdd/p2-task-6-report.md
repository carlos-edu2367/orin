# Plan 2 Task 6 — Tool-result aging

## Status

Implemented the Task 6 context-cost optimization in `AgenticTurnRuntime`.

## Implementation

- Added `AGED_TOOL_RESULT_CHARS = 400`.
- Added `_age_tool_results(messages, keep_recent)` as an in-place operation.
- Added OpenAI-role and Anthropic-block-shape detection through `_is_tool_result`.
- Added compression that preserves the first 400 characters and appends the required re-run pointer with the original character count.
- Invoked aging after appending each assistant tool-call message and its tool results, retaining the current batch via `keep_recent=len(results)`.
- Preserved Task 5 behavior: the pinned request is resolved once, tool-call/result units remain atomic for both provider shapes, surviving messages remain chronological, provider boundary contracts are unchanged, and tool results remain in the model-returned order.
- All changed Python modules already begin with `from __future__ import annotations`.

## Tests added

- `test_old_tool_results_are_compressed_but_recent_ones_are_kept`
- `test_anthropic_tool_results_are_compressed_in_their_block_shape`

The required focused red phase failed with the expected missing-helper `AttributeError`; the green phase passed both tests.

## Verification

- `uv run pytest tests/unit/agentic/test_context_window.py -k tool_results -v` — 2 passed, 7 deselected.
- `uv run pytest tests/unit/agentic -v` — 97 passed, 2 skipped.
- `uv run pytest tests/unit/agentic tests/unit/workers -v` — 101 passed, 2 skipped.
- `git diff --check` — no whitespace errors.
- `uv run pytest tests/unit -q` — blocked during collection by the pre-existing duplicate module basename collision between `tests/unit/provider_catalog/test_service.py` and `tests/unit/skills/test_service.py`.
- `uv run pytest tests/unit -q --import-mode=importlib` — also blocked by pre-existing test modules using directory-local imports (`test_manager_read`, `test_models_security`, and `conftest`).
- Ruff could not be run because the `ruff` executable is not installed in the project environment.

## Review concerns

The required scoped suites are green. The full-unit command remains unable to collect due to unrelated existing test-layout/import issues; no unrelated files were changed. The requested provider-stream review found no Task 6 diff and no boundary-contract change.

## Commit

`perf(agentic): compress tool results the model already read` (commit created after verification)

## Round 1 review fix

The reviewer found that an already-compressed result was selected again on
each later tool batch. `_compress` now recognizes its own terminal compression
marker and returns the existing payload unchanged, preserving both the aged
prefix and the original source length in the pointer.

Added regression coverage in
`tests/unit/agentic/test_context_window.py` proving repeated aging is
byte-for-byte stable and retains the original 2,000-character marker.

Verification for this fix:

- Focused aging tests — 3 passed, 7 deselected.
- `uv run pytest tests/unit/agentic -q` — 98 passed, 2 skipped.
- `uv run pytest tests/unit/agentic tests/unit/workers -q` — 102 passed, 2 skipped.
- `git diff --check` — no whitespace errors.
