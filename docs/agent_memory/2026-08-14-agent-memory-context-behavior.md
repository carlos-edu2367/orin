# Comportamento atual da memória no contexto do agente

> **Nota de atualização (Orin 0.13 — Pacote D):** esta nota histórica de 2026-08-14 foi superada a partir da 0.13 nos seguintes pontos:
> - **Índice híbrido sem corte de 500:** a busca e ranqueamento (`relevant` / `search`) passaram a usar `MemoryHybridIndex` (FTS5 BM25 + vetores opcionais fundidos com Reciprocal Rank Fusion, recência, afinidade de projeto, uso e confiança), paginando todas as memórias ativas do usuário sem truncamento em 500 registros.
> - **Ciclo de vida, tombstones, proveniência e quarentena:** `agent_memories` ganhou `status` (`active`, `proposed`, `quarantined`, `superseded`, `invalid`, `deleted`), `text_hash`, `valid_from`, `invalid_at`, proveniência verificada pelo runtime (`source_kind`, `source_ref`, `trust`) e trilha append-only em `memory_events`. `forget` grava tombstone com `fact = ''` (bloqueando recriação automática e sem `old_text` no evento `forgotten`), fontes externas (`tool`/`web`/`file`) entram em quarentena (`memory.quarantine.enabled = true`) e contradições geram cartões de conflito na página Memória.
> - **Escopo opcional por agente (`memory.agent.enabled`):** desligado por padrão; quando ativado, agentes customizados e o `verifier` recebem um resumo congelado próprio (`## O que o agente <nome> aprendeu`) e gravam propostas (`status = proposed`) em `scope_type = 'agent'` promovidas por escritor único (`MemoryPromotionGate`).
> - Consulte `docs/architecture/300-context-memory/301-memory.md` e `docs/architecture/200-agents/203-multi-agent.md` para a especificação atual.

- O worker cria uma `PostgresAgentMemoryStore` por turno, com isolamento por `user_id` e, em chats de projeto, pelo `project_id` exato.
- Ao construir o runtime, `TurnSession.build_runtime()` chama `memory.recent(limit=12)` e inclui até 12 fatos recentes no system prompt.
- Portanto, no caminho atual, memórias não são carregadas somente por relevância semântica: há um pré-carregamento bounded por recência.
- A ferramenta `recall` também é exposta quando a store existe. Ela faz busca lexical limitada a 8 resultados, usando interseção de termos e tags; não usa embeddings.
- A ferramenta `remember` grava explicitamente um fato durável, deduplica o mesmo fato e mantém tags limitadas. O prompt orienta o agente a usá-la para preferências/fatos duráveis, nunca para conversa transitória.
- A coleta via `MemoryContextSource` da arquitetura RFC é reference-first, autorizada e sem escrita implícita, mas não é o caminho que atualmente popula o prompt conversacional principal.
- Qualquer mudança para recuperação apenas relevante, ou para encorajar `recall` sob demanda, deve preservar os limites, o isolamento por escopo e a ausência de gravação implícita.
