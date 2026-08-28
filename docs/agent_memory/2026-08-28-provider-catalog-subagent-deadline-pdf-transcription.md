# Provider catalog, subagent deadline and PDF transcription

## Context

The provider settings screen reported the generic unavailable-provider state when refreshing catalogs, and delegated work could fail with `TURN_DEADLINE_EXCEEDED` after three minutes even while the parent chat turn remained active.

## Findings and decisions

- `provider_configurations` no longer owns encrypted key columns. Its credentials are stored in `provider_api_keys`, so `PostgresProviderCatalogRepository.credential()` must use `PostgresProviderApiKeyAdapter.next_available_key()` scoped to the requesting user and provider. Reading the removed legacy columns fails before an upstream catalog request can run.
- Every supported provider needs a composed catalog upstream: OpenAI (`GET /v1/models` with bearer authentication), Anthropic (`GET /v1/models` with `x-api-key` and `anthropic-version`, cursor paginated), OpenRouter, OmniRoute and Ollama. Catalog metadata is normalized and persisted under the existing user/provider scope; no credential is returned by the API.
- A subagent is part of its parent durable turn. It inherits the parent runtime deadline and cancellation signal instead of using a separate 180-second deadline. The worker deadline remains the outer operational guard.
- `transcribe_pdf` is a read-only agent tool for the PDF native text layer. The system prompt directs the agent to prefer it when plain text is sufficient and reserve `view_file` for layout, images, tables, diagrams or pages without a text layer. It never silently switches to visual reading.

## Validation

- Focused tests for catalog persistence, provider catalog clients/service, subagent session and PDF tools passed (`73 passed`).
- `python -m compileall -q src tests` and `git diff --check` passed. Release workflow verification remains part of the publish step.
