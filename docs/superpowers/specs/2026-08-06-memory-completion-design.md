# Memory Completion Design

**Status:** aprovado para execução nesta sessão

## Objetivo

Fechar as lacunas comprovadas na auditoria da RFC 301 e na integração aplicável da RFC 303 sem transformar o adapter in-memory em durabilidade de produção. A entrega preserva a separação entre Context temporário e Memory persistente, mantém portas substituíveis e deixa a composição transacional da RFC 601 explícita.

## Decisões

1. `MemorySearchAdapter` declara capabilities. O adapter de referência oferece somente `LEXICAL`; ausência de embeddings não é anunciada como busca semântica.
2. `MemoryManager.search` aplica ownership, Grant, classificação, status, validade e filtros de proveniência antes de criar referências públicas, tocar conteúdo ou chamar ranking. Busca não consome Grant; consumo pertence à resolução compartilhada idempotente.
3. `MemoryMatch` permanece reference-first e recebe citação verificável derivada de `memory_id`, versão, `source_ref`, integridade, provenance e razões bounded. O `MemoryContextSource` entrega somente candidatos de referência ao ContextManager e não grava Memory.
4. Os comandos `AuthorizeContextShare`, `CreateSharedContextReference`, `CreateStructuredHandoff`, `ResolveSharedContext`, `RevokeContextShare` e `ExpireContextShare` entram em `agentos.context.sharing`, que é a fonte canônica da RFC 303. O adapter `InMemoryMemorySharingService` vive em `agentos.memory` e implementa essa porta sem duplicar os modelos.
5. Resolução de Memory compartilhada exige autorização da fonte, Grant RFC 303 ativo, destino/Execution/purpose/classification exatos, versão e integridade. Revogação, expiração, invalidamento, supersession e mudança de versão falham fechado; retries da mesma chave são idempotentes e não consomem novamente.
6. A composição RFC 601 é representada por uma porta/protocolo de commit que recebe uma operação de Memory junto de revisão, auditoria e outbox. O adapter in-memory continua explicitamente process-local; `UNKNOWN` não é convertido em sucesso e exige inspeção.

## Fluxos e limites

```text
SaveMemory autorizado
  -> revisão + auditoria + outbox
  -> busca lexical bounded e explicável
  -> MemoryReference/citation
  -> Grant RFC 303
  -> SharedContextReference/Handoff
  -> resolve no destino
  -> MemoryContextSource
  -> ContextManager monta Context temporário
  -> revoke/expire/invalidate bloqueia nova resolução
```

Nenhum conteúdo completo, prompt, segredo, credencial, localização física ou consulta livre entra em Event, log, receipt, `repr`, referência ou snapshot. Nenhuma operação de Context chama mutação de Memory. Cross-user e cross-workspace permanecem negados por padrão; `USER` mantém `workspace_id=None` na origem e só é exposta por Grant explícito.

## Falhas e durabilidade

Negação, conflito, cancelamento, timeout e resultado indeterminado são outcomes distintos. Uma resolução ou commit não confirmado não publica sucesso. A implementação não escolhe PostgreSQL, Redis, Artifact Storage, broker, worker, índice vetorial ou modelo de embedding. O adapter in-memory é evidência de contrato e comportamento substituível, não uma promessa de durabilidade.

## Testes

Os testes novos cobrem capabilities, prefilter antes de conteúdo/ranking, ranking determinístico, citação/provenance, limites, deduplicação e invalidação; o ciclo completo MemoryReference→Grant→SharedContextReference/Handoff→resolve→Context; consumo/revogação/expiração/cross-scope; e composição RFC 601 com `UNKNOWN`/inspeção. A suíte transversal e os scans obrigatórios continuam sendo a evidência final.

## Matriz resumida de implementação

| Requisito | Implementação | Teste | Evidência esperada |
|---|---|---|---|
| RFC 301 lifecycle, ownership, versionamento, lineage, retenção e Events | `memory/models.py`, `security.py`, `in_memory.py` | `tests/unit/memory/` existente + lifecycle/classification regressions | suíte completa, compileall e diff check |
| RAG bounded e substituível | capabilities lexical, prefilter no Manager, citation em `MemoryMatch`, `MemoryContextSource` reference-first | `test_memory_search_and_consolidation.py`, `test_memory_context_compat.py` | focused RAG + scans dirigidos |
| RFC 303 canônica | comandos/modelos em `context/sharing.py`, `InMemoryMemorySharingService` | `test_memory_sharing.py`, `test_memory_sharing_boundaries.py` | ciclo resolve→Context e revoke/expire fechado |
| Context temporário sem escrita implícita | `MemoryContextSource.collect`/`collect_shared` e `ContextManagerService` sem mutators | testes de Context e integration boundaries | scan de `.save(` e `MemoryManager` |
| RFC 601 composition | `MemoryTransactionalCommitPort`, `MemoryCommitState`, `inspect_commit` | `test_memory_persistence_composition.py`, store UNKNOWN regression | outcome `UNKNOWN` sem receipt de sucesso |

## Limitações verdadeiras

O adapter continua process-local e não oferece durabilidade, migrações, recuperação após reinício, busca híbrida/semântica, embeddings, índice vetorial, Artifact Storage ou publicação exactly-once. A porta de composição RFC 601 existe, mas um adapter durável não foi implementado nesta sessão. O compartilhamento resolve referências e entrega candidatos; o próprio ContextManager do destino continua responsável pela montagem, orçamento e sanitização final.

## Evidência final — 2026-08-06

- `python -m pytest -q`: exit 0, `372 passed, 1 skipped`.
- `python -m compileall -q src tests`: exit 0.
- Scan de tecnologias proibidas: nenhuma ocorrência em `src/agentos/memory` ou `src/agentos/context`.
- Scan dirigido de Memory/Context: nenhuma operação de mutação de Memory no Context; ocorrências restantes são APIs, testes e metadados de autorização esperados.
- `git diff --check`: exit 0.
- Alterações pré-existentes do workspace permanecem preservadas e não fazem parte da entrega.
