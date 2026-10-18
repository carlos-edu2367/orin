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
