# RFC 406 Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar o pacote `agentos.capabilities` com contratos bounded, registry versionado, execução determinística, portas RFC 401/102/601 e evidência de segurança do RFC 406.

**Architecture:** Modelos imutáveis e um programa tipado de steps ficam separados de portas públicas. O serviço coordena Registry, `ExecutionControl`, Tool/Child ports e state/checkpoint port; nunca implementa Tool Runtime, persistência concreta ou uma segunda máquina de Execution. Um scheduler topológico puro decide steps prontos e aplica limite de paralelismo.

**Tech Stack:** Python 3.13, dataclasses frozen/slots, `StrEnum`, `Protocol`, pytest, portas RFC 102/601 existentes; sem novas dependências.

## Global Constraints

- Toda operação de execução carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`.
- Versão publicada é imutável; nomes e IDs não concedem autorização.
- Toda Tool passa por `CapabilityToolPort`; nenhum adapter concreto é importado ou instanciado.
- `ExecutionControl` é a autoridade única das transições canônicas.
- Checkpoint/event/outcome contém referências, não segredo, handle, objeto, conteúdo integral ou código executável.
- UNKNOWN não permite retry cego; terminal não é reaberto; child não herda grants, segredos ou Context integral.
- Alterações preexistentes do worktree não serão stageadas.

---

### Task 1: Modelos públicos e programa imutável

**Files:**
- Create: `src/agentos/capabilities/models.py`
- Create: `src/agentos/capabilities/__init__.py`
- Test: `tests/unit/capabilities/test_contracts.py`

**Interfaces:**
- Produces `CapabilityOperationContext`, `CapabilityRegistryOperationContext`, `CapabilityRef`, `CapabilityDescriptor`, `CapabilityLimits`, `CapabilityStep`, `CapabilityProgram`, `CapabilityRun`, `CapabilityCheckpoint`, outcomes, usage/effect/retry/permission models and event enum.

- [ ] Step 1: Write tests for frozen models, complete contexts, exact versions/status, positive limits, bounded structured values, no secret-like checkpoint fields, and canonical state mapping.
- [ ] Step 2: Run `python -m pytest -q tests/unit/capabilities/test_contracts.py`; confirm failure because the package is absent.
- [ ] Step 3: Implement only the immutable bounded models, validation and `execution_state_for_capability_state`.
- [ ] Step 4: Re-run the focused test and then `python -m pytest -q tests/unit/capabilities/test_contracts.py` until green.

### Task 2: Registry versioning and bootstrap

**Files:**
- Create: `src/agentos/capabilities/registry.py`
- Create: `tests/unit/capabilities/test_registry.py`

**Interfaces:**
- Consumes models.
- Produces `CapabilityRegistry` protocol, `InMemoryCapabilityRegistry`, `register`, `resolve`, `list`, `disable`, idempotency results and bootstrap allowlist behavior.

- [ ] Step 1: Write tests for immutable version conflict, authorized resolve/list, disable expected status, bootstrap only when empty with `SYSTEM_BOOTSTRAP`, and no authorization by known ID.
- [ ] Step 2: Run the focused registry tests and confirm the expected missing-package failure.
- [ ] Step 3: Implement the registry with copied immutable descriptors/programs, scoped idempotency fingerprints, status filtering and minimal audit records.
- [ ] Step 4: Run focused registry tests green and check reprs contain no program payload.

### Task 3: Ports, state facade and deterministic scheduler

**Files:**
- Create: `src/agentos/capabilities/ports.py`
- Create: `src/agentos/capabilities/state.py`
- Create: `src/agentos/capabilities/scheduler.py`
- Create: `tests/unit/capabilities/test_scheduler_and_boundaries.py`

**Interfaces:**
- Produces `CapabilityToolPort`, `ChildExecutionPort`, `CapabilityStatePort`, `CapabilityClock`, `ReadyStepScheduler`, `StepAuthorizationPort`, `ToolInvocationOutcome` and child command/query records.

- [ ] Step 1: Write tests for explicit dependencies, cycle/duplicate/binding rejection, deterministic ordering, maximum parallelism, no implicit fan-out, and structural boundary scans.
- [ ] Step 2: Run focused tests and verify RED.
- [ ] Step 3: Implement pure scheduler, protocols and `InMemoryCapabilityState` with allowlisted snapshots and outbox records.
- [ ] Step 4: Run focused tests green and inspect imports for concrete technology/bypass terms.

### Task 4: Service start/run/resume/inspect/cancel

**Files:**
- Create: `src/agentos/capabilities/service.py`
- Modify: `src/agentos/capabilities/__init__.py`
- Test: `tests/unit/capabilities/test_service_lifecycle.py`

**Interfaces:**
- Consumes `ExecutionControl`, `CapabilityRegistry`, the ports from Task 3 and an injected clock.
- Produces `CapabilityService.start`, `.run`, `.resume`, `.request_cancel`, `.inspect`, child context derivation and canonical Execution mapping.

- [ ] Step 1: Write tests for queued start, duplicate start, expected state-version conflict, acquire/elegibility, tool invocation through port, waiting mappings, child pause/resume, terminal immutability, inspect authorization and cancellation propagation.
- [ ] Step 2: Run focused lifecycle tests and verify RED.
- [ ] Step 3: Implement minimal service command flow, committing state before Execution transition when required, and map only through `ExecutionControl`.
- [ ] Step 4: Run lifecycle tests green and add regression tests for stale writer/late result/terminal retry.

### Task 5: Authorization, limits, retry, UNKNOWN and compensation

**Files:**
- Modify: `src/agentos/capabilities/service.py`
- Modify: `src/agentos/capabilities/models.py`
- Test: `tests/unit/capabilities/test_security_limits_retry_compensation.py`

**Interfaces:**
- Consumes descriptor permissions, tool outcome metadata, child depth and state usage.
- Produces step-level intersection checks, monotonic budgets, deterministic idempotency keys, reconciliation gate and explicit ordered compensation outcomes.

- [ ] Step 1: Write tests for denied Tool despite broad Capability grants, no escalation from untrusted arguments, all maximum limits, safe retry only, UNKNOWN requiring reconcile, ordered compensation/failure visibility and cancellation budget.
- [ ] Step 2: Run focused tests and verify RED.
- [ ] Step 3: Implement authorization intersection, limit accounting, retry classifier, reconcile path and compensation runner using only ports.
- [ ] Step 4: Run focused tests green and run the package boundary scan.

### Task 6: Integration/regression evidence and documentation

**Files:**
- Create: `tests/integration/capabilities/test_capability_execution_boundary.py`
- Create: `docs/superpowers/2026-08-07-rfc-406-capabilities-requirement-matrix.md`
- Create: `docs/superpowers/2026-08-07-rfc-406-capabilities-closeout.md`
- Modify: `docs/superpowers/2026-08-07-rfc-406-capabilities-next-session-prompt.md`

- [ ] Step 1: Write end-to-end boundary tests proving Capability -> ExecutionControl -> Tool port/child port -> state/outbox, plus optional PostgreSQL skip test when DSN is absent.
- [ ] Step 2: Run the new integration tests RED where dependencies are absent, recording explicit skips only for optional DSN/runtime.
- [ ] Step 3: Implement only test fixtures/verification helpers needed to prove the existing ports; do not add production adapters.
- [ ] Step 4: Run focused integration tests green.
- [ ] Step 5: Execute `python -m pytest -q`, `python -m compileall -q src tests`, `git diff --check`, `git status --short --branch` and the required `rg` scans.
- [ ] Step 6: Fill the matrix and closeout with actual output, independent review findings, commits, skips and the documented next gate; scan for TODO/placeholders and preserve unrelated worktree changes.

## Self-review coverage

Tasks 1–2 cover registry, models, contexts and versioning; Task 3 covers typed programs, scheduler, boundaries and persistence façade; Task 4 covers lifecycle, canonical Execution mapping, child operations and events; Task 5 covers authorization, limits, retry/UNKNOWN, compensation and cancellation; Task 6 covers integration, outbox evidence, regression, documentation and final commands. No production code is planned outside a failing-test cycle.
