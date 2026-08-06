# RFC 406 — Capabilities

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](401-tool-runtime.md), [RFC 402 — Resource Manager](402-resource-manager.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir Capabilities como programas compostos, versionados e autorizados que criam ou operam Executions e coordenam uma ou mais Tools pelo Tool Runtime. Capabilities controlam sequência, decisão, repetição limitada, paralelismo declarado, checkpoint e compensação sem acessar adapters concretos nem elevar permissões.

## Fora de escopo

- implementação de Tool, Resource, Agent, Orchestrator ou Runtime do Kernel;
- linguagem de workflow, DSL, engine, fila, banco ou formato de persistência;
- planejamento autônomo genérico e algoritmo de modelo;
- transação distribuída global ou rollback automático de todo efeito externo;
- endpoints, interface visual e instalação de plugins, Skills ou MCP;
- permitir composição dentro de Tool.

## Responsabilidades e não responsabilidades

Uma Capability DEVE:

- declarar objetivo, versão, entrada, saída, Tools permitidas, permissões, limites e política de cancelamento;
- criar uma `Execution` para uma nova tentativa de trabalho ou operar uma Execution elegível fornecida pelo Kernel;
- criar Execution filha para subtrabalho independente, durável, delegável ou com ciclo de vida próprio;
- invocar Tools exclusivamente pelo Tool Runtime, com versão exata e contexto sensível explícito;
- preservar ownership, correlação, causalidade, finalidade e autorização em cada passo;
- registrar estado composto, resultados por referência, checkpoints e terminal explícito;
- aplicar timeout, custo, quantidade de passos, iterações e concorrência;
- distinguir falha de passo, falha da Capability, cancelamento e compensação.

Uma Capability NÃO DEVE:

- chamar adapter, banco, filesystem, terminal, browser ou Provider diretamente;
- fornecer Tool Runtime ou Tool Registry a uma Tool;
- executar trabalho fora de Execution ou esconder subtrabalho relevante em estado interno;
- assumir que permissão da Capability inclui automaticamente permissão de suas Tools;
- converter resultado parcial, compensação incompleta ou cancelamento em sucesso;
- copiar Context, Memory ou Artifact integralmente quando uma referência basta;
- alterar a máquina de estados da RFC 102 por mecanismo paralelo.

## Arquitetura

```text
Solicitação autorizada
        │ start ou run
        ▼
 Capability Service / Runtime
        ├── CapabilityRegistry
        ├── CapabilityPolicy
        ├── Tool Runtime ──> Tool A / Tool B atômicas
        ├── Child Execution Port ──> Execution filha quando necessário
        ├── ExecutionFactory + ExecutionControl ─┐
        └── CapabilityState / Checkpoint facade ─┤
                                                 │ DomainChange + OutboxEntry
                                                 ▼
                                      TransactionalPersistence.transact
                                                 │ após COMMITTED
                                                 ▼
                                             outbox ──> EventBus
```

O Capability Runtime é domínio de composição, não o Runtime do Kernel. Ele opera sob o controle da Execution corrente e usa suas portas públicas. `ExecutionControl` confirma mudanças da `Execution`; a fachada de estado/checkpoint prepara mudanças do `CapabilityRun`. Ambas montam `DomainChange` e `OutboxEntry` para a única fronteira `TransactionalPersistence.transact` da RFC 601. O Capability Runtime não recebe publicador direto; a entrega posterior ao `EventBus` segue a RFC 103. O Tool Runtime continua responsável por validar e executar cada Tool.

## Dados

```text
CapabilityOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

CapabilityRef {
  capability_id: CapabilityId
  version: CapabilityVersion
}

CapabilityDescriptor {
  capability_ref: CapabilityRef
  name: CapabilityName
  description: Text
  input_schema: TypeSchema
  output_schema: TypeSchema
  allowed_tools: ToolRef[]
  allowed_child_capabilities: CapabilityRef[]
  permissions: PermissionRequirement[]
  limits: CapabilityLimits
  cancellation_policy: CapabilityCancellationPolicy
  compensation_policy: NONE | EXPLICIT_STEPS
  status: ACTIVE | DEPRECATED | DISABLED
}

CapabilityLimits {
  timeout: Duration
  maximum_steps: PositiveInteger
  maximum_tool_invocations: PositiveInteger
  maximum_child_executions: NonNegativeInteger
  maximum_parallel_steps: PositiveInteger
  maximum_cost: CostAmount | null
  maximum_resource_usage: ResourceBudget
}
```

Operações administrativas do catálogo usam contexto de autorização e auditoria próprio:

```text
CapabilityRegistryOperationContext {
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
```

`workspace_id` nulo designa somente Capability global. Manutenção administrativa pode omitir Agent e Execution apenas quando conserva usuário responsável, correlação administrativa, finalidade e ator explícitos.

```text
CapabilityRun {
  capability_run_id: CapabilityRunId
  capability_ref: CapabilityRef
  context: CapabilityOperationContext
  input_ref: InputReference
  state: CapabilityRunState
  state_version: Version
  current_steps: CapabilityStepId[]
  completed_steps: CapabilityStepRecord[]
  child_execution_ids: ExecutionId[]
  usage: ResourceUsage
  checkpoint_ref: CapabilityCheckpointRef | null
  result_ref: ResultReference | null
  started_at: Instant | null
  finished_at: Instant | null
}

CapabilityRunState = QUEUED | RUNNING | WAITING_TOOL | WAITING_CHILD |
  PAUSED | SUCCEEDED | FAILED | CANCELLED | COMPENSATING
```

Estados da Capability são internos ao run composto e não substituem `ExecutionState`. O mapeamento normativo é explícito:

| `CapabilityRunState` | `ExecutionState` canônico |
| --- | --- |
| `QUEUED` | `QUEUED` |
| `RUNNING` ou `COMPENSATING` | `RUNNING` |
| `WAITING_TOOL` | `WAITING_TOOL` |
| `WAITING_CHILD` | `PAUSED` |
| `PAUSED` | `PAUSED` |
| `SUCCEEDED` | `COMPLETED` |
| `FAILED` | `FAILED` |
| `CANCELLED` | `CANCELLED` |

Ao entrar em `WAITING_CHILD`, o adapter DEVE confirmar checkpoint e filhos aguardados antes de solicitar `ExecutionState = PAUSED`, liberando o Worker. A satisfação da espera solicita `PAUSED -> QUEUED`, nunca `WAITING_CHILD -> RUNNING` na máquina da Execution. Divergência entre projeção e estado canônico é falha reconciliável.

```text
CapabilityStep {
  step_id: CapabilityStepId
  kind: TOOL | CHILD_EXECUTION | DECISION | CHECKPOINT | COMPENSATION
  dependencies: CapabilityStepId[]
  authorization: PermissionRequirement[]
  timeout: Duration
  retry_policy: RetryPolicy
  input_bindings: InputBinding[]
  output_binding: OutputBinding | null
}

CapabilityStepRecord {
  step_id: CapabilityStepId
  attempt: PositiveInteger
  invocation_id: ToolInvocationId | null
  child_execution_id: ExecutionId | null
  outcome: StepOutcome
  result_ref: ResultReference | null
  effect_state: EffectState
  finished_at: Instant
}
```

## Contratos tipados

```text
interface CapabilityRegistry {
  register(request: RegisterCapability) -> RegistrationResult
  resolve(capability_ref: CapabilityRef, context: CapabilityOperationContext) -> CapabilityDescriptor
  list(query: AuthorizedCapabilityRegistryQuery) -> CapabilityDescriptor[]
  disable(request: DisableCapability) -> RegistrationResult

  invariant: versão publicada é imutável
  invariant: descriptor enumera limite máximo de Tools e child Capabilities utilizáveis
}

RegisterCapability {
  request_id: RegistryRequestId
  context: CapabilityRegistryOperationContext
  descriptor: CapabilityDescriptor
  program: CapabilityProgramRef
  package_integrity_ref: IntegrityRef
  idempotency_key: IdempotencyKey
}

DisableCapability {
  request_id: RegistryRequestId
  context: CapabilityRegistryOperationContext
  capability_ref: CapabilityRef
  expected_status: ACTIVE | DEPRECATED
  reason: DisableReason
  idempotency_key: IdempotencyKey
}

AuthorizedCapabilityRegistryQuery {
  context: CapabilityRegistryOperationContext
  capability_ref: CapabilityRef | null
  status: (ACTIVE | DEPRECATED | DISABLED)[]
  permission_filter: Permission[]
  page: PageRequest
}
```

Registro, listagem administrativa e desabilitação revalidam o contexto completo e produzem auditoria antes do efeito. Bootstrap pode omitir `agent_id` e `execution_id` somente quando o catálogo inicial estiver vazio, usar manifesto allowlisted com integridade verificada, `purpose = SYSTEM_BOOTSTRAP`, `user_id` responsável e `administrative_correlation_id` não nulos. As entradas são auditadas antes de `ACTIVE`; após inicialização, o caminho de bootstrap é desabilitado e não substitui versões existentes.

```text
interface CapabilityService {
  start(request: StartCapability) -> CapabilityAccepted
  run(request: RunCapability) -> CapabilityOutcome
  resume(request: ResumeCapability) -> CapabilityOutcome
  request_cancel(request: CancelCapability) -> CancelCapabilityResult
  inspect(query: AuthorizedCapabilityQuery) -> CapabilityRunSnapshot

  pre: ator pode criar ou operar a Execution no ownership e finalidade informados
  post: toda Tool foi invocada pelo Tool Runtime sob autorização própria
}

StartCapability {
  request_id: CapabilityRequestId
  capability_ref: CapabilityRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  task: TaskSnapshot
  input_ref: InputReference
  limits: CapabilityLimitRequest
  idempotency_key: IdempotencyKey
}

CapabilityAccepted {
  capability_run_id: CapabilityRunId
  execution_id: ExecutionId
  state: QUEUED
}
```

`start` cria uma nova Execution em `QUEUED` pela porta da RFC 102; não executa o programa no processo da API. `run` é invocado pelo Runtime/worker sobre Execution adquirida e elegível.

```text
RunCapability {
  capability_run_id: CapabilityRunId
  context: CapabilityOperationContext
  expected_state_version: Version
  resume_from: CapabilityCheckpointRef | null
}

CapabilityOutcome =
  | CapabilitySucceeded { result_ref: ResultReference, usage: ResourceUsage }
  | CapabilityWaiting { reason: TOOL | CHILD | USER | PAUSE, checkpoint_ref: CapabilityCheckpointRef }
  | CapabilityFailed { error: CapabilityError, compensation: CompensationOutcome | null }
  | CapabilityCancelled { reason: CancellationReason, compensation: CompensationOutcome | null }
```

```text
interface CapabilityToolPort {
  invoke(request: CapabilityToolInvocation) -> ToolInvocationOutcome

  post: request é traduzido para RFC 401 sem acesso à Tool concreta
}

CapabilityToolInvocation {
  capability_run_id: CapabilityRunId
  step_id: CapabilityStepId
  tool_ref: ToolRef
  context: CapabilityOperationContext
  arguments: StructuredValue
  idempotency_key: IdempotencyKey | null
  limits: ToolLimitRequest
}

interface ChildExecutionPort {
  create(request: CreateChildExecution) -> ExecutionId
  inspect(query: AuthorizedChildExecutionQuery) -> ExecutionSnapshot
  request_cancel(request: CancelChildExecution) -> CommandResult
}
```

Uma Capability não chama a si mesma nem outra Capability por função interna. Composição declarada de Capability cria uma Execution filha e usa a porta pública, respeitando limites de profundidade e causalidade.

## Criação e operação de Executions

Nova solicitação de Capability cria uma Execution com Task snapshot, Agent, ownership, correlação, limites e `causation_id`. Passos atômicos curtos pertencem à Execution corrente. Um passo cria Execution filha quando possui resultado independente, espera durável, delegação, política própria de retry/cancelamento ou necessidade de ownership operacional separado. A filha recebe contexto mínimo por referências, não herda autorização nem histórico completo.

Somente o Kernel confirma transições de Execution. A Capability solicita `WAITING_TOOL`, `WAITING_USER`, `PAUSED` ou terminal por portas compatíveis e só após seu estado e resultado estarem persistidos. Retry de Capability após terminal cria nova Execution e novo `capability_run_id`.

## Permissões e composição

A permissão efetiva de cada passo é a interseção de:

1. ator e `user_id`;
2. Workspace e classificação dos dados;
3. Agent e Execution;
4. `purpose` aprovado;
5. descriptor e versão da Capability;
6. Tool, argumentos e versão exata;
7. Resource, operação, lease, quota e política vigente.

Permissão ampla da Capability não preautoriza Tools. Mudança de dados, redirect, resultado de Tool ou output de modelo não pode expandir o conjunto declarado. Passo não autorizado falha antes do efeito ou aguarda aprovação explícita por contrato externo; nunca é ignorado.

## Checkpoints, idempotência e compensação

Checkpoint registra descriptor/version, estado, passos concluídos, referências, efeitos, uso, filhos e próxima decisão; não inclui handle vivo, segredo, output integral ou objeto de adapter. Retomada revalida política, ownership, versões e resultados.

Cada passo com efeito usa chave idempotente determinística no escopo do run e tentativa quando a Tool suportar. Efeito `UNKNOWN` é reconciliado antes de retry. Compensação é uma sequência explícita de Tools autorizadas, cada uma com seu próprio outcome; ela não apaga Events nem garante reversão completa. Falha de compensação permanece terminalmente visível.

## Fluxo normal

1. `start` valida descriptor, Task, ownership, finalidade e limites e cria Execution em `QUEUED`.
2. Worker adquire a Execution; o Runtime chama `run` com contexto completo.
3. A Capability carrega ou cria checkpoint, seleciona passos cujas dependências foram satisfeitas e revalida permissões.
4. Cada passo de Tool usa o Tool Runtime; subtrabalho adequado cria Execution filha correlacionada.
5. Resultados são validados, armazenados por referência, contabilizados e aplicados ao próximo passo.
6. Checkpoint, estado do run e entrada de outbox de `CapabilityCheckpointCreated` são confirmados juntos após efeitos e antes de espera ou nova região de risco.
7. Saída final, terminal do run e `CapabilityFinished` são confirmados atomicamente; a `ExecutionControl` confirma separadamente o terminal e Event da `Execution` sob sua própria transação canônica, sem publicação direta.

## Fluxo de falha

Falha de passo é classificada por retryability, efeito e criticidade. Retry respeita Tool, idempotência, orçamento e deadline. Passos dependentes não iniciam após falha impeditiva. Quando configurada, compensação executa em ordem declarada e sob nova autorização; seu resultado faz parte da falha. A Capability confirma checkpoint seguro e Event correspondente pela outbox, cancela filhos não necessários e solicita `FAILED` à `ExecutionControl` quando não há continuação. Falha de entrega após commit mantém o Event pendente para republicação e não altera o fato confirmado.

## Fluxo de cancelamento

Cancelamento da Execution ou Capability impede novos passos, propaga sinal a Tools ativas e Executions filhas conforme a política de ownership, aguarda limites seguros e reconcilia efeitos. Compensação em cancelamento só ocorre se explicitamente declarada, autorizada e dentro do deadline; cancelamento não concede permissão adicional para desfazer. O run confirma `CANCELLED` e sua entrada de outbox no mesmo commit, preserva resultados já confirmados por referência e nunca registra sucesso tardio.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `CapabilityStarted` | run autorizado iniciou sobre uma Execution |
| `CapabilityStepStarted` | passo tornou-se ativo após dependências e autorização |
| `CapabilityStepFinished` | passo terminou com outcome explícito |
| `CapabilityCheckpointCreated` | estado composto seguro foi confirmado |
| `CapabilityChildExecutionCreated` | Execution filha correlacionada foi criada |
| `CapabilityCompensationFinished` | compensação terminou com outcome explícito |
| `CapabilityFinished` | run terminou com sucesso e resultado confirmado |
| `CapabilityFailed` | run terminou sem continuação segura |
| `CapabilityCancelled` | cancelamento foi estabilizado e confirmado |

O Tool Runtime continua responsável por confirmar `ToolStarted` e `ToolFinished` em sua própria mudança + outbox; a Capability não duplica esses fatos. Todos os Events da tabela são preparados como `OutboxEntry` junto da mudança correspondente pela RFC 601 e entregues posteriormente conforme a RFC 103. Payloads usam IDs, refs, versão, passo, outcome, uso e razões sanitizadas, sem inputs ou resultados integrais.

## Segurança

- operações sensíveis declaram `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- operações administrativas do registro usam `administrative_correlation_id` quando não pertencem a uma Execution; bootstrap continua exigindo usuário responsável, finalidade, integridade e auditoria;
- autorização é recalculada por passo e nunca excede a interseção dos escopos;
- descriptor fixa versões e conjunto máximo de Tools/Capabilities filhas;
- argumentos derivados de modelo, web, arquivo ou Tool são não confiáveis e revalidados;
- referências são reautorizadas ao resolver e não transferem ownership;
- child Execution não herda permissão, segredo ou Context integral;
- checkpoints, Events e logs não contêm segredo ou handle vivo;
- aprovação humana, quando exigida, é comando autenticado e não texto em conteúdo externo.

## Observabilidade

Métricas incluem runs, duração, passos, fan-out, Tool calls, child Executions, espera, retries, compensações, custo, cancelamentos e falhas por categoria. Traces ligam Capability, passo, Tool invocation e Execution filha pela correlação e causalidade. Logs usam IDs, versões, finalidade, estados e códigos; inputs e outputs sensíveis ficam por referência. O estado deve permitir explicar por que um passo foi escolhido, negado, repetido ou compensado.

## Invariantes

- Capability é a unidade de composição; Tool permanece atômica e nunca chama Tool;
- todo run cria ou opera uma Execution governada pelo Kernel;
- subtrabalho independente usa Execution filha correlacionada;
- toda Tool é invocada pelo Tool Runtime com versão e autorização próprias;
- Capability depende apenas de portas públicas e nunca de adapter concreto;
- permissão efetiva nunca aumenta entre passos;
- limites de passos, tempo, custo, recursos, filhos e paralelismo são monotônicos;
- checkpoint não contém handle vivo ou segredo;
- retry exige idempotência ou reconciliação; compensação é explícita e falível;
- falha, cancelamento, parcial e sucesso são outcomes distintos e auditáveis.

## Extensibilidade

Novas Capabilities registram descriptor e programa versionados. Estratégias de passo, checkpoint, decisão e compensação podem variar atrás de portas, desde que seus efeitos passem pelo Tool Runtime ou por Executions filhas. Extensões não podem embutir adapter, chamada Tool-para-Tool, permissão implícita ou estado paralelo ao Kernel.

## Futuro

DSL declarativa, visualização de workflow, paralelismo determinístico, aprovação humana, marketplace e composição remota poderão especializar o modelo. Essas evoluções devem preservar Execution como unidade de trabalho, Tool atomicidade, autorização por passo, causalidade, limites, checkpoint seguro e cancelamento propagado.
