# RFC 801 — Workers e filas

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 402 — Resource Manager](../400-tools-resources/402-resource-manager.md), [RFC 405 — Browser](../400-tools-resources/405-browser.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md), [RFC 702 — Segurança](../700-api-security/702-security.md)

## Objetivo

Definir filas, pools de Worker, despacho, admissão, concorrência, backpressure, leases, retries, cancelamento, recuperação e isolamento para o processamento assíncrono do AgentOS.

## Fora de escopo

- escolher biblioteca, broker, processo, container, autoscaler ou topologia física;
- implementar funções de Worker, Runtime, fila ou infraestrutura;
- redefinir a máquina de estados de `Execution`;
- executar trabalho pesado na API ou automação de browser fora de Browser Workers;
- usar fila, lock, heartbeat ou cache como fonte durável de verdade.

## Responsabilidades e não responsabilidades

O subsistema de Workers DEVE:

- rotear trabalho ao pool compatível e aplicar limites por usuário, Workspace, classe e recurso;
- despachar apenas `Execution`s duráveis elegíveis, por referência e com contexto completo;
- tolerar entrega duplicada sem duplicar transição ou efeito;
- propagar cancelamento, timeout e correlação ao Runtime e aos Resources;
- detectar perda de lease, recuperar trabalho órfão e preservar checkpoints e efeitos já confirmados;
- oferecer backpressure explícito antes de esgotar processo, browser, provider ou store.

O subsistema NÃO DEVE:

- alterar estado de domínio fora de `ExecutionControl` e das portas proprietárias;
- interpretar um Agent como Worker ou um item de fila como `Execution`;
- confiar em lock Redis para proteger invariante durável;
- serializar Task, prompt, credencial, segredo, cookie ou conteúdo de Artifact na fila;
- redespachar indefinidamente nem transformar falha permanente em retry silencioso.

## Arquitetura e pools

```text
Produtores autorizados ──> DispatchCoordinator ──> filas particionadas
                                 │                    ├── Agent Pool
Estado durável / outbox <────────┤                    ├── Browser Pool
                                 │                    ├── Maintenance Pool
Cancelamento / leases / quotas ──┘                    └── Scheduler Pool
                                                          │
                                  Runtime / Resource Manager / Scheduler
```

| Pool | Trabalho permitido | Isolamento mínimo | Trabalho proibido |
| --- | --- | --- | --- |
| `AGENT` | loop de Agent, Provider, Tool e Capability sem browser | concorrência e orçamento por usuário, Workspace, Agent e classe de modelo | browser local, scheduler e manutenção global |
| `BROWSER` | sessões e ações mediadas pelo Browser Runtime | processo/contexto de browser, perfil, download e quota separados por ownership | acesso direto ao banco, execução geral de Agent e reuso cross-Workspace |
| `MAINTENANCE` | retenção, reconciliação, outbox, integridade e limpeza | escopo administrativo mínimo, lotes limitados e checkpoint | assumir Task de usuário ou contornar auditoria |
| `SCHEDULER` | detectar ocorrências vencidas, watchdogs e criar despachos idempotentes | eleição/lease com fencing e acesso somente aos contratos de agenda | executar a carga agendada ou o Runtime do Agent |

Cada implantação PODE compartilhar host físico, mas os pools permanecem filas, identidades operacionais, limites, credenciais e políticas distintas. Saturação do Browser Pool não consome reservas do Scheduler ou Maintenance Pool. Trabalho de usuário nunca herda privilégio administrativo do processo que o hospeda.

## Contexto operacional sensível

```text
WorkerOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  authorization_ref: AuthorizationDecisionRef
  configuration_snapshot_ref: ConfigurationSnapshotRef
  classification_ceiling: DataClassification
}
```

Todos os campos são obrigatórios, inclusive para manutenção e Scheduler, que usam `agent_id` de sistema e uma `Execution` administrativa. A autorização é revalidada ao adquirir trabalho; `authorization_ref` é evidência, não grant transferível.

## Item de trabalho e filas

```text
WorkItem {
  work_item_id: WorkItemId
  dispatch_id: DispatchId
  dispatch_attempt_id: DispatchAttemptId
  execution_id: ExecutionId
  context: WorkerOperationContext
  pool: WorkerPool
  work_kind: WorkKind
  priority: LOW | NORMAL | HIGH | CRITICAL_SYSTEM
  payload_ref: ImmutableWorkPayloadRef
  expected_execution_version: Version
  not_before: Instant
  expires_at: Instant
  attempt_number: PositiveInteger
  attempt_limit: PositiveInteger
  timeout: Duration
  idempotency_key: IdempotencyKey
  enqueued_at: Instant
}

Dispatch {
  dispatch_id: DispatchId
  execution_id: ExecutionId
  pool: WorkerPool
  work_kind: WorkKind
  state: PENDING | ACTIVE | SUCCEEDED | CANCELLED | EXPIRED | QUARANTINED
  version: Version
  idempotency_key: IdempotencyKey
  created_at: Instant
  updated_at: Instant
}

DispatchAttempt {
  dispatch_attempt_id: DispatchAttemptId
  dispatch_id: DispatchId
  attempt_number: PositiveInteger
  state: ENQUEUED | LEASED | ACKNOWLEDGED | RELEASED | EXPIRED | QUARANTINED
  version: Version
  reason_code: DispatchAttemptReason | null
  enqueued_at: Instant
  leased_at: Instant | null
  finished_at: Instant | null
}

WorkerPool = AGENT | BROWSER | MAINTENANCE | SCHEDULER

DispatchableWork =
  | AgentExecutionWork {
      work_kind: AGENT_EXECUTION
      target_pool: AGENT
      payload_ref: ImmutableWorkPayloadRef
    }
  | BrowserActionWork {
      work_kind: BROWSER_ACTION
      target_pool: BROWSER
      payload_ref: ImmutableWorkPayloadRef
    }
  | MaintenanceOperationWork {
      work_kind: MAINTENANCE_OPERATION
      target_pool: MAINTENANCE
      payload_ref: ImmutableWorkPayloadRef
    }
  | SchedulerOperationWork {
      work_kind: SCHEDULE_EVALUATION | WATCHDOG_CHECK
      target_pool: SCHEDULER
      payload_ref: ImmutableWorkPayloadRef
    }

QueuePartition {
  pool: WorkerPool
  ownership_bucket: OwnershipBucket
  priority: QueuePriority
  resource_class: ResourceClass
}
```

`payload_ref` aponta para intenção imutável e autorizada; não contém conteúdo sensível. `dispatch_id` identifica a decisão lógica de despachar uma `Execution`; `dispatch_attempt_id` identifica uma tentativa operacional dessa decisão. Redelivery preserva ambos os IDs e apenas incrementa contagem de entrega interna não usada como identidade. Retry operacional cria novo `DispatchAttempt` e preserva `dispatch_id`; retry de domínio após terminal cria nova `Execution`, novo `Dispatch` e nova causalidade conforme a RFC 102.

A unicidade conceitual de `DispatchAttempt` é `(dispatch_id, attempt_number)`, e cada `dispatch_attempt_id` referencia exatamente um `dispatch_id`. Auditoria, métricas e reconstrução registram os dois níveis para não contar redelivery como retry nem retry como nova intenção.

Filas são particionadas por pool e podem subdividir prioridade e classe de recurso. Fairness ponderada impede que um usuário, Workspace ou fila de alta taxa monopolize capacidade. `CRITICAL_SYSTEM` é reservado a recuperação e segurança explicitamente autorizadas, nunca a preferência arbitrária de usuário.

O mapeamento de `DispatchableWork` é fechado e global. API, Scheduler, reconciliador e produtores internos passam a união discriminada; nenhum deles fornece combinação livre de `work_kind` e `target_pool`. `WorkItem.pool/work_kind/payload_ref` são a projeção exata dessa união. Combinação divergente é rejeitada antes de criar `Dispatch`, inclusive em retry, replay e recovery. Task/Skill usam `AGENT_EXECUTION`; browser só recebe `BROWSER_ACTION` emitida pelo Browser Runtime; Scheduler jamais recebe Task/Skill Runtime.

## Contratos tipados

```text
interface WorkQueue {
  enqueue(command: EnqueueWork) -> EnqueueReceipt
  reserve(request: ReserveWork) -> WorkLease | NoWorkAvailable
  renew(command: RenewWorkLease) -> WorkLeaseReceipt
  acknowledge(command: AcknowledgeWork) -> AcknowledgeReceipt
  release(command: ReleaseWork) -> ReleaseReceipt
  signal_cancel(command: SignalWorkCancellation) -> CancellationSignalReceipt

  pre: contexto, pool, limites e referência durável foram validados
  post: aceite na fila não implica início nem sucesso da Execution
  invariant: redelivery preserva dispatch_id, dispatch_attempt_id e idempotency_key
}

interface DispatchCoordinator {
  submit(command: SubmitExecutionWork) -> DispatchReceipt
  inspect(query: InspectDispatch) -> DispatchView
  cancel(command: CancelDispatch) -> DispatchCancellationReceipt
  expire(command: ExpireWorkItem) -> WorkExpiryReceipt
  reconcile(command: ReconcileExecutionDispatch) -> ReconciliationReceipt

  pre: Execution existe, pertence ao contexto e está elegível
  post: despacho aceito fica reconstruível a partir do estado durável
}

EnqueueWork {
  operation_id: WorkerOperationId
  context: WorkerOperationContext
  item: WorkItem
  queue_partition: QueuePartition
}

ReserveWork {
  worker_id: WorkerId
  pool: WorkerPool
  supported_resource_classes: ResourceClass[]
  max_items: PositiveInteger
  lease_duration: Duration
}

WorkLease {
  lease_id: LeaseId
  item: WorkItem
  worker_id: WorkerId
  fencing_token: MonotonicFence
  acquired_at: Instant
  expires_at: Instant
}

ExpireWorkItem {
  operation_id: WorkerOperationId
  context: WorkerOperationContext
  dispatch_id: DispatchId
  dispatch_attempt_id: DispatchAttemptId
  expected_execution_version: Version
  expected_attempt_version: Version
  lease_id: LeaseId | null
  fencing_token: MonotonicFence | null
  observed_at: Instant
  reason: QUEUE_DEADLINE_EXCEEDED | EXECUTION_DEADLINE_EXCEEDED |
          ACTIVE_ATTEMPT_DEADLINE_EXCEEDED
  idempotency_key: IdempotencyKey
}

SubmitExecutionWork {
  operation_id: WorkerOperationId
  context: WorkerOperationContext
  execution_id: ExecutionId
  expected_execution_version: Version
  work: DispatchableWork
  admission_class: AdmissionClass
  idempotency_key: IdempotencyKey
}

DispatchReceipt =
  | DispatchAccepted { dispatch_id: DispatchId, queued_at: Instant }
  | DispatchAlreadyAccepted { dispatch_id: DispatchId }
  | DispatchDeferred { reason: BackpressureReason, retry_after: Duration | null }
  | DispatchRejected { reason: DispatchRejectionReason }

WorkExpiryReceipt =
  | WorkAttemptExpired { dispatch_attempt_id: DispatchAttemptId,
                         resulting_attempt_version: Version,
                         execution_outcome: DurableOutcomeRef }
  | WorkAttemptRedispatched { expired_attempt_id: DispatchAttemptId,
                              new_attempt_id: DispatchAttemptId,
                              resulting_attempt_version: Version }
  | WorkAttemptQuarantined { dispatch_attempt_id: DispatchAttemptId,
                             resulting_attempt_version: Version,
                             recovery_execution_id: ExecutionId }
  | WorkExpiryAlreadyApplied { dispatch_attempt_id: DispatchAttemptId,
                               resulting_attempt_version: Version }
  | WorkExpiryConflict { current_attempt_version: Version,
                         current_lease_id: LeaseId | null,
                         current_fencing_token: MonotonicFence | null }
```

Reuso da mesma chave com payload semanticamente igual retorna o mesmo `dispatch_id`; payload incompatível é rejeitado. Confirmação incerta é reconciliada por `InspectDispatch`, nunca resolvida criando item diferente por suposição.

`DispatchCoordinator.submit` valida o discriminante de `work` antes de persistência e novamente ao materializar a fila. Destino inválido retorna `DispatchRejected(INVALID_WORK_POOL_MAPPING)`; adapter de fila não pode corrigi-lo nem redirecioná-lo.

## Admissão, concorrência e backpressure

Antes de enfileirar e antes de iniciar, o coordenador verifica limites de itens, bytes de referência, idade, concorrência, Resource, Provider e custo estimado. Limites formam ceilings por sistema, pool, usuário, Workspace, Agent e classe de recurso; o mais restritivo prevalece.

Backpressure possui três resultados explícitos: `REJECT` para pedido inválido ou sem quota, `DEFER` com prazo limitado para pressão temporária e `SHED` apenas para trabalho descartável declarado. `Execution` aceita e durável não pode desaparecer por shedding: permanece `QUEUED`, recebe causa observável e é reconciliada. Produtores usam jitter e backoff limitado; polling apertado é proibido.

Concorrência do Browser inclui sessões, páginas, memória e downloads, não apenas jobs. Maintenance usa lotes e yield para não bloquear tráfego. Scheduler mantém reserva mínima para watchdogs. Dimensionamento físico pode mudar sem alterar os contratos.

## Leases, locks e fencing

Reserva de fila concede lease temporário, não ownership de domínio. Toda renovação confirma que Worker, item e fence ainda coincidem. Ao perder lease, o Worker interrompe novos efeitos, sinaliza cancelamento cooperativo e não confirma transição posterior.

Locks efêmeros incluem namespace de ambiente, `user_id`, `workspace_id`, recurso e finalidade. Um `fencing_token` monotônico acompanha cada escrita sensível; a porta durável rejeita token anterior e também revalida `expected_execution_version`. Expiração, failover ou partição podem produzir dois detentores, portanto exclusão Redis reduz disputa, mas não prova unicidade.

## Retries e quarentena

São distintos:

- **redelivery de transporte:** repete o mesmo `WorkItem`, `dispatch_id`, `dispatch_attempt_id` e efeito idempotente após ack perdido;
- **retry operacional:** cria novo `dispatch_attempt_id` e incrementa `attempt_number` sob o mesmo `dispatch_id`, preservando a `Execution` e contabilizando tempo/custo;
- **retry de domínio:** cria nova `Execution` e novo `dispatch_id` após terminal, com relação causal explícita.

Backoff é exponencial com jitter, teto, deadline e orçamento. Falhas permanentes, item inválido, versão incompatível, ownership divergente ou limite esgotado vão para quarentena observável por referência minimizada; nunca são repetidos. Retirar item da quarentena exige `Execution` administrativa autorizada e mantém a mesma identidade se for redelivery, ou cria nova `Execution` se for nova tentativa de domínio.

## Expiração do item

`WorkItem.expires_at` é o prazo operacional do attempt, não o terminal implícito da `Execution`. A fila NÃO DEVE reservar item expirado nem simplesmente descartá-lo. Um expirer/reconciliador executa `ExpireWorkItem`, lê estado e versões duráveis e solicita a decisão correspondente ao `ExecutionControl` com reason code explícito.

Para attempt `ENQUEUED`, `lease_id` e `fencing_token` devem ser nulos e o CAS usa `expected_attempt_version`. Para attempt `LEASED`, ambos são obrigatórios e devem coincidir com o lease ativo, além da versão; qualquer valor ausente, expirado ou antigo retorna `WorkExpiryConflict`. A transição que confirma expiração incrementa a versão do attempt.

- Se apenas o envelope em fila expirou e a `Execution` ainda está `QUEUED`, elegível e dentro de seu limite total, o attempt vira `EXPIRED`, é reconhecido na fila e um novo `DispatchAttempt` pode ser criado sob o mesmo `dispatch_id`.
- Se o deadline total da `Execution` expirou, o coordenador solicita ao `ExecutionControl` a transição de timeout permitida pela RFC 102. O item só recebe ack após outcome durável conhecido; expiração nunca é registrada como cancelamento sem comando de cancelamento.
- Se `expires_at` vence com lease ativo, o Worker interrompe novos efeitos, estabiliza ou reconcilia a operação pendente e solicita timeout ao `ExecutionControl`. O lease não autoriza confirmar resultado depois do prazo.
- Se versão, commit ou efeito permanecer `UNKNOWN`, o attempt vai para quarentena e um recovery idempotente é aberto; não há ack de sucesso nem novo attempt automático.
- Se a `Execution` já está terminal, o attempt expirado é reconhecido como stale, com referência ao terminal, sem reabrir a Execution.

`ExecutionControl` é o único owner da transição de estado da Execution. `DispatchCoordinator` é owner dos estados de `Dispatch`/`DispatchAttempt`; `WorkQueue` apenas materializa, reserva e reconhece o item. Toda decisão usa `expected_execution_version`, idempotency key e, quando havia lease, fence vigente.

### Transições concorrentes do attempt

| Origem | Destino | Condição atômica |
| --- | --- | --- |
| `ENQUEUED` | `LEASED` | reserve vence CAS de `version` e instala `lease_id`/fence novos |
| `ENQUEUED` | `EXPIRED` | expirer vence CAS antes da reserva, com lease/fence nulos |
| `ENQUEUED` | `QUARANTINED` | validação permanente vence CAS e registra razão |
| `LEASED` | `ACKNOWLEDGED` | Worker apresenta versão, `lease_id` e fence vigentes após outcome durável |
| `LEASED` | `RELEASED` | release/recovery apresenta versão, lease e fence vigentes |
| `LEASED` | `EXPIRED` | expirer estabiliza efeitos e apresenta versão, lease e fence vigentes |
| `LEASED` | `QUARANTINED` | outcome incerto/permanente e identidade operacional vigente |

`ACKNOWLEDGED`, `RELEASED`, `EXPIRED` e `QUARANTINED` são terminais para o attempt. Reserve e expiração concorrentes não podem ambos vencer: o primeiro CAS incrementa `version`; o perdedor relê. Renovação de lease também incrementa versão e pode emitir fence maior, invalidando expirer/Worker antigos. Novo retry cria outro attempt; nunca reabre um terminal.

## Fluxo normal

1. Uma transação confirma `Execution` em `QUEUED` e sua intenção de despacho.
2. O publicador/coordenador cria `Dispatch` e primeiro `DispatchAttempt`, materializando `WorkItem` idempotente na partição correta.
3. Um Worker compatível reserva lease, revalida ownership, versão, cancelamento, configuração e Resource.
4. `ExecutionControl` confirma `QUEUED -> STARTING`; somente então o Runtime apropriado é iniciado.
5. Heartbeats renovam o lease sem alterar estado de domínio.
6. O Runtime confirma checkpoints, uso, Events e terminal por portas públicas.
7. O Worker reconhece o item apenas após resultado durável conhecido; ack incerto é deduplicado no redelivery.

## Falhas, timeout e recuperação

- **fila indisponível:** a `Execution` permanece durável em `QUEUED`; outbox/reconciliador repõe o mesmo despacho quando a fila volta;
- **reinício de Worker:** lease expira, novo Worker reconcilia estado e checkpoint antes de redispatch;
- **Execution órfã:** watchdog compara estado durável, lease e heartbeat; fence antigo é invalidado e a RFC 102 decide `STARTING/RUNNING/WAITING_TOOL -> QUEUED`, `FAILED` ou reconciliação;
- **ack perdido:** o item reaparece com os mesmos `dispatch_id` e `dispatch_attempt_id` e não repete transição confirmada;
- **item expirado:** não é reservado; expirer chama `ExecutionControl`, registra reason code e então faz ack, novo attempt ou quarentena segundo outcome durável;
- **timeout de Resource:** nenhum efeito incerto é repetido antes de consultar/reconciliar a porta proprietária;
- **estado de commit desconhecido:** `inspect_commit` resolve o efeito antes de retry;
- **item venenoso:** quarentena registra reason code, versão e referência, sem payload sensível.

Recuperação nunca apaga uso, Events, checkpoints ou efeitos confirmados. Perda total da coordenação efêmera é reconstruída de `Execution`s elegíveis, outbox e registros duráveis.

## Cancelamento

`CancelDispatch` e sinal efêmero aceleram percepção, mas somente `ExecutionControl` confirma cancelamento. Item ainda não adquirido é marcado para não iniciar; item ativo recebe sinal cooperativo. O Worker verifica cancelamento antes de cada novo efeito e em limites seguros, estabiliza ação incerta e libera Resource. Se o sinal Redis for perdido, a leitura durável do estado impede continuação. Resultado tardio é auditado e não reabre terminal.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `WorkDispatched` | despacho durável foi materializado na fila |
| `WorkLeaseAcquired` | Worker adquiriu lease e fence válidos |
| `WorkLeaseLost` | lease deixou de autorizar novos efeitos |
| `WorkRetryScheduled` | retry operacional limitado foi agendado |
| `WorkItemExpired` | attempt expirou e recebeu decisão durável explícita |
| `WorkQuarantined` | item foi isolado após falha permanente ou limite |
| `WorkBackpressureApplied` | admissão foi adiada, recusada ou reduzida |
| `WorkRecoveryCompleted` | reconciliação chegou a estado conhecido |

Eventos seguem a RFC 103, carregam `execution_id`, `correlation_id`, `dispatch_id`, `dispatch_attempt_id`, `attempt_number`, pool e reason code minimizado. Entrega duplicada não cria nova tentativa sem decisão idempotente.

## Persistência e retenção

`Execution`, `Dispatch`, `DispatchAttempt`, idempotência, checkpoint, transições, custos e Events são duráveis. A materialização do item, leases, heartbeats, locks e sinais são efêmeros e possuem TTL. Registros de quarentena guardam referência, classificação, decisão e prazo; payload original continua no store proprietário. Índices conceituais cobrem `execution_id`, unicidade de `dispatch_id`, unicidade `(dispatch_id, attempt_number)`, `dispatch_attempt_id`, idempotência por ownership, estado/versão, pool/prioridade, `not_before`, `expires_at`, lease vencido e retenção.

## Segurança e isolamento

- credenciais de processo são específicas por pool e seguem privilégio mínimo;
- toda aquisição revalida `user_id`, `workspace_id`, `agent_id`, purpose e classificação;
- fila, lock, quota e deduplicação são namespaced por tenancy e ambiente;
- Browser Worker não compartilha perfil, cookies, downloads ou processo entre Workspaces;
- Maintenance e Scheduler não emprestam privilégio a payload de usuário;
- logs, Events e itens não contêm segredo, prompt, Task, resultado, cookie ou URL sensível;
- referências são opacas, curtas, expiradas e reautorizadas no consumo.

## Observabilidade

Métricas incluem backlog/idade por pool e classe, admissão, fairness, utilização, tempo de espera, leases adquiridos/perdidos, retries, quarentena, cancelamento, timeout, recovery e custo. Logs e spans carregam IDs de Execution, correlação, despacho, Worker, pool, tentativa e fence, sem payload. Alertas distinguem fila indisponível, starvation, lease churn, órfãos, poison items e saturação de Resource.

## Invariantes

- a API não executa Agent; trabalho pesado roda em Worker; browser roda somente em Browser Worker;
- todo trabalho pertence a uma `Execution`, inclusive manutenção e Scheduler;
- fila e lease não são fonte de verdade nem autorização;
- `work_kind` determina um único pool pelo mapa fechado e destino divergente é sempre rejeitado;
- somente um fence vigente pode confirmar novos efeitos, e toda escrita revalida estado durável;
- redelivery preserva `DispatchAttempt`; retry operacional cria outro attempt do mesmo `Dispatch`; nenhum deles cria nova `Execution` nem duplica efeito;
- expiração de item nunca descarta trabalho silenciosamente nem decide terminal fora de `ExecutionControl`;
- retry após terminal cria nova `Execution`;
- nenhum pool atravessa ownership ou herda privilégio de outro;
- perda total do estado efêmero é recuperável a partir do estado durável.

## Extensibilidade

Novos adapters de fila, estratégias de fairness e classes de Worker podem implementar estas portas. Novo pool exige matriz de trabalho permitido, credencial própria, limites, isolamento, cancelamento, recovery e sinais operacionais antes de ativação. Garantias mais fortes do broker não removem idempotência nem fencing.

## Futuro

Autoscaling preditivo, filas multi-região, placement por afinidade, preempção segura e capacidade reservada por organização poderão ser adicionados. Evoluções devem preservar identidade do despacho, autoridade durável, isolamento, deadline e correlação ponta a ponta.
