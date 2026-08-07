# RFC 603 — Workspaces Implementation Plan

> **For agentic workers:** Execute this plan inline with TDD checkpoints, preserve unrelated working-tree changes, and stage only explicit Workspace paths.

**Goal:** Implementar a semântica completa da RFC 603 no adapter de referência, integrando registro/outbox RFC 601 sem vazar tecnologia, root física ou handles.

**Architecture:** `agentos.workspaces` mantém contratos imutáveis e independentes de tecnologia. `WorkspaceManagerService` coordena `WorkspaceRegistry`, `WorkspaceRootAdapter`, quota/usage, leases/locks e eventos; adapters in-memory demonstram o comportamento de referência. `TransactionalWorkspaceRegistry` compõe o registro com `TransactionalPersistence` e outbox existente.

**Tech Stack:** Python 3.13+, dataclasses congeladas com `slots`, Protocols, `threading.RLock`, `datetime`, `hashlib/json`, `pytest`, portas RFC 601/103 e `compileall`. Nenhuma dependência nova.

## Global Constraints

- Ownership durável é confirmado antes do provisionamento externo.
- `PROVISIONING` não aceita operações normais; `DELETED` é terminal e IDs nunca são reutilizados.
- Root é escolhida exclusivamente pelo adapter; paths físicos e handles nativos não atravessam portas, eventos, erros ou logs.
- Containment exige identity/descriptor semantics; links, mounts, reparse points, hard-link ambiguity e incerteza falham fechado.
- Quota reserva antes do efeito e contabiliza depois; `STALE`/`DIVERGENT` bloqueia novas reservas.
- Leases e locks são bounded, revogáveis e vinculados a contexto, versão, identity, duração e fencing.
- Cleanup e reconcile são bounded, idempotentes, recuperáveis e nunca ampliam o alvo.
- SQLAlchemy/Alembic, filesystem real, Redis, HTTP, Artifact bytes e fornecedor remoto não entram em `agentos.workspaces`.

---

### Task 1: Registrar spec/plano e contratos públicos

**Files:**
- Create: `src/agentos/workspaces/__init__.py`
- Create: `src/agentos/workspaces/models.py`
- Create: `src/agentos/workspaces/ports.py`
- Create: `src/agentos/workspaces/security.py`
- Create: `tests/unit/workspaces/test_contracts.py`
- Create: `tests/unit/workspaces/test_security.py`
- Modify: `docs/superpowers/2026-08-07-workspaces-requirement-matrix.md` (criar na Task 8)

**Interfaces:**
- Produces all public Workspace contexts, opaque refs/identity/fence, states, quota/usage, lease/lock, lifecycle requests/results, errors and `WorkspaceManager`/adapter Protocols.
- Uses only `DataClassification`, `EventEnvelope` and RFC 601 public transaction types at integration boundaries.

- [ ] Write tests that fail for missing package, invalid contexts, unbounded fields, forbidden physical-root fields, non-serializable handles, exact states and immutable public models.
- [ ] Run `python -m pytest -q tests/unit/workspaces/test_contracts.py tests/unit/workspaces/test_security.py` and record the expected RED.
- [ ] Implement bounded frozen models, opaque types and security validation with sanitized repr/error helpers.
- [ ] Run the focused tests GREEN and refactor only without adding behavior.
- [ ] Commit explicit Workspace contract paths with `git add src/agentos/workspaces tests/unit/workspaces/test_contracts.py tests/unit/workspaces/test_security.py docs/superpowers/specs/2026-08-07-workspaces-design.md docs/superpowers/plans/2026-08-07-workspaces.md`.

### Task 2: Registry and reference root adapter

**Files:**
- Create: `src/agentos/workspaces/registry.py`
- Create: `src/agentos/workspaces/root_adapter.py`
- Create: `tests/unit/workspaces/test_registry.py`
- Create: `tests/unit/workspaces/test_root_adapter.py`

**Interfaces:**
- Consumes models/ports from Task 1.
- Produces `InMemoryWorkspaceRegistry`, `InMemoryWorkspaceRootAdapter`, root provisioning/resolution/cleanup receipts and test-only fault/issue injection that remains behind the adapter port.

- [ ] Write RED tests for atomic ownership-before-root, requested-ID collision, idempotent create, tombstone non-reuse, root staging, identity mismatch, root swap, symlink/junction/mount/reparse/hard-link rejection and opaque handle binding.
- [ ] Run focused RED and confirm failure is due to missing registry/adapter behavior.
- [ ] Implement copy-on-write registry with lock, tombstones and idempotency; implement isolated root records, identity comparison and fail-closed resolution/cleanup.
- [ ] Run `python -m pytest -q tests/unit/workspaces/test_registry.py tests/unit/workspaces/test_root_adapter.py` GREEN.
- [ ] Commit registry/root adapter with explicit paths.

### Task 3: Manager create/activate/inspect and lifecycle state machine

**Files:**
- Create: `src/agentos/workspaces/service.py`
- Create: `tests/unit/workspaces/test_manager_lifecycle.py`

**Interfaces:**
- Consumes `WorkspaceRegistry`, `WorkspaceRootAdapter`, clock/ID factory and event sink.
- Produces `WorkspaceManagerService.create`, `activate`, `inspect`, `transition`, sanitized event factory and exact permitted/prohibited transitions.

- [ ] Write RED tests for bootstrap authorization, provisioning failure, activate version/identity checks, every allowed transition, every prohibited transition, terminality, idempotency and no physical-root leakage.
- [ ] Run focused RED.
- [ ] Implement lifecycle with durable state mutation before external provisioning, explicit version checks and event-after-confirmation.
- [ ] Run focused lifecycle/security tests GREEN.
- [ ] Commit service lifecycle slice and tests.

### Task 4: Leases, administrative locks and fencing

**Files:**
- Modify: `src/agentos/workspaces/service.py`
- Modify: `src/agentos/workspaces/registry.py`
- Create: `tests/unit/workspaces/test_leases_and_fencing.py`

**Interfaces:**
- Consumes active Workspace snapshots and root adapter handles.
- Produces acquire/renew/release lease and internal administrative fencing lock with monotonic token and bounded permissions/budget.

- [ ] Write RED tests for cross-scope/context mismatch, non-ACTIVE rejection, active-lease quota, expiry/revocation, stale version/identity, lost lock, old fence and race revalidation.
- [ ] Run focused RED.
- [ ] Implement lease binding, root resolve-then-revalidate, expiry, drain/revoke and fencing checks under registry locking.
- [ ] Run focused lease/fencing tests GREEN and regression tests for lifecycle.
- [ ] Commit leases/fencing.

### Task 5: Quota reservations and usage

**Files:**
- Modify: `src/agentos/workspaces/models.py`
- Modify: `src/agentos/workspaces/ports.py`
- Modify: `src/agentos/workspaces/registry.py`
- Modify: `src/agentos/workspaces/service.py`
- Create: `tests/unit/workspaces/test_quotas.py`

**Interfaces:**
- Produces `reserve_usage`, `record_usage`, `release_reservation`, bounded reservation receipts and usage snapshots with `CURRENT/STALE/IN_PROGRESS/DIVERGENT`.

- [ ] Write RED tests for all quota dimensions, reserve-before-effect, concurrent reservations, stale/divergent blocking, max-file/depth validation, committed accounting, idempotency and reconciliation reset.
- [ ] Run focused RED.
- [ ] Implement atomic reservation/accounting and conservative usage policy; update active lease usage through the same registry authority.
- [ ] Run quota tests GREEN and full Workspace unit suite.
- [ ] Commit quota/usage.

### Task 6: Recoverable delete and reconciliation

**Files:**
- Modify: `src/agentos/workspaces/service.py`
- Modify: `src/agentos/workspaces/root_adapter.py`
- Create: `tests/unit/workspaces/test_cleanup_and_reconcile.py`

**Interfaces:**
- Produces bounded delete workflow, checkpoints/manifests by category, `reconcile(ROOT|USAGE|LEASES|CLEANUP|ALL)` and safe receipts.

- [ ] Write RED tests for exact-target checks, delete fencing, drain deadline, partial cleanup, root absent/swapped/orphaned, retry after DELETING/DELETED, bounded evidence, tombstone and no target expansion.
- [ ] Run focused RED.
- [ ] Implement delete phases and reconciliation; keep uncertain cleanup in `DELETING`/`RECOVERY_REQUIRED` and never return ACTIVE after deletion starts.
- [ ] Run focused cleanup/reconcile tests GREEN.
- [ ] Commit cleanup/reconcile.

### Task 7: RFC 601 persistence/outbox composition and boundary evidence

**Files:**
- Create: `src/agentos/workspaces/persistence.py`
- Create: `tests/unit/workspaces/test_persistence_boundary.py`
- Create: `tests/unit/workspaces/test_events.py`
- Create: `tests/integration/workspaces/test_postgres_optional.py`
- Create: `tests/unit/workspaces/test_boundary_scan.py`

**Interfaces:**
- `TransactionalWorkspaceRegistry` uses only `TransactionalPersistence`; it serializes bounded workspace metadata and writes events via `RecordChange`/`AuditChange`/`OutboxChange`.
- Event tests use in-memory sink and prove all eight event names, post-fact timing and sanitized payload.

- [ ] Write RED tests for round-trip registry metadata, optimistic conflict, transactional outbox, no SQLAlchemy/HTTP/Redis/path import, event minimization and PostgreSQL skip without DSN.
- [ ] Run focused RED.
- [ ] Implement the composition and optional test exactly behind existing ports; do not add migration unless generic RFC 601 records cannot represent the bounded data.
- [ ] Run focused tests and `python -m pytest -q tests/unit/persistence tests/unit/artifact_storage`.
- [ ] Commit persistence/events/boundary evidence.

### Task 8: Requirement matrix, closeout, independent review and final gates

**Files:**
- Create: `docs/superpowers/2026-08-07-workspaces-requirement-matrix.md`
- Create: `docs/superpowers/2026-08-07-workspaces-closeout.md`
- Modify: only directly implicated Workspace files after review findings.

- [ ] Run focused Workspace tests, full `python -m pytest -q`, `python -m compileall -q src tests`, required `rg` scans, `git diff --check`, optional PostgreSQL test and `git status --short --branch`.
- [ ] Perform independent read-only review of the Workspace diff against every RFC 603 section, specifically authorization, root races, canonicalization, fencing, idempotency, cleanup and leakage.
- [ ] For every actionable finding, add a RED regression test, implement the smallest GREEN fix and rerun the affected suite.
- [ ] Update matrix/closeout only with fresh evidence, explicit limitations and next gate.
- [ ] Repeat every final command after review fixes; only then report the gate status.

## Self-review

Every RFC 603 requirement maps to Tasks 1–8: contracts (1), ownership/root (2–3), lifecycle (3), leases/fencing (4), quotas (5), cleanup/reconciliation (6), persistence/events/boundaries (7), and evidence/review/final gates (8). No task relies on technology-specific details or postpones a normative Workspace behavior.
