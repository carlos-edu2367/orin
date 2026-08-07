# Artifact Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan inline with TDD checkpoints; preserve unrelated working-tree changes and stage only explicit Artifact Storage paths.

**Goal:** Fechar a semântica da RFC 602 no escopo de referência: contratos públicos, Manager, bytes in-memory, metadata/outbox, segurança, lifecycle, quotas, retenção, reconciliação e evidência.

**Architecture:** `agentos.artifact_storage` mantém contratos imutáveis e independentes de tecnologia. `ArtifactManager` coordena `ArtifactStorage`, `ArtifactMetadataRepository`, clock/IDs e `TransactionalPersistence`; `InMemoryArtifactStorage` guarda bytes e handles opacos; `InMemoryArtifactMetadataRepository` fornece metadata determinística; `TransactionalArtifactMetadataRepository` adapta metadata à porta RFC 601 usando registros versionados e outbox existente. O schema genérico da RFC 601 já suporta metadata JSON versionada, portanto não será criada migration específica sem uma lacuna comprovada.

**Tech Stack:** Python 3.13+, dataclasses congeladas com `slots`, `Protocol`, `hashlib`, `io`, `pytest`, portas RFC 601/103, `compileall`, `rg`.

## Global Constraints

- Somente `AVAILABLE` gera `ArtifactReference` resolvível.
- Namespace é derivado de `user_id`, `workspace_id` e categoria; `logical_name` não escolhe localização física.
- Quota é reservada antes da escrita e reconciliada pelo tamanho real; staging e recuperação contam.
- Chunks repetidos exigem offset, comprimento, checksum e idempotency key idênticos.
- `finalize` verifica tamanho/checksum sobre bytes persistidos antes de publicar metadata.
- Falha depois de efeito possível retorna `UNKNOWN`/reconciliação e nunca fabrica referência.
- Toda leitura revalida ownership, grant, versão, purpose, classificação e estado.
- Bytes não entram em metadata transacional, outbox, contexto, log ou referência.
- Domínio não importa SQLAlchemy, Alembic, HTTP, filesystem, SDK, Redis, broker ou fornecedor.
- Outbox registra somente fatos confirmados e usa payload mínimo sanitizado.
- PostgreSQL real é opcional via `AGENTOS_TEST_POSTGRES_DSN`; sem DSN o teste deve ser `skipped`.

---

### Task 1: Matriz de requisitos e contratos públicos

**Files:**
- Create: `src/agentos/artifact_storage/__init__.py`
- Create: `src/agentos/artifact_storage/models.py`
- Create: `src/agentos/artifact_storage/ports.py`
- Create: `src/agentos/artifact_storage/security.py`
- Create: `tests/unit/artifact_storage/test_contracts.py`
- Create: `tests/unit/artifact_storage/test_security.py`
- Create: `docs/superpowers/2026-08-06-artifact-storage-requirement-matrix.md`

**Interfaces:**
- Produces `ArtifactOperationContext`, `ArtifactNamespace`, `ArtifactMetadata`, `ArtifactReference`, `ArtifactGrant`, `ArtifactProvenance`, all request/result models, `ArtifactError`, `ArtifactStorage`, `ArtifactMetadataRepository` and `ArtifactManager` Protocol.
- Uses `DataClassification`, `EventEnvelope` and RFC 601 context/outbox types only at integration boundaries.

- [ ] **Step 1: Write failing contract tests** for frozen bounded models, enum values, opaque handle repr, `BytesSource`/sink bounds, path traversal rejection, secret-field rejection, classification ceiling and `ArtifactReference` without bytes/path.
- [ ] **Step 2: Run RED** with `python -m pytest -q tests/unit/artifact_storage/test_contracts.py tests/unit/artifact_storage/test_security.py`; expected failure is missing `agentos.artifact_storage`.
- [ ] **Step 3: Implement minimal models and security helpers** with exact bounded fields and sanitized reprs.
- [ ] **Step 4: Run GREEN** on the focused tests and refactor only without new behavior.
- [ ] **Step 5: Add the requirement matrix** mapping every RFC 602 section to file/test/evidence, marking PostgreSQL migration as “not required: generic RFC 601 record schema reused”.
- [ ] **Step 6: Commit** `git add src/agentos/artifact_storage tests/unit/artifact_storage/test_contracts.py tests/unit/artifact_storage/test_security.py docs/superpowers/2026-08-06-artifact-storage-requirement-matrix.md; git commit -m "feat: add artifact storage public contracts"`.

### Task 2: In-memory byte adapter

**Files:**
- Create: `src/agentos/artifact_storage/in_memory.py`
- Create: `tests/unit/artifact_storage/test_in_memory_storage.py`

**Interfaces:**
- Consumes byte-port models from Task 1.
- Produces `InMemoryArtifactStorage`, with deterministic `capabilities`, staging/seal, idempotent chunk writes, bound reads, verify, recoverable delete and injectable fault hooks.

- [ ] **Step 1: Write RED tests** for capability limits, opaque binding, append/repeat/conflict, expiry, seal checksum/size, open/read range/maximum bytes, cancellation, verify mismatch/indeterminate, recoverable delete and `UNKNOWN` fault receipts.
- [ ] **Step 2: Run focused RED** with `python -m pytest -q tests/unit/artifact_storage/test_in_memory_storage.py`.
- [ ] **Step 3: Implement the minimal in-memory state machine**: per-handle binding, staged bytearray, chunk fingerprint table, immutable sealed object, read handles, cleanup/recovery records and normalized faults.
- [ ] **Step 4: Run GREEN**, then run `python -m pytest -q tests/unit/artifact_storage/test_in_memory_storage.py tests/unit/artifact_storage/test_contracts.py`.
- [ ] **Step 5: Commit** `git add src/agentos/artifact_storage/in_memory.py tests/unit/artifact_storage/test_in_memory_storage.py; git commit -m "feat: add in-memory artifact bytes adapter"`.

### Task 3: Metadata repository and outbox composition

**Files:**
- Create: `src/agentos/artifact_storage/metadata.py`
- Create: `src/agentos/artifact_storage/persistence.py`
- Create: `tests/unit/artifact_storage/test_metadata_repository.py`
- Create: `tests/unit/artifact_storage/test_persistence_boundary.py`
- Modify: `src/agentos/persistence/in_memory.py` only if a demonstrated RFC 601 compatibility gap blocks the repository.

**Interfaces:**
- `InMemoryArtifactMetadataRepository` stores versioned metadata, grants, reservations, idempotency receipts, holds and references.
- `TransactionalArtifactMetadataRepository` consumes `TransactionalPersistence` and serializes metadata as bounded record payloads, registering `RecordChange`, `AuditChange` and `OutboxChange` through the existing transaction port.

- [ ] **Step 1: Write RED tests** for metadata round-trip, version conflict, grant revoke/expiry, reservation accounting, holds, idempotent mutation, ownership-filtered lookup and outbox payload minimization.
- [ ] **Step 2: Run RED** with `python -m pytest -q tests/unit/artifact_storage/test_metadata_repository.py tests/unit/artifact_storage/test_persistence_boundary.py`.
- [ ] **Step 3: Implement in-memory repository** with atomic copy-on-write mutation and explicit receipts.
- [ ] **Step 4: Implement transactional repository composition** using only `TransactionalPersistence`; encode metadata without bytes and emit RFC 103 envelopes only through RFC 601 transaction requests.
- [ ] **Step 5: Run GREEN** for focused repository/boundary tests and run `python -m pytest -q tests/unit/persistence` to protect the existing gate.
- [ ] **Step 6: Commit** `git add src/agentos/artifact_storage/metadata.py src/agentos/artifact_storage/persistence.py tests/unit/artifact_storage/test_metadata_repository.py tests/unit/artifact_storage/test_persistence_boundary.py; git commit -m "feat: add artifact metadata persistence boundary"`.

### Task 4: ArtifactManager lifecycle and writing

**Files:**
- Create: `src/agentos/artifact_storage/service.py`
- Create: `tests/unit/artifact_storage/test_manager_write.py`
- Create: `tests/unit/artifact_storage/test_manager_lifecycle.py`

**Interfaces:**
- `ArtifactManagerService` implements the public Manager operations from `ports.py`.
- It consumes the byte adapter, metadata repository, clock, ID factory, quota policy and optional event transaction sink.

- [ ] **Step 1: Write RED tests** for begin authorization/namespace/quota reservation, `ArtifactWriteStarted`, append normal/repeat/conflict/expiry, finalize success, wrong size/checksum, seal `UNKNOWN`, abort idempotence and no reference before availability.
- [ ] **Step 2: Run RED** with `python -m pytest -q tests/unit/artifact_storage/test_manager_write.py`.
- [ ] **Step 3: Implement begin/append/finalize/abort** with a single binding record, bounded source copy, chunk receipts, reconciliation path and quota release rules.
- [ ] **Step 4: Run GREEN**, then add lifecycle tests for quarantine, expiry, delete recoverable/permanent, holds, active references, `DELETING`, cleanup uncertain, resurrection prohibition and retention cutoff.
- [ ] **Step 5: Implement lifecycle/retention/verify** and run `python -m pytest -q tests/unit/artifact_storage/test_manager_write.py tests/unit/artifact_storage/test_manager_lifecycle.py`.
- [ ] **Step 6: Commit** `git add src/agentos/artifact_storage/service.py tests/unit/artifact_storage/test_manager_write.py tests/unit/artifact_storage/test_manager_lifecycle.py; git commit -m "feat: implement artifact manager lifecycle"`.

### Task 5: Authorized reads, grants and security regressions

**Files:**
- Create: `tests/unit/artifact_storage/test_manager_read.py`
- Create: `tests/unit/artifact_storage/test_manager_authorization.py`
- Modify: `src/agentos/artifact_storage/service.py` only through RED/GREEN fixes.

- [ ] **Step 1: Write RED tests** for fixed version/checksum, range reads, maximum bytes, cancel, expired read handle, cross-user/workspace, agent/execution mismatch, missing/different purpose, revoked/expired/wrong-version grant, classification ceiling, namespace mismatch and traversal/secret rejection.
- [ ] **Step 2: Run RED** with the two focused test files.
- [ ] **Step 3: Implement open/read/inspect authorization** with revalidation at open and each read boundary; translate partial effects without serving ambiguous data.
- [ ] **Step 4: Run GREEN** and add tests proving no sensitive value appears in `repr`, exception, event payload or recorded audit/log sink.
- [ ] **Step 5: Commit** `git add src/agentos/artifact_storage/service.py tests/unit/artifact_storage/test_manager_read.py tests/unit/artifact_storage/test_manager_authorization.py; git commit -m "feat: enforce artifact read authorization"`.

### Task 6: Events, adapter scans and optional migration evidence

**Files:**
- Create: `tests/unit/artifact_storage/test_events.py`
- Create: `tests/unit/artifact_storage/test_boundary_scan.py`
- Create: `tests/integration/artifact_storage/test_postgres_optional.py`
- Modify: `docs/architecture/600-platform-data/602-artifact-storage.md` only for an explicit implementation note if required.

- [ ] **Step 1: Write RED tests** for all seven event names, post-confirmation timing, deduplication, minimum payload, no bytes/path/secrets, domain import scan and optional PostgreSQL skip behavior.
- [ ] **Step 2: Run RED** for event/boundary tests.
- [ ] **Step 3: Implement the event factory/outbox adapter** and boundary assertions; use generic RFC 601 JSON records so no artifact-specific migration is needed.
- [ ] **Step 4: Run GREEN** and run `python -m pytest -q tests/unit/artifact_storage tests/integration/artifact_storage/test_postgres_optional.py`.
- [ ] **Step 5: Commit** `git add tests/unit/artifact_storage/test_events.py tests/unit/artifact_storage/test_boundary_scan.py tests/integration/artifact_storage/test_postgres_optional.py docs/architecture/600-platform-data/602-artifact-storage.md; git commit -m "test: prove artifact storage events and boundaries"`.

### Task 7: Closeout matrix, independent review and final gates

**Files:**
- Modify: `docs/superpowers/2026-08-06-artifact-storage-requirement-matrix.md`
- Create: `docs/superpowers/2026-08-06-artifact-storage-closeout.md`
- Modify: only files directly implicated by a review finding.

- [ ] **Step 1: Run focused and full verification**: `python -m pytest -q`, `python -m compileall -q src tests`, required `rg` scans, `git diff --check`, and status.
- [ ] **Step 2: Review the implementation against RFC 602 line-by-line**, checking every required test category and every excluded technology.
- [ ] **Step 3: For each finding, write a RED test, apply the smallest GREEN fix, rerun the affected suite and record evidence.**
- [ ] **Step 4: Update the matrix and closeout** only with actual command output, including PostgreSQL `skipped` when no DSN exists, production limitations and next RFC dependency.
- [ ] **Step 5: Run all final gates fresh** and commit docs with `git add` on explicit paths only.

## Self-review

- Every RFC 602 requirement maps to Tasks 1–7: contracts/ports (1), bytes/integrity (2), metadata/persistence (3), write/lifecycle/quota (4), reads/security (5), events/boundaries (6), evidence/closeout (7).
- No migration is silently omitted: the plan explicitly tests and documents reuse of the existing generic RFC 601 schema; a migration will be added only if the adapter cannot represent a required bounded field without it.
- No task depends on a concrete technology from the domain; SQLAlchemy remains confined to existing persistence adapters.
- No placeholders or vague deferred steps are used; each task names files, interfaces, tests and commands.
