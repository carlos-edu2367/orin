# RFC 403/402 — Filesystem e Resource Manager: especificação de desenho

**Data:** 2026-08-07  
**Status:** decisão de implementação para os dois gates  
**Escopo:** contratos públicos, adapters de referência e local, leasing, autorização por operação, contenção, quotas, eventos, persistência e reconciliação.

## Objetivo

Fechar RFC 403 antes de RFC 402, entregando uma porta de Filesystem independente de tecnologia e um Resource Manager que seja a única autoridade para alocar, autorizar, revogar, liberar e reconciliar Resources. O fluxo Filesystem usa Workspace para ownership, root, fencing e quota; não duplica lifecycle ou autoridade de Workspace.

## Brainstorming e decisão

Foram consideradas três fronteiras:

1. **Portas separadas com composição explícita (escolhida).** `agentos.filesystem` define modelos, validação e `FilesystemPort`; `agentos.resources` define catálogo, leases, handles e adapters. O Resource Manager injeta um validador de handle no Filesystem. Um adapter in-memory prova corridas e um adapter local usa somente roots provisionadas por um resolvedor interno autorizado. Essa opção preserva containment, permite testar cada política isoladamente, mantém o Resource Manager como autoridade de lease e funciona em Windows/Linux com capability detection e fail-closed.
2. **Um serviço monolítico de Resource/Filesystem.** Simplifica composição inicial, mas mistura path policy com catálogo, torna impossível substituir Filesystem sem alterar leasing e facilita bypass de ownership/quota. Rejeitada por violar as fronteiras das RFCs 402/403.
3. **Somente adapters de referência.** Seria suficiente para contratos abstratos, mas não entrega o adapter local operacional exigido pelo ADR 005 e não demonstraria revalidação contra a filesystem real. Rejeitada; o adapter local implementará apenas capacidades que consegue provar.

## Arquitetura escolhida

```text
WorkspaceManager ──> WorkspaceRootAdapter / Workspace leases / quota
        │
        ├── ResourceManagerService
        │     ├── catalog + policy + lease/fence state
        │     ├── ResourceAdapter (Filesystem/Terminal/Browser)
        │     ├── usage + audit + cleanup supervisor
        │     └── optional TransactionalPersistence outbox journal
        │
        └── FilesystemService ──> FilesystemPort
                    ├── logical WorkspacePath policy
                    ├── root resolver + identity revalidation
                    ├── quota reservation/accounting hooks
                    ├── in-memory reference adapter
                    └── local adapter com boundary física privada
```

`FilesystemOperationContext` e `ResourceOperationContext` carregam `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`. Todo request de Filesystem carrega `lease_id` e `AuthorizedResourceHandle`; o handle é a prova operacional emitida pelo Resource Manager, não um ID suficiente por si só.

`WorkspaceRootResolver` retorna somente `CanonicalWorkspaceRoot` com root ref, identity e policy version opacas. O adapter local usa um binding interno não exportado para obter o diretório físico; nenhum caminho físico, handle nativo, PID, sessão ou conteúdo aparece em modelo, erro, evento, auditoria ou persistência.

## Contratos e fluxo de autoridade

### Filesystem

`WorkspacePath` é uma tupla não vazia de segmentos seguros. O parser rejeita caminho absoluto, drive, UNC, URL, device namespace, `~`, variáveis, alternate data stream, separadores, segmentos vazios, `.`/`..`, controles, Unicode ambíguo e case policy não determinística. O adapter revalida cada componente contra a root, rejeita links/reparse/mount/hard-link não provado e falha fechado quando não consegue garantir containment.

As operações `stat`, `list`, `read`, `create_directory`, `write`, `move`, `copy` e `remove` retornam resultados tipados com `NOT_FOUND`, `REJECTED`, `CONFLICT`, `QUOTA_EXCEEDED`, `TIMEOUT`, `CANCELLED` e `UNKNOWN_EFFECT`. `read` entrega bytes somente a um `ByteSink` limitado. `write` suporta `CREATE_NEW`, `REPLACE`, `APPEND`, `REQUIRE_ATOMIC` e `BEST_EFFORT`, com versionamento e idempotência. Move/copy exigem origem e destino sob a mesma root.

Antes de cada efeito, o serviço valida contexto, capability, lease, handle, root identity, versão esperada, symlink policy, tipo, depth, bytes e reservas de quota. Depois do efeito, revalida identity, containment, tamanho e versão antes de publicar o resultado e o Event.

### Resource Manager

O catálogo registra `FILESYSTEM`, `TERMINAL` e `BROWSER`, capacidades, modos de isolamento, limites, saúde e adapter lógico. `acquire` valida contexto/ownership, Workspace ativo, capabilities, permissões, purpose, orçamento, duração, health e isolamento derivado; o chamador nunca fornece `isolation_key`. O lease recebe fence monotônico, estado e expiration.

`authorize` revalida contexto completo, lease, estado, capability, expiration, fence e budget, emitindo `AuthorizedResourceHandle` efêmero e não serializável vinculado a lease, operação e adapter. `renew`, `release`, `revoke` e `inspect` são idempotentes e revalidam o mesmo binding. Lease expirado, liberado, revogado, transferido ou com fence antigo é sempre rejeitado.

Filesystem, Terminal e Browser possuem adapters de referência completos para allocate/inspect/cancel/cleanup. Terminal e Browser permanecem simulados porque execução de shell e Playwright são limites tecnológicos das RFCs 404/405; seus handles, lifecycle, cancelamento e cleanup são reais dentro do adapter de referência e não atravessam a porta.

`CleanupSupervisor.sweep` percorre leases não terminais com limite e deadline. `reconcile` usa checkpoints bounded, quarentena e estado `UNKNOWN` explícito. Cleanup falho não devolve Resource saudável ao pool e publica `ResourceCleanupFailed` somente após a falha ter sido observada.

## Persistência, eventos e auditoria

O estado in-memory é o adapter determinístico de referência. A composição opcional com `TransactionalPersistence` grava snapshots bounded de leases, uso, fences, cleanup checkpoints e auditoria por `RecordChange`/`AuditChange`/`OutboxChange`; não grava handles vivos nem dados físicos. O `event_sink` em memória e o journal transacional compartilham o mesmo factory de envelopes mínimos. A publicação ocorre apenas depois da confirmação do fato; a outbox é a autoridade para retry pós-commit.

Eventos Filesystem: `FilesystemReadFinished`, `FilesystemEntryCreated`, `FilesystemEntryChanged`, `FilesystemEntryRemoved`, `FilesystemOperationRejected`. Eventos Resource: `ResourceLeaseGranted`, `ResourceLeaseRenewed`, `ResourceLeaseReleased`, `ResourceLeaseRevoked`, `ResourceLeaseExpired`, `ResourceCleanupFailed`. Payloads contêm somente IDs, tipo, Workspace, Execution, correlação, versões, contagens, uso, outcome e razão categórica.

Auditoria bounded responde ator, usuário, Workspace, Agent, Execution, purpose, capability, Resource lógico, instante, decisão e uso agregado. Path físico, bytes, conteúdo, handles e segredos são proibidos.

## Estratégia de testes

O desenvolvimento segue TDD em fatias: contratos/segurança, adapter in-memory, operações/quotas/corridas, adapter local/capability detection, Resource Manager/leases, adapters Terminal/Browser, persistência/outbox e integração ponta a ponta. Cada comportamento novo terá teste RED observado antes da implementação, GREEN e regressão.

Os testes cobrem ownership cruzado, binding de actor/purpose, traversal/links/root swap/races, symlink/reparse/mount/hard-link, quotas e reservas, overwrite/atomicidade/version conflict, timeout/cancelamento/efeito desconhecido, concorrência/idempotência/restart, handles não serializáveis, ausência de leakage, eventos pós-fato, round-trip RFC 601, integração com Workspace/Artifact e adapters de referência/local. O teste PostgreSQL permanece condicionado a `AGENTOS_TEST_POSTGRES_DSN` e, sem DSN, registra `skipped` sem simular sucesso.

## Limitações tecnológicas legítimas

O adapter local não promete uma garantia mais forte do que as primitives de segurança disponíveis no host: quando descriptor-relative/no-follow ou inspeção confiável de reparse não estiver disponível, a operação correspondente é rejeitada. O adapter de Terminal não inicia shell real e o de Browser não inicia Playwright; ambos demonstram a semântica de Resource da RFC 402, enquanto os contratos especializados de execução permanecem atrás das portas RFC 404/405. PostgreSQL, Redis, broker, HTTP e workers concretos continuam adapters externos e não entram no domínio.
