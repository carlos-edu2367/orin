# Ollama streaming tool-call accumulation

- A second Ollama failure was reproduced on a later request in the same conversation. The Cloud endpoint returned several NDJSON chunks for one assistant response, with `message.tool_calls` repeated across chunks and indexed by `function.index`.
- The previous normalizer treated every chunk as a new tool call and discarded `message.thinking`. Ollama's streaming contract requires accumulating `thinking`, `content`, and `tool_calls` before sending the assistant history back.
- `normalize_ndjson` now merges tool arguments by index, emits one normalized call per index, accumulates thinking, and `AgenticTurnRuntime` preserves thinking in the Ollama follow-up assistant message. OpenRouter and other providers keep their existing paths.
- Regression coverage is in `tests/unit/agentic/test_provider_stream_payload.py` and `tests/unit/agentic/test_agentic_runtime_loop.py`.
