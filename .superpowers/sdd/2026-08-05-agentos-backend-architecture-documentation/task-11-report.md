# Relatório — Task 11: ADRs de execução, dados e eventos

**Status:** Concluída e verificada em 2026-08-06.

## Arquivos criados

- `docs/adr/001-arq-workers.md`
- `docs/adr/002-postgresql-as-system-of-record.md`
- `docs/adr/003-sse-for-client-event-streaming.md`
- `docs/adr/009-redis-for-ephemeral-coordination.md`

## Resumo

Os quatro ADRs registram as decisões aprovadas sem introduzir código: ARQ como adapter de Workers isolados por pool; PostgreSQL como fonte transacional de verdade; SSE como stream unidirecional e autorizado de projeções para o cliente; e Redis somente para coordenação efêmera e reconstruível.

Cada decisão declara benefícios, custos operacionais, falhas aceitas, limites explícitos, alternativas rejeitadas ou adiadas e vínculos com as RFCs normativas. Os ADRs preservam as invariantes de que PostgreSQL decide estado durável, Redis não o substitui, a API não executa Runtime e SSE não expõe Event Bus, outbox ou broker.

## Verificação

- Os quatro documentos estão em `docs/adr/` e estão em Markdown.
- Cada ADR contém: Status, Contexto, Decisão, Consequências, Alternativas consideradas e Relações com RFCs.
- Cada ADR explicita custos, falhas e o que a decisão não resolve.
- Os links relativos foram verificados resolvendo cada destino Markdown a partir do diretório de seu ADR; todos apontam para RFCs existentes.
- Nenhum arquivo contém backend, endpoint, schema executável ou código de produção.
