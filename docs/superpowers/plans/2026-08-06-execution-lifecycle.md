# Execution Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the RFC 102 `Execution` lifecycle domain and the RFC 601 transactional/outbox boundary without adding Runtime or infrastructure adapters.

**Architecture:** Keep immutable domain values and the exact state graph in focused modules. `ExecutionControl` validates commands and creates one typed `TransactionRequest`; `InMemoryTransactionalPersistence` is the only adapter and exists solely for tests. State, minimal audit, and outbox are committed together, with idempotency and optimistic version checks at the persistence boundary.

**Tech Stack:** Python 3.13+, standard library (`dataclasses`, `enum`, `datetime`, `typing`), pytest as the test runner. No framework, database, queue, ORM, Redis, browser, or AI SDK.

## Global Constraints

- Backend Python 3.13+ only.
- Implement exactly `QUEUED`, `STARTING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`, `PAUSED`, `COMPLETED`, `FAILED`, and `CANCELLED`.
- Allow only RFC 102 transitions; terminal states have no outgoing transition.
- Require an explicit `expected_version` on every mutation; creation uses explicit `None`.
- Make commands idempotent by `idempotency_key`; incompatible reuse is rejected.
- Keep `ExecutionControl` and `TransactionalPersistence` technology-independent.
- Persist state change, minimal audit, and `OutboxEntry` in one logical transaction.
- Do not publish Events directly; only persist outbox entries.
- Do not add Runtime, API, SSE, workers, ARQ, Redis, PostgreSQL, ORM, migrations, Providers, Browser, Tools, Capabilities, or Event Bus.
- Sensitive commands carry `user_id`, `workspace_id` when applicable, `agent_id`, `execution_id`, `correlation_id`, and `purpose`.
- Do not store secrets, raw sensitive payloads, provider objects, or credentials.

### Task 1: Create the failing test suite

**Files:**
- Create: `tests/unit/execution/test_execution_control.py`
- Create: `tests/unit/execution/test_in_memory_persistence.py`
- Create: `tests/unit/execution/conftest.py`

**Interfaces:**
- Consumes the approved public API described in Tasks 2–4; tests intentionally import missing modules first.
- Produces the behavioral contract for all later tasks.

- [ ] **Step 1: Write fixtures and public test helpers**

Create a fixture that builds a valid `Execution`, `ExecutionCommandContext`, `ExecutionControl`, and in-memory persistence. Use opaque references instead of task text or sensitive payloads. Add a helper that builds commands with explicit `expected_version` and a fresh idempotency key.

- [ ] **Step 2: Write one failing test for each valid transition**

Cover every RFC 102 edge: `QUEUED->STARTING`, `QUEUED->CANCELLED`, `STARTING->RUNNING`, `STARTING->QUEUED`, `STARTING->FAILED`, `STARTING->CANCELLED`, all five `RUNNING` destinations, all five `WAITING_TOOL` destinations, all four `WAITING_USER` destinations, and all three `PAUSED` destinations. Assert resulting state, exactly one version increment, one audit entry, one outbox Event, and event type matching the target fact.

- [ ] **Step 3: Write failing tests for rejected behavior**

Cover an absent edge such as `QUEUED->RUNNING`, every terminal outgoing edge, `WAITING_TOOL->COMPLETED`, direct `WAITING_USER->RUNNING`, direct `PAUSED->RUNNING`, missing command context fields, missing expected version on an existing execution, and incompatible idempotency-key reuse.

- [ ] **Step 4: Write failing tests for concurrency, idempotency, and required outcomes**

Write tests for a stale version conflict, exact repeat returning `AlreadyApplied` without duplicate writes, cancellation, failure with sanitized categorical error, atomic rollback when the transaction is rejected, and `UNKNOWN` commit followed by `inspect_commit` showing the committed state and outbox together.

- [ ] **Step 5: Run the tests and verify the expected RED state**

Run: `python -m pytest tests/unit/execution -q`

Expected: collection fails because `agentos.execution` does not exist yet. If collection reports a test typo instead, fix only the test and rerun until the failure is caused by the missing implementation.

### Task 2: Implement domain values and events

**Files:**
- Create: `src/agentos/__init__.py`
- Create: `src/agentos/execution/__init__.py`
- Create: `src/agentos/execution/models.py`
- Create: `src/agentos/execution/events.py`

**Interfaces:**
- Produces immutable domain dataclasses for `Execution`, `Ownership`, `ExecutionLimits`, `ExecutionUsage`, `ExecutionResult`, `ExecutionFailure`, `CancellationReason`, and `TaskSnapshot`.
- Produces `ExecutionState`, `Version`, `ExecutionEventType`, `EventEnvelope`, `AuditRecord`, and `OutboxEntry` used by Tasks 3–4.

- [ ] **Step 1: Define opaque ID aliases and exact enums**

Use `NewType` aliases over `str` and `int` for IDs and `Version`. Define only the nine required `ExecutionState` values and categorical enums for cancellation and failure reasons.

- [ ] **Step 2: Define validated immutable values**

Use frozen, slotted dataclasses. Reject blank IDs, non-positive versions, negative limits/usage/iterations, naive timestamps, and result/failure values that violate state invariants. Keep task/result data reference-based and sanitized.

- [ ] **Step 3: Define the event envelope and outbox entry**

Require an execution ID and positive sequence for Execution events, UTC timestamps, ownership, correlation, source, classification, and a minimal mapping payload. `OutboxEntry` must carry source execution ID and expected source version.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/unit/execution -q`

Expected: tests progress past domain imports and fail only on missing ports/control/persistence behavior.

### Task 3: Implement ports, commands, and transaction results

**Files:**
- Create: `src/agentos/execution/ports.py`

**Interfaces:**
- Produces `ExecutionCommandContext`, command dataclasses, typed `CommandResult` variants, `TransactionRequest`, `TransactionReceipt`, `TransactionResult` variants, `ExecutionControl` Protocol, and `TransactionalPersistence` Protocol.

- [ ] **Step 1: Define the required command context**

Make all six sensitive scope fields explicit and validate nonblank `purpose`, IDs, and correlation. Include `command_id`, `idempotency_key`, `expected_version`, and `requested_at` on mutating command types.

- [ ] **Step 2: Define command types for lifecycle operations**

Add create, acquire, transition, cancel, pause, resume, provide-input-by-reference, and commit-changes commands. No command carries raw input, provider payload, credentials, or a technology-specific handle.

- [ ] **Step 3: Define atomic transaction contracts**

Make `TransactionRequest` include one `DomainChange`, one `AuditRecord`, and one `OutboxEntry`, plus context, transaction ID, expected version, operation fingerprint, and idempotency key. Define `Committed`, `Rejected`, `Conflicted`, and `Indeterminate` persistence outcomes.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/unit/execution -q`

Expected: imports succeed and tests fail on the unimplemented control and adapter.

### Task 4: Implement the control service and test-only adapter

**Files:**
- Create: `src/agentos/execution/control.py`
- Create: `src/agentos/execution/in_memory.py`

**Interfaces:**
- Consumes the models, events, and ports from Tasks 2–3.
- Produces `ExecutionControlService` and `InMemoryTransactionalPersistence` implementing the public protocols.

- [ ] **Step 1: Encode the RFC 102 transition table as data**

Store a mapping from `ExecutionState` to allowed destination states. Validate membership before any persistence call and reject terminal sources.

- [ ] **Step 2: Build one transaction for every accepted mutation**

Read the authorized execution, compare `expected_version`, calculate version plus one, update only the allowed fields, create the target-specific Event, create minimal audit data, and submit all three records through `transact`.

- [ ] **Step 3: Implement command idempotency and result mapping**

Use a semantic fingerprint derived from command type, target state, reason/reference fields, and expected version. Return the prior result for an exact repeat, reject incompatible reuse, map version conflicts to `Conflict`, and propagate `Indeterminate` without retrying.

- [ ] **Step 4: Implement atomic in-memory persistence**

Validate ownership and current version before changing anything. On a normal commit, update the execution and append audit/outbox together. On rejection, apply nothing. On configured indeterminate commit, apply the complete unit and return `TransactionIndeterminate`; `inspect_commit` returns the receipt for that transaction/idempotency pair.

- [ ] **Step 5: Run focused tests and refactor only after green**

Run: `python -m pytest tests/unit/execution -q`

Expected: all execution tests pass. Refactor duplication only while keeping the suite green.

### Task 5: Add minimal project configuration and final verification

**Files:**
- Create: `pyproject.toml`

**Interfaces:**
- Produces the Python 3.13+ project metadata and pytest configuration without runtime dependencies.

- [ ] **Step 1: Add minimal configuration**

Declare Python `>=3.13`, the `src` package layout, and pytest test paths. Do not add FastAPI, Playwright, Redis, SQLAlchemy, AI SDKs, or production persistence dependencies.

- [ ] **Step 2: Run the complete relevant suite**

Run: `python -m pytest tests/unit/execution -q`

Expected: exit code 0 with all execution tests passing.

- [ ] **Step 3: Inspect scope and forbidden imports**

Run: `rg -n "FastAPI|fastapi|Playwright|playwright|Redis|redis|SQLAlchemy|sqlalchemy|Runtime|ARQ|arq|Provider|Browser|Tool|Capability" src tests pyproject.toml`

Expected: no forbidden implementation imports or out-of-scope subsystems; references in test names or explanatory comments must not be implementation dependencies.

- [ ] **Step 4: Perform RFC self-review**

Check the final diff against RFCs 000, 050, 060, 102, 103, and 601: exact states and edges, terminal immutability, version/idempotency semantics, complete command context, event envelope, one atomic state/audit/outbox request, and explicit `UNKNOWN` handling.
