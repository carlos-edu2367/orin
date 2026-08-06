# RFC 103 — Sistema de eventos

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](101-runtime.md), [RFC 102 — Ciclo de vida da Execution](102-execution-lifecycle.md), [RFC 104 — Pipeline de contexto](104-context-pipeline.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir o `EventBus` interno, o envelope canônico e as garantias de publicação, entrega, ordenação, deduplicação, consumo, retenção, replay, auditoria e segurança dos fatos relevantes do AgentOS.

## Fora de escopo

- escolha de broker, banco, outbox, transporte, serialização ou biblioteca concreta;
- protocolo de eventos destinado a clientes ou conexão frontend;
- endpoint, tabela, schema ORM ou topologia de deploy;
- Event Sourcing obrigatório para estado de domínio;
- garantia global de exactly-once;
- conteúdo detalhado de eventos que pertençam a RFCs futuras.

## Responsabilidades e não responsabilidades

O sistema de eventos DEVE:

- registrar fatos imutáveis no passado com identidade, tempo, origem e ownership;
- preservar correlação, causalidade e sequência por `Execution`;
- separar confirmação do fato de sua entrega aos consumidores;
- tolerar entrega ao-menos-uma-vez com deduplicação explícita;
- permitir auditoria e replay autorizados dentro da retenção;
- proteger conteúdo sensível e impedir vazamento entre usuários e Workspaces.

O `EventBus` NÃO DEVE:

- aceitar Event como substituto de comando ou autorização;
- conceder acesso porque um consumidor conhece `event_id` ou `execution_id`;
- expor banco, outbox ou broker ao frontend;
- prometer ordenação global ou exactly-once;
- transportar objetos proprietários de Provider ou adapter;
- permitir que consumidor altere o fato da fonte;
- ser o único local do estado atual de uma entidade sem RFC que adote Event Sourcing explicitamente.

## Arquitetura

```text
Produtor de domínio / ExecutionControl
   │ DomainChange + EventEnvelope
   ▼
TransactionalPersistence.transact ──> estado + outbox no mesmo commit
                                               │ publicação assíncrona
                                               ▼
                                        OutboxPublisher ──> EventBus
                           ┌──────────┼──────────┐
                           ▼          ▼          ▼
                       projeção   auditoria   automação
```

O produtor confirma mudança de estado e entrada de outbox exclusivamente por `TransactionalPersistence.transact`, contrato canônico da RFC 601. `ExecutionControl` é a fachada de domínio para mudanças de `Execution`; ela não constitui uma segunda transação. Um publicador entrega entradas já confirmadas ao `EventBus`. Adapters podem variar, mas não podem criar janela em que o estado seja confirmado e seu Event seja perdido definitivamente.

Interfaces de cliente observam eventos por uma porta de aplicação ou transporte autorizado. Elas não leem outbox, broker ou banco e não publicam Events de domínio diretamente.

## Envelope canônico

```text
EventEnvelope<TPayload> {
  event_id: EventId
  event_type: EventType
  event_version: PositiveInteger
  occurred_at: Instant
  source: EventSource
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  sequence: ExecutionSequence | null
  user_id: UserId
  workspace_id: WorkspaceId | null
  execution_id: ExecutionId | null
  classification: DataClassification
  payload: TPayload
}
```

- `event_id` é opaco, único e imutável;
- `event_type` usa `PascalCase` e descreve fato passado;
- `event_version` versiona o contrato do tipo, não a ordem do fato;
- `occurred_at` é UTC em RFC 3339/ISO 8601 com offset explícito;
- `source` identifica módulo ou componente lógico público, não endereço ou segredo de infraestrutura;
- `correlation_id` agrupa o fluxo lógico;
- `causation_id` aponta a causa direta quando conhecida;
- `execution_id` é obrigatório e não nulo se o fato pertence a uma `Execution`;
- `sequence` é obrigatório e não nulo exatamente quando `execution_id` for não nulo;
- `user_id` é obrigatório para fatos pertencentes a pessoa;
- `workspace_id` segue o ownership da entidade e não pode ser removido por adapter;
- `classification` orienta exposição e retenção, sem substituir autorização;
- `payload` contém dados mínimos do fato e referências preferencialmente a conteúdo volumoso.

## Contratos públicos

```text
interface OutboxPublisher {
  publish_pending(request: PublishOutboxBatch) -> OutboxPublishResult

  pre: toda entrada pertence a commit COMMITTED da RFC 601
  post: publicação não cria, altera nem confirma estado de domínio
  invariant: retry reutiliza o mesmo event_id
}

PublishOutboxBatch {
  publisher_ref: PublisherRef
  partition_ref: OutboxPartitionRef
  after_position: OutboxPosition | null
  maximum_events: PositiveInteger
  lease: PublicationLease
}
```

`OutboxPublishResult` informa Events publicados, pendências e falhas por referência operacional; ele não funciona como receipt de commit. Não existe `EventPublisher.append`: produtores registram Events somente no campo `outbox` de `TransactionRequest` da RFC 601, diretamente por sua fachada de domínio quando houver uma.

```text
interface EventBus {
  publish(batch: EventEnvelope<EventPayload>[]) -> PublishReceipt
  subscribe(subscription: SubscriptionSpec, consumer: EventConsumer) -> SubscriptionRef
}

interface EventConsumer {
  handle(delivery: EventDelivery) -> DeliveryDisposition
}

EventDelivery {
  event: EventEnvelope<EventPayload>
  delivery_id: DeliveryId
  attempt: PositiveInteger
}

DeliveryDisposition = ACKNOWLEDGED | RETRYABLE_FAILURE | PERMANENT_FAILURE
```

```text
interface EventArchive {
  query(query: AuthorizedEventQuery) -> EventPage
  replay(request: ReplayRequest) -> ReplayJobRef

  pre: ator possui autorização para todos os escopos solicitados
  post: replay não altera identidade, payload ou occurred_at do Event original
}
```

## Publicação transacional e outbox conceitual

Sempre que um fato acompanha mudança persistente, ambos DEVEM ser registrados pela única unidade atômica de `TransactionalPersistence.transact`. A outbox transacional abstrata é obrigatória como semântica, sem impor tabela, broker ou biblioteca:

1. validar comando, ownership e versão;
2. preparar `DomainChange` e `OutboxEntry` com `EventEnvelope` e IDs definidos;
3. confirmar estado e entradas de outbox juntos em um único `TransactionReceipt`;
4. publicar pendências no `EventBus`;
5. marcar entrega operacional sem alterar o Event;
6. repetir publicação quando a confirmação de entrega for incerta.

Para fatos externos que não compartilham a mesma unidade transacional, o adapter DEVE oferecer chave idempotente, reconciliação ou registro durável antes de afirmar o fato. Um Event só usa nome de sucesso após confirmação.

Falha após o passo 3 pode causar publicação duplicada, mas não perda definitiva. Falha antes do passo 3 não publica fato não confirmado.

## Semântica de entrega e deduplicação

A entrega é **ao-menos-uma-vez**. O mesmo `event_id` pode chegar repetidamente, inclusive após timeout ou replay. Consumidores DEVEM:

- deduplicar por `event_id` no escopo do consumidor;
- tornar efeitos idempotentes ou registrar processamento e efeito numa unidade atômica conceitual;
- reconhecer somente depois de confirmar o efeito necessário;
- classificar falha como retryable ou permanente;
- não gerar novo fato de sucesso para uma duplicata já aplicada;
- preservar `correlation_id` e usar o `event_id` causador em `causation_id` de novos Events.

`delivery_id` distingue tentativas operacionais, mas não substitui `event_id` para deduplicação. Exactly-once de entrega não é prometido.

## Ordenação e sequência

Events vinculados à mesma `Execution` recebem `sequence` inteira, positiva, única e estritamente crescente na ordem em que os fatos são confirmados. Não há lacunas semânticas exigidas: lacunas percebidas podem indicar atraso, filtro de autorização ou retenção, e consumidores que exigem ordem devem aguardar ou reconciliar.

Garantias:

- nenhum par da mesma `Execution` compartilha sequência;
- sequência maior representa confirmação posterior na ordem lógica, independentemente do relógio de parede;
- entrega pode ocorrer fora de ordem e deve ser reordenada quando o consumidor depender disso;
- Events sem `execution_id` não usam `sequence` de Execution;
- não existe ordem total entre Executions;
- `occurred_at` não é usado sozinho para inferir causalidade.

Se um consumidor detectar sequência anterior à já aplicada, trata como duplicata ou evento atrasado. Se detectar lacuna, não inventa o fato ausente.

## Consumidores

Cada consumidor declara:

```text
SubscriptionSpec {
  consumer_name: ConsumerName
  accepted_event_types: EventTypePattern[]
  accepted_versions: EventVersionRange[]
  ownership_scope: OwnershipScope
  ordering_requirement: NONE | PER_EXECUTION
  data_clearance: DataClassification
  replay_policy: ReplayPolicy
}
```

Consumidores são isolados: falha de um não bloqueia confirmação do produtor nem progresso de outros. Backoff, limite de tentativas e quarentena são políticas operacionais substituíveis. Falhas permanentes permanecem observáveis e auditáveis; não são descartadas silenciosamente.

Consumidor deve rejeitar versão incompatível de forma explícita. Mudança incompatível de payload cria nova `event_version`; produtores podem oferecer janela de coexistência sem alterar Events históricos.

## Retenção, replay e auditoria

Retenção é definida por tipo, classificação, obrigação de auditoria e ownership. Ela DEVE declarar duração explícita ou política de permanência, descarte seguro e efeito sobre replay. Remover conteúdo por política não permite falsificar o envelope; quando necessário, referências podem expirar e o Event preserva metadados mínimos autorizados.

Replay:

- relê Events históricos sem mudar `event_id`, sequência ou tempo;
- exige ator autorizado e escopo de `user_id`/`workspace_id` explícito;
- é distinguido de entrega ao vivo por metadado operacional fora do envelope imutável;
- respeita versão aceita, classificação, retenção e rate limits;
- não deve repetir efeito externo sem deduplicação ou modo de reconstrução seguro;
- gera trilha auditável da solicitação e de seu resultado.

Auditoria permite reconstruir fatos, transições e cadeia causal, mas não implica armazenar prompts, arquivos ou segredos dentro de payloads.

## Catálogo inicial

Todos os nomes abaixo relatam fatos passados. Payloads são mínimos e podem evoluir por versão compatível.

| Event | Fato confirmado | Campos específicos mínimos |
| --- | --- | --- |
| `AgentCreated` | identidade persistente de Agent foi criada | `agent_id`, `agent_version` |
| `ExecutionStarted` | `Execution` entrou em `RUNNING` | `agent_id`, `state_version`, `started_at` |
| `ExecutionFinished` | `Execution` entrou em `COMPLETED` | `result_ref`, `usage`, `finished_at` |
| `ExecutionCancelled` | `Execution` entrou em `CANCELLED` | `reason_code`, `state_version`, `finished_at` |
| `ToolStarted` | invocação autorizada de Tool foi aceita | `tool_ref`, `invocation_id`, `arguments_summary` |
| `ToolFinished` | invocação de Tool terminou com resultado explícito | `tool_ref`, `invocation_id`, `outcome`, `result_ref` |
| `BrowserOpened` | Resource de browser foi aberto por porta autorizada | `browser_session_ref`, `resource_policy_ref` |
| `MemorySaved` | registro de Memory foi persistido por decisão explícita | `memory_id`, `memory_scope`, `provenance_ref` |
| `DecisionCreated` | decisão estruturada foi registrada | `decision_id`, `decision_type`, `provenance_ref` |
| `CheckpointCreated` | checkpoint seguro foi confirmado | `checkpoint_id`, `iteration`, `state_version` |

`execution_id` é não nulo quando o fato resulta de trabalho de uma `Execution`, como deve ocorrer para ações de usuário, Agent, Tool, Browser, Memory e Decision. Eventos administrativos verdadeiramente externos só podem omiti-lo se sua RFC definir o fato como não pertencente a trabalho executável; isso não cria caminho lateral para realizar trabalho.

`arguments_summary`, razões e referências DEVEM ser sanitizados. `ToolFinished.outcome` distingue sucesso, falha e cancelamento; o nome afirma apenas que a invocação terminou, não que teve sucesso.

Eventos adicionais iniciais usados pelo Kernel incluem `ExecutionQueued`, `ExecutionWaitingForTool`, `ExecutionWaitingForUser`, `ExecutionPaused`, `ExecutionResumed` e `ExecutionFailed`, com os mesmos requisitos.

## Fluxo normal

1. Produtor confirma o fato e seu envelope por `TransactionalPersistence.transact`, por meio da fachada de domínio aplicável.
2. Publicador encontra a pendência e envia ao `EventBus`.
3. O `EventBus` entrega a cada consumidor autorizado, possivelmente mais de uma vez.
4. Consumidor valida tipo, versão, ownership e sequência.
5. Consumidor deduplica, aplica efeito e confirma a entrega.
6. Registros de operação permitem medir atraso e falha sem alterar o Event.

## Fluxo de falha

- falha antes da confirmação: nenhum Event de sucesso é publicado;
- falha após confirmação e antes de entrega: pendência é republicada;
- confirmação de entrega perdida: Event pode ser entregue novamente;
- consumidor retryable: nova tentativa preserva `event_id`;
- consumidor permanentemente incompatível: entrega é isolada e alertada;
- lacuna de sequência: consumidor aguarda, busca replay autorizado ou reconcilia estado pela porta responsável;
- Event malformado ou não autorizado: é rejeitado e auditado, nunca corrigido silenciosamente em trânsito.

## Fluxo de cancelamento

Cancelar uma `Execution`, subscription ou replay ocorre por comando próprio. O Event `ExecutionCancelled` é publicado somente quando o terminal foi confirmado. Cancelar replay impede novas entregas do job, mas não apaga Events nem desfaz efeitos já confirmados. Cancelar consumidor não muda o fato da fonte e preserva seu cursor/checkpoint conforme política declarada.

## Segurança e dados sensíveis

- publicação e consumo validam `user_id`, `workspace_id`, finalidade e classificação;
- conhecer um ID não concede acesso;
- segredos, tokens, credenciais, cookies e chaves privadas são proibidos em envelope e payload;
- conteúdo volumoso ou sensível deve permanecer em Artifact/Memory apropriado e ser representado por referência autorizada;
- payloads aplicam minimização, sanitização e redaction antes da confirmação;
- filtros de cliente são aplicados em porta autorizada e não em consulta direta ao storage;
- replay e auditoria são ações auditadas e sujeitas à mesma política de acesso;
- consumidores não podem aumentar privilégios nem cruzar ownership por correlação.

## Observabilidade

Métricas incluem Events confirmados e publicados, atraso de outbox, tentativas, duplicatas, lacunas, atraso por consumidor, falhas permanentes, backlog, replay e descarte por retenção. Logs e traces usam IDs, tipo, versão e sequência, sem payload sensível. Alertas distinguem falha do produtor, da publicação e do consumidor.

## Extensibilidade

Novos tipos registram owner, versão, finalidade, classificação, esquema conceitual, retenção e compatibilidade. Consumidores podem ser adicionados sem modificar produtor. Adapters de bus, archive e publicação são substituíveis. Uma mudança de tecnologia não altera envelope, entrega ao-menos-uma-vez ou deduplicação.

## Invariantes

- Event é fato imutável no passado, nunca comando;
- todo Event possui identidade, instante, origem, correlação e ownership aplicável;
- Event de `Execution` possui `execution_id` e sequência estritamente crescente;
- estado confirmado e intenção de publicação não podem divergir permanentemente;
- entrega é ao-menos-uma-vez e consumidores deduplicam;
- não existe garantia de ordenação global ou exactly-once;
- replay preserva identidade e não ignora autorização;
- frontend não acessa banco, outbox ou broker diretamente;
- payload não contém segredo nem objeto proprietário;
- consumidor não adquire ownership do estado da fonte.

## Futuro

Schema registry, assinaturas de integridade, compactação, particionamento e replicação multi-região poderão especializar adapters. Garantias mais fortes podem existir localmente, mas consumidores continuam corretos sob o contrato mínimo de entrega duplicável e ordem somente por `Execution`.
