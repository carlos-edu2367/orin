# Memory Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar as lacunas de RAG, compartilhamento RFC 303, composição RFC 601 e evidência da RFC 301 mantendo o adapter in-memory bounded e substituível.

**Architecture:** `agentos.context.sharing` mantém os contratos canônicos de Grant, referência, handoff e comandos de resolução. `agentos.memory` implementa o adapter in-memory que consulta Memory por referência autorizada e entrega candidatos ao ContextManager. Busca lexical declara capabilities e só recebe registros após prefilter de segurança; durabilidade continua atrás de uma porta de commit compatível com RFC 601.

**Tech Stack:** Python standard library, frozen/slotted dataclasses, Protocol, pytest e adapters in-memory existentes; nenhuma dependência concreta de banco, cache, broker, provider, API ou índice semântico.

## Global Constraints

- Context remains temporary and no Context operation writes or renews Memory.
- Every sensitive operation carries complete user/workspace/Agent/Execution/correlation/purpose/actor context.
- Ownership, Grant, classification, status, version and integrity are revalidated before content access.
- Search capabilities are truthful: the reference adapter exposes lexical search only.
- Shared Memory uses only canonical RFC 303 contracts and fails closed after revoke/expire/version change.
- State, revision, audit and outbox are one conceptual commit; `UNKNOWN` requires inspection.
- Production durability, PostgreSQL, migrations, Redis, Artifact Storage, embeddings and vector indexes remain out of scope.

---

### Task 1: Add truthful RAG capabilities and prefilter evidence

**Files:**
- Modify: `src/agentos/memory/models.py`
- Modify: `src/agentos/memory/ports.py`
- Modify: `src/agentos/memory/in_memory.py`
- Modify: `src/agentos/memory/context_compat.py`
- Test: `tests/unit/memory/test_memory_search_and_consolidation.py`
- Test: `tests/unit/memory/test_memory_context_compat.py`

- [x] Add a failing test for lexical-only capabilities, deterministic tie-breaking, citation fields and authorization-before-ranking behavior.
- [x] Run the focused tests and confirm the new assertions fail for the missing public contract.
- [x] Add `MemorySearchCapability`, `MemorySearchCapabilities`, bounded citation data and a `capabilities` property; expose only `LEXICAL` from the reference adapter.
- [x] Refactor search into metadata prefilter → authorization → bounded content materialization/ranking, without consuming grants during search; preserve status/expiry/version invalidation semantics.
- [x] Update `MemoryContextSource` to accept bounded query/filter input, preserve source/version/provenance/reasons and emit reference-first candidates without mutation.
- [x] Run focused tests and the existing Memory suite (`372 passed, 1 skipped` at the final verification gate).

### Task 2: Integrate canonical RFC 303 sharing

**Files:**
- Modify: `src/agentos/context/sharing.py`
- Create: `src/agentos/memory/sharing.py`
- Modify: `src/agentos/memory/__init__.py`
- Test: `tests/unit/context/test_context_sharing_contracts.py`
- Create: `tests/unit/memory/test_memory_sharing.py`
- Create: `tests/unit/integration/test_memory_sharing_boundaries.py`

- [x] Add failing contract tests for canonical commands, Grant lifecycle, allowed kinds/filters, source+target execution and idempotency.
- [x] Run the focused tests and confirm missing command/service behavior.
- [x] Add the RFC 303 command/result dataclasses to the canonical sharing module without introducing Memory-specific duplicates.
- [x] Implement `InMemoryMemorySharingService` using injected `MemoryManager`, canonical sharing models and a small lifecycle store; require source authorization, exact destination/purpose/classification, bounded budgets, version/integrity, single-use/multi-use consumption and closed failure after revoke/expire/invalidate/supersession.
- [x] Emit minimal share facts through the injected event recorder or a public callback, never content.
- [x] Run share and full Memory/Context/integration tests.

### Task 3: Make RFC 601 composition explicit

**Files:**
- Modify: `src/agentos/memory/ports.py`
- Modify: `src/agentos/memory/in_memory.py`
- Test: `tests/unit/memory/test_memory_store.py`
- Create: `tests/unit/memory/test_memory_persistence_composition.py`

- [x] Add failing tests for a transaction adapter contract, `UNKNOWN` commit outcome and required inspection before retry.
- [x] Run the focused tests and confirm the missing composition contract.
- [x] Add a narrow `MemoryTransactionalCommitPort` protocol/adapter boundary that maps Memory commit intent to RFC 601 outcomes without importing concrete persistence.
- [x] Keep `InMemoryMemoryStore` process-local and ensure no success receipt/event is returned for `UNKNOWN` or failed commit.
- [x] Run the focused persistence tests and full suite.

### Task 4: Fresh verification and requirement evidence

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-memory-completion-design.md`
- Modify: `docs/superpowers/plans/2026-08-06-memory-completion.md`

- [x] Run the full pytest suite, compileall, forbidden-technology scan, directed Memory/Context scans and `git diff --check`.
- [x] Inspect each match and record fresh counts, exit states, limitations and the final RFC matrix.
- [x] Re-read RFC 301/303/104/103/601 and verify each requirement against implementation and tests.
- [x] Commit only completion files plus implementation/tests belonging to Memory; preserve unrelated working-tree changes.

## Fresh evidence to be filled after the final verification gate

Final verification on 2026-08-06:

- `python -m pytest -q`: exit 0, `372 passed, 1 skipped in 3.75s`.
- `python -m compileall -q src tests`: exit 0.
- Forbidden-technology scan over `src/agentos/memory` and `src/agentos/context`: exit 1 from `rg` with no matches; wrapper reported `FORBIDDEN_SCAN: no matches`.
- Directed Memory/Context scans: matches are limited to public Memory APIs, tests, validation terms and authorization metadata; no Context mutation path or prohibited content sink was found.
- `git diff --check`: exit 0.
- Existing unrelated modifications remain unstaged and are excluded from this delivery.
