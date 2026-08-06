# RFC 902 — Skills

**Estado:** Normativa para o contrato de Skill; implementação futura  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 201 — Agent](../200-agents/201-agent.md), [RFC 301 — Memory](../300-context-memory/301-memory.md), [RFC 406 — Capabilities](../400-tools-resources/406-capabilities.md), [RFC 602 — Artifact Storage](../600-platform-data/602-artifact-storage.md), [RFC 604 — Configuração](../600-platform-data/604-configuration.md), [RFC 702 — Segurança](../700-api-security/702-security.md), [RFC 801 — Workers](../800-operations/801-workers.md), [RFC 802 — Scheduler](../800-operations/802-scheduler.md), [RFC 901 — Plugin SDK](901-plugin-sdk.md)

## Objetivo

Definir Skill como workflow reutilizável, declarativo, versionado e autorizado que transforma uma solicitação em uma ou mais `Execution`s rastreáveis. A RFC cobre manifesto, registro, contexto mínimo, permissões, inputs, outputs, Artifacts, checkpoints, falhas, cancelamento, agendamento e evolução sem criar um runtime paralelo ao Kernel.

## Fora de escopo

- escolher DSL, linguagem de templates, engine de workflow, banco, fila, editor visual ou formato físico de pacote;
- implementar Agent, Tool, Capability, Scheduler, Worker ou Plugin SDK;
- permitir script arbitrário, eval, chamada direta a adapter ou acesso ao backend;
- definir marketplace, cobrança, publicação comunitária ou descoberta sem confiança;
- prometer transação distribuída, rollback automático ou execução exatamente uma vez;
- fornecer código, endpoints, schemas ORM ou configuração executável.

## Responsabilidades e não responsabilidades

Uma Skill DEVE:

- declarar objetivo, versão, schemas, workflow, referências exatas, permissões, limites, contexto requerido e política de Artifacts;
- criar uma `Execution` raiz por tentativa ou ocorrência agendada;
- criar `Execution` filha para cada unidade independente, durável, delegável, paralela ou com lifecycle próprio;
- fixar um snapshot imutável da definição antes do início;
- montar somente o Context mínimo necessário por etapa e usar referências para conteúdo durável;
- revalidar autorização e finalidade antes de cada efeito e ao retomar;
- produzir estado, checkpoint, uso, resultados e Events observáveis;
- aplicar timeout, budget, fan-out, profundidade, retries e cancelamento explícitos;
- integrar recorrência exclusivamente pelo Scheduler da RFC 802.

Uma Skill NÃO DEVE:

- executar fora de `Execution` ou ocultar trabalho relevante em thread/processo interno;
- chamar Tool, Resource, Provider, storage ou adapter diretamente;
- herdar automaticamente todo o Context, Memory, histórico, grants ou segredos do Agent chamador;
- usar texto de input, web, modelo ou Artifact como autorização ou alteração do workflow;
- alterar uma versão publicada ou migrar run em andamento silenciosamente;
- converter resultado parcial, etapa pulada, compensação incompleta ou cancelamento em sucesso;
- usar Schedule como autoridade de permissão ou executar dentro do Scheduler Worker.

## Arquitetura

```text
Usuário/API/Scheduler
        |
        v
  Skill Service -----> Skill Registry
        |
        +---- cria Execution raiz pelo Kernel
        |
        v
  Skill Runtime sobre Agent Worker
        +---- Context Manager
        +---- Capability Service / Tool Runtime por portas públicas
        +---- Child Execution Port
        +---- Artifact Manager
        +---- Execution Control + Event Publisher
```

Skill é um workflow orientado ao objetivo e empacotável para reutilização. Capability é programa composto de menor nível que coordena Tools sob um contrato operacional. Uma Skill pode referenciar Capabilities e Tasks, mas não as chama por função privada. Cada efeito passa pela porta proprietária e toda espera durável é refletida em estado/checkpoint compatível com a RFC 102.

## Manifesto, definição e dados

```text
SkillRef {
  skill_id: SkillId
  version: SemanticVersion
}

SkillManifest {
  manifest_version: ManifestVersion
  skill_ref: SkillRef
  name: SkillName
  description: Text
  input_schema: TypeSchema
  output_schema: TypeSchema
  workflow_ref: SkillWorkflowRef
  required_capabilities: CapabilityRef[]
  required_tools: ToolRef[]
  required_agent_constraints: AgentConstraint[]
  requested_permissions: SkillPermissionRequest[]
  context_contract: SkillContextContract
  artifact_policy: SkillArtifactPolicy
  limits: SkillLimits
  cancellation_policy: SkillCancellationPolicy
  scheduling_policy: SkillSchedulingPolicy
  compatibility: SkillCompatibility
  package_integrity: IntegrityDescriptor
}

SkillContextContract {
  required_inputs: ContextRequirement[]
  optional_inputs: ContextRequirement[]
  prohibited_classes: DataClassification[]
  maximum_context_size: ByteSize
  memory_access: NONE | EXPLICIT_REFERENCES | AUTHORIZED_QUERY
  history_access: NONE | SELECTED_MESSAGES | AUTHORIZED_SUMMARY
}

SkillArtifactPolicy {
  accepted_input_kinds: ArtifactKind[]
  produced_output_kinds: ArtifactKind[]
  intermediate_retention: RetentionPolicyRef
  result_retention: RetentionPolicyRef
  maximum_artifact_bytes: ByteSize
  allow_external_export: Boolean
}

SkillLimits {
  total_timeout: Duration
  maximum_steps: PositiveInteger
  maximum_child_executions: NonNegativeInteger
  maximum_parallel_branches: PositiveInteger
  maximum_retries_per_step: NonNegativeInteger
  maximum_cost: CostAmount | null
  maximum_resource_usage: ResourceBudget
}
```

`requested_permissions` é limite superior e não grant. Tool/Capability declarada no manifesto não está automaticamente autorizada. A definição publicada é imutável e não contém segredo, credencial, conteúdo de Artifact ou código executável arbitrário.

```text
SkillWorkflow {
  skill_ref: SkillRef
  workflow_version: Version
  entry_step_id: SkillStepId
  steps: SkillStep[]
  terminal_outputs: SkillOutputBinding[]
}

SkillStep =
  | TaskStep {
      step_id: SkillStepId
      task_template_ref: TaskTemplateRef
      agent_selector: AgentSelector
      dependencies: SkillStepId[]
      input_bindings: SkillInputBinding[]
      result_binding: SkillResultBinding
    }
  | CapabilityStep {
      step_id: SkillStepId
      capability_ref: CapabilityRef
      dependencies: SkillStepId[]
      input_bindings: SkillInputBinding[]
      result_binding: SkillResultBinding
    }
  | DecisionStep {
      step_id: SkillStepId
      condition_ref: TypedConditionRef
      dependencies: SkillStepId[]
      allowed_successors: SkillStepId[]
    }
  | JoinStep {
      step_id: SkillStepId
      dependencies: SkillStepId[]
      completion_policy: ALL | ANY_SUCCESS | MINIMUM_SUCCESS
    }
  | ApprovalStep {
      step_id: SkillStepId
      approval_policy_ref: ApprovalPolicyRef
      dependencies: SkillStepId[]
    }
  | ArtifactStep {
      step_id: SkillStepId
      artifact_operation: CREATE | TRANSFORM_REFERENCE | EXPORT
      dependencies: SkillStepId[]
      artifact_policy_ref: ArtifactPolicyRef
    }
```

Decision usa condição tipada sobre metadata e resultados permitidos; não executa texto arbitrário. `TaskStep` sempre cria `Execution` filha. `CapabilityStep` pode operar na Execution corrente somente quando atômico para o workflow e sem lifecycle independente; se for durável, delegável, paralela ou tiver retry/cancelamento próprio, cria Execution filha conforme a RFC 406.

## Contextos sensíveis e estado do run

```text
SkillAdministrativeContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId | null
  execution_id: ExecutionId | null
  correlation_id: CorrelationId
  administrative_correlation_id: AdministrativeCorrelationId | null
  purpose: Purpose
  actor: ActorRef
}

invariant: exatamente um de execution_id ou administrative_correlation_id é não nulo
invariant: agent_id é não nulo quando execution_id for não nulo

SkillOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

SkillRun {
  skill_run_id: SkillRunId
  skill_ref: SkillRef
  root_execution_id: ExecutionId
  context: SkillOperationContext
  definition_digest: Digest
  state: SkillRunState
  state_version: Version
  active_steps: SkillStepId[]
  completed_steps: SkillStepRecord[]
  child_execution_ids: ExecutionId[]
  input_refs: InputReference[]
  result_refs: ResultReference[]
  artifact_refs: ArtifactReference[]
  checkpoint_ref: SkillCheckpointRef | null
  usage: ResourceUsage
  started_at: Instant | null
  finished_at: Instant | null
}

SkillRunState = QUEUED | RUNNING | WAITING_CHILD | WAITING_USER |
  PAUSED | SUCCEEDED | FAILED | CANCELLED | COMPENSATING
```

Estados de `SkillRun` são projeção interna e não substituem `ExecutionState`. A Execution raiz é a autoridade de lifecycle. O mapeamento normativo é:

| `SkillRunState` | `ExecutionState` canônico |
| --- | --- |
| `QUEUED` | `QUEUED` |
| `RUNNING` ou `COMPENSATING` | `RUNNING` |
| `WAITING_CHILD` | `PAUSED` |
| `WAITING_USER` | `WAITING_USER` |
| `PAUSED` | `PAUSED` |
| `SUCCEEDED` | `COMPLETED` |
| `FAILED` | `FAILED` |
| `CANCELLED` | `CANCELLED` |

Ao entrar em `WAITING_CHILD`, a Skill DEVE confirmar checkpoint e filhos aguardados antes de solicitar `ExecutionState = PAUSED`. A satisfação da espera solicita `PAUSED -> QUEUED`. Divergência é falha reconciliável; o run não pode declarar terminal diferente do terminal confirmado da Execution.

## Registro, resolução e contratos tipados

```text
interface SkillRegistry {
  register(request: RegisterSkillVersion) -> SkillRegistrationReceipt
  deprecate(request: DeprecateSkillVersion) -> SkillLifecycleReceipt
  disable(request: DisableSkillVersion) -> SkillLifecycleReceipt
  resolve(request: ResolveSkillVersion) -> SkillResolutionOutcome
  inspect(query: InspectSkillVersion) -> AuthorizedSkillView

  invariant: versão publicada, digest e workflow são imutáveis
}

RegisterSkillVersion {
  operation_id: SkillOperationId
  context: SkillAdministrativeContext
  manifest: SkillManifest
  workflow: SkillWorkflow
  expected_package_digest: Digest
  idempotency_key: IdempotencyKey
}

ResolveSkillVersion {
  context: SkillAdministrativeContext | SkillOperationContext
  selector: SkillResolutionSelector
  required_status: ACTIVE | ACTIVE_OR_DEPRECATED
  compatibility_requirements: SkillCompatibilityRequirement[]
}

SkillResolutionSelector =
  | ExactSkillSelector { skill_ref: SkillRef }
  | ConstrainedSkillSelector {
      skill_id: SkillId
      version_constraint: SkillVersionConstraint
      resolution_policy_snapshot_ref: SkillResolutionPolicySnapshotRef
    }

SkillResolutionOutcome =
  | SkillResolved {
      skill_ref: SkillRef
      manifest_digest: Digest
      workflow_digest: Digest
      status: ACTIVE | DEPRECATED
      resolution_evidence_ref: SkillResolutionEvidenceRef
    }
  | SkillResolutionDenied { reason: AuthorizationDenial }
  | SkillResolutionDisabled { disabled_ref: SkillRef }
  | SkillResolutionIncompatible {
      candidate_ref: SkillRef | null
      reason: SkillCompatibilityFailure
    }
  | SkillResolutionNotFound { selector: SkillResolutionSelector }
  | SkillResolutionIndeterminate { reason: SkillResolutionFailure }
```

Registro valida aciclicidade das dependências, alcance de terminais, schemas, limites, referências, permissões e integridade. Ciclo de workflow ou dependência de Skill é rejeitado. Versão desabilitada permanece inspecionável, mas não inicia novo run nem nova ocorrência. Somente `SkillResolved` autoriza a continuação; deny, disabled, incompatible, not found ou indeterminate falham fechado e não permitem fallback silencioso para outra versão.

```text
interface SkillService {
  start(request: StartSkill) -> SkillAccepted
  start_scheduled(request: StartScheduledSkill) -> SkillAccepted
  run(request: RunSkill) -> SkillOutcome
  resume(request: ResumeSkill) -> SkillOutcome
  provide_input(request: ProvideSkillInput) -> SkillInputReceipt
  request_cancel(request: CancelSkill) -> SkillCancelReceipt
  inspect(query: InspectSkillRun) -> AuthorizedSkillRunView

  pre: ator pode criar ou operar a Execution no ownership e purpose informados
  post: toda tentativa aceita possui exatamente uma Execution raiz
  post: start_scheduled usa a Execution já materializada pela ocorrência e não cria outra
}

StartSkill {
  request_id: SkillRequestId
  skill_ref: SkillRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  task: TaskSnapshot
  input_refs: InputReference[]
  requested_limits: SkillLimitRequest
  idempotency_key: IdempotencyKey
}

SkillAccepted {
  skill_run_id: SkillRunId
  execution_id: ExecutionId
  resolved_skill_ref: SkillRef
  state: QUEUED
}

StartScheduledSkill {
  request_id: SkillRequestId
  context: SkillOperationContext
  schedule_id: ScheduleId
  occurrence_id: OccurrenceId
  materialized_target: MaterializedScheduledSkillTarget
  idempotency_key: IdempotencyKey
}

RunSkill {
  skill_run_id: SkillRunId
  context: SkillOperationContext
  expected_state_version: Version
  checkpoint_ref: SkillCheckpointRef | null
}

SkillOutcome =
  | SkillSucceeded { result_refs: ResultReference[], artifact_refs: ArtifactReference[] }
  | SkillWaiting { reason: CHILD | USER | PAUSE, checkpoint_ref: SkillCheckpointRef }
  | SkillFailed { error: SkillError, partial_refs: ResultReference[] }
  | SkillCancelled { reason: CancellationReason, partial_refs: ResultReference[] }
```

`start` resolve com outcome tipado, fixa versão, Task snapshot, inputs e limites e cria nova Execution em `QUEUED` somente após `SkillResolved`. `start_scheduled` recebe da RFC 802 um `MaterializedScheduledSkillTarget` persistido e a Execution já criada atomicamente para a ocorrência; valida que contexto, `schedule_id`, `occurrence_id`, versão, digests, Task, inputs, limites e policy snapshots coincidem e cria somente o `SkillRun` ligado àquela Execution. Retry depois de terminal cria novo `skill_run_id` e nova `execution_id`, vinculados causalmente. Redelivery do mesmo comando idempotente retorna a mesma aceitação.

## Contexto mínimo e permissões

Cada etapa recebe uma `StepContextView` construída pela RFC 104 a partir da interseção entre contrato da Skill, dependências concluídas, finalidade, budget e autorização atual. O mínimo inclui apenas instrução da etapa, schemas, refs necessárias, resumo autorizado e IDs de correlação; todo item deve possuir proveniência e classificação.

```text
StepContextRequest {
  context: SkillOperationContext
  skill_ref: SkillRef
  skill_run_id: SkillRunId
  step_id: SkillStepId
  required_context: ContextRequirement[]
  dependency_result_refs: ResultReference[]
  maximum_size: ByteSize
}

StepContextView {
  context_view_id: ContextViewId
  included_refs: ContextItemRef[]
  omitted_requirements: ContextOmission[]
  policy_snapshot_ref: PolicySnapshotRef
  expires_at: Instant
}
```

Histórico integral, Memory completa, outros Workspaces e inputs de branches não dependentes ficam de fora. Ausência de requisito obrigatório falha antes da etapa; omissão opcional é observável. Aprovação humana é comando autenticado vinculado a run, step, versão esperada, escopo e expiração; texto em documento ou output de modelo nunca aprova.

A permissão efetiva de uma etapa é a interseção de ator, owner, Workspace, Agent, Execution, `purpose`, manifesto/versão, step, Tool/Capability, Resource, referência de dados e policy vigente. A Skill não pode delegar direito que não possui; Execution filha recebe grant explícito, limitado e revogável.

## Artifacts, resultados e checkpoints

Inputs e outputs volumosos ou duráveis são `ArtifactReference` ou `ResultReference`, nunca cópias automáticas no Context. Criação usa staging, checksum, classificação, provenance, ownership e seal conforme a RFC 602. Cada Artifact produzido registra `skill_ref`, `skill_run_id`, `step_id`, `execution_id`, inputs derivados por referência e policy de retenção.

Exports externos exigem `allow_external_export`, autorização específica no destino e revalidação de classificação. Falha de export não apaga o Artifact interno. Resultados parciais preservam seu outcome; não são promovidos a saída final sem binding terminal válido.

Checkpoint contém versão/digests da Skill, `state_version`, steps, decisões, referências, efeitos, filhos, uso e próxima posição. Não contém Context integral, segredo, handle vivo ou bytes de Artifact. Retomada revalida integridade, ownership, grants, referências, limites e status da versão. Desativação da Skill impede novos runs; run já iniciado pode retomar a versão fixada apenas se policy não a tiver revogado ou colocado em quarentena.

## Agendamento

Skill recorrente usa `SKILL_RECURRENCE`, `ScheduledSkillTarget`, `SkillScheduleBinding` e `MaterializedScheduledSkillTarget` definidos canonicamente na RFC 802. `CreateSchedule.target` persiste o binding completo; a RFC 902 não mantém uma cópia divergente do tipo. O binding inclui selector, snapshot do template de Task, snapshots de inputs, limites de Execution/Skill e snapshots das policies de Context, Artifact e autorização. O Scheduler nunca armazena segredo, Context montado ou conteúdo de Artifact.

```text
CreateScheduledSkill {
  schedule_command: CreateSchedule

  invariant: schedule_command.target é ScheduledSkillTarget
  invariant: schedule_command.target.binding é SkillScheduleBinding persistível
  invariant: schedule_type derivado de target_kind é SKILL_RECURRENCE
  invariant: schedule_command.target.destination_pool = AGENT
  invariant: policy explícita ou derivada coincide com target.binding.skill_selector.resolution_policy
}
```

`Schedule.configuration_snapshot_policy`, da RFC 802, é a fonte canônica persistida. `CreateSchedule` recebe `PINNED`, `RESOLVE_AT_FIRE` ou `DERIVE_FROM_TARGET`; a admissão normaliza o valor e exige igualdade com `ScheduledSkillSelector.resolution_policy`. Assim, `PINNED` só acompanha `PinnedSkillSelector`, e `RESOLVE_AT_FIRE`, somente `ResolveSkillAtFireSelector`; combinações contraditórias falham antes da persistência. Update de target e policy é atômico e obedece à mesma regra.

`PINNED` persiste `SkillRef`, manifest digest e workflow digest exatos, mas não congela credencial ou autorização. `RESOLVE_AT_FIRE` persiste `skill_id`, `SkillVersionConstraint` e `SkillResolutionPolicySnapshotRef`; ele não pode carregar `SkillRef` exata como substituto da constraint. No disparo, o Scheduler lê a policy canônica, chama `SkillRegistry.resolve`, aceita somente `SkillResolved` e grava versão, digests e `resolution_evidence_ref` em `MaterializedScheduledSkillTarget`. A resolução de cada ocorrência é imutável e não migra ocorrências passadas.

Materialização reautoriza os snapshots do binding, cria Task snapshot e exatamente uma Execution raiz na mesma fronteira conceitual da ocorrência. O Agent Pool consome `StartScheduledSkill` com esse materialized target; redelivery preserva occurrence, Execution, resolução e `SkillRun`. Scheduler detecta, resolve e despacha, mas jamais executa a Skill.

Pausar ou cancelar Schedule impede ocorrências futuras conforme RFC 802, mas não cancela runs materializados. Cancelar uma SkillRun exige comando próprio. Skill desabilitada, input revogado ou autorização negada no disparo resulta em ocorrência rejeitada/falha observável, não em uso de snapshot privilegiado.

## Fluxo normal

1. Chamador autorizado resolve `SkillRef` com `SkillResolutionOutcome` e envia Task snapshot, inputs por referência, purpose e limites; no caminho agendado, o Scheduler fornece o `MaterializedScheduledSkillTarget` já persistido.
2. Skill Service valida schemas, ownership, grants e idempotência e cria Execution raiz em `QUEUED`.
3. Agent Worker adquire a Execution; Skill Runtime fixa manifest/workflow digests e monta Context mínimo para o primeiro step.
4. Steps iniciam somente após dependências e autorização; trabalho independente cria Executions filhas correlacionadas.
5. Resultados e Artifacts são validados, selados e vinculados por referência antes de liberar sucessores.
6. Esperas e regiões de risco confirmam checkpoint antes de solicitar estado compatível ao Kernel.
7. Outputs terminais são validados e persistidos antes de `SkillFinished` e `ExecutionFinished`.

## Falhas, timeout e recuperação

Falha é classificada por step, retryability, efeito e alcance. Retry operacional permanece no mesmo run somente quando idempotência ou reconciliação prova segurança e há budget; nova tentativa de domínio cria nova Execution. Step dependente não inicia após falha impeditiva. Branches independentes podem concluir conforme a política de join, sem ocultar seus outcomes.

Timeout de step, child, aprovação ou total é explícito e não vira cancelamento sem comando. Efeito `UNKNOWN` bloqueia retry até reconciliação. Crash do Worker recupera do checkpoint e dos registros duráveis; outputs não selados são abortados ou reconciliados. Checkpoint incompatível, versão ausente ou grant revogado leva a falha fechada ou espera por decisão autorizada, nunca a migração silenciosa.

Falha de publicação não desfaz fato confirmado; outbox/eventual delivery segue RFC 103. Falha de Artifact Storage antes do seal não produz output válido. Falha após seal é reconciliada pelo identificador e checksum antes de criar outro Artifact.

## Cancelamento e compensação

Cancelamento da Execution raiz impede novos steps, propaga sinal para children pertencentes ao run conforme policy, solicita cancelamento das invocações ativas e aguarda limites seguros. Child compartilhada ou já terminal não é apagada. Efeitos confirmados e Artifacts preservam histórico; efeitos incertos são reconciliados.

Compensação, quando declarada, é workflow explícito, limitado e novamente autorizado. Ela não recebe permissões adicionais, não apaga Events e pode falhar parcialmente. O terminal da raiz continua `CANCELLED` quando o cancelamento venceu a corrida, mesmo que compensação tenha sucesso. Resultado tardio não publica `SkillFinished` nem reabre a Execution.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `SkillRegistered` | versão imutável entrou no Registry |
| `SkillDeprecated` | versão passou a desencorajar novos usos |
| `SkillDisabled` | versão deixou de aceitar novos runs |
| `SkillStarted` | run iniciou sobre Execution raiz |
| `SkillStepStarted` | step ficou ativo após dependências e autorização |
| `SkillStepFinished` | step terminou com outcome explícito |
| `SkillChildExecutionCreated` | child correlacionada foi confirmada |
| `SkillArtifactProduced` | Artifact foi selado e vinculado ao run |
| `SkillCheckpointCreated` | checkpoint seguro foi confirmado |
| `SkillWaitingForInput` | espera autenticada por input/aprovação foi confirmada |
| `SkillFinished` | outputs válidos e terminal de sucesso foram confirmados |
| `SkillFailed` | run terminou sem continuação segura |
| `SkillCancelled` | cancelamento foi estabilizado e confirmado |

Events de run usam `execution_id` raiz ou da child que confirmou o fato e sequência correspondente. Payloads contêm IDs, refs, versão, step, outcome, uso e razões sanitizadas; instruções, Context, input, output e conteúdo de Artifact não são copiados.

## Segurança

- definição, templates, inputs e Artifacts são dados não confiáveis até validação;
- toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- Skill não herda automaticamente grants do autor, instalador, Agent ou Schedule;
- referências são opacas e reautorizadas em cada resolução, retomada e ocorrência;
- Context mínimo aplica classificação, proveniência, redaction, tamanho e expiração;
- segredo permanece em `SecretReference` e handle efêmero, nunca em workflow, checkpoint ou Event;
- branches e children são isolados por ownership e recebem somente refs necessárias;
- decisão, modelo, web ou arquivo não pode elevar permissão nem mudar grafo publicado;
- fan-out, profundidade, custo, tempo, bytes e export são limitados antes e durante o run;
- versões de plugin que fornecem Skills continuam sujeitas integralmente à RFC 901.

## Observabilidade

Logs e traces conectam Skill, run, step, Execution raiz, Executions filhas, Capability, Tool e Artifact por IDs e causalidade. Métricas incluem runs, duração, steps, branches, fan-out, espera, retries, contexto omitido, Artifacts, custo, schedules, cancelamentos, compensações e falhas categóricas. Conteúdo e identificadores de alta cardinalidade não são labels.

Inspeção deve explicar versão/digests resolvidos, caminho de decisão, dependências, grants efetivos, contexto incluído/omitido, children, outcomes e referências produzidas. Replay para diagnóstico reconstrói decisões a partir de evidência; não reexecuta efeitos.

## Invariantes

- Skill é workflow versionado; versão publicada e digests são imutáveis;
- cada tentativa ou ocorrência materializada cria exatamente uma Execution raiz;
- subtrabalho independente, durável ou delegável cria Execution filha;
- Kernel permanece autoridade do lifecycle; SkillRun não cria estado paralelo;
- todo efeito usa a porta pública proprietária e autorização própria;
- Context de step é mínimo, expirável, proveniente e autorizado;
- Permission request não é grant e permissão efetiva nunca aumenta;
- Artifact durável é referenciado, classificado, íntegro e pertencente ao mesmo ownership autorizado;
- Schedule detecta e despacha, mas nunca executa Skill;
- retry exige idempotência ou reconciliação; cancelamento não apaga efeito confirmado;
- sucesso, parcial, falha, compensação e cancelamento permanecem distintos.

## Extensibilidade

Novos tipos de step exigem semântica tipada de dependência, autorização, Execution, checkpoint, erro, cancelamento, evento e resultado antes de entrar no catálogo. Runtimes, editores e formatos de pacote alternativos podem implementar os contratos sem introduzir script arbitrário ou acesso direto a adapters.

Bibliotecas de Skill, templates, validadores e resolvers podem evoluir por versão. Campo namespaced desconhecido permanece inerte. Extensão não pode modificar grafo publicado durante o run, salvo mecanismo futuro explicitamente versionado que preserve snapshot, causalidade, limites e auditabilidade.

## Futuro

Editor visual, composição de Skills, parâmetros organizacionais, aprovação multiator, simulação, dry-run, verificação estática avançada e marketplace poderão especializar o modelo. Workflows dinâmicos só serão adotados quando suas decisões forem limitadas, versionadas, reconstruíveis e incapazes de ampliar permissões ou escapar da `Execution`.
