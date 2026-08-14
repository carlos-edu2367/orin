# Provider runtime diagnostic

On 2026-08-14, provider configuration and catalog refresh succeeded for OpenRouter, Ollama Cloud and OmniRoute using non-secret diagnostic values. The generic provider-unavailable banner appeared immediately afterwards because the UI lists the refreshed catalog.

SQLite strips the offset from `DateTime(timezone=True)`. `PostgresProviderCatalogRepository.list` must restore UTC on a naive catalog `refreshed_at` value before constructing `ProviderModelRecord`, which requires timezone-aware timestamps. Without that normalization, `GET /v1/providers/{provider}/models` raises a 500 even though saving and refreshing succeeded.

This is an API persistence-boundary defect, not a provider credential or worker defect. The local runtime showed the expected SQLite heartbeats (`chat-worker` and `scheduled-chat-worker`). Keep provider errors sanitized: do not log keys or upstream response bodies.
