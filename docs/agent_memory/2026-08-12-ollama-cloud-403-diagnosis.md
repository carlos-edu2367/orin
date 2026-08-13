# Ollama Cloud 403 diagnosis

The local setup was reaching the documented `https://ollama.com/api/chat` endpoint with a Bearer token, but Ollama returned HTTP 403 during the authenticated connection probe. The previous adapter converted this into a generic 500, so the UI only showed that the provider was unavailable.

The implementation now trims API keys before storage, distinguishes upstream 401/403 responses from transport failures, returns the sanitized `provider_credentials_rejected` error, and shows the user that the Ollama Cloud key was rejected. No credential or response body is persisted in this memory.
