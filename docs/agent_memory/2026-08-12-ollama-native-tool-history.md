# Ollama native tool history compatibility

- O transporte Ollama usa o endpoint nativo `/api/chat`, enquanto OpenRouter e os demais providers OpenAI-compatible usam mensagens no formato OpenAI.
- No contrato nativo Ollama, argumentos de `assistant.tool_calls[].function` são objetos e mensagens de resultado usam `tool_name`; o histórico interno do Orin usa string JSON e `tool_call_id` para manter compatibilidade com OpenAI.
- A segunda rodada de um loop com ferramenta falhava porque o histórico OpenAI era enviado sem adaptação ao Ollama. A correção fica em `HTTPProviderStreamTransport._ollama_messages`, na fronteira do provider, clonando mensagens, convertendo argumentos estruturados e resolvendo IDs para nomes.
- OpenRouter não deve passar por essa conversão. O teste de regressão está em `tests/integration/agentic/test_provider_tool_loop.py` e cobre a segunda requisição nativa.
