# Ollama connection card

- The provider settings page already models Ollama Local and Cloud as one `OllamaSetup` form; the backend derives the mode from the saved base URL.
- Local Ollama uses the default `http://localhost:11434` and does not require an API key. Cloud uses `https://ollama.com` and requires a key before save.
- The visual redesign is intentionally frontend-only: it preserves the existing API routes, mutation flow, disabled/loading states, and accessible labels while giving the Ollama form a dedicated connection-card hierarchy.
