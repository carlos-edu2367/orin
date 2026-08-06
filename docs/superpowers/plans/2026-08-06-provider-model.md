# Provider API e Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline execution) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar os contratos públicos RFC 501/502, um catálogo/resolver determinístico em memória, a porta Provider normalizada e adapters compatíveis com o Runtime, sem dependências tecnológicas.

**Architecture:** `agentos.providers.models` contém somente contratos imutáveis e validação. `ports` define Protocols; `catalog` mantém revisões/selections em memória por trás dessas portas; `resolver` aplica constraints, pricing, profiles, fallback e snapshots; `provider` valida e normaliza a fronteira; `compat` preserva a superfície reduzida do Runtime. A implementação em memória é uma referência substituível, não persistência de produção.

**Tech Stack:** Python 3.13+, frozen/slotted dataclasses, `Protocol`, `StrEnum`, `NewType`, `Decimal`, `pytest`; zero runtime dependencies.

## Global Constraints

- Toda operação sensível preserva `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`; `actor_ref` é preservado quando presente.
- Descriptors, revisions, pricing, profiles, approved snapshots e selections são imutáveis.
- Hard constraints são avaliados antes de score; preço desconhecido nunca vira zero.
- `DISABLED` e `RETIRED` não entram em nova invocação; `RETIRED` não reativa.
- Fallback é explicitamente desabilitado, ordenado ou materializado por policy; não amplia permissões ou budget.
- Uso e custo são monotônicos e preservados em todas as tentativas e outcomes.
- Nenhum segredo, SDK, payload proprietário, prompt completo, resposta completa, banco, Redis, filesystem, FastAPI ou adapter concreto entra em `src/agentos/providers`.
- Cada comportamento novo segue RED → GREEN → REFACTOR e o teste deve falhar antes do código de produção.
- O workspace não possui `.git`; não executar comandos de commit.

---

### Task 1: Public identity, enums and operation contexts

**Files:**
- Create: `src/agentos/providers/models.py`
- Create: `src/agentos/providers/__init__.py`
- Create: `src/agentos/providers/ports.py`
- Create: `tests/unit/providers/conftest.py`
- Create: `tests/unit/providers/test_provider_model_contracts.py`

**Interfaces:**
- Consumes: `agentos.execution.models` IDs, `CancellationReason`, `DataClassification`.
- Produces: opaque reference aliases, `ProviderOperationContext`, `ModelCatalogOperationContext`, shared enums, validation helpers, `CancellationSignal` Protocol and public exports.

- [ ] **Step 1: Write the failing contract tests**

```python
def test_provider_context_requires_all_sensitive_fields():
    with pytest.raises(ValueError):
        ProviderOperationContext(
            user_id="user-1", workspace_id="workspace-1", agent_id="agent-1",
            execution_id="execution-1", correlation_id="correlation-1",
            purpose="", actor_ref="actor-1",
        )


def test_public_references_are_opaque_and_non_blank():
    with pytest.raises(ValueError):
        ModelRef("")


def test_cancellation_signal_is_cooperative_and_reference_only():
    signal = CancellationSignalRef("cancel:1")
    assert signal == "cancel:1"
    assert "secret" not in repr(signal).lower()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_provider_model_contracts.py -q`

Expected: FAIL because `agentos.providers` and its public contracts do not exist.

- [ ] **Step 3: Implement minimal immutable shared contracts**

Define aliases with a helper-backed immutable reference wrapper where runtime validation is required, or `NewType` aliases plus validation in containing dataclasses where compatibility with existing string references is needed. Define `ProviderStatus`, `ModelStatus`, `DataClassification`-compatible capability enums, `Retryability`, `Measurement`, `CancellationRequirement`, `FallbackMode`, `Role`, `FinishReason`, and `ProviderErrorCategory`. Validate non-blank strings, positive/non-negative integers, timezone-aware datetimes, non-negative `Decimal`, and tuple collections. Keep `actor_ref` on both operation contexts and provide a shared `same_scope` helper that compares all six sensitive fields plus purpose.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_provider_model_contracts.py -q`

Expected: PASS.

---

### Task 2: Descriptors, capabilities, pricing, profiles and requirements

**Files:**
- Modify: `src/agentos/providers/models.py`
- Modify: `tests/unit/providers/test_provider_model_contracts.py`
- Create: `tests/unit/providers/test_catalog_contracts.py`

**Interfaces:**
- Consumes: Task 1 references, enums and contexts.
- Produces: `ProviderDescriptor`, `ProviderModelBinding`, `ModelDescriptor`, revisions, capabilities, `ModelCost`, `ModelProfile`, constraints, `FallbackRequest`, `ModelRequirements`, `ApprovedModelRequirementsSnapshot`, and sanitized explanation/rejection types.

- [ ] **Step 1: Write failing descriptor and immutability tests**

```python
def test_descriptor_revisions_and_approved_snapshot_are_immutable(catalog_fixture):
    descriptor = catalog_fixture.model_descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.status = ModelStatus.DISABLED
    snapshot = catalog_fixture.snapshot
    with pytest.raises(FrozenInstanceError):
        snapshot.maximum_cost = Decimal("0")


def test_missing_pricing_is_not_zero(catalog_fixture):
    assert catalog_fixture.model_descriptor.cost.input_per_million_tokens is None
    assert catalog_fixture.model_descriptor.cost.comparable is False
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_catalog_contracts.py -q`

Expected: FAIL because descriptor, pricing and snapshot types do not exist.

- [ ] **Step 3: Implement the public catalog value objects**

Use frozen/slotted dataclasses and tuples. Model pricing with nullable unit prices, explicit `measurement_basis`, `pricing_revision`, validity interval and a `comparable` property that is false when required components are absent. Define model capabilities for vision, tools, streaming and cancellation; `ResolvedCapabilities` is a public projection only. Define `ModelConstraint`/`ConstraintCode`, weighted preferences, profile revision/status, fallback policy references, `CandidateRejection`, `SelectionExplanation`, `SelectedModel`, `ModelSelection` and the complete approved snapshot. Mark all potentially sensitive free-text fields as `repr=False` and accept only sanitized public summaries.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_catalog_contracts.py -q`

Expected: PASS.

---

### Task 3: Catalog port and in-memory revision store

**Files:**
- Modify: `src/agentos/providers/ports.py`
- Create: `src/agentos/providers/catalog.py`
- Create: `tests/unit/providers/test_catalog_store.py`

**Interfaces:**
- Consumes: Task 1–2 public types.
- Produces: `ModelCatalogPort`, `CatalogMutation` requests/results, authorized query types, `InMemoryModelCatalog`, and immutable revision/selection storage.

- [ ] **Step 1: Write failing store tests**

```python
def test_register_provider_is_idempotent_for_same_key_and_rejects_version_conflict(catalog_fixture):
    first = catalog_fixture.catalog.register_provider(catalog_fixture.register_provider)
    again = catalog_fixture.catalog.register_provider(catalog_fixture.register_provider)
    assert again == first
    conflicting = replace(catalog_fixture.register_provider, expected_catalog_version=first.catalog_version + 1)
    with pytest.raises(CatalogConflictError):
        catalog_fixture.catalog.register_provider(conflicting)


def test_status_transitions_are_valid_and_revision_history_is_immutable(catalog_fixture):
    catalog_fixture.catalog.register_model(catalog_fixture.register_model)
    changed = catalog_fixture.catalog.change_model_status(catalog_fixture.disable_model)
    assert changed.status is ModelStatus.DISABLED
    with pytest.raises(CatalogConflictError):
        catalog_fixture.catalog.change_model_status(catalog_fixture.reactivate_without_revision)


def test_selection_is_loaded_only_with_matching_ownership(catalog_fixture):
    selection = catalog_fixture.catalog.record_selection(catalog_fixture.selection)
    assert catalog_fixture.catalog.inspect_selection(catalog_fixture.selection_query).selection_ref == selection.selection_ref
    with pytest.raises(PermissionError):
        catalog_fixture.catalog.inspect_selection(catalog_fixture.other_owner_selection_query)
```

- [ ] **Step 2: Run the store tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_catalog_store.py -q`

Expected: FAIL because the catalog port, mutation requests and store do not exist.

- [ ] **Step 3: Implement the minimal in-memory catalog**

Store each published revision as a frozen value under opaque refs, maintain a monotonically increasing catalog version, and retain prior revisions. Compare request scope and idempotency payloads; return the same mutation result for the same key/payload and raise a sanitized conflict for a different payload or unexpected version. Enforce the RFC transition graph, requiring a new revision to reactivate disabled/deprecated models and never allowing retired reactivation. Implement provider/model/profile/pricing registration, authorized get/list, status changes, and record/load selection plus approved snapshot. Never store credentials, bindings beyond opaque refs, payloads or task content.

- [ ] **Step 4: Run the store tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_catalog_store.py -q`

Expected: PASS.

---

### Task 4: Deterministic resolver, constraints, snapshots and fallback

**Files:**
- Create: `src/agentos/providers/resolver.py`
- Modify: `src/agentos/providers/catalog.py`
- Create: `tests/unit/providers/test_model_resolver.py`

**Interfaces:**
- Consumes: `ModelCatalogPort`, Task 2 requirements and Task 3 revisions/selections.
- Produces: `ModelResolver`, `ModelResolutionOutcome`, `ModelResolverService.resolve`, `resolve_fallback`, deterministic filtering/ranking and revalidation.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_hard_constraints_reject_before_preference_score(resolver_fixture):
    result = resolver_fixture.resolver.resolve(resolver_fixture.requirements_with_incompatible_preferred_model)
    assert isinstance(result, NoCompatibleModel)
    assert result.considered[0].code is ConstraintCode.DATA_CLASSIFICATION
    assert result.explanation is None or "secret" not in repr(result).lower()


def test_same_snapshot_resolves_deterministically(resolver_fixture):
    first = resolver_fixture.resolver.resolve(resolver_fixture.requirements)
    second = resolver_fixture.resolver.resolve(resolver_fixture.requirements)
    assert first == second


def test_unknown_cost_cannot_satisfy_required_budget(resolver_fixture):
    result = resolver_fixture.resolver.resolve(resolver_fixture.requirements_with_cost_ceiling)
    assert isinstance(result, NoCompatibleModel)
    assert any(rejection.code is ConstraintCode.COST_UNKNOWN for rejection in result.considered)


def test_fallback_is_materialized_and_never_broadens_scope(resolver_fixture):
    primary = resolver_fixture.resolved_selection
    result = resolver_fixture.resolver.resolve_fallback(resolver_fixture.fallback_request)
    assert isinstance(result, ModelResolved)
    assert result.selection.context == primary.context
    assert result.selection.primary.model_ref in {m.model_ref for m in primary.fallbacks}
```

- [ ] **Step 2: Run resolver tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_model_resolver.py -q`

Expected: FAIL because the resolver and filtering pipeline do not exist.

- [ ] **Step 3: Implement the resolution pipeline**

Validate complete context, cancellation and requirements before catalog reads. Pin requested/current catalog and policy versions, profile/pricing revisions and an availability snapshot. Form candidates from the explicit model/profile set; remove provider/model statuses not eligible; apply classification, region, context, input/output/total, required capabilities, streaming, cancellation, response format, allow/deny and budget constraints. Under a cost ceiling reject non-comparable pricing unless an explicit policy allows unknown cost; never score rejected candidates.

Rank surviving candidates using versioned preferences and a stable key `(preference score, cost-known flag/cost, latency tier, quality tier, model_ref)`. Materialize primary plus only explicit ordered/policy fallbacks, with explanations containing references, codes and bounded metrics only. Create an immutable approved snapshot carrying the exact scope, constraints and fixed versions; record selection before returning it. Revalidate selection/snapshot immediately before fallback and reject expired, changed, disabled or retired candidates. Cancellation returns `ModelResolutionCancelled` before registration.

- [ ] **Step 4: Run resolver tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_model_resolver.py -q`

Expected: PASS.

---

### Task 5: Provider invocation contracts, normalization and conceptual streaming

**Files:**
- Modify: `src/agentos/providers/models.py`
- Modify: `src/agentos/providers/ports.py`
- Create: `src/agentos/providers/provider.py`
- Create: `tests/unit/providers/test_provider_api.py`

**Interfaces:**
- Consumes: Task 1–4 references, selection and approved snapshot.
- Produces: normalized messages/parts, invocation limits, usage/cost, errors/outcomes, stream events/terminal snapshots, `ProviderPort`, `ProviderInvocationValidator` and cancellation semantics.

- [ ] **Step 1: Write failing Provider API tests**

```python
def test_invocation_rejects_scope_mismatch_before_fake_provider_effect(provider_fixture):
    request = replace(provider_fixture.request, context=replace(provider_fixture.context, purpose="other-purpose"))
    with pytest.raises(ProviderContractError) as error:
        provider_fixture.validator.validate(request)
    assert error.value.category is ProviderErrorCategory.POLICY_REJECTED
    assert provider_fixture.fake.calls == []


@pytest.mark.parametrize("category", [ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.AUTHENTICATION,
                                      ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.INVALID_REQUEST,
                                      ProviderErrorCategory.CANCELLED])
def test_provider_error_categories_are_public_and_sanitized(category, provider_fixture):
    error = ProviderError(category=category, code="PUBLIC_CODE", message="safe summary",
                          retryability=Retryability.SAFE, provider_ref=provider_fixture.provider_ref)
    assert "api_key" not in repr(error).lower()


def test_usage_and_cost_remain_present_on_failure_and_cancellation(provider_fixture):
    outcome = GenerationFailed(provider_fixture.invocation_id, provider_fixture.error,
                               provider_fixture.usage, provider_fixture.cost)
    assert outcome.usage.input_tokens == 10
    assert outcome.cost.amount == Decimal("0.02")


def test_stream_sequences_are_positive_and_terminal_is_explicit(provider_fixture):
    with pytest.raises(ValueError):
        StreamOpened(provider_fixture.stream_id, 0)
```

- [ ] **Step 2: Run Provider tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_provider_api.py -q`

Expected: FAIL because the normalized Provider contracts do not exist.

- [ ] **Step 3: Implement public Provider contracts and validator**

Define `ProviderInvocationRequest`, `ProviderMessage`, `TextPart`, `ImagePart`, `ToolResultPart`, `RefusalPart`, `ToolDeclaration`, `ResponseFormat`, sampling and limits. Use opaque references for images/results and `UntrustedStructuredValue` for Tool arguments. Define usage/cost measurement with null unknown fields and validate total token consistency. Define all RFC error categories with sanitized message, retryability, retry-after, acceptance state, partial-output flag and opaque diagnostic ref.

Define success, tool-call, user-input, failed, cancelled and indeterminate outcomes with invocation refs and accounting. Define stream metadata, strictly positive sequences, deltas, usage events, exactly-one terminal event, read/cancel/await/inspect requests and cancel results. `ProviderPort` and `ProviderInvocationPort` must expose the complete RFC methods. `ProviderInvocationValidator` checks context equality, selection/snapshot ownership, validity, active status, limits, capabilities, cancellation and response format without calling a concrete provider. Implement a small `ProviderOutcomeNormalizer` for timeout/exception-free categorized inputs and monotonic accumulation; no SDK exception type is imported.

- [ ] **Step 4: Run Provider tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_provider_api.py -q`

Expected: PASS.

---

### Task 6: Runtime compatibility adapters and full-port integration

**Files:**
- Create: `src/agentos/providers/compat.py`
- Modify: `src/agentos/runtime/models.py`
- Modify: `src/agentos/runtime/ports.py`
- Modify: `src/agentos/runtime/service.py`
- Modify: `src/agentos/providers/__init__.py`
- Create: `tests/unit/providers/test_runtime_compatibility.py`
- Modify: `tests/unit/runtime/conftest.py`
- Modify: `tests/unit/runtime/test_runtime_security.py`

**Interfaces:**
- Consumes: canonical `ModelResolver`, `ProviderPort`, `ResolveModel`, `ProviderInvocationRequest` and existing Runtime reference types.
- Produces: `RuntimeModelResolverAdapter`, `RuntimeProviderAdapter`, complete-port aliases and a Runtime path that carries canonical selection/snapshot references without concrete dependencies.

- [ ] **Step 1: Write failing integration tests**

```python
def test_runtime_model_adapter_preserves_scope_and_returns_reference_only(provider_runtime_fixture):
    result = provider_runtime_fixture.model_adapter.resolve(provider_runtime_fixture.runtime_request_model)
    assert result.selection_ref == "selection:1"
    assert result.approved_requirements_ref == "approved:1"
    assert provider_runtime_fixture.resolver.requests[0].requirements.context.purpose == "runtime-test"
    assert "binding" not in repr(result).lower()


def test_runtime_provider_adapter_preserves_context_and_maps_failure(provider_runtime_fixture):
    outcome = provider_runtime_fixture.provider_adapter.generate(provider_runtime_fixture.runtime_provider_request)
    assert outcome.error.category is ProviderErrorCategory.TIMEOUT
    assert provider_runtime_fixture.provider.requests[0].context.execution_id == "execution-1"


def test_existing_runtime_fake_surface_remains_compatible(runtime_fixture):
    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)
    assert isinstance(outcome, CompletedOutcome)
```

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python -m pytest tests/unit/providers/test_runtime_compatibility.py tests/unit/runtime -q`

Expected: FAIL because canonical-to-runtime adapters and complete-port mappings do not exist.

- [ ] **Step 3: Implement adapters and preserve Runtime behavior**

Map `Runtime.OperationContext` to canonical contexts, build `ResolveModel` from the opaque requirements reference, and convert `ModelResolved`, `NoCompatibleModel` and `ModelResolutionCancelled` to the legacy Runtime outcomes without exposing canonical internals. Map Runtime Provider requests to canonical invocation requests only when a canonical approved snapshot is available; otherwise return a sanitized contract failure rather than guessing. Map normalized outcomes back to legacy `ProviderFinal`, `ProviderToolRequest`, `ProviderUserInputRequest`, `ProviderFailed` and `ProviderCancelled`, preserving all usage/cost deltas and retryability categories.

Update the Runtime Protocol annotations to refer to compatibility interfaces while keeping existing fake constructors and service control flow valid. Do not make the Runtime import `catalog.py`, concrete storage or provider adapters. Add a complete resolver integration fixture that supplies a fake catalog/provider through ports only.

- [ ] **Step 4: Run integration and regression tests and verify GREEN**

Run: `python -m pytest tests/unit/providers/test_runtime_compatibility.py tests/unit/runtime -q`

Expected: all provider integration and existing Runtime tests PASS.

---

### Task 7: Full verification, dependency scan and RFC audit

**Files:**
- Modify only files identified by failing tests from Tasks 1–6.

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest -q`

Expected: exit code 0 and no failures.

- [ ] **Step 2: Run compilation**

Run: `python -m compileall -q src tests`

Expected: exit code 0.

- [ ] **Step 3: Scan Provider boundaries**

Run: `rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|MemoryStore|ArtifactStorage|requests|httpx" src/agentos/providers`

Expected: no concrete infrastructure, transport, SDK or storage matches.

- [ ] **Step 4: Audit the requirements explicitly**

Check source and tests for: complete sensitive context; immutable revisions/status; idempotent mutations; hard constraints before score; unknown pricing; capabilities/input/output/context limits; deterministic selection; sanitized explanations; immutable/revalidated snapshots; explicit bounded fallback; provider success/tool/user/failure/cancellation/indeterminate outcomes; error categories/retryability; streaming sequence/terminal semantics; monotonic attempt accounting; disabled/retired rejection; Runtime compatibility; and absence of secrets/proprietary payloads from public repr/errors/manifests.

- [ ] **Step 5: Record evidence**

Capture the fresh exit codes and counts from the complete pytest, compileall and boundary scan commands. Do not claim completion unless all required commands have fresh successful evidence. No commit is possible because the workspace has no Git repository.
