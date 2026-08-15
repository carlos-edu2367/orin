# Ollama Cloud E2E validation

- A falha observada na conversa `chat_101a51e30b374924aa39bb54bf4c9f4c` não era causada pelo fluxo de plugins: os turnos terminaram com `PROVIDER_STREAM_FAILED` antes de qualquer evento de instalação.
- O log do worker registrava `POST https://ollama.com/api/chat` com `401 Unauthorized`. A credencial fornecida pelo usuário foi validada pelo endpoint local de teste e funcionou; o valor da chave não deve ser registrado nesta memória.
- O provider Ollama foi configurado localmente com Cloud em `https://ollama.com`, o catálogo foi atualizado com 19 modelos e uma conversa real respondeu `OLLAMA_E2E_OK` usando o identificador catalogado `gemma4:31b`.
- O catálogo local não retornou `gemma4:32b`; a seleção deve continuar usando a autoridade do catálogo (`gemma4:31b`) até que o provider exponha outro identificador.
