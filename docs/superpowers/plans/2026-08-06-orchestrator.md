# Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o plano de controle RFC 202 em contratos públicos, adapters in-memory, bridges de compatibilidade e testes normativos sem dependências concretas.

**Architecture:** Modelos imutáveis validam planos/DAGs e commands; `OrchestratorService` coordena `PlanStorePort`, `AgentRegistry`, `ExecutionFactory`, scheduling, dispatch e supervision. `InMemoryPlanStore` fornece atomicidade idempotente de plano + outbox para referência; `compat.py` traduz Execution/Agent/Event/Persistence sem duplicar suas máquinas ou portas.

**Tech Stack:** Python 3.13, dataclasses, Protocols, hashlib/json, pytest; somente APIs públicas de `agentos.execution`, `agentos.agents`, `agentos.events` e `agentos.persistence`.

## Global Constraints

- Não importar FastAPI, HTTP, SDK de Provider, SQLAlchemy, Alembic, Redis, filesystem, broker, fila, scheduler concreto ou worker.
- Não criar segunda máquina de estados de Execution nem persistência paralela.
- `workspace_id=None` significa escopo estritamente do usuário.
- Toda decisão revalida ownership, Agent/configuração, purpose e classificação.
- Dispatch não recebe prompt, Context, Memory, credencial, resposta, histórico ou payload proprietário.
- `UNKNOWN` exige `inspect_commit`; terminais nunca são reabertos; retry usa nova Execution e nova chave.
- Eventos são mínimos, sanitizados, bounded e confirmados na outbox antes de qualquer publicação.
- Teste novo deve observar RED antes do código GREEN; commits devem incluir somente os arquivos do task.

### Task 1: Contratos imutáveis e segurança de plano

**Files:**
- Create: `src/agentos/orchestrator/models.py`
- Create: `src/agentos/orchestrator/security.py`
- Test: `tests/unit/orchestrator/test_models_security.py`

**Interfaces:**
- Produces `OpaqueReference`, `OrchestrationPlanDraft`, `OrchestrationPlan`, `PlannedWork`, `DependencyEdge`, `OrchestrationPolicy`, `ScheduleConstraint`, intents, commands, receipts/outcomes and `validate_plan`/`fingerprint`.

- [ ] Write tests for UTC/bounds, opaque refs, duplicate/cyclic DAG rejection, legal conditions/policies, stable bounded fingerprints, sensitive-data rejection and sanitized public errors.
- [ ] Run `python -m pytest -q tests/unit/orchestrator/test_models_security.py`; observe failure because package does not exist.
- [ ] Implement frozen/slotted dataclasses, enums, bounded recursive ref validation, canonical fingerprinting and Kahn/topological validation.
- [ ] Re-run the focused tests, then `python -m compileall -q src/agentos/orchestrator`.
- [ ] Commit `feat: add orchestrator plan contracts and security`.

### Task 2: Ports and deterministic in-memory coordination

**Files:**
- Create: `src/agentos/orchestrator/ports.py`
- Create: `src/agentos/orchestrator/in_memory.py`
- Test: `tests/unit/orchestrator/test_ports_and_adapters.py`

**Interfaces:**
- Produces `Orchestrator`, `ExecutionFactory`, `SchedulingPort`, `DispatchPort`, `SupervisionPort`, `PlanStorePort`, `PlanAccessContext`, `DispatchRequest`, `ScheduleTrigger`, `SupervisionSnapshot`, `PlanStoreResult`, `InMemoryPlanStore`, `InMemoryScheduling`, `InMemoryDispatch`, `InMemorySupervision`.

- [ ] Write tests for authorized get/list, cross-owner non-disclosure, idempotent submit, divergent fingerprint, expected-version conflicts, `COMMITTED`/`NOT_COMMITTED`/`UNKNOWN` inspection, single materialization and minimum dispatch shape.
- [ ] Run the focused tests and observe missing Protocols/adapters.
- [ ] Implement port-only dataclasses and in-memory records; use atomic dictionary updates, immutable snapshots, bounded events and explicit fault injection for commit states.
- [ ] Re-run focused tests and verify no concrete infrastructure term appears in the package.
- [ ] Commit `feat: add orchestrator plan and coordination ports`.

### Task 3: Execution/Agent/Event compatibility bridges

**Files:**
- Create: `src/agentos/orchestrator/compat.py`
- Test: `tests/unit/orchestrator/test_compat.py`

**Interfaces:**
- Produces `ExecutionControlExecutionFactory`, `AgentResolverAdapter`, `AgentAdministrationAdapter`, `ExecutionCancellationAdapter` and minimal event/persistence translation helpers.

- [ ] Write tests with real in-memory Kernel/Agent ports proving creation goes through `ExecutionControl`, config version is fixed, cancellation uses expected version, and no direct EventBus/persistence mutation occurs.
- [ ] Run focused tests to observe missing bridge.
- [ ] Implement translation using only existing public Protocols and legacy/canonical event types; translate `Indeterminate` to `UNKNOWN` without claiming success.
- [ ] Re-run focused tests and boundary import checks.
- [ ] Commit `feat: bridge orchestrator to kernel ports`.

### Task 4: Submit and plan evaluation service

**Files:**
- Create: `src/agentos/orchestrator/service.py`
- Test: `tests/unit/orchestrator/test_submit_and_evaluate.py`

**Interfaces:**
- Produces `OrchestratorService.submit`, `.evaluate`, `.request_cancel`, `.request_retry`.

- [ ] Write tests for `RunAgentTask`, `ExecutePlan`, `ContinueExecution`, admin delegation, ready/not-ready dependencies, `not_before`, expiration, bounded parallelism and Agent/config revalidation.
- [ ] Run focused tests to observe missing service.
- [ ] Implement authorization-first submit, immutable plan creation, dependency evaluation, one materialization per work/version, transactional event facts before dispatch, and no materialization on rejected/unknown commits.
- [ ] Re-run focused tests; preserve `QUEUED`/terminal semantics and do not dispatch sensitive payloads.
- [ ] Commit `feat: implement orchestrator submission and evaluation`.

### Task 5: Cancellation, failure propagation and retry

**Files:**
- Modify: `src/agentos/orchestrator/service.py`
- Modify: `src/agentos/orchestrator/in_memory.py`
- Test: `tests/unit/orchestrator/test_cancel_retry_and_failure.py`

**Interfaces:**
- Extends `request_cancel`, `request_retry`, dependency failure policies and recovery commands.

- [ ] Write tests for cancel-before/after materialization, terminal immutability, version conflicts, DO_NOT_MATERIALIZE, CANCEL_RELATED, failure handler, new retry identity/key and no timeout-to-cancel conversion.
- [ ] Run focused tests to observe each missing behavior.
- [ ] Implement explicit policy dispatch, expected-version Kernel commands, relation/cause refs and idempotent cancellation receipts.
- [ ] Re-run focused tests and inspect event payloads for sanitization.
- [ ] Commit `feat: add orchestrator cancellation and retry policies`.

### Task 6: Public exports, integration and full requirement tests

**Files:**
- Create: `src/agentos/orchestrator/__init__.py`
- Test: `tests/unit/orchestrator/test_public_contracts.py`
- Test: `tests/unit/orchestrator/test_boundaries_and_requirements.py`
- Modify: `tests/unit/integration/test_kernel_boundaries.py` only if the existing generic boundary matrix must include `orchestrator`.

**Interfaces:**
- Produces stable package exports and regression coverage for ownership, events, persistence atomicity, cross-domain imports, PostgreSQL skip rationale and all public outcomes.

- [ ] Write public import, event/outbox, supervision-observational, cross-domain dependency and forbidden-token tests.
- [ ] Run focused tests to observe missing exports/coverage.
- [ ] Export only stable names; keep adapters unexported unless reference tests need them; update boundary matrix to scan Orchestrator.
- [ ] Run the complete orchestrator suite and then the complete repository suite.
- [ ] Commit `test: cover orchestrator normative requirements`.

### Task 7: Audit and final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-orchestrator-design.md`
- Modify: `docs/superpowers/plans/2026-08-06-orchestrator.md`

- [ ] Review every requirement in RFCs 050, 060, 101, 102, 103, 104, 201, 202, 203 and 601 plus ADRs 002, 009, 012 and 013 against code/tests.
- [ ] Run `python -m pytest -q`, `python -m compileall -q src tests`, the exact forbidden-token scan, a transversal boundary scan, `git diff --check` and `git status --short --branch`.
- [ ] Record fresh test counts, PostgreSQL skip reason, limitations, commits and changed files in both docs.
- [ ] Request technical review from a reviewer subagent, fix Critical/Important findings with regression tests, and rerun all final checks.
- [ ] Commit `docs: record orchestrator audit and verification`.
