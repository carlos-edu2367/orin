# Provider, Model, and Conversation Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple provider credentials from models, make an authorized model catalog selectable in a conversational home flow, and let parent agents create child agents within an explicit model-selection policy.

**Architecture:** A provider configuration becomes a credential/enablement record only. A separate server-side model-catalog boundary retrieves, normalizes, caches, and authorizes available models; selected models become immutable agent-profile configuration, not provider state. The home composer creates a task from user text only through a new authorized application contract that materializes an opaque prompt reference and binds an agent/model selection.

**Tech Stack:** Python/FastAPI/Pydantic/SQLAlchemy/PostgreSQL; React/TypeScript/Vite/Vitest/Playwright; provider HTTP adapters behind ports.

## Global Constraints

- Never send a provider API key to the browser after its initial `PUT`; catalog refresh happens server-side using the stored credential.
- A provider configuration has only provider, enabled state, opaque `secret_ref`, and timestamps. It never has a selected model.
- A catalog model ID is provider-qualified (`provider` + `model_id`), not a display label; OpenRouter model IDs keep their author prefix such as `anthropic/...`.
- The browser consumes only sanitized model metadata: provider, model ID, display name, supported capabilities, context limit, optional pricing summary, availability timestamp, and favorite state.
- The existing `agentos.providers` catalog/resolver domain is the source of truth for runtime eligibility. Do not create a second incompatible model-resolver abstraction.
- The existing `POST /v1/executions` remains compatible for API callers. The new conversational flow is additive until a migration explicitly removes the low-level form.
- Use TDD for every behavior change: focused RED, smallest production change, focused GREEN, then the relevant suite.
- Do not hard-code model names such as "Fable", "Opus", or "Sonnet". Catalog refresh decides what is available for a credential at that time.
- A child-agent creation request may choose only an approved model from its parent’s provider by default. Cross-provider selection requires an explicit policy grant, never an implicit fallback.

---

## Product decisions to preserve

1. **Credentials are independent of models.** Settings asks only for an API key and enabled state. Changing a default/favorite model never rotates or overwrites the credential.
2. **The user explicitly selects provider and model when starting a chat.** This is essential for OpenRouter, where a single credential exposes models from multiple vendors.
3. **Model availability is credential-scoped and cached.** The client never calls provider catalog APIs directly and never receives a credential. A refresh may be user-triggered and should show the last successful refresh when the provider is unavailable.
4. **Favorites are a usability filter, not an authorization bypass.** The full authorized catalog remains available through search; favorites provide a fast picker and the bounded set agents may choose from by default.
5. **Agent model selection is versioned configuration.** An agent uses a model profile/reference captured at creation or configuration revision. A later favorite/catalog refresh does not silently change a running execution.
6. **The home page starts with intent, not opaque infrastructure IDs.** The primary input is the task/message. Agent/provider/model selectors have human labels and useful defaults; raw `agent_id` and `task_ref` stay developer/API-level inputs.

## Target API contracts

These are proposed public contracts to implement and test; they do not exist in the current gateway.

```ts
type ProviderPublicState = {
  provider: 'openai' | 'anthropic' | 'openrouter'
  enabled: boolean
  secret_ref: string
  catalog_refreshed_at: string | null
}

type ProviderModel = {
  provider: ProviderPublicState['provider']
  model_id: string
  display_name: string
  context_window: number | null
  capabilities: string[]
  input_modalities: string[]
  output_modalities: string[]
  pricing: { input_per_million: string | null; output_per_million: string | null } | null
  is_favorite: boolean
  refreshed_at: string
}

type ModelSelection = {
  provider: ProviderPublicState['provider']
  model_id: string
}

type CreateConversationInput = {
  message: string
  selection: ModelSelection
  workspace_id: string | null
}
```

```http
PUT    /v1/providers/{provider}                 { api_key, enabled? }
GET    /v1/providers/{provider}                 -> ProviderPublicState
DELETE /v1/providers/{provider}                 -> ProviderPublicState(enabled=false)
POST   /v1/providers/{provider}/models:refresh  -> { refreshed_at, count }
GET    /v1/providers/{provider}/models          -> { items: ProviderModel[], refreshed_at }
PUT    /v1/providers/{provider}/favorites/{id}  -> ProviderModel(is_favorite=true)
DELETE /v1/providers/{provider}/favorites/{id}  -> ProviderModel(is_favorite=false)
POST   /v1/conversations                         -> { conversation_id, agent_id, execution_id, state_version }
```

`POST /v1/conversations` must validate that the provider is enabled, the model exists in the caller’s cached authorized catalog, and the caller owns the selection. It writes the message to an authorized task/prompt store and passes only the resulting opaque `task_ref` to the existing execution boundary.

## Task 1: Separate provider credentials from model selection

**Files:**
- Modify: `src/agentos/api/gateway.py`, `src/agentos/api/contracts.py`, `src/agentos/persistence/postgres/{schema,provider_configuration}.py`, `src/agentos/persistence/postgres/migrations/versions/`
- Modify: `frontend/src/api/providers.ts`, `frontend/src/features/providers/ProviderSettingsPage.tsx`
- Test: `tests/integration/api/test_provider_configuration_postgres_optional.py`, `frontend/tests/unit/ProviderSettingsPage.test.tsx`, `frontend/tests/e2e/provider-settings.spec.ts`

- [ ] **Step 1: Write failing backend and frontend tests.** Assert that `PUT /v1/providers/anthropic` accepts an API key without a model; its public response never contains the key or model; the settings page has no model input and remains usable after save/reload.
- [ ] **Step 2: Run focused tests and verify RED.** Run the named integration, unit, and E2E specs. Expected failures: request validation still requires `model`, and the page still renders `Modelo`.
- [ ] **Step 3: Make the minimum schema/adapter/API change.** Add a migration that makes the legacy `model` column nullable, stop reading/writing it, return `provider`, `enabled`, `secret_ref`, and `catalog_refreshed_at`, and change the settings client/form to submit only `api_key` and enablement.
- [ ] **Step 4: Run focused tests and verify GREEN.** Confirm the credential can be configured, inspected, revoked, and never appears in any response body.
- [ ] **Step 5: Preserve migration safety.** Add a migration test for an existing row with a legacy model value; it must remain usable as a credential after the migration and the old model value must not be exposed or interpreted as an agent default.

## Task 2: Build an authorized, durable provider-model catalog

**Files:**
- Create: `src/agentos/provider_catalog/{models,ports,service}.py`, `src/agentos/persistence/postgres/provider_models.py`, migration for model cache/favorites
- Modify: `src/agentos/api/{contracts,gateway}.py`, `src/agentos/bootstrap/production.py`
- Test: `tests/unit/provider_catalog/`, `tests/integration/api/test_provider_model_catalog_postgres_optional.py`

- [ ] **Step 1: Write failing tests for ownership, sanitization, and cache behavior.** A refresh for user A must not be listable by user B; model rows must contain no API key; disabled/unconfigured providers reject refresh; a stale cached list remains readable with its timestamp if a later upstream refresh fails.
- [ ] **Step 2: Define the port and normalized data.** Create `ProviderModelCatalogPort.refresh(context, provider)` and `.list(context, provider)` with a normalized `ProviderModelRecord`; persist `user_id`, `provider`, `model_id`, display metadata, normalized capabilities, refresh timestamp, and no raw upstream payload.
- [ ] **Step 3: Implement provider adapters behind the port.** Start with OpenRouter using its documented `GET /api/v1/models` catalog, server-side only. Normalize `id`, `name`, context length, modalities, supported parameters, and pricing; treat unsupported/unknown fields as absent. Add OpenAI only after verifying a list request with a configured credential. For Anthropic, use an adapter only when the implementation session verifies an official supported list endpoint; otherwise surface a documented curated catalog/refresh limitation rather than inventing an endpoint.
- [ ] **Step 4: Expose refresh/list HTTP endpoints.** Require mutable authentication/authorization for refresh, ownership checks for list, rate limiting, `Idempotency-Key` for refresh, and sanitized error envelopes.
- [ ] **Step 5: Verify against Postgres and a fake upstream adapter.** Prove refresh/list, user isolation, upstream failure with retained cache, and that an API key or raw upstream description cannot appear in the public DTO.

## Task 3: Add favorites and eligibility policy

**Files:**
- Modify: `src/agentos/provider_catalog/{models,ports,service}.py`, `src/agentos/persistence/postgres/provider_models.py`, `src/agentos/api/{contracts,gateway}.py`
- Test: `tests/unit/provider_catalog/test_favorites.py`, `tests/integration/api/test_provider_model_catalog_postgres_optional.py`

- [ ] **Step 1: Write failing tests.** Favorite/unfavorite only accepts a model present in the caller’s provider catalog; the same model name under another provider is distinct; list marks favorites without changing any provider credential.
- [ ] **Step 2: Implement a `(user_id, provider, model_id)` favorite record.** Keep favorites as a separate table/relationship so catalog refresh never destroys them; hide a favorite that is no longer available but retain it for audit until the user removes it.
- [ ] **Step 3: Make list output deterministic.** Return favorites first, then display name/model ID; expose a query flag for favorites-only but keep the default searchable full authorized catalog.
- [ ] **Step 4: Verify policy boundaries.** Test that disabled providers, foreign users, and unknown models cannot create a favorite or become eligible for agent selection.

## Task 4: Bind provider/model selections to versioned agents and runtime resolution

**Files:**
- Modify: `src/agentos/agents/{models,ports,service}.py`, `src/agentos/providers/{models,ports,catalog,resolver}.py`, production persistence/composition files
- Create: a durable catalog adapter only if the existing in-memory `ModelCatalogPort` cannot be composed safely
- Test: `tests/unit/agents/`, `tests/unit/providers/`, Postgres integration tests for the selected path

- [ ] **Step 1: Write failing tests for an agent configuration revision.** Creating/configuring an agent with `{ provider, model_id }` must resolve to an immutable `model_profile_ref`/selection; a later provider refresh or favorite reorder cannot change that revision.
- [ ] **Step 2: Reuse the existing provider domain.** Map `ProviderModelRecord` into the existing `ModelDescriptor`/`ModelProfile` and resolver eligibility rather than bypassing `ModelResolver`. Preserve model status, provider status, capability, purpose, classification, cost, and fallback checks.
- [ ] **Step 3: Persist the missing production catalog path.** If the current `InMemoryModelCatalog` is still the only adapter, add the smallest Postgres implementation for descriptor/profile/selection records and compose it with the runtime; do not claim a model is executable until this binding is real.
- [ ] **Step 4: Verify authorization and execution binding.** Prove user A cannot bind user B’s model; a disabled provider/model is rejected; the resulting execution has the selected agent/config version and model selection audit reference.

## Task 5: Replace the raw-ID home flow with a conversation composer

**Files:**
- Create: `src/agentos/conversations/` application boundary and Postgres adapter/migration; `frontend/src/features/conversations/ConversationComposer.tsx`
- Modify: `src/agentos/api/{contracts,gateway}.py`, `src/agentos/bootstrap/production.py`, `frontend/src/app/Home.tsx`, routes/types/API client/styles
- Test: backend unit/integration conversation tests; `frontend/tests/unit/ConversationComposer.test.tsx`; `frontend/tests/e2e/conversation-create.spec.ts`

- [ ] **Step 1: Write failing E2E and backend contract tests.** The home user types a message, chooses enabled provider/model, submits once, and lands on its execution; no raw `agent_id` or `task_ref` is requested or rendered.
- [ ] **Step 2: Implement the conversation application boundary.** Validate message length/classification, materialize an authorized opaque task/prompt reference, create or select a user-owned conversation agent with the selected model profile, then delegate to the existing `ExecutionApplication.create` path.
- [ ] **Step 3: Implement the progressive UI.** Start with message input; use favorite models as quick choices; provide searchable provider/model selectors; show disabled/unconfigured providers with a clear route to settings; preserve a retry-safe `Idempotency-Key` per create intention.
- [ ] **Step 4: Verify disclosure and accessibility.** Keyboard selection, labels, loading/error/retry states, and no secret/raw identifier leakage. Test a provider with an OpenRouter catalog containing models authored by multiple vendors.

## Task 6: Govern child-agent model choice

**Files:**
- Modify: `src/agentos/multi_agent/{models,ports,service}.py`, `src/agentos/agents/`, `src/agentos/provider_catalog/`, tool-runtime registration/composition
- Test: `tests/unit/multi_agent/`, `tests/integration/` for collaboration/model policy

- [ ] **Step 1: Write failing policy tests.** A parent on Anthropic may select any approved Anthropic model for a child; it cannot select an unavailable model, another user’s favorite, or an OpenRouter/OpenAI model unless a cross-provider grant exists.
- [ ] **Step 2: Add an internal `list_available_models` capability.** Its output is bounded to provider-qualified, policy-approved records and safe metadata; it never exposes API keys, raw provider responses, or other users’ preferences.
- [ ] **Step 3: Extend child-agent creation.** Require an explicit `ModelSelection` or a deterministic policy default; resolve it to a new child `AgentConfiguration` revision before `delegate()` creates the child execution.
- [ ] **Step 4: Audit and prove behavior.** Emit only selection IDs/provider/model IDs in facts; prove the child execution uses its own model profile and parent model does not silently propagate as an unrestricted global default.

## Task 7: Frontend provider/catalog experience and closeout

**Files:**
- Modify: `frontend/src/features/providers/ProviderSettingsPage.tsx`, `frontend/src/api/providers.ts`, styles/routes/docs
- Create: model-picker/favorites components and tests
- Modify: `docs/frontend/{BACKEND_DISCOVERY,BACKEND_CAPABILITY_MATRIX,BACKEND_UI_MAPPING,UX_UI_SPEC}.md`

- [ ] **Step 1: Write frontend RED tests.** Provider settings contains no model field; refresh/list/favorite state is accessible; model picker distinguishes provider and model ID; home never falls back to an arbitrary provider/model.
- [ ] **Step 2: Implement settings/catalog UI.** Provider panels show credential status, last catalog refresh, refresh action, and favorite management; they do not choose an execution model.
- [ ] **Step 3: Run all verification.** Execute backend with and without Postgres, compileall, frontend unit/E2E/visual/lint/build, and repeated isolated specs for catalog refresh, conversation creation, and child-agent policy.
- [ ] **Step 4: Update documentation with evidence.** Record real supported provider adapters, cache behavior, API contracts, model-selection policy, migration path, and any provider whose official API did not permit dynamic discovery.

## Verification matrix

| Behavior | Required proof |
| --- | --- |
| Credential has no model | Postgres API test plus settings E2E; responses never include key or `model` |
| OpenRouter catalog | adapter contract test with normalized fixture; authorized refresh/list cache test |
| Ownership | foreign user gets 404/authorization failure for catalog, favorite, agent, conversation, and child-model paths |
| Model selection | execution/agent configuration records provider-qualified model selection immutably |
| Home usability | E2E starts from message plus human model picker; no ID/ref fields visible |
| Multi-agent policy | unit/integration proof for same-provider allowed, unavailable denied, cross-provider denied unless granted |
| Resilience | provider catalog network/rate-limit failure returns sanitized error and preserves last known cache |

## Implementation evidence (2026-08-10)

- [x] Credentials and model selection are separated. The legacy database column is nullable only for migration compatibility and is neither written nor returned.
- [x] PostgreSQL persists user-scoped normalized catalog rows, favorites, resolver selection snapshots, versioned agent model configuration and opaque conversation prompts.
- [x] Production composes OpenRouter's server-side dynamic catalog adapter. OpenAI and Anthropic are deliberately unsupported for dynamic refresh until their corresponding official adapters are composed; the API emits a sanitized unavailable result instead of inventing a list.
- [x] The conversation application resolves an exact cached provider-qualified descriptor through `ModelResolverService`, persists the selection, binds it to agent configuration version 1, and invokes the existing execution application with only an opaque prompt reference.
- [x] Home uses a labeled message/provider/searchable-model composer with favorite shortcuts. Provider settings own refresh and favorite controls; neither surface renders raw task or agent identifiers.
- [x] Delegation keeps catalog-profiled children on their parent's provider by default; a cross-provider child requires an injected explicit grant port.

## Alternatives rejected

- **A single `model` column in `provider_configurations`:** conflates credential lifecycle with per-agent execution choice and makes a multi-model provider unusable.
- **Fetching provider catalogs in the browser:** leaks the credential boundary and bypasses ownership/rate-limit/audit controls.
- **Hard-coded named models in the UI:** becomes stale and cannot represent OpenRouter’s provider-qualified catalog.
- **Letting a child agent select any model seen by the parent:** allows unbounded cost/capability escalation; use catalog/favorite/policy-constrained selections instead.
- **Replacing the existing `ModelResolver`:** duplicates established eligibility/fallback semantics; extend and compose the existing provider domain.

## External API evidence

- OpenRouter documents `GET /api/v1/models`, including provider-qualified IDs, filtering, capabilities, modalities, context, and pricing: https://openrouter.ai/docs/api/api-reference/models/get-models
- OpenAI documents `GET /v1/models` for the models available to the authenticated API credential: https://platform.openai.com/docs/api-reference/models/object
