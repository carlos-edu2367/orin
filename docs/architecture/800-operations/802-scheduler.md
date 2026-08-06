# RFC 802 — Scheduler

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md), [RFC 604 — Configuração](../600-platform-data/604-configuration.md), [RFC 701 — API e SSE](../700-api-security/701-api-sse.md), [RFC 702 — Segurança](../700-api-security/702-security.md), [RFC 801 — Workers](801-workers.md), [RFC 902 — Skills](../900-extensibility/902-skills.md)

## Objetivo

Definir agendamento durável de `Execution`s futuras, recorrências de Skill, watchdogs e rotinas de manutenção, incluindo timezone, ocorrências, misfire, overlap, idempotência, concorrência, cancelamento e recuperação.

## Fora de escopo

- escolher expressão cron, biblioteca, fila, banco, serviço de tempo ou engine concreta;
- executar a carga agendada dentro do Scheduler Worker;
- definir o formato futuro de pacote de Skill;
- prometer disparo exatamente no instante nominal ou entrega exatamente uma vez;
- usar relógio local, timer em memória ou Redis como autoridade da agenda.

## Responsabilidades e não responsabilidades

O Scheduler DEVE:

- persistir intenção, regra temporal, ownership, versão, policy e próximo instante;
- materializar cada ocorrência lógica no máximo uma vez no estado durável;
- despachar de modo pelo menos uma vez, deduplicável, para o pool correto;
- resolver timezone e horário de verão de forma explícita e reproduzível;
- aplicar políticas limitadas de misfire, catch-up, overlap, retry e expiração;
- operar watchdogs e manutenção por comandos auditáveis, sem mutar domínios diretamente.

O Scheduler NÃO DEVE:

- executar Agent, Skill, Tool, browser ou limpeza no processo de agendamento;
- inferir autorização do criador para sempre; cada ocorrência é reautorizada;
- reescrever ocorrências históricas após editar uma agenda;
- criar avalanche ilimitada de catch-up;
- confundir nova ocorrência recorrente com retry da ocorrência anterior.

## Arquitetura

```text
Comando autorizado ──> ScheduleService ──> Schedule durável + Event/outbox
                                                │
Relógio UTC confiável ──> Scheduler Workers ──> claim com lease/fence
                                                │
                                      materializa Occurrence
                                                │
                                      DispatchCoordinator
                                                │
                                  Agent / Browser / Maintenance Worker
```

PostgreSQL é a autoridade para `Schedule` e `ScheduleOccurrence`. Redis PODE coordenar eleição, wakeup e locks, mas perda total não perde agendas. O Scheduler Pool apenas calcula, materializa e despacha; o pool de destino executa a `Execution` criada.

## Tipos de agenda

| Tipo | Semântica |
| --- | --- |
| `FUTURE_EXECUTION` | uma única ocorrência em instante futuro para Task imutável |
| `SKILL_RECURRENCE` | ocorrências recorrentes que criam `Execution` para referência e versão de Skill |
| `WATCHDOG` | verificação periódica de condição operacional que pode solicitar recovery, timeout ou alerta |
| `MAINTENANCE` | retenção, reconciliação, limpeza ou integridade em lotes e checkpoints |

Watchdog detecta e solicita; não escreve estado de `Execution`, lease ou Resource contornando a porta proprietária. Limpeza respeita retenção, legal hold, ownership e auditoria; não é exclusão arbitrária.

## Contexto sensível

```text
ScheduleOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  authorization_ref: AuthorizationDecisionRef
  classification_ceiling: DataClassification
}
```

Criar, alterar, pausar, retomar, cancelar e disparar agenda são ações atribuídas a uma `Execution`. Agendas globais de sistema usam usuário responsável, Agent de sistema e `workspace_id = null`; não omitem correlação nem purpose.

## Entidades e estados

```text
Schedule {
  schedule_id: ScheduleId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  state_owner: SCHEDULE_SERVICE
  schedule_type: ScheduleType
  target: ScheduledTarget
  rule: ScheduleRule
  timezone: IanaTimeZone
  dst_policy: DaylightSavingPolicy
  misfire_policy: MisfirePolicy
  overlap_policy: OverlapPolicy
  state: ACTIVE | PAUSED | CANCELLED | EXPIRED
  version: Version
  authorization_policy_ref: AuthorizationPolicyRef
  configuration_snapshot_policy: ConfigurationSnapshotPolicy
  next_fire_at: Instant | null
  starts_at: Instant
  ends_at: Instant | null
  created_execution_id: ExecutionId
  correlation_id: CorrelationId
  retention_policy_ref: RetentionPolicyRef
  created_at: Instant
  updated_at: Instant
}

ScheduleRule =
  | AtInstant { fire_at: Instant }
  | FixedInterval { interval: Duration, anchor_at: Instant }
  | CalendarRecurrence { expression: CalendarExpression }

ConfigurationSnapshotPolicy = PINNED | RESOLVE_AT_FIRE
ConfigurationSnapshotPolicyInput = PINNED | RESOLVE_AT_FIRE | DERIVE_FROM_TARGET

ScheduledTarget =
  | ScheduledTaskTarget {
      target_kind: TASK
      immutable_target_ref: TaskSnapshotRef
      target_version: Version
      destination_pool: AGENT
      limits: ExecutionLimits
    }
  | ScheduledSkillTarget {
      target_kind: SKILL
      binding: SkillScheduleBinding
      destination_pool: AGENT
    }
  | ScheduledWatchdogTarget {
      target_kind: WATCHDOG
      immutable_target_ref: WatchdogDefinitionRef
      target_version: Version
      destination_pool: SCHEDULER
      limits: ExecutionLimits
    }
  | ScheduledMaintenanceTarget {
      target_kind: MAINTENANCE_ROUTINE
      immutable_target_ref: MaintenanceRoutineRef
      target_version: Version
      destination_pool: MAINTENANCE
      limits: ExecutionLimits
    }

SkillScheduleBinding {
  skill_selector: ScheduledSkillSelector
  task_template_snapshot_ref: TaskTemplateSnapshotRef
  input_snapshot_refs: InputSnapshotRef[]
  execution_limits: ExecutionLimits
  skill_limits: SkillLimitRequest
  context_policy_snapshot_ref: ContextPolicySnapshotRef
  artifact_policy_snapshot_ref: ArtifactPolicySnapshotRef
  authorization_policy_snapshot_ref: AuthorizationPolicySnapshotRef
}

ScheduledSkillSelector =
  | PinnedSkillSelector {
      resolution_policy: PINNED
      skill_ref: SkillRef
      manifest_digest: Digest
      workflow_digest: Digest
    }
  | ResolveSkillAtFireSelector {
      resolution_policy: RESOLVE_AT_FIRE
      skill_id: SkillId
      version_constraint: SkillVersionConstraint
      resolution_policy_snapshot_ref: SkillResolutionPolicySnapshotRef
    }

MaterializedScheduledSkillTarget {
  skill_ref: SkillRef
  manifest_digest: Digest
  workflow_digest: Digest
  task_snapshot_ref: TaskSnapshotRef
  input_snapshot_refs: InputSnapshotRef[]
  execution_limits: ExecutionLimits
  skill_limits: SkillLimitRequest
  context_policy_snapshot_ref: ContextPolicySnapshotRef
  artifact_policy_snapshot_ref: ArtifactPolicySnapshotRef
  authorization_policy_snapshot_ref: AuthorizationPolicySnapshotRef
  resolution_evidence_ref: SkillResolutionEvidenceRef
}

ScheduleOccurrence {
  occurrence_id: OccurrenceId
  schedule_id: ScheduleId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  state_owner: SCHEDULE_ENGINE
  schedule_version: Version
  occurrence_version: Version
  logical_scheduled_at: Instant
  timezone: IanaTimeZone
  timezone_database_version: TimeZoneDatabaseVersion
  requested_local_time: LocalDateTime | null
  effective_local_time: LocalDateTime | null
  effective_offset: UtcOffset
  state: PLANNED | CLAIMED | MATERIALIZED | DISPATCHED |
         SKIPPED | CANCELLED | FAILED
  claim_id: ScheduleClaimId | null
  claim_owner: WorkerId | null
  claim_fencing_token: MonotonicFence | null
  claim_expires_at: Instant | null
  state_fencing_token: MonotonicFence
  materialized_skill_target: MaterializedScheduledSkillTarget | null
  execution_id: ExecutionId | null
  dispatch_id: DispatchId | null
  dispatch_attempt_count: NonNegativeInteger
  next_dispatch_attempt_at: Instant | null
  idempotency_key: IdempotencyKey
  reason_code: ScheduleOccurrenceReason | null
  created_at: Instant
  updated_at: Instant
}
```

Unicidade conceitual de ocorrência é `(schedule_id, schedule_version, logical_scheduled_at)`. `occurrence_id` e `idempotency_key` são determinísticos dentro desse escopo, sem expor dados sensíveis. Alterar regra cria nova versão; ocorrências confirmadas permanecem vinculadas à versão antiga.

`SkillScheduleBinding` é parte canônica e persistida de `ScheduledSkillTarget`; portanto `CreateSchedule.target` e `UpdateSchedule.replacement_target` carregam selector, snapshots de input e Task, limites e policies como uma única unidade versionada. `input_snapshot_refs` são referências imutáveis, autorizadas e sem segredo bruto. O Scheduler não busca inputs ou templates implícitos fora desse binding.

`Schedule.configuration_snapshot_policy` é a fonte canônica persistida da política de resolução. Para alvo `SKILL`, ela DEVE ser igual a `ScheduledSkillSelector.resolution_policy`: `PINNED` exige `PinnedSkillSelector`, e `RESOLVE_AT_FIRE` exige `ResolveSkillAtFireSelector`. Para os demais targets imutáveis desta RFC, a única política válida é `PINNED`. Nenhum consumidor escolhe entre os dois campos; após admissão, todos leem a política canônica do `Schedule` e tratam o discriminante do selector como prova estrutural de consistência.

`CreateSchedule.configuration_snapshot_policy` pode carregar o valor explícito ou `DERIVE_FROM_TARGET`. Na derivação, `PinnedSkillSelector` e targets imutáveis resultam em `PINNED`, enquanto `ResolveSkillAtFireSelector` resulta em `RESOLVE_AT_FIRE`; o serviço persiste somente o valor resolvido, nunca `DERIVE_FROM_TARGET`. Valor explícito contraditório, selector malformado ou alvo do qual não seja possível derivar exatamente uma política é rejeitado antes de criar a agenda. `UpdateSchedule` aplica a mesma normalização atomicamente e não pode conservar política antiga incompatível com um replacement target.

Para `PINNED`, o registro guarda `SkillRef` exata e digests imutáveis. Para `RESOLVE_AT_FIRE`, guarda `skill_id`, `version_constraint` e o snapshot da policy que limita a resolução; uma `SkillRef` exata nesse variant é proibida. Cada ocorrência persiste em `materialized_skill_target` a versão exata resolvida, digests, Task materializada, inputs, limites, snapshots de policy e evidência da decisão antes de criar a Execution. Falha, negativa ou incompatibilidade de resolução impede materialização e falha fechado.

`destination_pool` é derivado de `target_kind`, não uma escolha livre do chamador. `TASK` e `SKILL` sempre vão ao Agent Pool; qualquer browser necessário é solicitado pelo Agent via Browser Runtime e executado no Browser Pool. `WATCHDOG` fica no Scheduler Pool apenas para verificar e emitir comando à porta proprietária; `MAINTENANCE_ROUTINE` vai ao Maintenance Pool. Combinações diferentes são rejeitadas antes de persistir. Em particular, Scheduler Worker NÃO DEVE iniciar Runtime, Task ou Skill.

`schedule_type` também deve corresponder ao discriminante: `FUTURE_EXECUTION -> TASK`, `SKILL_RECURRENCE -> SKILL`, `WATCHDOG -> WATCHDOG` e `MAINTENANCE -> MAINTENANCE_ROUTINE`. Não existe alvo direto para Browser Pool.

Ao despachar pela RFC 801, `TASK` e `SKILL` são convertidos somente em `AgentExecutionWork`; `WATCHDOG`, somente em `SchedulerOperationWork(WATCHDOG_CHECK)`; e `MAINTENANCE_ROUTINE`, somente em `MaintenanceOperationWork`. O `DispatchCoordinator` revalida esse mapa global e rejeita divergência mesmo que o registro de agenda esteja malformado.

## Timezone e horário de verão

Todo instante persistido usa UTC. `Schedule.timezone` é a única fonte de timezone; `ScheduleRule` não repete esse campo. Regras de calendário exigem timezone IANA nomeado e versão de dados de timezone registrada na ocorrência. Offset fixo não substitui timezone em recorrência civil.

```text
DaylightSavingPolicy {
  ambiguous_local_time: EARLIER_OFFSET | LATER_OFFSET
  nonexistent_local_time: SKIP | SHIFT_FORWARD
}
```

Agenda é rejeitada sem política explícita. Para calendário, `requested_local_time` preserva o horário civil produzido pela regra; `effective_local_time`, `effective_offset` e `logical_scheduled_at` preservam o resultado após aplicar DST. `SKIP` confirma ocorrência `SKIPPED`; `SHIFT_FORWARD` mantém solicitado e efetivo distintos. Para regras puramente UTC, os campos locais podem ser nulos, mas timezone e versão continuam registrados. Mudança posterior da base de timezone não altera histórico; próximos cálculos usam a versão ativa e publicam ajuste observável se o instante mudar.

## Misfire, catch-up e overlap

```text
MisfirePolicy =
  | SkipMissed { grace: Duration }
  | FireOnceNow { grace: Duration }
  | CatchUpBounded { grace: Duration, max_occurrences: PositiveInteger,
                     max_age: Duration }

OverlapPolicy =
  | ForbidOverlap
  | Serialize
  | AllowBounded { max_active: PositiveInteger }
```

Uma ocorrência fica vencida quando o relógio passa `logical_scheduled_at + grace` sem materialização válida. `SKIP` registra cada ocorrência pulada dentro do horizonte avaliado; `FIRE_ONCE_NOW` colapsa o intervalo em uma ocorrência de recovery identificada; `CATCH_UP_BOUNDED` nunca excede quantidade ou idade. Excedente é marcado `SKIPPED`, não fica implícito.

`FORBID_OVERLAP` pula ou difere conforme regra declarada; `SERIALIZE` preserva ordem lógica e espera terminal da anterior; `ALLOW_BOUNDED` limita ativas. A contagem usa estado durável, não somente itens de fila. Falha terminal da ocorrência anterior não cancela recorrência salvo policy explícita.

## Máquinas de estado e ownership

`ScheduleService` é o único owner das transições comandadas de `Schedule`; `ScheduleEngine` é o único owner de expiração temporal e de `ScheduleOccurrence`; `DispatchCoordinator` confirma o despacho referenciado, mas não escreve ocorrência diretamente. Toda mutação recebe `expected_version`; claim, materialização e reconciliação também recebem `claim_id` e fence quando a ocorrência estiver `CLAIMED`.

### Schedule

| Origem | Destino | Owner | Operação e condição |
| --- | --- | --- | --- |
| inexistente | `ACTIVE` | `ScheduleService` | `create`, alvo/policies válidos e idempotência nova |
| `ACTIVE` | `PAUSED` | `ScheduleService` | `pause` autorizado com `expected_version` |
| `PAUSED` | `ACTIVE` | `ScheduleService` | `resume`; recalcula próximo instante e aplica misfire |
| `ACTIVE` | `ACTIVE` | `ScheduleService` | `update`; cria nova versão para futuro não materializado |
| `PAUSED` | `PAUSED` | `ScheduleService` | `update`; cria nova versão sem reativar |
| `ACTIVE` | `CANCELLED` | `ScheduleService` | `cancel` confirmado |
| `PAUSED` | `CANCELLED` | `ScheduleService` | `cancel` confirmado |
| `ACTIVE` | `EXPIRED` | `ScheduleEngine` | ocorrência única materializada ou `ends_at` ultrapassado |
| `PAUSED` | `EXPIRED` | `ScheduleEngine` | `ends_at` ultrapassado; pausa não prolonga validade |

`CANCELLED` e `EXPIRED` são terminais. Pausar uma agenda `PAUSED` e cancelar uma agenda `CANCELLED` retornam `AlreadyApplied` para a mesma idempotency key; retomar `ACTIVE`, reativar terminal, reduzir versão e editar histórico são rejeitados. `update` invalida claims da versão anterior por fence; ocorrências futuras `PLANNED/CLAIMED` da versão substituída recebem decisão `CANCELLED` com razão `SCHEDULE_VERSION_SUPERSEDED`, enquanto ocorrências já materializadas permanecem intactas.

### ScheduleOccurrence

| Origem | Destino | Owner | Operação e condição |
| --- | --- | --- | --- |
| inexistente | `PLANNED` | `ScheduleEngine` | cálculo durável e único da ocorrência |
| `PLANNED` | `CLAIMED` | `ScheduleEngine` | `claim_due`, Schedule `ACTIVE`, versão atual e lease/fence novo |
| `CLAIMED` | `CLAIMED` | `ScheduleEngine` | takeover/reconciliação de claim expirado persiste novo claim e fence estritamente maior |
| `CLAIMED` | `PLANNED` | `ScheduleEngine` | claim expirou sem efeito; `reconcile` invalida fence antigo |
| `PLANNED` | `SKIPPED` | `ScheduleEngine` | misfire/overlap/DST exige skip |
| `CLAIMED` | `SKIPPED` | `ScheduleEngine` | policy confirmada antes da materialização e fence vigente |
| `CLAIMED` | `MATERIALIZED` | `ScheduleEngine` | `materialize` confirma uma Execution, Event/outbox e limpa claim |
| `MATERIALIZED` | `MATERIALIZED` | `ScheduleEngine` | retry de despacho transitório incrementa contador e agenda próximo attempt |
| `MATERIALIZED` | `DISPATCHED` | `ScheduleEngine` | `DispatchCoordinator` prova aceite do mesmo `dispatch_id` |
| `MATERIALIZED` | `FAILED` | `ScheduleEngine` | despacho permanente/limite esgotado após reconciliação |
| `PLANNED` | `CANCELLED` | `ScheduleEngine` | agenda cancelada/expirada ou versão substituída antes de claim |
| `CLAIMED` | `CANCELLED` | `ScheduleEngine` | cancel/edit/expiração vence claim e fence antes de materializar |

`DISPATCHED`, `SKIPPED`, `CANCELLED` e `FAILED` são terminais para a ocorrência. Retry de entrega da fila não muda a ocorrência; retry de despacho mantém `MATERIALIZED`, o mesmo `execution_id` e `dispatch_id`, incrementando `dispatch_attempt_count`. Retry de domínio após terminal da Execution cria nova Execution relacionada, nunca outra materialização da ocorrência. Nenhuma transição de `PLANNED/CLAIMED` para `DISPATCHED` é permitida.

Cada escrita de `ScheduleOccurrence` exige `expected_occurrence_version` e fence igual a `state_fencing_token`; fence menor é rejeitado mesmo que a versão pareça atual. Materialização preserva no registro o fence que venceu a corrida. Dispatch/retry usam esse fence até uma reconciliação emitir fence maior; assim, um Scheduler antigo não confirma despacho depois de pause, cancel, edit ou takeover.

Pause impede novos claims e materializações. Claim já ativo é reconciliado para `PLANNED` antes de soltar o fence; ocorrência ainda não materializada será avaliada por misfire no resume. Cancel e edit vencem claims aplicáveis e impedem materialização com `schedule_version` antiga. Materialização que confirmou antes da mudança vence a corrida e continua independente; o comando concorrente não a apaga.

## Contratos tipados

```text
interface ScheduleService {
  create(command: CreateSchedule) -> ScheduleWriteReceipt
  update(command: UpdateSchedule) -> ScheduleWriteReceipt
  pause(command: PauseSchedule) -> ScheduleWriteReceipt
  resume(command: ResumeSchedule) -> ScheduleWriteReceipt
  cancel(command: CancelSchedule) -> ScheduleWriteReceipt
  inspect(query: InspectSchedule) -> AuthorizedScheduleView
  list_occurrences(query: ListScheduleOccurrences) -> OccurrencePage

  pre: contexto completo, alvo e seus snapshots persistíveis, timezone e policies são válidos
  pre: configuration_snapshot_policy é normalizável para exatamente um valor coerente com o target
  post: mutação e Event/outbox são confirmados na mesma fronteira conceitual
}

interface ScheduleEngine {
  claim_due(request: ClaimDueSchedules) -> ScheduleClaim[]
  materialize(command: MaterializeOccurrence) -> MaterializationResult
  dispatch(command: DispatchOccurrence) -> OccurrenceDispatchResult
  retry_dispatch(command: RetryOccurrenceDispatch) -> OccurrenceDispatchResult
  reconcile(command: ReconcileSchedule) -> ScheduleReconciliationReceipt

  pre: Worker pertence ao Scheduler Pool e possui lease/fence vigente
  pre: target_kind, schedule_type e destination_pool obedecem ao mapeamento fechado
  post: ocorrência em MATERIALIZED ou DISPATCHED possui exatamente uma execution_id durável
  post: ocorrência SKILL materializada possui MaterializedScheduledSkillTarget completo
  invariant: Schedule.configuration_snapshot_policy é a única policy canônica persistida
  invariant: Scheduler nunca inicia Runtime, Task ou Skill
}

CreateSchedule {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  target: ScheduledTarget
  configuration_snapshot_policy: ConfigurationSnapshotPolicyInput
  rule: ScheduleRule
  timezone: IanaTimeZone
  dst_policy: DaylightSavingPolicy
  misfire_policy: MisfirePolicy
  overlap_policy: OverlapPolicy
  starts_at: Instant
  ends_at: Instant | null
  idempotency_key: IdempotencyKey
}

ScheduleMutationCommand {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  schedule_id: ScheduleId
  expected_version: Version
  idempotency_key: IdempotencyKey
}

PauseSchedule = ScheduleMutationCommand
ResumeSchedule = ScheduleMutationCommand
CancelSchedule = ScheduleMutationCommand

UpdateSchedule extends ScheduleMutationCommand {
  replacement_target: ScheduledTarget | null
  replacement_configuration_snapshot_policy: ConfigurationSnapshotPolicyInput | null
  replacement_rule: ScheduleRule | null
  replacement_timezone: IanaTimeZone | null
  replacement_dst_policy: DaylightSavingPolicy | null
  replacement_misfire_policy: MisfirePolicy | null
  replacement_overlap_policy: OverlapPolicy | null
  replacement_ends_at: Instant | null
}

ClaimDueSchedules {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  worker_id: WorkerId
  due_before: Instant
  max_claims: PositiveInteger
  lease_duration: Duration
}

ScheduleClaim {
  claim_id: ScheduleClaimId
  schedule_id: ScheduleId
  schedule_version: Version
  occurrence_id: OccurrenceId
  occurrence_version: Version
  worker_id: WorkerId
  fencing_token: MonotonicFence
  expires_at: Instant
}

MaterializeOccurrence {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  schedule_id: ScheduleId
  expected_schedule_version: Version
  occurrence_id: OccurrenceId
  expected_occurrence_version: Version
  logical_scheduled_at: Instant
  claim_id: ScheduleClaimId
  fencing_token: MonotonicFence
  idempotency_key: IdempotencyKey
}

DispatchOccurrence {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  schedule_id: ScheduleId
  expected_schedule_version: Version
  occurrence_id: OccurrenceId
  expected_occurrence_version: Version
  fencing_token: MonotonicFence
  execution_id: ExecutionId
  dispatch_id: DispatchId
  idempotency_key: IdempotencyKey
}

RetryOccurrenceDispatch extends DispatchOccurrence {
  expected_attempt_count: NonNegativeInteger
  retry_reason: ScheduleDispatchRetryReason
}

ReconcileSchedule {
  operation_id: ScheduleOperationId
  context: ScheduleOperationContext
  schedule_id: ScheduleId
  expected_schedule_version: Version
  occurrence_id: OccurrenceId
  expected_occurrence_version: Version
  claim_id: ScheduleClaimId | null
  fencing_token: MonotonicFence
  reason: ScheduleReconciliationReason
  idempotency_key: IdempotencyKey
}

MaterializationResult =
  | OccurrenceMaterialized {
      occurrence_id: OccurrenceId
      execution_id: ExecutionId
      materialized_skill_target: MaterializedScheduledSkillTarget | null
    }
  | OccurrenceAlreadyMaterialized {
      occurrence_id: OccurrenceId
      execution_id: ExecutionId
      materialized_skill_target: MaterializedScheduledSkillTarget | null
    }
  | OccurrenceSkipped { occurrence_id: OccurrenceId, reason: MisfireReason }
  | MaterializationDeferred { reason: ScheduleDeferralReason, retry_after: Duration }
  | MaterializationRejected { reason: ScheduleRejectionReason }

OccurrenceDispatchResult =
  | OccurrenceDispatched { occurrence_id: OccurrenceId, dispatch_id: DispatchId,
                           resulting_version: Version }
  | OccurrenceDispatchAlreadyApplied { occurrence_id: OccurrenceId,
                                       dispatch_id: DispatchId,
                                       resulting_version: Version }
  | OccurrenceDispatchDeferred { occurrence_id: OccurrenceId,
                                 next_attempt_at: Instant,
                                 resulting_version: Version }
  | OccurrenceDispatchRejected { reason: ScheduleRejectionReason,
                                 current_version: Version }

ScheduleReconciliationReceipt =
  | ExpiredClaimTakenOver {
      occurrence_id: OccurrenceId
      previous_claim_id: ScheduleClaimId
      replacement_claim_id: ScheduleClaimId
      previous_fencing_token: MonotonicFence
      new_fencing_token: MonotonicFence
      resulting_state: CLAIMED
      resulting_occurrence_version: Version
    }
  | OccurrenceReturnedToPlanned {
      occurrence_id: OccurrenceId
      previous_fencing_token: MonotonicFence
      new_fencing_token: MonotonicFence
      resulting_state: PLANNED
      resulting_occurrence_version: Version
    }
  | MaterializationReconciled {
      occurrence_id: OccurrenceId
      execution_id: ExecutionId
      new_fencing_token: MonotonicFence
      resulting_state: MATERIALIZED
      resulting_occurrence_version: Version
    }
  | DispatchReconciled {
      occurrence_id: OccurrenceId
      execution_id: ExecutionId
      dispatch_id: DispatchId
      new_fencing_token: MonotonicFence
      resulting_state: DISPATCHED
      resulting_occurrence_version: Version
    }
  | DispatchRetryReconciled {
      occurrence_id: OccurrenceId
      dispatch_id: DispatchId
      next_attempt_at: Instant
      new_fencing_token: MonotonicFence
      resulting_state: MATERIALIZED
      resulting_occurrence_version: Version
    }
  | OccurrenceReconciliationFailed {
      occurrence_id: OccurrenceId
      reason: ScheduleReconciliationFailure
      new_fencing_token: MonotonicFence
      resulting_state: FAILED
      resulting_occurrence_version: Version
    }
  | ScheduleReconciliationPending {
      occurrence_id: OccurrenceId
      reason: INDETERMINATE_COMMIT | INDETERMINATE_DISPATCH
      retry_after: Duration
      new_fencing_token: MonotonicFence
      resulting_state: CLAIMED | MATERIALIZED
      resulting_occurrence_version: Version
    }
  | ScheduleAlreadyReconciled {
      occurrence_id: OccurrenceId
      resulting_state: PLANNED | CLAIMED | MATERIALIZED | DISPATCHED |
                       SKIPPED | CANCELLED | FAILED
      current_fencing_token: MonotonicFence
      current_occurrence_version: Version
    }
  | ScheduleReconciliationConflict {
      occurrence_id: OccurrenceId
      current_state: PLANNED | CLAIMED | MATERIALIZED | DISPATCHED |
                     SKIPPED | CANCELLED | FAILED
      current_fencing_token: MonotonicFence
      current_occurrence_version: Version
}
```

Em `UpdateSchedule`, `replacement_configuration_snapshot_policy = null` conserva a policy atual somente se `replacement_target` também for nulo ou for estruturalmente compatível com ela. Para trocar entre `PinnedSkillSelector` e `ResolveSkillAtFireSelector`, o comando DEVE enviar policy correspondente ou `DERIVE_FROM_TARGET`; a nova versão de Schedule confirma target e policy juntos. CAS por `expected_version` impede que updates concorrentes confirmem metade da mudança.

O mesmo comando e payload retorna o mesmo resultado. A mesma chave com outro payload é rejeitada. Materialização confirma `ScheduleOccurrence`, seu `materialized_skill_target` quando o alvo for Skill, nova `Execution` em `QUEUED` e Event/outbox em uma fronteira atômica conceitual. A fila pode receber duplicatas, mas o `execution_id` e a resolução da ocorrência não mudam.

Toda reconciliação que altera ou reserva estado compara `expected_occurrence_version` e persiste `new_fencing_token > fencing_token` recebido antes de devolver o receipt e emitir `ScheduleReconciled`. Em takeover de claim expirado, também invalida o claim anterior e devolve `previous_fencing_token` e o fence novo em `ExpiredClaimTakenOver` ou `OccurrenceReturnedToPlanned`. `AlreadyReconciled` apenas informa o fence vigente e `Conflict` força releitura; nenhum Worker pode agir apenas porque o TTL local expirou.

Reconciliação de materialização incerta consulta o commit/idempotency key da transação: `COMMITTED` devolve `MaterializationReconciled` com a única `execution_id`; `NOT_COMMITTED` pode retornar a `PLANNED` ou assumir novo claim com fence maior; `UNKNOWN` devolve `ScheduleReconciliationPending` e não cria outra Execution. Reconciliação de despacho consulta `dispatch_id`: aceite durável devolve `DispatchReconciled`; ausência confirmada agenda `DispatchRetryReconciled` sob o mesmo dispatch; resultado desconhecido permanece `MATERIALIZED` e pendente. Falha permanente somente produz `OccurrenceReconciliationFailed` depois de excluir efeito já confirmado.

## Semântica de disparo

O Scheduler garante detecção persistente e despacho **pelo menos uma vez**; não garante pontualidade absoluta nem execução exatamente uma vez. Uma ocorrência materializada corresponde a exatamente uma `Execution`. Redelivery usa a mesma Execution; retry após seu terminal cria nova Execution relacionada e não outra ocorrência.

A cada disparo são revalidados agenda ativa, versão, ownership, Agent/Skill/Task, autorização, policy, limites e validade das referências. `PINNED` revalida a `SkillRef` e os digests fixados. `RESOLVE_AT_FIRE` resolve `skill_id + version_constraint` sob `resolution_policy_snapshot_ref`, recebe outcome tipado da RFC 902 e persiste a versão/digests escolhidos e a evidência na ocorrência. Deny, versão desabilitada, incompatível, ausente ou resultado indeterminado falha fechado. Nenhuma opção permite recuperar segredo ou privilégio revogado.

## Fluxos normais por classe

- **futura:** materializa uma ocorrência, despacha e expira a agenda após confirmação;
- **Skill recorrente:** lê o `SkillScheduleBinding`, reautoriza inputs e policies, resolve o selector, persiste `MaterializedScheduledSkillTarget` e cria uma Execution por ocorrência com Task snapshot, limites e referências fixados;
- **watchdog:** coleta sinal autorizado, classifica condição e solicita comando idempotente de alerta, timeout ou recovery à porta proprietária;
- **manutenção:** cria Execution administrativa em lote, com checkpoint, budget, janela e pool `MAINTENANCE`.

## Falhas, timeout e recuperação

- **Scheduler parado:** no reinício consulta `next_fire_at`, aplica misfire e catch-up limitado;
- **dois schedulers:** unicidade durável e fence fazem apenas um materializar; o outro recebe `AlreadyMaterialized` ou conflito;
- **commit desconhecido:** inspeção por idempotency key resolve antes de repetir;
- **fila indisponível após materialização:** ocorrência fica `MATERIALIZED`; outbox/reconciliador repõe o mesmo despacho;
- **evento duplicado:** consumidores deduplicam por `event_id`; não cria ocorrência;
- **relógio regressivo ou salto:** Scheduler usa relógio monotônico para duração e UTC confiável para calendário, detecta desvio e pausa novos claims além do limite;
- **alvo revogado ou incompatível:** ocorrência falha/é pulada com reason code; não executa versão alternativa silenciosa;
- **watchdog sem certeza:** registra `INDETERMINATE` e reconcilia; não força terminal por ausência isolada de heartbeat;
- **limpeza interrompida:** novo Worker retoma checkpoint idempotente e revalida retenção.

## Cancelamento, pausa e edição

Pausar impede materializações futuras após a versão confirmada; ocorrência já materializada continua como `Execution` independente e só para por comando de cancelamento próprio. Cancelar torna a agenda terminal, mas não cancela Executions existentes sem comando explícito e autorizado. Editar cria nova versão e recalcula apenas futuro não materializado. Corridas usam `expected_version`; o primeiro commit decide, e o perdedor relê.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `ScheduleCreated` | agenda durável foi criada |
| `ScheduleUpdated` | nova versão passou a reger ocorrências futuras |
| `SchedulePaused` | novas materializações foram suspensas |
| `ScheduleResumed` | agenda voltou a ficar elegível |
| `ScheduleCancelled` | agenda deixou de aceitar novas ocorrências |
| `ScheduleOccurrenceMaterialized` | ocorrência e Execution foram confirmadas |
| `ScheduleOccurrenceClaimed` | ocorrência recebeu claim, versão e fence vigentes |
| `ScheduleOccurrenceSkipped` | ocorrência foi pulada por policy explícita |
| `ScheduleDispatchDelayed` | ocorrência materializada aguarda despacho |
| `ScheduleDispatchRetried` | novo attempt do mesmo dispatch foi agendado |
| `ScheduleReconciled` | claim, materialização ou despacho incerto chegou a estado conhecido |
| `ScheduleClaimTakenOver` | claim expirado foi invalidado e fence estritamente maior assumiu a ocorrência |
| `ScheduleClockDriftDetected` | desvio ultrapassou limite operacional |
| `WatchdogConditionDetected` | condição operacional foi confirmada pelo watchdog |

Eventos carregam `schedule_id`, versão, `occurrence_id`, instante lógico, `execution_id` quando criado, timezone/policy refs, correlação e razão categórica. Não carregam Task, prompt, segredo ou conteúdo do alvo.

## Persistência, retenção e índices

Agenda, versões, ocorrências, idempotência, decisões de misfire/overlap, timezone resolvido, Execution criada, dispatch e Events são duráveis. Leases de claim, wakeups, locks e timers são efêmeros; identidade, fence e prazo do claim ativo permanecem no registro durável para takeover seguro. Retenção preserva histórico suficiente para deduplicação, auditoria e reconstrução; legal hold prevalece sobre limpeza. Índices conceituais cobrem `(state, next_fire_at)`, ownership, tipo, alvo/versionamento, unicidade da ocorrência, `(occurrence_id, occurrence_version)`, `state_fencing_token`, `execution_id`, `dispatch_id`, ocorrências `MATERIALIZED` sem despacho e claims vencidos.

## Segurança e isolamento

- agenda nunca congela credencial bruta; autorização é reavaliada no disparo;
- ownership é aplicado no create, read, claim, materialize e dispatch;
- referência de Skill, Task, rotina e configuração é imutável/versionada e reautorizada;
- contexto de sistema não permite alvo de usuário escapar do Workspace;
- timezone, expressão e limites são validados contra custo e cardinalidade abusivos;
- schedules não armazenam segredo, prompt, cookie, token ou conteúdo de Artifact;
- mudanças, disparos privilegiados, watchdogs e cleanup são auditados.

## Observabilidade

Métricas incluem agendas ativas, atraso entre instante lógico/materialização/despacho, misfires, catch-up, overlaps, conflitos, clock drift, claims vencidos, falhas por alvo, watchdogs e duração de manutenção. Logs e spans carregam schedule/occurrence/execution/correlation IDs, versão, instante nominal/efetivo e policy refs. Alertas cobrem Scheduler sem progresso, tempestade de catch-up, drift, repetição de falha e cleanup fora da janela.

## Invariantes

- agenda e ocorrência são duráveis; timer, lease e lock são descartáveis;
- cada ocorrência materializada referencia exatamente uma `Execution`;
- `target_kind`, `schedule_type` e `destination_pool` obedecem ao mapeamento fechado; Scheduler nunca executa Task/Skill Runtime;
- disparo é pelo menos uma vez no transporte e idempotente no domínio;
- Scheduler não executa a carga agendada nem muta domínio proprietário;
- UTC é canônico e recorrência civil exige timezone IANA e política DST;
- `Schedule.timezone` é a fonte única; cada ocorrência preserva versão tz, horário local solicitado/efetivo, offset e instante UTC;
- toda transição respeita owner, versão esperada e fence vigente quando operacional;
- catch-up e overlap são sempre limitados;
- revogação ou cancelamento não é contornado por snapshot antigo;
- alteração de agenda não reescreve histórico.

## Extensibilidade

Novas expressões temporais, tipos de alvo e adapters de relógio podem ser registrados por contratos versionados. Cada extensão declara determinismo, timezone, custo máximo, misfire, overlap, autorização, eventos e compatibilidade. Expressão desconhecida falha fechada; não é reinterpretada.

## Futuro

Calendários de feriado, dependências entre agendas, janelas empresariais, quotas organizacionais e coordenação multi-região poderão ser adicionados. Multi-região exigirá autoridade temporal e unicidade de ocorrência explícitas, sem enfraquecer fencing, idempotência ou ownership.
