# RFC 601 — Persistência

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 301 — Memory](../300-context-memory/301-memory.md), [RFC 602 — Artifact Storage](602-artifact-storage.md), [RFC 603 — Workspaces](603-workspaces.md), [RFC 604 — Configuração](604-configuration.md)

## Objetivo

Definir a fronteira de persistência transacional do AgentOS, suas unidades de consistência, concorrência, publicação por outbox, retenção, recuperação e auditoria. PostgreSQL é a fonte transacional de verdade para estado durável; Redis é infraestrutura exclusivamente efêmera para filas, pub/sub, sessões, locks, cancelamentos e coordenação.

## Fora de escopo

- definir tabelas, índices, SQL, ORM, migrações ou topologia física;
- escolher serviço gerenciado, versão, driver, pool ou mecanismo de backup;
- transformar PostgreSQL em Artifact Storage ou guardar conteúdo binário volumoso no estado de domínio;
- usar Redis como fonte de verdade, arquivo histórico, ledger de auditoria ou substituto de transação;
- definir schemas internos de Memory, Artifact, Workspace ou Configuration além das garantias comuns;
- prometer consistência distribuída atômica entre PostgreSQL e sistemas externos.

## Responsabilidades e não responsabilidades

A camada de persistência DEVE:

- persistir entidades, versões, relacionamentos e Events duráveis no PostgreSQL;
- oferecer transações explícitas, controle de concorrência, idempotência e leitura consistente;
- confirmar mudança de domínio e entrada de outbox na mesma transação;
- separar estado durável de coordenação efêmera e reconstruir esta última quando perdida;
- aplicar ownership em toda consulta e mutação, inclusive no modo single-user;
- manter políticas versionadas de retenção, tombstones quando necessários e trilha de auditoria;
- suportar backup, restauração, verificação de integridade e reconciliação após desastre;
- esconder tecnologia e layout físico atrás de portas tipadas.

A camada NÃO DEVE:

- permitir leitura ou escrita direta pelo frontend, Agent, Tool, Capability ou Provider;
- autorizar operação apenas porque o chamador conhece um ID;
- retornar entidade de outro usuário ou Workspace como fallback;
- confirmar sucesso antes do commit durável ou mascarar `UNKNOWN` como sucesso;
- depender de chave, sessão, lock, fila ou cache Redis para preservar estado de domínio;
- manter segredo ou conteúdo sensível em log, Event, chave de cache ou label de métrica;
- executar regra de negócio que pertença ao componente proprietário da entidade.

## Arquitetura e autoridade dos stores

```text
Componentes de domínio
        │ portas tipadas + PersistenceOperationContext
        ▼
TransactionalPersistence
   ├── PostgreSQL: estado durável, versões, Events, outbox, auditoria
   ├── Redis: filas, pub/sub, sessões, locks, cancelamentos, coordenação efêmera
   └── ArtifactStorage: conteúdo durável volumoso por referência
```

PostgreSQL é a autoridade para decidir se Agent, Execution, Event, Memory, Workspace, configuração e referências duráveis existem e em qual versão. Redis pode acelerar ou coordenar o acesso, mas todo dado necessário para recuperar o estado correto após perda total do Redis reside no PostgreSQL ou em outra porta durável explicitamente proprietária, como `ArtifactStorage` para bytes.

Uma fila Redis representa trabalho a consumir, não a existência da `Execution`; pub/sub representa notificação, não o Event histórico; sessão representa autenticação efêmera, não o usuário; lock representa exclusão temporária, não ownership; token de cancelamento representa sinal, não o estado terminal. O componente proprietário confirma o efeito durável no PostgreSQL.

## Dados e escopos de consistência

```text
PersistenceOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

DurableRecord<T> {
  record_id: RecordId
  record_type: RecordType
  ownership: OwnershipRef
  value: T
  version: Version
  classification: DataClassification
  retention_policy_ref: RetentionPolicyRef
  created_at: Instant
  updated_at: Instant
}

OwnershipRef {
  user_id: UserId
  workspace_id: WorkspaceId | null
}
```

Operações administrativas sem Agent ou Execution de negócio ainda são executadas por uma `Execution` administrativa auditável; por isso operações sensíveis não omitem os seis campos de escopo. Registros estritamente globais podem ter `workspace_id = null`, mas sua mutação continua vinculada a usuário autorizado, Agent de sistema, Execution, correlação e finalidade.

```text
TransactionOptions {
  consistency: STRONG
  isolation_requirement: READ_COMMITTED | REPEATABLE_READ | SERIALIZABLE
  timeout: Duration
  read_only: Boolean
}

TransactionReceipt {
  transaction_id: TransactionId
  commit_state: COMMITTED | NOT_COMMITTED | UNKNOWN
  committed_at: Instant | null
  affected_versions: RecordVersionRef[]
  outbox_event_ids: EventId[]
}
```

`READ_COMMITTED` é o mínimo para operações comuns. Fluxos que dependem de múltiplas leituras estáveis usam `REPEATABLE_READ`; invariantes globais ou alocação concorrente sem chave natural segura exigem `SERIALIZABLE` ou mecanismo equivalente. A escolha é declarada pelo contrato do domínio e não rebaixada silenciosamente pelo adapter.

## Contratos tipados

```text
interface TransactionalPersistence {
  transact<T>(request: TransactionRequest<T>) -> TransactionResult<T>
  read<T>(query: AuthorizedRead<T>) -> AuthorizedRecord<T> | NotFound
  scan<T>(query: AuthorizedScan<T>) -> AuthorizedRecordPage<T>
  inspect_commit(query: InspectCommit) -> TransactionReceipt

  pre: contexto completo, autorização e limites foram validados
  post: resultado é limitado ao ownership e à classificação autorizados
  post: COMMITTED implica estado e outbox duráveis na mesma fronteira
  invariant: nenhuma outra porta confirma atomicamente DomainChange e Event
}

TransactionRequest<T> {
  transaction_id: TransactionId
  context: PersistenceOperationContext
  options: TransactionOptions
  expected_versions: RecordVersionRef[]
  idempotency_key: IdempotencyKey
  changes: DomainChange<T>[]
  outbox: OutboxEntry[]
}

OutboxEntry {
  event: EventEnvelope<EventPayload>
  source_record_ref: RecordRef
  expected_source_version: Version
  publication_partition: OutboxPartitionRef
}

TransactionResult<T> =
  | TransactionCommitted<T> { receipt: TransactionReceipt, result: T }
  | TransactionRejected { reason: PersistenceRejectionReason }
  | TransactionConflicted { current_versions: RecordVersionRef[] }
  | TransactionIndeterminate { transaction_id: TransactionId }
```

`DomainChange<T>` descreve intenção tipada pertencente ao componente de domínio; não expõe SQL, tabela ou objeto ORM. `OutboxEntry` é parte da mesma solicitação e não uma chamada posterior de publicação. Toda mudança relevante que exige Event inclui ao menos uma entrada; Event sem mudança persistente usa uma mudança de registro factual/auditoria sob a mesma fronteira. `expected_versions` implementa concorrência otimista. A mesma `idempotency_key`, no mesmo ownership, operação e finalidade, retorna o efeito previamente confirmado ou conflito explícito; nunca duplica mutação.

```text
AuthorizedRead<T> {
  context: PersistenceOperationContext
  record_ref: RecordRef<T>
  expected_version: Version | null
  classification_ceiling: DataClassification
  consistency: STRONG
}

AuthorizedScan<T> {
  context: PersistenceOperationContext
  record_type: RecordType
  ownership_scope: OwnershipScope
  filter: BoundedDomainFilter
  page: PageRequest
  classification_ceiling: DataClassification
  consistency: STRONG
}

InspectCommit {
  context: PersistenceOperationContext
  transaction_id: TransactionId
  idempotency_key: IdempotencyKey
}
```

Consultas sempre incluem filtro de ownership imposto pelo servidor. `NotFound` não distingue inexistência de falta de autorização para atores que não podem observar essa diferença. Paginação usa cursor opaco vinculado ao escopo e à versão da consulta.

```text
interface EphemeralCoordination {
  enqueue(request: EnqueueWork) -> QueueReceipt
  publish(request: PublishNotification) -> PublishReceipt
  create_session(request: CreateSession) -> SessionRef
  acquire_lock(request: AcquireEphemeralLock) -> LockLease
  signal_cancel(request: SignalCancellation) -> CancellationReceipt

  invariant: nenhum retorno desta porta confirma efeito durável de domínio
  invariant: perda total do conteúdo pode ser recuperada a partir de autoridade durável
}

EphemeralContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  expires_at: Instant
}
```

Redis só pode materializar os tipos `QUEUE_ITEM`, `PUBSUB_NOTIFICATION`, `SERVER_SESSION`, `LOCK_LEASE`, `CANCELLATION_SIGNAL` e `COORDINATION_STATE`. Cada item possui TTL ou regra explícita de expiração. Cache adicional só é permitido como projeção descartável, nunca para tomar decisão de autorização ou existência sem revalidação durável.

## Transações, concorrência e idempotência

`TransactionalPersistence.transact` é a única fronteira atômica de escrita do acervo. Fachadas de domínio, inclusive `ExecutionControl`, validam invariantes e constroem `TransactionRequest`, mas não implementam commit alternativo. `EventBus`, `OutboxPublisher`, Redis e notificações operacionais atuam somente depois de `COMMITTED` e não podem tornar uma mutação válida. Um `TransactionIndeterminate` é inspecionado por `inspect_commit` antes de qualquer nova tentativa.

Uma transação cobre apenas estado que compartilha a unidade atômica do PostgreSQL. Mudanças multi-entidade que preservam um invariante são confirmadas juntas. Chamadas a Provider, Artifact Storage, filesystem ou outro sistema não permanecem abertas dentro de transação longa; usa-se reserva durável, outbox, estado intermediário explícito e compensação idempotente.

Locks de banco são curtos e limitados. Locks Redis podem reduzir concorrência operacional, mas não protegem sozinhos invariantes: expiração, failover ou partição podem criar dois detentores. Toda escrita revalida versão, estado e ownership no PostgreSQL. Deadlock, serialization failure e timeout são resultados retryable somente quando o comando é idempotente e o orçamento permite.

## Outbox conceitual

Para toda mudança com Event correspondente:

1. o domínio valida comando, ownership, finalidade, versão e classificação;
2. a transação grava `changes` e cada `OutboxEntry` juntos;
3. após commit, um publicador lê pendências autorizadas e publica no `EventBus`;
4. confirmação incerta provoca nova publicação do mesmo `event_id`;
5. consumidores deduplicam segundo a RFC 103;
6. a posição operacional da publicação pode ser compactada, mas o Event segue sua retenção própria.

Falha entre commit e publicação cria atraso, não perda definitiva. A outbox é conceitual: esta RFC não impõe tabela ou broker. Nenhuma gravação em Redis substitui a confirmação transacional.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `PersistenceTransactionCommitted` | unidade transacional sensível foi confirmada |
| `PersistenceTransactionConflicted` | versões impediram mutação concorrente |
| `OutboxPublicationDelayed` | fato durável aguarda publicação além do limite |
| `RetentionRunFinished` | aplicação de política terminou com contagens explícitas |
| `PersistenceRecoveryFinished` | recuperação e reconciliação chegaram a resultado conhecido |
| `PersistenceIntegrityViolationDetected` | verificação encontrou divergência durável |

Eventos operacionais minimizam payload e não incluem valores persistidos, SQL, segredo ou configuração sensível. Eventos de domínio específicos continuam pertencendo às RFCs responsáveis.

## Retenção, exclusão e auditoria

Cada categoria durável declara política versionada, base de retenção, prazo, classificação, obrigação legal, tratamento de derivados e forma de descarte. Expiração lógica precede remoção física quando referências, auditoria ou restauração exigirem uma janela segura. Exclusão não confirmada não é anunciada como concluída.

Tombstones mínimos impedem ressurreição por retry, replay, cache, réplica atrasada ou restauração. Eles não conservam conteúdo excluído e possuem retenção própria. Legal hold suspende descarte sem ampliar autorização. Auditoria registra ator, seis campos sensíveis, comando, decisão, versões, política e outcome; não duplica conteúdo.

## Backup, recuperação e reconciliação

Backups devem ser criptografados, testados e associados a objetivos documentados de ponto e tempo de recuperação. Restauração ocorre em ambiente isolado, valida integridade e ownership antes de promover o store restaurado. Recuperar PostgreSQL e Artifact Storage para instantes divergentes exige reconciliação de referências; bytes órfãos ou referências sem conteúdo ficam em quarentena, nunca são silenciosamente reutilizados.

Redis não é restaurado como autoridade. Após indisponibilidade ou perda, filas, locks, cancelamentos e projeções são reconstruídos a partir de Executions e estados duráveis elegíveis. Sessões podem ser invalidadas. Locks antigos são considerados expirados; o PostgreSQL decide se o trabalho ainda pode prosseguir.

## Fluxo normal

1. Componente envia comando com contexto, expectativa de versão e idempotência.
2. A porta valida autorização, classificação e escopo.
3. PostgreSQL lê e bloqueia somente o necessário no isolamento requerido.
4. Estado, auditoria mínima e outbox são preparados.
5. Commit confirma todos ou nenhum dos efeitos.
6. O publicador entrega Events; coordenação Redis acorda workers ou clientes.
7. Resultados retornam versões e referência de commit, não detalhes físicos.

## Fluxo de falha

- validação, autorização ou versão divergente falham antes do efeito;
- conflito ou deadlock não é convertido em overwrite silencioso;
- falha antes do commit resulta em `NOT_COMMITTED`;
- perda de conexão no commit resulta em `UNKNOWN` e exige `inspect_commit` antes de retry;
- falha de Redis não desfaz commit nem altera verdade durável;
- outbox atrasada é republicada e alertada;
- corrupção ou divergência isola a partição lógica afetada e inicia reconciliação;
- restauração incompleta não é promovida como store saudável.

## Fluxo de cancelamento

Cancelamento antes do commit interrompe novas operações e desfaz a transação. Após o commit, a mutação permanece confirmada; o cancelamento impede etapas seguintes e não apaga o Event. Sinal Redis perdido não altera o pedido durável de cancelamento quando este existir no estado da Execution. Rotinas de retenção e recuperação param em limites seguros, preservam checkpoint durável e relatam progresso parcial sem afirmar conclusão.

## Segurança

- toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- acesso é negado por padrão e queries são particionadas por ownership;
- credenciais de banco não entram em configuração legível por Agent nem em Events;
- dados em trânsito, repouso, backups e réplicas usam proteção compatível com classificação;
- conexões e papéis seguem menor privilégio e separam leitura, escrita, migração e recuperação;
- mensagens de erro não revelam existência cross-workspace, SQL ou valores;
- Redis não recebe segredos duráveis, conteúdo volumoso ou dados necessários à recuperação;
- exportação, retenção e remoção preservam consentimento e obrigações auditáveis.

## Observabilidade

Métricas incluem latência e taxa por operação, commits, rollbacks, conflitos, deadlocks, conexões, saturação, idade da outbox, retries, resultados indeterminados, backlog de retenção, duração de backup/restore e violações de integridade. Redis expõe filas, atraso, TTL, locks, sessões e cancelamentos apenas como saúde efêmera. Logs e traces usam IDs, tipos, versões, purpose e códigos sanitizados; queries, valores, tokens e payloads não são labels.

## Invariantes

- PostgreSQL é a fonte transacional de verdade do estado durável.
- Redis serve somente filas, pub/sub, sessões, locks, cancelamentos e coordenação efêmera.
- perda completa de Redis não pode destruir nem reescrever estado de domínio.
- nenhuma decisão de ownership depende apenas de cache ou lock efêmero.
- estado e outbox do mesmo fato são confirmados na mesma transação conceitual.
- commit indeterminado é inspecionado antes de retry.
- concorrência nunca causa last-write-wins implícito em dados versionados.
- toda mutação sensível é idempotente, correlacionável e auditável.
- retenção e restauração não ampliam acesso nem ressuscitam conteúdo removido.
- tecnologia, schema e handles de conexão não atravessam as portas públicas.

## Extensibilidade

Novos stores podem ser adicionados como projeções, índices ou portas especializadas, desde que declarem autoridade, consistência, reconstrução, retenção e falha. Nenhum novo store pode substituir silenciosamente PostgreSQL como verdade transacional ou Artifact Storage como proprietário de conteúdo. Sharding, replicas e cache preservam filtros de ownership e semântica de versão.

## Futuro

Particionamento, réplicas de leitura, multi-região, change data capture, arquivamento frio, criptografia por tenant e recuperação contínua poderão especializar adapters. Qualquer evolução deve manter verdade transacional única, outbox confiável, Redis descartável, auditoria mínima e recuperação verificável.
