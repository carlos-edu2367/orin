# AgentOS Kernel Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Auditar e fechar formalmente `Execution`, `Events`, `Context`, `Providers` e `Runtime` contra as RFCs 050, 060, 101–104, 201, 501, 502 e 601, corrigindo apenas lacunas dentro do escopo.

**Architecture:** Manter as portas públicas atuais e os adapters em memória como referências de teste. Corrigir primeiro os contratos e a atomicidade conceitual de Execution/outbox, depois entrega e autorização de Events, Context, Provider/Model Catalog, Runtime e a prova transversal Agent→Kernel.

**Tech Stack:** Python 3.13+, dataclasses congeladas, `Protocol`, `StrEnum`, `pytest`, `compileall`, `rg`; sem dependências de infraestrutura.

## Global Constraints

- Não implementar Orchestrator, Multi-agent, Memory, Blackboard, Tools, Capabilities, Artifact Storage, Workspaces, API, SSE, workers ou Scheduler.
- Não criar PostgreSQL, SQLAlchemy, Alembic, Redis, Kafka, RabbitMQ, FastAPI, HTTP client, filesystem ou SDK de Provider.
- `ExecutionControl` é a única porta mutante do Runtime.
- `Events` usa o envelope canônico; adapters legados precisam ser explícitos e testados.
- Nenhuma lacuna de produção será mascarada por fake; adapters em memória serão identificados como tais.
- RFC 201 será preservada, exceto por regressão demonstrada na integração.

---

### Task 1: Fechar Execution, escopo de idempotência e transação conceitual

**Files:**
- Modify: `src/agentos/execution/control.py`
- Modify: `src/agentos/execution/in_memory.py`
- Modify: `src/agentos/execution/events.py`
- Test: `tests/unit/execution/test_execution_control.py`
- Test: `tests/unit/execution/test_in_memory_persistence.py`
- Test: `tests/unit/execution/test_event_publisher_integration.py`

**Interfaces:**
- Consumes: `ExecutionCommandContext`, `TransactionRequest`, `ExecutionControl`.
- Produces: scoped idempotency, rejected forged transactions, unchanged public command results, and safe legacy-to-canonical outbox adapter behavior.

- [x] Write tests for same idempotency key in different execution/workspace scopes, forged transaction context, and two workers racing on one version.
- [x] Run focused tests and observe RED for the new assertions.
- [x] Include all ownership fields and execution identity in the in-memory idempotency key; validate transaction context, source execution, expected version, event identity and event scope before mutation.
- [x] Bound/sanitize legacy execution event payloads without exposing secrets; retain `events.compat` as the sole canonical adapter.
- [x] Run execution tests and the execution/event integration tests GREEN.

### Task 2: Fechar Events, outbox cursor, dedupe e consultas autorizadas

**Files:**
- Modify: `src/agentos/events/models.py`
- Modify: `src/agentos/events/in_memory.py`
- Modify: `src/agentos/events/compat.py`
- Test: `tests/unit/events/test_event_contracts.py`
- Test: `tests/unit/events/test_outbox_publisher.py`
- Test: `tests/unit/events/test_event_archive.py`
- Test: `tests/unit/events/test_event_bus.py`

**Interfaces:**
- Consumes: `EventEnvelope`, `PublishOutboxBatch`, `AuthorizedEventQuery`, `ReplayRequest`.
- Produces: canonical bounded envelope, cursor that does not skip unresolved outbox entries, per-consumer dedupe and ownership-narrow archive behavior.

- [x] Write tests for invalid event type/payload, pending outbox before a committed record, cross-agent query, unknown replay ID, delayed event, duplicate and sequence gap.
- [x] Run focused tests and observe RED.
- [x] Validate canonical event naming and preserve existing bounded payload security; make outbox position advance only through the last contiguous resolved record.
- [x] Restrict archive query/replay by all applicable context fields and return sanitized authorization behavior for unknown IDs.
- [x] Run the full Events suite GREEN.

### Task 3: Fechar Context e atualização por turno

**Files:**
- Modify: `src/agentos/context/models.py`
- Modify: `src/agentos/context/service.py`
- Test: `tests/unit/context/test_context_contracts.py`
- Test: `tests/unit/context/test_context_pipeline.py`
- Test: `tests/unit/context/test_context_lifecycle.py`

**Interfaces:**
- Consumes: `ContextSource`, recorder, policy, clock and cancellation ports.
- Produces: deterministic authorized snapshots/manifests, explicit required/optional degradation and no implicit Memory write.

- [x] Write tests for provenance/enum validation, reserved budget, required item failure after sanitization, turn replay conflict and source-kind provenance.
- [x] Run focused tests and observe RED.
- [x] Add only contract validations and deterministic bookkeeping needed by RFC 104; do not add storage or history loading.
- [x] Run the full Context suite GREEN and verify `repr`/manifest never contains sensitive inline content.

### Task 4: Fechar Provider/Model Catalog constraints, cost, status e fallback

**Files:**
- Modify: `src/agentos/providers/models.py`
- Modify: `src/agentos/providers/catalog.py`
- Modify: `src/agentos/providers/resolver.py`
- Modify: `src/agentos/providers/provider.py`
- Test: `tests/unit/providers/test_model_resolver.py`
- Test: `tests/unit/providers/test_catalog_store.py`
- Test: `tests/unit/providers/test_provider_api.py`
- Test: `tests/unit/providers/test_provider_model_contracts.py`

**Interfaces:**
- Consumes: public descriptors, profile revisions, `ModelRequirements`, `ProviderInvocationRequest` and canonical outcomes.
- Produces: hard constraints before score, deterministic profile-aware selection, real cost ceiling, immutable snapshot revalidation, explicit bounded fallback and distinct `INDETERMINATE` outcome.

- [x] Write tests for profile required constraints/preferences, allowed purposes, actual estimated cost, provider/model binding mismatch, capability/image/tool validation, indeterminate outcome and fallback category/attempt/budget preservation.
- [x] Run focused tests and observe RED.
- [x] Implement minimal public validation and resolver changes; never add SDK, credentials, HTTP or provider-specific branching.
- [x] If `FallbackMode.POLICY` lacks a materialized policy port, keep it explicitly rejected/documented rather than discovering candidates implicitly.
- [x] Run the full Providers suite GREEN and run the forbidden dependency scan for Provider code.

### Task 5: Fechar Runtime accounting, timeout e outcomes canônicos

**Files:**
- Modify: `src/agentos/runtime/models.py`
- Modify: `src/agentos/runtime/ports.py`
- Modify: `src/agentos/runtime/service.py`
- Modify: `src/agentos/providers/compat.py`
- Test: `tests/unit/runtime/test_runtime_loop.py`
- Test: `tests/unit/runtime/test_runtime_controls.py`
- Test: `tests/unit/runtime/test_runtime_limits_and_recovery.py`
- Test: `tests/unit/runtime/test_runtime_contracts.py`

**Interfaces:**
- Consumes: `ExecutionControl`, Context/Resolver/Provider/Action/Checkpoint/Budget ports.
- Produces: monotonic accounting for every external outcome, distinct cancellation/timeout/indeterminate behavior, safe recovery and no EventBus/persistence dependency.

- [x] Write tests for usage on user-input and provider-cancelled outcomes, canonical indeterminate mapping, post-effect deadline, action accounting and terminal re-execution.
- [x] Run focused tests and observe RED.
- [x] Add the smallest outcome/translation changes and persist consumed usage through `ExecutionControl.commit` before returning a terminal/waiting result.
- [x] Keep all provider/context/event activity behind public ports and preserve recursive tool round-trip limits.
- [x] Run all Runtime tests GREEN.

### Task 6: Provar integração transversal com Agent RFC 201

**Files:**
- Modify: `tests/unit/agents/test_agent_compat.py`
- Modify: `tests/unit/runtime/test_runtime_security.py`
- Create: `tests/unit/integration/test_kernel_boundaries.py`

**Interfaces:**
- Consumes: Agent registry/compatibility ports and all five kernel public contracts.
- Produces: evidence for `agent_config_version`, ownership/correlation/purpose continuity, suspended/archived rejection, Context ephemeral semantics and no concrete dependency leakage.

- [x] Write tests for authorized config snapshot, suspended Agent rejection before execution, context-not-memory, Runtime-not-EventBus, and no concrete persistence in Context/Provider.
- [x] Run the integration tests and observe RED where behavior is missing.
- [x] Add only compatibility adapters or boundary assertions; do not change Agent behavior without a demonstrated regression.
- [x] Run integration and all existing Agent tests GREEN.

### Task 7: Atualizar planos, auditar RFCs e executar verificação final

**Files:**
- Modify: `docs/superpowers/plans/2026-08-06-execution-lifecycle.md`
- Modify: `docs/superpowers/plans/2026-08-06-runtime.md`
- Modify: `docs/superpowers/plans/2026-08-06-context-pipeline.md`
- Modify: `docs/superpowers/plans/2026-08-06-event-system.md`
- Modify: `docs/superpowers/plans/2026-08-06-provider-model.md`
- Modify: `docs/superpowers/plans/2026-08-06-agent.md`
- Modify: this closeout spec and plan with evidence/limitations

- [x] Mark prior steps `[x]` only where implementation and tests exist; append explicit limitation notes for production infrastructure and unimplemented policy fallback.
- [x] Perform requirement-by-requirement review against RFC 050, 060, 101, 102, 103, 104, 201, 501, 502 and 601.
- [x] Run exactly:
  - `python -m pytest -q`
  - `python -m compileall -q src tests`
  - `rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit" src/agentos/execution src/agentos/runtime src/agentos/context src/agentos/events src/agentos/providers`
  - `git diff --check`
  - `git status --short --branch`
- [x] Record fresh output and do not claim complete while any critical test, boundary or plan truthfulness requirement remains unresolved.

## Evidence and final status (2026-08-06)

- Inventory closed within the reference/in-memory scope: Execution, Events, Context, Providers and Runtime are complete at the public-contract and boundary-test level; production persistence, broker, distributed lease, worker/scheduler and physical retention remain explicitly out of scope.
- Agent RFC 201 remains unchanged and complete in its original scope; integration evidence covers ownership/correlation/purpose continuity and boundary isolation.
- Fresh verification: `python -m pytest -q` passed with `326 passed, 1 skipped`; `python -m compileall -q src tests` exited 0; the required forbidden-dependency scan exited 1 with no matches (the expected zero-match result); `git diff --check` exited 0.
- Final closeout regressions also cover accumulated fallback cost, tool approval/limits, and unconfirmed action-failure commits. No concrete infrastructure was created. The working tree remains intentionally dirty with this work and pre-existing user artifacts; no commit was created by this closeout.
