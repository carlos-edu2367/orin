# Ollama Cloud authentication probe

- Ollama's official API docs use `https://ollama.com/api` for direct Cloud access and `Authorization: Bearer <OLLAMA_API_KEY>` for inference requests.
- `GET /api/tags` and model detail requests are suitable for catalog discovery but do not prove that a Cloud API key can generate a response.
- The provider connection test now performs a minimal authenticated `POST /api/chat` with `stream: false` and `num_predict: 1` after catalog discovery, so invalid or missing Cloud keys fail during setup instead of surfacing later as a generic `PROVIDER_STREAM_FAILED` chat error.
- The reported chat used model `deepseek-v4-flash:0731`, which is currently listed by Ollama's public `/api/tags`; the observed failure alone did not prove the model name or base URL was wrong.
