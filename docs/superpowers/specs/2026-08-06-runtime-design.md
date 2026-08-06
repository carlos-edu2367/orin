# Runtime Design

**Goal:** Implement the framework-independent AgentOS Runtime that governs one `Execution` at a time through public ports, preserving lifecycle, ownership, limits, cancellation, pause, failure, result and checkpoint-reference invariants from RFCs 050, 060, 101, 102, 103, 104, 501, 502 and 601.

## Boundaries

The Runtime is synchronous Python 3.13+ domain code. It receives a `RuntimeRequest`, loads and validates the authorized `Execution` through `ExecutionControl`, and coordinates Context, model selection, Provider, actions and checkpoint recovery through protocols. It never imports or instantiates FastAPI, workers, queues, persistence, Event Bus, Provider SDKs, Tools, Capabilities or concrete adapters.

`ExecutionControl` remains the only mutating boundary for the `Execution`. Runtime-side state is ephemeral and limited to opaque references, normalized public outcomes and bounded accounting. All lifecycle, usage, result, failure, cancellation and checkpoint associations are submitted through `ExecutionControl` commands; the Runtime never calls `TransactionalPersistence` or publishes Events directly.

## Public contract

`RuntimeRequest` carries `execution_id`, `user_id`, optional `workspace_id`, `agent_id`, actor and worker references, correlation/purpose context, model-requirements reference, optional checkpoint reference and an operation timestamp. The Runtime derives the complete sensitive operation context from the authorized `Execution`; a mismatch in user, workspace, Agent or correlation is rejected without exposing storage details.

`RuntimeOutcome` has four variants:

- `CompletedOutcome`: execution ID, result reference and final public usage;
- `WaitingOutcome`: execution ID and either `WAITING_USER` or `PAUSED`;
- `FailedOutcome`: execution ID and categorized `RuntimeError`;
- `CancelledOutcome`: execution ID and sanitized cancellation reason.

Payloads are reference-only. No public Runtime type contains a prompt, private action arguments, token, credential, provider payload, SDK object or complete result.

## Ports

- `ContextManager`: assemble and apply-turn operations return snapshots identified by manifest references; finalize only receives execution identity and disposition.
- `ModelResolver`: resolves public requirements into an opaque selection and approved snapshot reference; fallback is explicit and policy-gated.
- `ProviderPort`: accepts normalized context/selection references and returns final generation, action requests, user-input requests, failure or cancellation with public usage/cost.
- `ToolCapabilityPort`: invokes one opaque action request and returns result reference, failure or cancellation plus public usage.
- `CheckpointPort`: only `load` and `latest_safe`; it never writes. Safe checkpoint association is represented by a reference-only related change submitted to `ExecutionControl`.
- `Clock`: supplies UTC instants and monotonic elapsed time for deterministic tests.
- `BudgetPolicy`: evaluates execution, provider and action limits before and after external effects.

Every sensitive request carries `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` and `purpose`.

## Normative loop

1. Acquire the Runtime's single-execution guard, load the authorized Execution and reject terminal or ineligible states.
2. Acquire `QUEUED -> STARTING` through `ExecutionControl`; then transition `STARTING -> RUNNING` after ownership and optional checkpoint validation.
3. Assemble Context through `ContextManager`, resolve the model through `ModelResolver`, and check cancellation, pause, timeout and budget before each external effect.
4. Invoke the abstract Provider. Count one accepted Provider invocation as one iteration and account for public usage/cost regardless of success, failure or cancellation.
5. For a final response, apply the Context turn, associate any safe checkpoint, commit result/usage, then transition to `COMPLETED` through `ExecutionControl`.
6. For an action request, transition `RUNNING -> WAITING_TOOL`, invoke the action once, reconcile its result, and commit the action reference/usage before returning to `RUNNING`.
7. For a user-input request, transition to `WAITING_USER` and return `WaitingOutcome`.
8. At safe boundaries, a pause becomes `PAUSED`; a cancellation becomes `CANCELLED`; timeout and budget exhaustion become categorized `FAILED`. A late external result is reconciled only as accounting/audit data and can never reopen a terminal execution.
9. Finalize Context ephemerally after a terminal or waiting disposition.

The Runtime does not retry an external effect unless the normalized outcome declares it safe or policy-dependent and the request is idempotent/reconcilable. An uncertain effect is recovered through public inspection/checkpoint references rather than blind replay.

## Error and accounting semantics

Runtime errors carry a stable category, public code, retryability and optional diagnostic reference. Categories distinguish initialization, Context, model resolution, Provider, action, checkpoint, timeout, limit, cancellation, concurrency and reconciliation failures. Execution timeout, Provider timeout and action timeout remain distinct. `ExecutionUsage` remains monotonic and includes duration, iterations, public token usage and confirmed/comparable cost.

Limits are checked before and after every Provider/action effect. Maximum iterations, cost and measured tokens prevent the next effect and produce an explicit failure. Missing measurements remain unavailable; they are never represented as confirmed zero.

## Concurrency and terminality

One Runtime instance owns at most one active execution loop. A second concurrent call is rejected as a concurrency error. `ExecutionControl` optimistic versions and idempotency protect duplicate acquisitions and state mutations. Terminal states are returned as terminal outcomes and are never reopened or converted by late results.

## Testing

Tests use only in-test fakes for every port. They cover the required lifecycle, Tool round trip, waiting user, pause, cancellation races, distinct timeouts, iteration/cost/token limits, port failures, late results, duplicate acquisition, checkpoint recovery, idempotency/reconciliation, ExecutionControl-only mutation, no direct Event publication, context propagation and absence of sensitive payloads.

## Explicit non-goals

This change does not implement Context Manager, Model Catalog, Provider, Tool, Capability, Browser, Resource, Memory, Event Bus, workers, queues, FastAPI, PostgreSQL, Redis, ORM, Alembic or any concrete adapter.
