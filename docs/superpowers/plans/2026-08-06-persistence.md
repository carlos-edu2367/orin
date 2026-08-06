# RFC 601 Persistence Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the canonical RFC 601 transactional persistence port, its in-memory reference adapter, SQLAlchemy 2/Alembic PostgreSQL adapter, and explicit Execution compatibility bridge.

**Architecture:** Public persistence contracts live in `src/agentos/persistence/` and contain only frozen Python values, Protocols and sanitized result types. A generic in-memory adapter proves the contract; the PostgreSQL package owns SQLAlchemy, internal mappings, error translation and migrations. `execution_compat.py` translates the existing Execution port without changing Runtime or ExecutionControl.

**Tech Stack:** Python 3.13 target (current interpreter 3.12.10), frozen dataclasses, `Protocol`, `StrEnum`, pytest, SQLAlchemy 2, Alembic, SQLite in-memory contract harness, optional PostgreSQL via `AGENTOS_TEST_POSTGRES_DSN`.

## Global Constraints

- PostgreSQL is the sole durable transactional authority; Redis and brokers are out of scope.
- `TransactionalPersistence` exposes exactly `transact`, `read`, `scan` and `inspect_commit`.
- SQLAlchemy and Alembic appear only in `src/agentos/persistence/postgres/` and its migrations.
- `ExecutionControl` remains the only mutating Runtime-facing Execution façade.
- State, minimal audit, idempotency and outbox are one commit unit.
- `COMMITTED` is returned only after durable commit; commit acknowledgement loss returns `UNKNOWN`.
- Same idempotency scope plus same fingerprint replays the receipt; divergent fingerprint is an explicit conflict.
- Reads and scans apply ownership and classification filters before materializing results.
- Cursors are opaque, bounded and bound to query/context/classification/store revision.
- Migrations are never run on import, Runtime startup or domain calls.
- No provider, context, event bus, agent, Redis, worker, scheduler, API or artifact implementation is added.
- Preserve pre-existing user changes in the working tree; commit only files intentionally added by this plan.

---

### Task 1: Add canonical public contracts and dependency declarations

**Files:**
- Create: `src/agentos/persistence/__init__.py`
- Create: `src/agentos/persistence/models.py`
- Create: `src/agentos/persistence/ports.py`
- Create: `src/agentos/persistence/security.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/persistence/test_contracts.py`

**Interfaces:**
- Produces `PersistenceOperationContext`, `TransactionOptions`, `RecordReference`, `ExpectedVersion`, `RecordChange`, `AuditChange`, `OutboxChange`, `TransactionRequest`, `TransactionReceipt`, `TransactionResult`, `AuthorizedRead`, `AuthorizedScan`, `AuthorizedRecord`, `AuthorizedRecordPage`, `NotFound`, and `TransactionalPersistence`.
- `TransactionalPersistence.transact(request)`, `.read(query)`, `.scan(query)`, and `.inspect_commit(query)` are the only canonical methods.

- [ ] **Step 1: Write RED contract tests.** Add tests that construct a complete context, reject blank/overlong required fields, reject invalid transaction options and page limits, ensure record/reference `repr` values are opaque, and ensure payload freeze rejects secrets, oversized text and unsupported objects.

- [ ] **Step 2: Run the focused tests.**

  Run: `python -m pytest tests/unit/persistence/test_contracts.py -q`

  Expected: FAIL because `agentos.persistence` does not exist.

- [ ] **Step 3: Implement the public value types.** Use frozen, slotted dataclasses and bounded immutable mappings. Reuse `agentos.events.models.DataClassification` rather than defining a second classification enum. Define `PersistenceErrorCode` and `Retryability` with sanitized messages and no exception `repr` containing values. Make `AuthorizedRead`/`AuthorizedScan` reject a page limit above 100 and `TransactionOptions` reject non-positive timeouts.

- [ ] **Step 4: Add only justified dependencies.** Update `[project].dependencies` to include `SQLAlchemy>=2.0,<3` and `alembic>=1.14,<2`, without adding Redis, broker, HTTP or provider packages.

- [ ] **Step 5: Run the focused tests again.**

  Run: `python -m pytest tests/unit/persistence/test_contracts.py -q`

  Expected: PASS with no warnings.

- [ ] **Step 6: Commit the public contract slice.**

  Run: `git add src/agentos/persistence pyproject.toml tests/unit/persistence/test_contracts.py; git commit -m "feat: add canonical persistence contracts"`

### Task 2: Implement the canonical in-memory adapter

**Files:**
- Create: `src/agentos/persistence/in_memory.py`
- Test: `tests/unit/persistence/test_in_memory_transactions.py`
- Test: `tests/unit/persistence/test_in_memory_authorization.py`

**Interfaces:**
- Consumes Task 1 public types.
- Produces `InMemoryTransactionalPersistence` implementing the canonical Protocol, with test-only `seed`, `reject_next`, `not_committed_next`, `indeterminate_next`, `audit_records`, and `confirmed_outbox` inspection helpers.

- [ ] **Step 1: Write RED atomicity/idempotency tests.** Cover same fingerprint replay, divergent fingerprint rejection, optimistic version conflict, duplicate event ID rejection, state+audit+outbox visibility after commit, and no partial mutation after rejection.

- [ ] **Step 2: Run the focused tests.**

  Run: `python -m pytest tests/unit/persistence/test_in_memory_transactions.py -q`

  Expected: FAIL because the adapter is not implemented.

- [ ] **Step 3: Implement the minimal transaction engine.** Validate every change against request context and classification before changing dictionaries. Store idempotency by `(user_id, workspace_id, agent_id, execution_id, purpose, idempotency_key)`. Apply all records, audit entries, idempotency receipt and outbox entries only after every check passes. Use a monotonically increasing store revision and reject duplicate outbox event IDs.

- [ ] **Step 4: Add RED commit-state tests.** Verify `not_committed_next` produces `NOT_COMMITTED` with no visible effect, `indeterminate_next` applies the unit but returns `TransactionIndeterminate`, and `inspect_commit` returns the durable committed receipt without replaying the request.

- [ ] **Step 5: Implement commit inspection.** Keep an internal receipt index keyed by authorized context and transaction ID. Return `NOT_COMMITTED` for a known rejected transaction, `COMMITTED` for an applied unknown commit, and a sanitized lookup failure for an unrelated context/key.

- [ ] **Step 6: Run the transaction tests.**

  Run: `python -m pytest tests/unit/persistence/test_in_memory_transactions.py -q`

  Expected: PASS.

- [ ] **Step 7: Write and verify RED authorization tests.** Assert that cross-user/workspace/agent/execution/purpose reads return `NotFound`, records above the classification ceiling are hidden, and a mismatched cursor cannot be reused.

- [ ] **Step 8: Implement bounded reads/scans.** Apply authorization and classification before returning `AuthorizedRecord`. Bind cursor tokens to a SHA-256 query fingerprint, context, classification and store revision; cap page size at 100 and return empty pages for unauthorized scans.

- [ ] **Step 9: Run all in-memory tests.**

  Run: `python -m pytest tests/unit/persistence/test_in_memory_transactions.py tests/unit/persistence/test_in_memory_authorization.py -q`

  Expected: PASS.

- [ ] **Step 10: Commit the in-memory slice.**

  Run: `git add src/agentos/persistence/in_memory.py tests/unit/persistence/test_in_memory_transactions.py tests/unit/persistence/test_in_memory_authorization.py; git commit -m "feat: add in-memory persistence adapter"`

### Task 3: Add SQLAlchemy internal schema and explicit Alembic migration

**Files:**
- Create: `src/agentos/persistence/postgres/__init__.py`
- Create: `src/agentos/persistence/postgres/schema.py`
- Create: `src/agentos/persistence/postgres/errors.py`
- Create: `src/agentos/persistence/postgres/migrate.py`
- Create: `src/agentos/persistence/postgres/migrations/env.py`
- Create: `src/agentos/persistence/postgres/migrations/script.py.mako`
- Create: `src/agentos/persistence/postgres/migrations/versions/0001_initial_persistence.py`
- Test: `tests/unit/persistence/test_postgres_schema.py`
- Test: `tests/unit/persistence/test_migrations.py`

**Interfaces:**
- Produces internal `metadata`, table definitions, `create_engine_for_tests`, normalized database error mapping, and explicit `upgrade(dsn, revision="head")`.
- No symbol from this task is imported by Runtime, Events, Context, Providers or Agents.

- [ ] **Step 1: Write RED schema tests.** Assert that metadata declares records, audit, outbox and idempotency tables, unique event/idempotency constraints, ownership columns, version columns and classification columns.

- [ ] **Step 2: Run the schema tests.**

  Run: `python -m pytest tests/unit/persistence/test_postgres_schema.py -q`

  Expected: FAIL because the PostgreSQL package does not exist.

- [ ] **Step 3: Implement internal SQLAlchemy 2 mappings.** Use `DeclarativeBase` or SQLAlchemy Core tables only in this package. Store bounded JSON-safe snapshots in JSON columns, decimal cost as text/decimal-safe values, timezone-aware timestamps, ownership fields, `record_version`, and immutable event IDs. Add indexes for ownership, record type/version and outbox pending reads.

- [ ] **Step 4: Write RED migration tests.** Construct the adapter/schema without calling Alembic and assert the database has no persistence tables until `upgrade()` is explicitly invoked; then run `upgrade()` against `sqlite:///:memory:` and assert the tables exist.

- [ ] **Step 5: Implement the explicit migration entry point.** Configure Alembic programmatically with the package migration directory. `upgrade()` is the only function that invokes Alembic; imports and adapter construction do not call it.

- [ ] **Step 6: Run schema and migration tests.**

  Run: `python -m pytest tests/unit/persistence/test_postgres_schema.py tests/unit/persistence/test_migrations.py -q`

  Expected: PASS.

- [ ] **Step 7: Commit the schema slice.**

  Run: `git add src/agentos/persistence/postgres tests/unit/persistence/test_postgres_schema.py tests/unit/persistence/test_migrations.py; git commit -m "feat: add persistence schema and migrations"`

### Task 4: Implement the PostgreSQL/SQLAlchemy adapter

**Files:**
- Create: `src/agentos/persistence/postgres/adapter.py`
- Modify: `src/agentos/persistence/postgres/__init__.py`
- Test: `tests/unit/persistence/test_postgres_adapter.py`
- Test: `tests/integration/persistence/test_postgres_optional.py`

**Interfaces:**
- Implements the canonical `TransactionalPersistence` with `PostgresTransactionalPersistence(engine_or_dsn, session_factory=None, commit_hook=None)` and no automatic migration.
- Uses Task 3 schema and maps SQLAlchemy/DBAPI errors to `PersistenceErrorCode` and retryability without exposing SQL or driver messages.

- [ ] **Step 1: Write RED SQLite adapter tests.** Cover explicit schema setup, create/update/read/scan, same-key replay, divergent fingerprint, expected-version conflict, atomic audit/outbox insertion, rollback, duplicate event constraint and bounded authorization.

- [ ] **Step 2: Run the focused adapter tests.**

  Run: `python -m pytest tests/unit/persistence/test_postgres_adapter.py -q`

  Expected: FAIL because the adapter is not implemented.

- [ ] **Step 3: Implement engine/session composition.** Accept a DSN or prebuilt engine from composition. Configure pool and timeout options only through constructor arguments. Use a session transaction per `transact`; apply supported isolation/read-only/timeout options without assuming SQLite supports PostgreSQL locking.

- [ ] **Step 4: Implement transactional writes.** Within one session transaction, lock/read expected records, validate ownership/classification/fingerprints, update record versions, insert audit/idempotency/outbox rows, and commit. Roll back on every rejection or exception. Invoke the injected `commit_hook` only around commit so tests can simulate `NOT_COMMITTED` and lost acknowledgement without changing domain code.

- [ ] **Step 5: Implement read, scan and inspect.** Use server-side ownership/classification predicates, bounded filters and keyset/offset state represented only by the opaque cursor. `inspect_commit` reads the idempotency receipt under the same context scope and never replays the transaction.

- [ ] **Step 6: Implement normalized error translation.** Map integrity conflicts to explicit rejection/conflict codes, deadlocks and serialization failures to retryable codes, statement/transaction timeouts to retryable timeout codes, and connection loss during commit to `TransactionIndeterminate`. Public messages contain only stable codes and opaque transaction IDs.

- [ ] **Step 7: Run unit adapter tests.**

  Run: `python -m pytest tests/unit/persistence/test_postgres_adapter.py -q`

  Expected: PASS.

- [ ] **Step 8: Add optional PostgreSQL tests.** Skip unless `AGENTOS_TEST_POSTGRES_DSN` is set. When configured, explicitly run the migration, then cover concurrent expected-version conflict, transaction isolation/locking and database-specific deadlock/timeout normalization.

- [ ] **Step 9: Run optional tests with the environment rule.**

  Run: `python -m pytest tests/integration/persistence/test_postgres_optional.py -q`

  Expected: PASS when DSN is configured; otherwise explicit skips and no automatic service creation.

- [ ] **Step 10: Commit the SQL adapter slice.**

  Run: `git add src/agentos/persistence/postgres/adapter.py src/agentos/persistence/postgres/__init__.py tests/unit/persistence/test_postgres_adapter.py tests/integration/persistence/test_postgres_optional.py; git commit -m "feat: implement SQLAlchemy persistence adapter"`

### Task 5: Add explicit Execution compatibility bridge

**Files:**
- Create: `src/agentos/persistence/execution_compat.py`
- Modify: `src/agentos/persistence/__init__.py`
- Test: `tests/unit/persistence/test_execution_compat.py`
- Test: `tests/unit/integration/test_kernel_boundaries.py`

**Interfaces:**
- Produces `ExecutionTransactionalPersistenceAdapter` implementing `agentos.execution.ports.TransactionalPersistence` over any canonical persistence adapter.
- Preserves old `ExecutionControlService` result types and converts legacy Execution event envelopes through `agentos.events.compat` explicitly.

- [ ] **Step 1: Write RED compatibility tests.** Seed an `Execution` through the canonical in-memory adapter, load it through the old Execution port, perform a transition through `ExecutionControlService`, and assert that the canonical record, audit and canonical outbox all commit together. Add an indeterminate commit test requiring `inspect_commit` before retry.

- [ ] **Step 2: Run the focused compatibility tests.**

  Run: `python -m pytest tests/unit/persistence/test_execution_compat.py -q`

  Expected: FAIL because the bridge is not implemented.

- [ ] **Step 3: Implement bounded Execution serialization.** Encode/decode the `Execution` aggregate using only JSON-safe strings, numbers, enums, references and ISO timestamps. Keep the serializer in the compatibility module; do not add persistence imports to `execution.models` or `execution.control`.

- [ ] **Step 4: Implement request/result translation.** Map `TransactionRequest` to canonical record/audit/outbox changes, map canonical receipts/results back to the old result algebra, and scope lookup/inspection with every context field. Use `to_canonical_event(..., agent_id=...)` and `from_execution_event(...)` only at this bridge.

- [ ] **Step 5: Run compatibility and boundary tests.**

  Run: `python -m pytest tests/unit/persistence/test_execution_compat.py tests/unit/integration/test_kernel_boundaries.py -q`

  Expected: PASS; SQLAlchemy/Alembic strings appear only in the PostgreSQL package/migrations.

- [ ] **Step 6: Commit the bridge slice.**

  Run: `git add src/agentos/persistence/execution_compat.py src/agentos/persistence/__init__.py tests/unit/persistence/test_execution_compat.py tests/unit/integration/test_kernel_boundaries.py; git commit -m "feat: bridge execution persistence contract"`

### Task 6: Complete requirement coverage and final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-persistence-design.md`
- Modify: `docs/superpowers/plans/2026-08-06-persistence.md`
- Create or modify: `tests/unit/persistence/test_security_regressions.py`
- Create or modify: `tests/unit/persistence/test_requirement_matrix.py`

**Interfaces:**
- Consumes all previous public contracts and adapters.
- Produces fresh evidence for the RFC/ADR acceptance criteria and an honest limitation record for unavailable PostgreSQL integration.

- [ ] **Step 1: Write RED security regression tests.** Prove that exception strings, `repr`, audit records and persistence errors omit SQL, passwords, credentials, secrets, prompt/raw payload fields and proprietary content.

- [ ] **Step 2: Implement only the minimum sanitization fixes exposed by those tests.** Do not add logging of record payloads or schema details.

- [ ] **Step 3: Run the complete mandatory verification.**

  Run: `python -m pytest -q`

  Expected: all existing and new unit tests pass; optional PostgreSQL tests are skipped only when the DSN is absent.

  Run: `python -m compileall -q src tests`

  Expected: exit code 0.

  Run: `rg -n "SQLAlchemy|sqlalchemy|Alembic|alembic" src/agentos --glob '!persistence/postgres/**'`

  Expected: no matches.

  Run: `git diff --check`

  Expected: exit code 0.

  Run: `git status --short --branch`

  Expected: report only intentional persistence files plus pre-existing user changes.

- [ ] **Step 4: Audit each requirement against RFC 050, 060, 101–104, 201, 501, 502, 601 and ADRs 002, 009, 012.** Record covered behavior, evidence command/test, and explicit limitation for backup/restore/replication/multi-region/DR and absent PostgreSQL DSN.

- [ ] **Step 5: Commit the final evidence update.**

  Run: `git add docs/superpowers/specs/2026-08-06-persistence-design.md docs/superpowers/plans/2026-08-06-persistence.md tests/unit/persistence; git commit -m "docs: record persistence verification"`

## Self-review

- Scope is one subsystem: the RFC 601 persistence boundary plus its required adapters and compatibility bridge.
- All public names used in later tasks are defined in Task 1.
- Every task has a RED test, focused verification, implementation, and commit checkpoint.
- PostgreSQL concurrency claims are separated from SQLite harness claims and guarded by the DSN rule.
- No task creates Redis, a broker, worker/scheduler infrastructure, or a domain outside RFC 601.
