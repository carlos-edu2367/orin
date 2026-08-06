# Multi-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar a fronteira RFC 203 de colaboração multi-agent em contratos públicos bounded, adapters in-memory e bridges para os domínios existentes.

**Architecture:** `agentos.context` será a fonte única dos contratos canônicos de compartilhamento (`StructuredHandoff`/`HandoffRef`); `agentos.multi_agent` conterá modelos, segurança, portas, serviço e adapter in-memory. O serviço solicitará lifecycle pela `ExecutionControl`, resolução/administração pela RFC 201, seed mínimo pela Context Sharing e fatos pela outbox de Events, sem importar tecnologia ou estado interno de outro domínio.

**Tech Stack:** Python 3.13, dataclasses congeladas, `Protocol`, `StrEnum`, `hashlib/json`, pytest; somente portas públicas existentes e novos adapters in-memory de referência.

## Global Constraints

- Não usar `git reset --hard`, `git checkout --`, limpeza ampla ou sobrescrever alterações preexistentes.
- `ExecutionControl` continua sendo a única fachada mutante do lifecycle de `Execution`.
- `StructuredHandoff`/`HandoffRef` serão definidos uma única vez em `agentos.context` e consumidos por alias.
- Toda mensagem e delegação assíncrona terá Execution própria; retry terá nova Execution.
- `PAUSED -> QUEUED` será a única retomada pública de espera; não criar `WAITING_CHILD`.
- Ownership, `user_id`, `workspace_id`, owner, Agent ativo, purpose, classification, Grants, deadline, correlation e idempotência serão revalidados no uso.
- Não importar FastAPI, HTTP, SDK de Provider, SQLAlchemy, Alembic, Redis, filesystem, ArtifactStorage, requests, httpx, Kafka, RabbitMQ, broker, worker ou scheduler no pacote multi-agent.
- Events serão fatos mínimos, bounded, posteriores ao commit, deduplicáveis e sem conteúdo sensível.
- Adapters in-memory serão documentados como referência de processo, não como durabilidade ou exactly-once.

---

### Task 1: Contratos canônicos de compartilhamento RFC 303

**Files:**
- Modify: `src/agentos/context/models.py`
- Modify: `src/agentos/context/ports.py`
- Modify: `src/agentos/context/__init__.py`
- Test: `tests/unit/context/test_context_sharing_contracts.py`

**Interfaces:**
- Produces frozen `ContextShareGrant`, `SharedContextReference`, `StructuredHandoff`, `HandoffRef`, `DelegatedGrantRef`, `TaskSnapshot`, `Criterion`, `Constraint`, `OutputContractRef`, `ContextShareBudget` and `ContextSharingService`.

- [x] Write failing tests for opaque refs, required expiration/integrity/version, ownership alignment, classification ceiling, bounded refs/snapshots and forbidden redelegation.
- [x] Run `python -m pytest -q tests/unit/context/test_context_sharing_contracts.py` and confirm RED.
- [x] Add the canonical RFC 303 models and port using existing context naming/conventions; keep all content as refs or bounded summaries.
- [x] Run the focused test file and the existing Context suite; confirm GREEN without changing Context assembly semantics.
- [x] Commit only the sharing contract files and test with `feat: add canonical context sharing contracts`.

### Task 2: Multi-agent value models and security

**Files:**
- Create: `src/agentos/multi_agent/models.py`
- Create: `src/agentos/multi_agent/security.py`
- Create: `tests/unit/multi_agent/test_models_security.py`

**Interfaces:**
- Produces `Collaboration`, participant/state types, `AgentMessage`, `Delegation`, `DelegationResult`, `WaitRegistration`, all command/receipt types, policy enums and bounded sanitized errors.

- [x] Write RED tests for immutable bounded commands, participant removal, message kinds, wait rules, terminal result exclusivity, cancellation scopes and classification ceilings.
- [x] Run the focused tests and record the expected missing-symbol/validation failures.
- [x] Implement frozen/slotted dataclasses, tuple normalization, opaque refs, UTC checks, bounded inline summaries and deterministic fingerprinting scoped by ownership.
- [x] Run focused tests plus `python -m compileall -q src tests` for the changed packages.
- [x] Commit with `feat: add multi-agent contracts and security`.

### Task 3: Public ports and in-memory repositories

**Files:**
- Create: `src/agentos/multi_agent/ports.py`
- Create: `src/agentos/multi_agent/in_memory.py`
- Create: `src/agentos/multi_agent/__init__.py`
- Create: `tests/unit/multi_agent/test_ports_and_in_memory.py`

**Interfaces:**
- Consumes public Agent/Execution/Context/Events contracts.
- Produces `MultiAgentCoordinator`, `AgentResolverPort`, `ExecutionLifecyclePort`, `ContextSharingPort`, `MultiAgentEventRecorder`, `CollaborationStore`, `InMemoryMultiAgentStore`, and commit-state receipts.

- [x] Write RED tests for collaboration persistence, idempotent record creation, participant removal without history deletion, event dedupe and commit `UNKNOWN` inspection.
- [x] Run the focused tests and verify failures are caused by absent ports/adapter.
- [x] Implement only process-local records and narrow Protocols; do not import persistence internals or technology.
- [x] Run the focused tests, existing Events tests and the forbidden-token scan on `src/agentos/multi_agent`.
- [x] Commit with `feat: add multi-agent public ports and in-memory store`.

### Task 4: Compatibility bridges for existing domains

**Files:**
- Create: `src/agentos/multi_agent/compat.py`
- Create: `tests/unit/multi_agent/test_compat.py`

**Interfaces:**
- Produces adapters that translate Agent Registry/Administration, `ExecutionControl`, Context Sharing and canonical Events into the narrow ports from Task 3.

- [x] Write RED tests proving Agent resolution rejects suspended/archived/cross-workspace agents, admin creation returns `AdministrativeExecutionRef`, and Execution commands use expected versions.
- [x] Run focused RED tests.
- [x] Implement translation only through public imports; map `Accepted`/`AlreadyApplied`/`Rejected`/`Conflict`/`Indeterminate` to sanitized multi-agent outcomes.
- [x] Run Agent, Execution and focused multi-agent compatibility tests.
- [x] Commit with `feat: bridge multi-agent to public kernel ports`.

### Task 5: Collaboration, send and delegation service

**Files:**
- Create: `src/agentos/multi_agent/service.py`
- Create: `tests/unit/multi_agent/test_collaboration_and_delegation.py`

**Interfaces:**
- Produces `MultiAgentCoordinatorService.request_agent_creation`, `.send`, `.delegate`, `.return_result`.

- [x] Write RED tests for informative/request/response/control messages, own `delivery_execution_id`, idempotency/fingerprint conflict, deadlines, canonical handoff validation, one child Execution per attempt, retry with new ID, no context/Grant inheritance and sanitized result refs.
- [x] Run focused tests and inspect the failure reasons.
- [x] Implement validate-create-record-event flow; use `ExecutionControl` through the bridge to create delivery/child Executions and never mutate `Execution` objects directly.
- [x] Add reauthorization on delivery/delegation consumption, explicit failure/cancelled terminals and outbox-only event recording.
- [x] Run the service tests plus all Agent/Execution/Context/Event suites.
- [x] Commit with `feat: implement multi-agent messaging and delegation`.

### Task 6: Wait, failure propagation and cancellation

**Files:**
- Modify: `src/agentos/multi_agent/models.py`
- Modify: `src/agentos/multi_agent/service.py`
- Modify: `src/agentos/multi_agent/in_memory.py`
- Create: `tests/unit/multi_agent/test_wait_failure_cancel.py`

**Interfaces:**
- Produces `wait_for`, `request_cancel`, terminal event reconciliation and explicit `PROPAGATE`, `CONTINUE_WITH_FAILURE_REF`, `REQUEST_RETRY`, `CASCADE`, `DETACH_IF_AUTHORIZED`, `CANCEL_CHILD_ONLY` behavior.

- [x] Write RED tests for ALL/ANY/MINIMUM_COUNT bounds, checkpoint then PAUSED, resume via QUEUED, deadline/tardy results, child dedupe, failed checkpoint, parent/child/subtree cancellation and partial cancellation.
- [x] Run focused RED tests.
- [x] Implement bounded wait registrations and event-driven reevaluation; use expected state versions and idempotency keys for pause/resume/cancel commands.
- [x] Implement failure policy and retry identity/cause preservation without terminal reopening or fabricated success.
- [x] Run focused tests and complete multi-agent suite.
- [x] Commit with `feat: add multi-agent waits and propagation policies`.

### Task 7: Boundary integration, documentation and final audit

**Files:**
- Create: `tests/unit/integration/test_multi_agent_boundaries.py`
- Modify: `docs/superpowers/specs/2026-08-06-multi-agent-design.md`
- Modify: `docs/superpowers/plans/2026-08-06-multi-agent.md`

**Interfaces:**
- Consumes all public package contracts and produces evidence for no concrete dependencies, no direct Execution mutation, no Context/Memory/prompt/secret copy and canonical RFC coverage.

- [x] Write failing boundary tests for prohibited imports, direct state mutation, EventBus command use and ownership/correlation/purpose continuity across every flow.
- [x] Run boundary RED, then implement only compatibility/doc fixes needed by demonstrated failures.
- [x] Run exactly `python -m pytest -q`, `python -m compileall -q src tests`, the required `rg` scan, `git diff --check` and `git status --short --branch`.
- [x] Run a transversal scan over `agents`, `orchestrator`, `execution`, `runtime`, `context`, `events` and `persistence`; inspect every changed file for technology leakage.
- [x] Audit each requirement against RFCs 050, 060, 101–104, 201–203, 303, 501–502, 601 and ADRs 002, 009, 012, 013; record uncovered production limitations honestly.
- [x] Commit only final docs/tests if needed with `docs: record multi-agent verification and limitations`.

## Evidence and limitations

- `python -m pytest -q`: 307 passed, 1 skipped.
- `python -m compileall -q src tests`: passed.
- Forbidden dependency scan in `src/agentos/multi_agent`: zero matches.
- `git diff --check`: passed; existing LF/CRLF warnings are unrelated to whitespace errors.
- The in-memory store and fakes prove public-contract behavior only; they do not provide process durability, distributed leases, transport delivery, broker semantics, production outbox storage, exactly-once effects or recovery across processes.
- No production adapter for PostgreSQL, Redis, broker, mailbox, worker or scheduler was added.
