# Ollama Cloud probe root cause

- The provider settings frontend sends the Cloud key and `https://ollama.com` to the expected API route; the key is not persisted in the frontend response.
- The connection test fetches the public model catalog, then probes `models[0]` with `POST /api/chat`.
- Ollama's catalog order is dynamic and can place a premium, preview, or otherwise unauthorized model first. A valid account key can receive HTTP 403 for that model while being valid for another listed model.
- `src/agentos/provider_catalog/ollama.py` currently classifies both HTTP 401 and HTTP 403 as `OllamaCloudAuthenticationError`; the API therefore reports `provider_credentials_rejected` even when the actual failure is model/account entitlement.
- Reproduction with a mocked catalog: the first model returns 403, the second returns 200; the current code reports the first failure as credential rejection.
- No credential, response body, or secret was stored in this memory.
- The catalog fix records the normalized `base_url` that produced each persisted model catalog, clears `catalog_refreshed_at` whenever provider configuration changes, and hides catalogs that are disabled, not refreshed, or sourced from a different URL. This prevents a Local catalog from appearing after switching to Cloud, including for rows created before the fix.
- The frontend clears its in-memory model list immediately when switching Ollama mode and when the post-save catalog refresh fails, so stale Local models cannot remain visible during a Cloud setup failure.
- Cloud connection verification now tries subsequent catalog models after one model rejects access; a restricted first model no longer makes an otherwise usable Cloud credential fail setup.

## Recommended correction

Separate invalid credentials from model access failures, and do not use an arbitrary first catalog item as the setup probe. Either validate only the authenticated/catalog access contract, or probe a model selected by the user. If a generation probe remains, surface a model-access error separately from an invalid-key error and add regression coverage for a valid key with a forbidden first model.
