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
