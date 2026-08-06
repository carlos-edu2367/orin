# AgentOS RFC 601 Persistence Boundary — Design Specification

**Status:** Approved for implementation
**Date:** 2026-08-06
**Scope:** Durable transactional persistence boundary, PostgreSQL adapter, migrations, and explicit Execution compatibility adapter.

## Goal

Replace the reference-only persistence boundary with a technology-independent public port and a SQLAlchemy 2/Alembic PostgreSQL adapter without changing Runtime, Execution rules, Events, Context, Agents, Providers, or Model Catalog contracts.

PostgreSQL is the durable authority for state, ownership, versions, audit records, idempotency records, and outbox entries. Redis, brokers, workers, schedulers, Artifact Storage, API, and distributed commit+publish remain outside this implementation.

## Decisions

The implementation uses the approved “canonical port plus explicit compatibility adapter” approach:

1. `src/agentos/persistence/` owns stable public dataclasses, enums, Protocols, sanitized failures, bounded authorization queries, and the canonical transaction result algebra.
2. `src/agentos/persistence/in_memory.py` implements the canonical port as a deterministic test adapter.
3. `src/agentos/persistence/postgres/` is the only technology package. SQLAlchemy, Alembic, engine/session details, physical schema and database exception translation remain inside it.
4. `src/agentos/persistence/execution_compat.py` adapts the canonical port to the existing `agentos.execution.ports.TransactionalPersistence` contract. The existing ExecutionControl façade remains unchanged and remains the only mutating Runtime-facing façade.
5. The existing `agentos.execution.in_memory.InMemoryTransactionalPersistence` remains available for the current Kernel tests. It is not silently redefined as the canonical port; the new bridge makes compatibility explicit.

## Public contract

The public port has exactly four operations:

```text
TransactionalPersistence.transact(request) -> TransactionResult
TransactionalPersistence.read(query) -> AuthorizedRecord | NotFound
TransactionalPersistence.scan(query) -> AuthorizedRecordPage
TransactionalPersistence.inspect_commit(query) -> TransactionReceipt
```

### Operation context

Every operation carries `PersistenceOperationContext` with the six ownership/correlation fields required by RFC 601 plus the actor:

```text
PersistenceOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}
```

All text is non-blank and bounded. `workspace_id` may be null only for explicitly user-scoped work. The adapter never infers missing ownership from a record ID, credential, cursor, event or database session.

### Transaction types

`TransactionOptions` expresses consistency, isolation, timeout and `read_only`. `TransactionRequest` contains a transaction ID, complete context, options, an idempotency key, a caller-supplied operation fingerprint, expected versions, JSON-safe immutable record changes, minimal audit entries and canonical outbox entries.

Record references, version references and outbox references are opaque value objects. Public values never expose SQL, ORM instances, sessions, connections, table names, credentials or physical paths.

The result algebra is:

```text
TransactionCommitted { receipt, records, already_applied }
TransactionRejected { code, retryability }
TransactionConflicted { conflicts }
TransactionIndeterminate { transaction_id }
```

`TransactionReceipt.commit_state` is explicitly one of `COMMITTED`, `NOT_COMMITTED` or `UNKNOWN`. A committed result is returned only after durable commit. A connection failure during commit is `UNKNOWN`; callers must call `inspect_commit` before retrying.

### Authorized reads and scans

`AuthorizedRead` and `AuthorizedScan` carry context, record type/reference filters, a classification ceiling, bounded filter maps and an opaque page request. Unauthorized, cross-user, cross-workspace, cross-agent, cross-execution, wrong-purpose and above-ceiling records resolve as `NotFound` or an empty page; they never reveal existence.

Page cursors are opaque and bound to the complete query fingerprint, operation context, filters, classification ceiling and store revision. Limits have a public maximum. A cursor from another query, context, classification or store revision is rejected with a sanitized public error.

## Atomicity and idempotency

One `transact` call is the only atomic write boundary. It validates the complete request before mutation, then confirms all record changes, minimal audit entries, idempotency record and outbox entries in the same database transaction.

The idempotency scope includes ownership, execution, operation/purpose and fingerprint. A repeated key with the same fingerprint returns the original receipt and effect. A repeated key with a different fingerprint returns an explicit idempotency conflict. Outbox event IDs are unique, and a retry cannot create the same event twice.

Expected versions are checked under the transaction’s write lock. A mismatch returns `TransactionConflicted`; last-write-wins is never implicit. A rejected request or rollback leaves no partial record, audit, idempotency or outbox row visible.

## Authorization and classification

The adapter applies server-side predicates for every read, scan and mutation. It revalidates user, workspace, agent, execution, correlation and purpose where the record carries those dimensions. Classification is checked before materializing data. Error text, `repr`, logs and operational events contain only sanitized codes and opaque IDs; they never include SQL, credentials, secrets, prompts, raw payloads or proprietary values.

## PostgreSQL adapter

The adapter uses SQLAlchemy 2 for engine/session/transaction handling and internal mappings. Internal tables cover the minimum durable authority:

- versioned records with record type, opaque reference, ownership, classification and JSON-safe snapshot;
- minimal audit entries;
- outbox entries with immutable event identity and source/version relation;
- idempotency keys, fingerprints and receipts.

Constraints and indexes enforce unique record references, monotonic version updates, ownership predicates, unique event IDs, source/version relation and idempotency scope. The public layer never imports the SQLAlchemy package.

The adapter receives DSN, pool settings, timeout and credentials from composition. It does not create a database, service or container. SQLite in-memory is a contract harness only; PostgreSQL-specific locking, isolation and database error integration tests run only when `AGENTOS_TEST_POSTGRES_DSN` is set.

## Migrations

Alembic migrations are versioned under the PostgreSQL technology package. The initial migration creates only the tables, constraints and indexes listed above. A public administrative helper may invoke `upgrade`, but construction, import, Runtime startup and domain operations never invoke migrations implicitly.

Backup, restore, replication, partitioning, multi-region and disaster recovery are documented limitations, not simulated by this adapter.

## Execution compatibility

The compatibility adapter translates existing `ExecutionDomainChange`, legacy Execution event envelopes, audit records and receipts to/from canonical persistence records. It preserves the signatures and result behavior consumed by `ExecutionControlService`, including `Accepted`, `AlreadyApplied`, `Rejected`, `Conflict` and `Indeterminate`.

The translation is the only place allowed to serialize/deserialize the `Execution` aggregate and legacy event envelope. Runtime, Context, Events, Agents and Providers do not import SQLAlchemy, Alembic or schema types.

## Error and recovery semantics

Database constraint violations, deadlocks, serialization failures, timeouts, connection failures and unknown database errors are normalized into bounded public codes with retryability. Deadlock/serialization/timeout are retryable only when the caller’s idempotency and budget policy permits. A failure before commit is `NOT_COMMITTED`; a lost commit acknowledgement is `UNKNOWN`; no adapter invents success.

`inspect_commit` is authorized by the same context and idempotency scope. It returns the durable receipt if the transaction committed, `NOT_COMMITTED` when the transaction is known absent, or `UNKNOWN` when the database cannot establish a final state. It never replays a command.

## Testing strategy

The test suite is layered:

1. Public contract tests cover complete/incomplete context, bounded options, opaque references, classification, cursor binding and sanitized representations.
2. In-memory contract tests cover ownership, idempotency, fingerprint conflicts, expected versions, atomicity, rollback, `UNKNOWN`/inspection, outbox deduplication and bounded reads/scans.
3. SQLAlchemy SQLite tests cover schema mapping, explicit migration invocation, transaction behavior, normalized errors and the same canonical contract where SQLite semantics are valid.
4. Optional PostgreSQL tests cover row locking, isolation, deadlock/timeout/constraint normalization and concurrent optimistic version conflicts only when `AGENTOS_TEST_POSTGRES_DSN` is configured.
5. Compatibility and boundary tests prove existing Execution behavior and that Kernel/domain packages contain no concrete persistence dependency.

Every production behavior is introduced by a RED test, followed by minimal GREEN implementation and a focused/full verification run. The mandatory final commands are:

```text
python -m pytest -q
python -m compileall -q src tests
```

The final audit also scans for SQLAlchemy/Alembic outside the PostgreSQL adapter/migrations and reports optional PostgreSQL test status explicitly.

## Explicit non-goals

This session does not add Redis, queues, pub/sub, sessions, locks, leases, workers, Scheduler, DispatchCoordinator, Artifact Storage, Workspaces, Memory, Blackboard, Configuration, API/FastAPI/SSE, Provider execution, filesystem, distributed transactions, exactly-once delivery or commit+publish atomicity.

## Acceptance criteria

- A single typed canonical persistence port exists and is technology-independent.
- PostgreSQL confirms state, audit, idempotency and outbox atomically.
- Conflicts, idempotency divergence, rollback and indeterminate commit are explicit.
- Reads/scans are bounded, opaque-cursor based, ownership filtered and classification aware.
- Alembic migrations are ordered and explicitly operated.
- Existing ExecutionControl and in-memory adapter remain green through an explicit compatibility bridge.
- Runtime, Agent, Events, Context and Providers remain free of concrete persistence imports.
- Required tests, boundary scans, compileall and final RFC/ADR audit have fresh evidence.
