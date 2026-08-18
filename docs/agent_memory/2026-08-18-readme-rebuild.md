# README rebuild

## Decisão

O README foi reescrito em 2026-08-18 para refletir o produto executável atual, com foco em instalação Windows, fluxos de conversa e execução, diferenciais técnicos e arquitetura local-first.

## Evidências usadas

- `docs/RETRIEVAL.md` documenta embeddings locais via Ollama, vetores derivados por projeto, fallback BM25 e o modo remoto explícito.
- `docs/LAUNCHER.md` documenta o instalador, o comando `orin`, SQLite local, workers, scheduler, Electron e os comandos de operação.
- Os ADRs e documentos de arquitetura documentam ProviderPort/Model Catalog, runtime, API/SSE e fronteiras de persistência.
- As imagens em `docs/images/readme/` foram derivadas de screenshots visuais existentes do frontend, sem credenciais ou dados externos.

## Cuidado de manutenção

Ao adicionar ou remover capacidades importantes, atualizar primeiro as documentações normativas correspondentes e depois revisar as afirmações, links e imagens do README. Manter explícita a diferença entre o runtime instalado local baseado em SQLite e arquiteturas de integração/produção descritas nos ADRs.
