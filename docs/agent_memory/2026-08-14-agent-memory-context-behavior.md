# Comportamento atual da memória no contexto do agente

- O worker cria uma `PostgresAgentMemoryStore` por turno, com isolamento por `user_id` e, em chats de projeto, pelo `project_id` exato.
- Ao construir o runtime, `TurnSession.build_runtime()` chama `memory.recent(limit=12)` e inclui até 12 fatos recentes no system prompt.
- Portanto, no caminho atual, memórias não são carregadas somente por relevância semântica: há um pré-carregamento bounded por recência.
- A ferramenta `recall` também é exposta quando a store existe. Ela faz busca lexical limitada a 8 resultados, usando interseção de termos e tags; não usa embeddings.
- A ferramenta `remember` grava explicitamente um fato durável, deduplica o mesmo fato e mantém tags limitadas. O prompt orienta o agente a usá-la para preferências/fatos duráveis, nunca para conversa transitória.
- A coleta via `MemoryContextSource` da arquitetura RFC é reference-first, autorizada e sem escrita implícita, mas não é o caminho que atualmente popula o prompt conversacional principal.
- Qualquer mudança para recuperação apenas relevante, ou para encorajar `recall` sob demanda, deve preservar os limites, o isolamento por escopo e a ausência de gravação implícita.
