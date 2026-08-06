# Execution Lifecycle Design

**Goal:** Implement only the backend domain for `Execution` and its `ExecutionControl` port, with RFC 102 state transitions, optimistic versioning, command idempotency, and atomic state/audit/outbox preparation.

## Boundaries

The domain is synchronous, framework-independent Python 3.13+ code. `ExecutionControl` owns authorization-context validation, transition rules, and transaction-request construction. `TransactionalPersistence` is the only write boundary; it receives a typed request containing the domain change, one minimal audit record, and the corresponding `OutboxEntry`.

The in-memory persistence adapter is test-only infrastructure. It stores executions, committed audit records, outbox entries, and idempotency receipts in memory. It does not model a database, queue, worker, broker, or external service.

## Domain model

`Execution` carries opaque identifiers, ownership, agent and task reference, lifecycle state, monotonic `state_version`, correlation, limits, usage, result or sanitized failure, and timestamps. The only states are `QUEUED`, `STARTING`, `RUNNING`, `WAITING_TOOL`, `WAITING_USER`, `PAUSED`, `COMPLETED`, `FAILED`, and `CANCELLED`.

The state graph is exactly the transition table in RFC 102. Terminal states have no outgoing edges. Every existing-execution mutation has an explicit `expected_version`; creation carries an explicit `expected_version=None`. State changes increment the version exactly once.

## Commands and idempotency

Every sensitive command carries `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, and `purpose`, plus `command_id`, `idempotency_key`, `expected_version`, and a requested timestamp. The adapter fingerprints the semantic command. Repeating the same key and fingerprint returns the prior result without another state, audit, or outbox write. Reusing a key with a different fingerprint is rejected.

Specialized commands enforce RFC 102 conditions: acquire starts `QUEUED -> STARTING`, runtime transitions handle the remaining graph, pause only enters `PAUSED`, resume and authorized user input enter `QUEUED`, cancel enters `CANCELLED`, and failure enters `FAILED` with a sanitized failure value. No command can reopen a terminal.

## Events and atomic persistence

Each confirmed state transition creates one event named from the target fact (`ExecutionQueued`, `ExecutionStarted`, `ExecutionWaitingForTool`, `ExecutionWaitingForUser`, `ExecutionPaused`, `ExecutionResumed`, `ExecutionFinished`, `ExecutionFailed`, or `ExecutionCancelled`). The event includes identity, UTC timestamp, source, correlation, ownership, execution ID, positive sequence, version, and a minimal payload containing previous/new states and a reason code or reference where needed.

RFC 102 does not define a separate `ExecutionStarting` event. The `QUEUED -> STARTING` acquisition is therefore recorded as an `ExecutionQueued` outbox event whose payload explicitly says `to_state=STARTING`; no additional state or technology-specific event is introduced. `PAUSED -> QUEUED` uses the RFC-defined `ExecutionResumed` fact.

`TransactionalPersistence.transact` receives `DomainChange`, `AuditRecord`, and `OutboxEntry` in one request. The in-memory adapter validates version and idempotency before applying all three together. A configured indeterminate result is applied atomically and returned as `TransactionIndeterminate`; `inspect_commit` reveals the committed receipt without allowing an unsafe blind retry.

`CommitExecutionChanges` accepts typed, reference-only related changes. Their fingerprint participates in command idempotency, while the domain still stores only lifecycle state and bounded usage fields; raw input, prompts, provider payloads, and secrets never cross the boundary.

## Errors and security

Rejections are typed as invalid command, unauthorized context, invalid transition, terminal mutation, version conflict, idempotency conflict, or persistence rejection. Failure and cancellation data are categorical or reference-based; raw task text, provider payloads, credentials, tokens, and secrets are not stored or logged.

## Verification

Unit tests cover every allowed edge, representative forbidden edges and all required concurrency, idempotency, terminal, cancellation, failure, atomicity, and indeterminate-commit behaviors. No Runtime, API, worker, Event Bus, PostgreSQL adapter, ORM, Redis, provider, browser, tool, or capability is introduced.
