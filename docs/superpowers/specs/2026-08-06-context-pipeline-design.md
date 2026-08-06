# Context Pipeline Design

**Goal:** Implement the RFC 104 Context domain as a deterministic, reference-only pipeline and expose it through a canonical `agentos.context` package while preserving the Runtime's existing minimum Context port.

## Scope and boundaries

The implementation owns Context contracts, candidate validation, sanitization, deterministic ordering, token budgeting, manifest recording, turn updates, cancellation and ephemeral disposal. It does not implement Memory, Artifact Storage, filesystem, search, Provider, tokenizer SDKs, Event Bus or durable persistence.

All external collection and manifest access goes through injected Protocols. The service receives candidates; it never discovers or resolves concrete Memory, Artifact, Event, Tool or Provider records itself. Voluminous values are represented by opaque references. Content classified as secret or credential is never returned in a snapshot or manifest.

## Canonical package and compatibility

`agentos.context.models` is the source of truth for RFC 104 types. `agentos.context.ports` contains `ContextManager`, `ContextSource`, `ContextManifestRecorder`, `ContextPolicy`, `ContextClock` and cancellation/token-estimation ports. `agentos.context.service.ContextManagerService` is the reference implementation.

`agentos.runtime` keeps its existing lightweight request/snapshot shapes for current Runtime consumers. Compatibility aliases and an adapter in the Context package translate the Runtime request into the canonical request and translate the canonical snapshot into reference-only Runtime fields. The Runtime remains dependent on a public Context port and never imports a concrete source or recorder.

## Data flow

1. Validate complete operation scope, turn, purpose, task, budget and ownership.
2. Resolve a fixed policy snapshot containing policy/tokenizer versions, classification ceiling and source cutoff.
3. Ask each authorized `ContextSource` for bounded candidates carrying the complete scope.
4. Revalidate candidate ownership, classification, provenance, integrity and content/reference shape.
5. Sanitize untrusted content, secrets, malformed structures and oversized inline values; preserve source role and transformation chain.
6. Remove duplicates, then order deterministically by priority, dependency, relevance, cost, valid recency and source diversity.
7. Allocate category limits and the effective input budget while preserving output/control reservations; apply the declared overflow policy.
8. Record included/excluded items and transformations in a manifest through `ContextManifestRecorder`.
9. Return a temporary snapshot only after required-item, order, authorization and budget postconditions hold.

`apply_turn` validates the previous manifest reference and expected turn, converts explicit turn references into candidates, accounts for usage exactly once, and reruns the complete selection pipeline. It does not load all history or retain an item merely because it was previously selected. `finalize` drops ephemeral execution state and never calls a Memory port.

## Errors and security

Errors use stable categories and retryability, with sanitized codes and optional diagnostic references. Required authorized items that cannot fit or validate raise an explicit budget/validation error. Optional invalid or over-budget candidates become manifest exclusions. Cancellation stops collection/processing at safe boundaries and does not record an incomplete usable manifest.

Ownership is revalidated when loading a prior manifest and whenever a reference-bearing candidate is accepted. A reference never grants authorization. User/file/web/Memory/Tool/Event/Provider content remains untrusted data and cannot become system or agent authority through sanitization or priority.

## Testing strategy

Tests are contract-first and use in-memory sources, recorder, policy and clock fakes. They cover validation, complete context propagation, required task/control items, deterministic ordering, category and global budgets, overflow transformations/exclusions, secret redaction, prompt-injection delimiting, ownership/classification rejection, reference-only manifests, turn chaining/deduplicated usage, cancellation, finalize disposal and Runtime compatibility. The full existing Runtime and Execution suites remain green.
