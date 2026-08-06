# Context Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic RFC 104 Context domain in `agentos.context` and connect it to the existing Runtime through a reference-only compatibility adapter.

**Architecture:** `agentos.context.models` owns the public data contracts and categorized errors; `agentos.context.ports` owns all injected boundaries; `ContextManagerService` performs validation, sanitization, deterministic selection, accounting, manifest recording and ephemeral lifecycle. `RuntimeContextManagerAdapter` translates the existing Runtime request/snapshot shapes without moving concrete Context sources or storage into Runtime.

**Tech Stack:** Python 3.13+, frozen/slotted dataclasses, `Protocol`, `StrEnum`, `pytest`; zero runtime dependencies and no concrete persistence, Memory, Artifact, Provider or tokenizer implementation.

## Global Constraints

- Context is temporary and never becomes Memory implicitly.
- Every sensitive source/recorder request carries `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` and `purpose`.
- Ownership and classification are revalidated for candidates and manifest references; knowing a reference does not grant access.
- Snapshots and manifests contain only sanitized inline data and opaque references, never secrets, credentials or complete proprietary payloads.
- No source, Provider, Memory, Artifact Storage, filesystem, search, Event Bus or persistence adapter is implemented in this change.
- The Runtime keeps its current lightweight Context compatibility surface and remains framework/infrastructure independent.
- Production behavior is built test-first: each behavior has a failing test run before its implementation.

---

### Task 1: Define canonical Context contracts and public ports

**Files:**
- Create: `src/agentos/context/models.py`
- Create: `src/agentos/context/ports.py`
- Create: `src/agentos/context/__init__.py`
- Create: `tests/unit/context/test_context_contracts.py`

**Interfaces:**
- Consumes: `ExecutionId`, `UserId`, `WorkspaceId`, `CorrelationId`, `DataClassification` and existing Runtime reference conventions.
- Produces: `ContextOperationContext`, `ContextAssemblyRequest`, `ContextBudget`, `ContextCandidate`, `ContextItem`, `ContextSnapshot`, `ContextManifest`, `ContextTurnUpdate`, `TokenAccounting`, `Provenance`, enums, categorized `ContextError`, and the canonical Protocols.

- [ ] **Step 1: Write failing contract tests**

```python
def test_assembly_request_requires_complete_sensitive_scope():
    with pytest.raises(ValueError):
        ContextAssemblyRequest(
            context=ContextOperationContext(
                user_id="user-1", workspace_id="workspace-1", agent_id="agent-1",
                execution_id="execution-1", correlation_id="correlation-1", purpose="",
            ),
            turn=1,
            task=TaskSnapshot(reference="task:1", content="do work"),
            budget=ContextBudget(maximum_input_tokens=100),
        )


def test_context_budget_rejects_negative_reservations():
    with pytest.raises(ValueError):
        ContextBudget(maximum_input_tokens=100, reserved_control_tokens=-1)


def test_snapshot_and_manifest_are_reference_and_sanitized_shapes():
    snapshot = ContextSnapshot(
        execution_id="execution-1", turn=1, items=(),
        token_accounting=TokenAccounting(), manifest_ref="manifest:1",
        assembled_at=NOW,
    )
    assert snapshot.manifest_ref == "manifest:1"
    assert "secret" not in repr(snapshot).lower()
```

- [ ] **Step 2: Run the contract tests and verify the expected RED failure**

Run: `python -m pytest tests/unit/context/test_context_contracts.py -q`

Expected: FAIL because `agentos.context` and its public types do not exist.

- [ ] **Step 3: Implement the minimal immutable contracts**

Define non-blank opaque references, timezone-aware instants, positive turns, non-negative token/cost values and tuple-based collections. Include `ContextItemKind`, `ContextPriority`, `OverflowPolicy`, `ContextDisposition`, `ContextErrorCategory` and `Retryability`. Model `ContentReference` separately from inline strings. Define `ContextOperationContext` with the six sensitive fields plus `purpose`; define `AuthorizedContextQuery` with the complete context, cutoff, classification ceiling and allowed kinds.

Implement Protocols with these signatures:

```python
class ContextSource(Protocol):
    source_kind: SourceKind
    def collect(self, query: AuthorizedContextQuery) -> tuple[ContextCandidate, ...]: ...

class ContextManifestRecorder(Protocol):
    def record(self, manifest: ContextManifest) -> ContextManifestReference: ...
    def load(self, reference: ContextManifestReference, ownership: OwnershipScope) -> ContextManifest: ...

class ContextManager(Protocol):
    def assemble(self, request: ContextAssemblyRequest) -> ContextSnapshot: ...
    def apply_turn(self, request: ContextTurnUpdate) -> ContextSnapshot: ...
    def finalize(self, execution_id: ExecutionId, disposition: ContextDisposition) -> None: ...
```

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run: `python -m pytest tests/unit/context/test_context_contracts.py -q`

Expected: PASS with no warnings.

---

### Task 2: Implement validation, sanitization and deterministic candidate preparation

**Files:**
- Create: `src/agentos/context/service.py`
- Create: `tests/unit/context/test_context_pipeline.py`

**Interfaces:**
- Consumes: Task 1 models and `ContextSource`, `ContextManifestRecorder`, `ContextPolicy`, `ContextClock`, `CancellationSignal` Protocols.
- Produces: `ContextManagerService(sources, recorder, policy, clock, cancellation=None)` implementing canonical `assemble`, `apply_turn` and `finalize`.

- [ ] **Step 1: Write failing validation and sanitization tests**

```python
def test_assemble_sends_complete_scope_to_every_source(context_fixture):
    context_fixture.manager.assemble(context_fixture.request)
    query = context_fixture.source.queries[0]
    assert query.context.user_id == "user-1"
    assert query.context.workspace_id == "workspace-1"
    assert query.context.agent_id == "agent-1"
    assert query.context.execution_id == "execution-1"
    assert query.context.correlation_id == "correlation-1"
    assert query.context.purpose == "context-test"


def test_optional_secret_candidate_is_excluded_without_leaking_value(context_fixture):
    context_fixture.source.candidates = (
        candidate(kind=ContextItemKind.MESSAGE, content="api_key=super-secret-value"),
    )
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    assert all("super-secret-value" not in repr(item) for item in snapshot.items)
    manifest = context_fixture.recorder.manifests[-1]
    assert any(record.reason == "SANITIZATION_FAILED" for record in manifest.excluded)


def test_cross_workspace_candidate_is_rejected(context_fixture):
    context_fixture.source.candidates = (candidate(workspace_id="other-workspace"),)
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.OWNERSHIP
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/unit/context/test_context_pipeline.py -q`

Expected: FAIL because `ContextManagerService` and the in-memory test ports do not exist.

- [ ] **Step 3: Implement scope validation and candidate preparation**

Validate request scope, task reference/content, budget, model requirements reference and prior manifest ownership before any source call. Build an authorized query for each source with the fixed cutoff and classification ceiling. Revalidate every returned candidate's user/workspace/agent/execution ownership, provenance reference, classification and non-negative estimate. Reject cross-scope candidates with a sanitized `ContextError`.

Sanitize inline content before it enters any returned object. Redact bearer tokens and key/value secrets using bounded regular expressions, reject malformed control structures, and mark all non-system/agent/task/control content as untrusted data. Never include the original value in an error, manifest reason, `repr`, or diagnostic reference. Add a transformation record when redaction or data delimiting occurs. Content references remain opaque and receive no authorization from their spelling.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/unit/context/test_context_pipeline.py -q`

Expected: PASS.

---

### Task 3: Implement ordering, budget allocation, overflow and manifest recording

**Files:**
- Modify: `src/agentos/context/service.py`
- Modify: `tests/unit/context/test_context_pipeline.py`

**Interfaces:**
- Consumes: prepared candidates from Task 2 and fixed policy/tokenizer snapshots.
- Produces: deterministic snapshots and manifests with included/excluded/transformation records and complete token accounting.

- [ ] **Step 1: Write failing selection and budget tests**

```python
def test_required_items_are_preserved_before_optional_items(context_fixture):
    context_fixture.request = replace(
        context_fixture.request,
        budget=ContextBudget(maximum_input_tokens=5, overflow_policy=OverflowPolicy.EXCLUDE_OPTIONAL),
    )
    context_fixture.source.candidates = (
        candidate(kind=ContextItemKind.MESSAGE, priority=ContextPriority.LOW, tokens=4),
        candidate(kind=ContextItemKind.CONTROL_STATE, priority=ContextPriority.REQUIRED, tokens=1),
    )
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    assert [item.kind for item in snapshot.items] == [ContextItemKind.CONTROL_STATE]


def test_required_item_that_cannot_fit_raises_budget_error(context_fixture):
    context_fixture.request = replace(
        context_fixture.request,
        budget=ContextBudget(maximum_input_tokens=2),
    )
    context_fixture.source.candidates = (candidate(priority=ContextPriority.REQUIRED, tokens=3),)
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.BUDGET


def test_manifest_has_only_references_and_categorical_reasons(context_fixture):
    snapshot = context_fixture.manager.assemble(context_fixture.request)
    manifest = context_fixture.recorder.manifests[-1]
    assert snapshot.manifest_ref == manifest.manifest_id
    assert all(record.content is None for record in manifest.included)
    assert all("secret" not in repr(record).lower() for record in manifest.excluded)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/unit/context/test_context_pipeline.py -k "required or manifest" -q`

Expected: FAIL because selection and manifest allocation are not implemented.

- [ ] **Step 3: Implement deterministic selection and accounting**

Remove duplicate candidate IDs and dominated representations first. Sort with a stable key: required priority first, dependency rank, descending relevance, ascending estimated tokens, valid newest `created_at`, then source kind/candidate reference. Apply per-kind limits and available input tokens after control reservation; preserve required items, then high/normal/low according to policy. Reference content already has reference cost; do not fabricate a tokenizer. Use the injected token estimator/policy snapshot to account input, reserved output, reserved control, included, excluded and transformed totals.

When optional candidates cannot fit, record categorical exclusions. When an inline candidate can be safely replaced by its opaque reference, create a transformed reference item and record the transformation chain; do not invent summaries or learned ranking. If any authorized required item remains invalid or over budget, raise `ContextError` before recorder `record` is called for an unusable snapshot.

Create a manifest with policy version, tokenizer profile, source cutoff, previous manifest ID, reference-only records, transformations and accounting. Record it once, then create the snapshot with its returned manifest reference and a temporary context reference. Enforce the postconditions after recording; a recorder that returns a mismatched reference raises a reconciliation error.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/unit/context/test_context_pipeline.py -q`

Expected: PASS.

---

### Task 4: Implement turn updates, cancellation and ephemeral finalization

**Files:**
- Modify: `src/agentos/context/service.py`
- Modify: `tests/unit/context/test_context_pipeline.py`
- Create: `tests/unit/context/test_context_lifecycle.py`

**Interfaces:**
- Consumes: Task 3 manifests and the canonical `ContextTurnUpdate` reference types.
- Produces: `apply_turn` with expected-turn/manifest chaining, exactly-once usage deltas, cooperative cancellation and `finalize` disposal.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_apply_turn_chains_manifest_without_loading_full_history(context_fixture):
    first = context_fixture.manager.assemble(context_fixture.request)
    update = ContextTurnUpdate(
        context=context_fixture.request.context,
        expected_turn=1,
        previous_manifest_ref=first.manifest_ref,
        model_message=TurnReference(reference="result:1", kind=ContextItemKind.MESSAGE),
        usage=TokenAccounting(input_tokens=3, output_tokens=2),
    )
    second = context_fixture.manager.apply_turn(update)
    assert second.turn == 2
    assert context_fixture.recorder.manifests[-1].previous_manifest_id == first.manifest_ref


def test_apply_turn_rejects_stale_turn(context_fixture):
    first = context_fixture.manager.assemble(context_fixture.request)
    update = replace(context_fixture.update, previous_manifest_ref=first.manifest_ref, expected_turn=0)
    with pytest.raises(ContextError) as error:
        context_fixture.manager.apply_turn(update)
    assert error.value.category is ContextErrorCategory.TURN_CONFLICT


def test_cancelled_assembly_does_not_record_usable_manifest(context_fixture):
    context_fixture.cancellation.cancelled = True
    with pytest.raises(ContextError) as error:
        context_fixture.manager.assemble(context_fixture.request)
    assert error.value.category is ContextErrorCategory.CANCELLED
    assert context_fixture.recorder.manifests == []


def test_finalize_discards_ephemeral_state_and_never_saves_memory(context_fixture):
    context_fixture.manager.assemble(context_fixture.request)
    context_fixture.manager.finalize("execution-1", ContextDisposition.DISCARD)
    assert context_fixture.manager.active_executions == ()
    assert context_fixture.recorder.finalized == [("execution-1", ContextDisposition.DISCARD)]
```

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run: `python -m pytest tests/unit/context/test_context_lifecycle.py -q`

Expected: FAIL because turn state, cancellation and finalization are not implemented.

- [ ] **Step 3: Implement incremental turn state and disposal**

Load the previous manifest through the recorder with the exact request ownership, verify execution/turn chain and derive the next turn as `expected_turn + 1`. Convert only explicit message/tool/decision/event/control references into candidates; do not query a history source unless a configured source is explicitly part of the request. Deduplicate usage with a per-execution applied-update key derived from the previous manifest and expected turn; conflicting reuse raises `TURN_CONFLICT`.

Check cancellation before source calls and between each candidate processing/allocation step. `finalize` removes in-memory execution state regardless of disposition and accepts only known dispositions. It may retain recorder audit metadata through the injected recorder, but it never calls a Memory-like method and never stores inline Context content.

- [ ] **Step 4: Run lifecycle tests and verify GREEN**

Run: `python -m pytest tests/unit/context/test_context_lifecycle.py -q`

Expected: PASS.

---

### Task 5: Add Runtime compatibility adapter and package exports

**Files:**
- Create: `src/agentos/context/compat.py`
- Modify: `src/agentos/context/__init__.py`
- Modify: `tests/unit/runtime/test_runtime_security.py`
- Create: `tests/unit/context/test_runtime_compatibility.py`

**Interfaces:**
- Consumes: canonical `ContextManagerService` and existing `agentos.runtime.models` request/snapshot shapes.
- Produces: `RuntimeContextManagerAdapter` implementing the current Runtime `ContextManager` surface and returning reference-only Runtime snapshots.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_runtime_adapter_preserves_complete_operation_context(context_fixture):
    adapter = RuntimeContextManagerAdapter(context_fixture.manager)
    result = adapter.assemble(runtime_request())
    assert result.context_ref
    assert result.manifest_ref
    assert context_fixture.source.queries[0].context == canonical_context_from_runtime_request()


def test_runtime_adapter_does_not_expose_canonical_items_or_payloads(context_fixture):
    result = RuntimeContextManagerAdapter(context_fixture.manager).assemble(runtime_request())
    assert set(vars(result)) == {"context_ref", "manifest_ref"}
    assert "prompt" not in repr(result).lower()
```

- [ ] **Step 2: Run compatibility tests and verify RED**

Run: `python -m pytest tests/unit/context/test_runtime_compatibility.py -q`

Expected: FAIL because the adapter and canonical-to-runtime conversion do not exist.

- [ ] **Step 3: Implement the adapter without changing Runtime business logic**

Translate Runtime `OperationContext` into canonical `ContextOperationContext`, create a reference-only `TaskSnapshot` from `task_ref`, create the canonical budget from the configured Runtime-facing defaults, and pass the model requirements reference as an opaque policy input. Convert canonical snapshots to `agentos.runtime.models.ContextSnapshot(context_ref, manifest_ref)`. Translate Runtime turn result references into `TurnReference` values and preserve expected turn/previous manifest semantics. Finalize by mapping Runtime `ExecutionState` to canonical `ContextDisposition`.

Export the canonical package and adapter. Keep the existing Runtime fake/test surface valid; production composition can inject `RuntimeContextManagerAdapter(ContextManagerService(...))` and no Runtime source imports are added.

- [ ] **Step 4: Run Runtime and compatibility tests and verify GREEN**

Run: `python -m pytest tests/unit/context tests/unit/runtime -q`

Expected: all Context and Runtime tests pass.

---

### Task 6: Full verification and requirement audit

**Files:**
- Modify only files identified by failing tests from Tasks 1–5.

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest -q`

Expected: exit code 0 and no failures.

- [ ] **Step 2: Run compilation and boundary scans**

Run: `python -m compileall -q src tests`

Expected: exit code 0.

Run: `rg -n "(FastAPI|fastapi|Playwright|playwright|Redis|redis|SQLAlchemy|sqlalchemy|Alembic|alembic|openai|anthropic|google\.generativeai|MemoryStore|ArtifactStorage|ProviderPort)" src/agentos/context`

Expected: no concrete infrastructure/provider/storage dependency matches in the Context package.

- [ ] **Step 3: Audit the RFC 104 checklist against code and tests**

Verify explicitly that the code has public assemble/apply/finalize methods; complete scope propagation; fixed cutoff/policy; source collection; ownership/classification/provenance/integrity revalidation; sanitization and untrusted-data delimiting; deterministic ordering; category/global budgeting; required-item failure; reference-only snapshots/manifests; manifest chaining; cooperative cancellation; ephemeral finalization; and no implicit Memory write.

- [ ] **Step 4: Record final status with fresh evidence**

Report exact test and compile commands, counts, any environment limitation, and the created files. Do not claim completion until all commands from this task have fresh successful output.
