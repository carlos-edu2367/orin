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

- [x] Write tests for UTC/bounds, opaque refs, duplicate/cyclic DAG rejection, legal conditions/policies, stable bounded fingerprints, sensitive-data rejection and sanitized public errors.
- [x] Run focused RED, implement frozen/slotted dataclasses, bounded recursive ref validation, canonical fingerprinting and Kahn/topological validation, then verify GREEN.
- [x] Commit `37b5cbe` (`feat: add orchestrator plan contracts and security`).

### Task 2: Ports and deterministic in-memory coordination

**Files:**
- Create: `src/agentos/orchestrator/ports.py`
- Create: `src/agentos/orchestrator/in_memory.py`
- Test: `tests/unit/orchestrator/test_ports_and_adapters.py`

**Interfaces:**
- Produces `Orchestrator`, `ExecutionFactory`, `SchedulingPort`, `DispatchPort`, `SupervisionPort`, `PlanStorePort`, `PlanAccessContext`, `DispatchRequest`, `ScheduleTrigger`, `SupervisionSnapshot`, `PlanStoreResult`, `InMemoryPlanStore`, `InMemoryScheduling`, `InMemoryDispatch`, `InMemorySupervision`.

- [x] Write and run RED tests, implement Protocols, atomic reference records, immutable snapshots, bounded events and explicit commit-state fault injection.
- [x] Verify ownership, idempotency, inspection, materialization and minimum dispatch behavior.
- [x] Commit `915974d` (`feat: add orchestrator plan and coordination ports`).

### Task 3: Execution/Agent/Event compatibility bridges

**Files:**
- Create: `src/agentos/orchestrator/compat.py`
- Test: `tests/unit/orchestrator/test_compat.py`

**Interfaces:**
- Produces `ExecutionControlExecutionFactory`, `AgentResolverAdapter`, `AgentAdministrationAdapter`, `ExecutionCancellationAdapter` and minimal event/persistence translation helpers.

- [x] Write and run RED tests proving `ExecutionControl` creation, fixed config version and expected-version cancellation.
- [x] Implement public-port translation and `Indeterminate` → `UNKNOWN`; add deterministic execution identity by idempotency key.
- [x] Commit `da9c263` (`feat: bridge orchestrator to kernel ports`) and hardening in `d5b2709`.

### Task 4: Submit and plan evaluation service

**Files:**
- Create: `src/agentos/orchestrator/service.py`
- Test: `tests/unit/orchestrator/test_submit_and_evaluate.py`

**Interfaces:**
- Produces `OrchestratorService.submit`, `.evaluate`, `.request_cancel`, `.request_retry`.

- [x] Write and run RED tests for submit/evaluate, schedule windows, DAG readiness and Agent/config revalidation.
- [x] Implement authorization-first submission, immutable plans, dependency evaluation, one materialization per primary work/version, transactional event facts before dispatch and minimal dispatch.
- [x] Commit `e029d84` (`feat: implement orchestrator submission and evaluation`).

### Task 5: Cancellation, failure propagation and retry

**Files:**
- Modify: `src/agentos/orchestrator/service.py`
- Modify: `src/agentos/orchestrator/in_memory.py`
- Test: `tests/unit/orchestrator/test_cancel_retry_and_failure.py`

**Interfaces:**
- Extends `request_cancel`, `request_retry`, dependency failure policies and recovery commands.

- [x] Write and run RED tests for cancellation before/after materialization, terminality, failure policies, handler materialization, retry identity and schedule cancellation.
- [x] Implement expected-version Kernel cancellation, retry limits, pending reconciliation, explicit failure propagation and trigger cancellation after commit.
- [x] Commit `0d501ef` (`feat: add orchestrator cancellation and retry policies`) and `d5b2709` (`fix: harden orchestrator reconciliation and ownership`).

### Task 6: Public exports, integration and full requirement tests

**Files:**
- Create: `src/agentos/orchestrator/__init__.py`
- Test: `tests/unit/orchestrator/test_public_contracts.py`
- Test: `tests/unit/orchestrator/test_boundaries_and_requirements.py`
- Modify: `tests/unit/integration/test_kernel_boundaries.py` only if the existing generic boundary matrix must include `orchestrator`.

**Interfaces:**
- Produces stable package exports and regression coverage for ownership, events, persistence atomicity, cross-domain imports, PostgreSQL skip rationale and all public outcomes.

- [x] Write public import, event/outbox, supervision-observational, cross-domain and forbidden-token tests.
- [x] Export stable names and verify the complete Orchestrator suite (`32 passed`).
- [x] Commit `15054e8` (`test: cover orchestrator normative requirements`).

### Task 7: Audit and final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-orchestrator-design.md`
- Modify: `docs/superpowers/plans/2026-08-06-orchestrator.md`

- [x] Auditar RFCs/ADRs: RFC 050/060 (bounds/ownership), 101/102 (Kernel/version/terminal), 103 (outbox), 104 (refs), 201 (Agent), 202 (Orchestrator), 203 (fora), 601 (persistência canônica), ADRs 002/009/012/013 (tecnologia atrás de portas).
- [x] Executar verificações finais: `280 passed, 1 skipped`, compileall, scans sem matches, diff check e status.
- [x] Registrar skip PostgreSQL, limitações, commits e arquivos na spec.
- [x] Review independente retornou Critical/Important; regressões foram adicionadas em `test_review_regressions.py` e corrigidas em `d5b2709`.
- [x] Esta atualização documental completa a auditoria; permanece deliberadamente sem incluir RFC 203, scheduler físico, pool, broker, lease ou storage tecnológico.
