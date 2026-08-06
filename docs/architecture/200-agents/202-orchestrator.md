# RFC 202 — Orchestrator

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 201 — Agent](201-agent.md), [RFC 203 — Multi-agent](203-multi-agent.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir o Orchestrator como plano de controle que transforma intenções autorizadas em Executions, coordena dependências, agendamento, despacho, deadlines, timeout operacional, cancelamento, recuperação e continuidade por portas públicas. O Orchestrator coopera com o Kernel, mas não executa LLM, Agent, Tool ou Capability.

## Relação com o Kernel

O Runtime da [RFC 101 — Runtime](../100-kernel/101-runtime.md) continua sendo o Kernel de execução que governa uma `Execution` por vez. O Orchestrator fica na camada de orquestração e usa as portas públicas do Kernel:

- Orchestrator decide **quando e qual trabalho autorizado deve virar ou retomar uma Execution**;
- máquina de estados do Kernel decide **se uma transição é válida**;
- Runtime decide **como governar o loop de uma Execution adquirida**;
- ContextManager decide **qual Context mínimo e autorizado montar**;
- adapters operacionais decidem **como transportar, persistir ou processar o despacho**.

Nenhuma dessas fronteiras permite ao Orchestrator escrever diretamente estado, montar prompt, chamar Provider ou selecionar tecnologia concreta.

## Fora de escopo

- execução de LLM, Provider, Tool, Capability, Skill ou Resource;
- algoritmo de Agent ou composição de Context;
- endpoint, protocolo de cliente ou mecanismo de autenticação de transporte;
- broker, fila, scheduler, Worker, banco, cache, lease ou ORM concretos;
- estratégia de autoscaling, prioridade comercial ou SLO de produção;
- implementação de workflow, DAG engine ou retry distribuído;
- criação de estados adicionais de Execution.

## Responsabilidades e não responsabilidades

O Orchestrator DEVE:

- aceitar intenções autenticadas e autorizadas com ownership, correlação e idempotência;
- coordenar criação administrativa de Agents por Executions;
- criar Executions válidas com Agent, Task, limites e snapshots resolvidos;
- manter planos e dependências sem violar a máquina de estados da RFC 102;
- materializar Executions somente quando pré-condições e janela de agendamento permitirem;
- solicitar despacho com o mínimo de dados, preferencialmente `execution_id` e identidade operacional;
- coordenar retries como novas Executions quando a tentativa anterior já for terminal;
- detectar deadlines, ausência de progresso e perda operacional por portas de supervisão;
- propagar cancelamento e falha segundo política explícita;
- preparar fatos correlacionados e auditáveis como entradas de outbox no mesmo commit das mudanças que os confirmam, segundo as RFCs 103 e 601;
- preservar isolamento de usuário, Workspace e Agent em cada decisão.

O Orchestrator NÃO DEVE:

- invocar Provider ou executar LLM diretamente;
- executar loop de Agent dentro do processo de API ou orquestração;
- carregar prompt completo, histórico bruto ou Memory privada no despacho;
- importar SDK de Provider, cliente de fila, modelo ORM ou Resource concreto;
- alterar estado de Execution contornando `ExecutionControl`;
- converter timeout em cancelamento ou falha em sucesso;
- reutilizar uma Execution terminal para retry;
- inferir autorização por relação pai-filho, correlação ou conhecimento de ID;
- manter Worker ocupado durante espera longa que possa ser retomada por Event.

## Arquitetura e fronteiras

```text
Intenção autorizada
        │
        ▼
   Orchestrator
     ├── AgentRegistry / AgentAdministration
     ├── ExecutionFactory
     ├── SchedulingPort
     ├── DispatchPort
     ├── SupervisionPort
     ├── ContextPolicyPort
     └── fachadas de domínio
             ├── ExecutionControl
             └── PlanStorePort
                     │ DomainChange + OutboxEntry
                     ▼
          TransactionalPersistence.transact
                     │ após COMMITTED
                     ▼
             outbox ──> EventBus
```

`SchedulingPort`, `DispatchPort` e `SupervisionPort` são contratos lógicos. Seus adapters podem usar tecnologias diferentes sem alterar regras de domínio. `ExecutionControl` confirma mudanças de `Execution`; `PlanStorePort` é a fachada para mudanças de plano. Ambas montam `DomainChange` e `OutboxEntry` e usam a única fronteira `TransactionalPersistence.transact` da RFC 601. O Orchestrator não recebe publicador, objetos de broker, handles de Worker ou payloads proprietários, e a entrega posterior da outbox segue a RFC 103.

## Entidades e dados conceituais

Planos descrevem intenção e dependência; não são trabalho em execução. Um nó só se torna tentativa concreta quando uma `Execution` é criada.

```text
OrchestrationPlan {
  plan_id: OrchestrationPlanId
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  correlation_id: CorrelationId
  nodes: PlannedWork[]
  dependencies: DependencyEdge[]
  policy: OrchestrationPolicy
  created_at: Instant
  version: Version
}

PlannedWork {
  work_id: PlannedWorkId
  agent_id: AgentId
  task: TaskSnapshot
  schedule: ScheduleConstraint | null
  deadline_at: Instant | null
  limits: ExecutionLimits
  idempotency_key: IdempotencyKey
  materialized_execution_id: ExecutionId | null
}

DependencyEdge {
  predecessor_work_id: PlannedWorkId
  successor_work_id: PlannedWorkId
  condition: DependencyCondition
  failure_policy: DependencyFailurePolicy
}

DependencyCondition = COMPLETED | TERMINAL | RESULT_MATCHED
DependencyFailurePolicy = DO_NOT_MATERIALIZE | MATERIALIZE_FAILURE_HANDLER | CANCEL_RELATED
```

Um `PlannedWork` não autoriza efeito externo e não possui estado de Execution. O Orchestrator cria a Execution sucessora em `QUEUED` somente quando dependências, agendamento, owner e política estiverem satisfeitos. Isso preserva a semântica de `QUEUED` como tentativa aceita e elegível para despacho.

```text
OrchestrationPolicy {
  cancellation_policy: CancellationPropagationPolicy
  failure_policy: FailurePropagationPolicy
  retry_policy_ref: RetryPolicyRef | null
  maximum_parallel_executions: PositiveInteger
  context_sharing_policy_ref: ContextSharingPolicyRef
}

ScheduleConstraint {
  not_before: Instant
  expires_at: Instant | null
}
```

Agendamento não altera a máquina de estados. Antes de `not_before`, existe somente intenção planejada. Depois de `expires_at`, a intenção não é materializada; o fato é registrado e o plano segue sua política de falha. Trabalho recorrente futuro cria nova Execution por ocorrência.

## Contratos públicos

O pseudocódigo é tipado, contratual e não executável.

```text
interface Orchestrator {
  submit(request: OrchestrationRequest) -> OrchestrationReceipt
  evaluate(plan_id: OrchestrationPlanId, trigger: EvaluationTrigger) -> EvaluationOutcome
  request_cancel(command: CancelOrchestration) -> CancellationReceipt
  request_retry(command: RetryExecution) -> ExecutionRef

  pre: actor está autorizado no user, Workspace e Agents envolvidos
  post: todo trabalho materializado possui Execution própria
  post: repetição da mesma chave não duplica plano nem Execution
}

OrchestrationRequest {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  intent: OrchestrationIntent
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  idempotency_key: IdempotencyKey
  requested_at: Instant
}

OrchestrationIntent =
  | RunAgentTask { agent_id: AgentId, task: TaskSnapshot, limits: ExecutionLimits }
  | AdministerAgent { operation: AgentAdministrationIntent }
  | ExecutePlan { plan: OrchestrationPlanDraft }
  | ContinueExecution { execution_id: ExecutionId, input_ref: InputReference }
```

```text
interface ExecutionFactory {
  create(request: CreateExecutionRequest) -> ExecutionSnapshot

  pre: Agent e config_version estão resolvidos e autorizados
  pre: agendamento e dependências estão satisfeitos
  post: Execution nasce em QUEUED conforme RFC 102
  post: ExecutionQueued é registrado com a mudança de estado
}

CreateExecutionRequest {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  agent_config_version: AgentConfigVersion
  task: TaskSnapshot
  limits: ExecutionLimits
  parent_execution_id: ExecutionId | null
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  idempotency_key: IdempotencyKey
  context_seed_refs: ContextSeedReference[]
}
```

```text
interface SchedulingPort {
  register(trigger: ScheduleTrigger) -> ScheduleReceipt
  cancel(trigger_id: ScheduleTriggerId, command: CancelCommand) -> CommandResult
}

interface DispatchPort {
  request_dispatch(request: DispatchRequest) -> DispatchReceipt
}

DispatchRequest {
  execution_id: ExecutionId
  expected_state_version: Version
  processing_class: ProcessingClass
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}
```

O `DispatchRequest` NÃO DEVE conter prompt completo, Context, Memory, credencial, resposta de Provider ou histórico. O Worker recebe a identidade mínima, adquire a Execution por porta e o Runtime solicita Context ao ContextManager.

```text
interface SupervisionPort {
  observe(query: SupervisionQuery) -> SupervisionSnapshot
}

SupervisionSnapshot {
  execution_id: ExecutionId
  observed_state: ExecutionState
  state_version: Version
  last_progress_at: Instant | null
  processing_ownership: ProcessingOwnership | null
  pending_action_ref: PendingActionRef | null
}
```

Supervisão observa; não altera storage. Recuperação e cancelamento passam por comandos do Kernel com versão esperada.

## Criação de Agent

Uma criação de Agent é trabalho e DEVE ser uma Execution administrativa:

1. o Orchestrator valida ator, `user_id`, Workspace, política e chave idempotente;
2. resolve o Agent administrativo autorizado que assumirá a Task de criação;
3. cria uma Execution em `QUEUED` cujo resultado esperado é `AgentRef`;
4. a porta `AgentAdministration` confirma identidade e configuração inicial;
5. `AgentCreated` é registrado somente após confirmação;
6. a Execution administrativa termina com referência ao Agent ou falha explicitamente.

O Orchestrator não instancia Agent diretamente, não cria linha de banco e não usa um chat como identidade.

## Dependências e prontidão

O Orchestrator avalia dependências por Events e consultas autorizadas ao estado atual. Event não é autorização nem estado atual por si só. Uma dependência está satisfeita somente quando:

- a identidade e o terminal da predecessor foram validados;
- a condição declarada foi atendida;
- resultado ou Artifact referenciado está disponível e autorizado;
- `user_id` e Workspace permanecem compatíveis;
- a versão do plano ainda é vigente.

Dependência falha não vira estado oculto de Execution. Se a sucessora ainda não foi materializada, ela permanece intenção e segue `failure_policy`. Se uma Execution relacionada já existe em `QUEUED`, uma política de cancelamento pode solicitar `QUEUED -> CANCELLED`; não há transição `QUEUED -> FAILED`. Se trabalho de tratamento for necessário, ele recebe outra Execution.

## Agendamento e despacho

O fluxo de agendamento é:

1. registrar uma intenção com `not_before`, deadline e chave idempotente;
2. receber trigger operacional, sem confiar nele como autorização;
3. revalidar plano, relógio, ownership, Agent, versão e dependências;
4. materializar uma Execution em `QUEUED` uma única vez;
5. solicitar despacho com versão esperada;
6. deixar Worker e Runtime aplicarem `QUEUED -> STARTING -> RUNNING`.

Pedidos de despacho duplicados são tolerados: aquisição e versão impedem dois loops válidos. O Orchestrator nunca publica `ExecutionStarted`; esse fato pertence ao Kernel após entrada confirmada em `RUNNING`.

## Contexto mínimo

O Orchestrator seleciona apenas referências iniciais necessárias à finalidade: Task imutável, Agent/config version, resultados de dependência, Artifact refs e handoff refs. Não monta o prompt final nem resolve conteúdo em massa.

`context_seed_refs` DEVE:

- possuir proveniência, classificação, owner e versão;
- ser limitado ao mínimo necessário;
- excluir histórico bruto por padrão;
- ser reautorizado pelo ContextManager;
- preferir Artifact, Memory e resultado por referência;
- registrar por que cada referência foi incluída.

Uma Execution filha não herda automaticamente Context, autoridade, Memory ou Grants da mãe.

## Deadlines, timeout e recuperação

Deadline de orquestração, timeout de Execution e expiração operacional são distintos:

- **deadline antes da materialização:** a intenção expira sem criar Execution e a expiração com seu Event é confirmada no mesmo commit do plano;
- **deadline de uma Execution `QUEUED`:** política pode emitir comando explícito de cancelamento; se aceito, termina `CANCELLED` e a causa registra deadline de política;
- **timeout total, de Provider ou ação:** é governado por Runtime e RFC 102, normalmente produzindo `FAILED`, não `CANCELLED`;
- **expiração de lease ou ausência de progresso:** indica necessidade de reconciliação; não prova falha da Task.

Na recuperação, o Orchestrator observa versão e checkpoint, confirma perda do direito de processamento e solicita ao Kernel redispatch seguro. Ele não repete efeito externo nem muda estado diretamente. Se reconciliação não puder provar consistência, o Kernel termina a tentativa em `FAILED`. Retry posterior cria nova Execution com mesmo `correlation_id` e causalidade explícita.

## Cancelamento e propagação

```text
CancellationPropagationPolicy =
  CANCEL_DESCENDANTS | CANCEL_ONLY_TARGET | DETACH_AUTHORIZED_DESCENDANTS

CancelOrchestration {
  actor: ActorRef
  target: OrchestrationPlanId | ExecutionId
  policy: CancellationPropagationPolicy
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
  requested_at: Instant
}
```

O Orchestrator enumera Executions alcançáveis dentro do mesmo ownership e solicita cancelamento idempotente individual. Relação causal não concede autoridade. `DETACH_AUTHORIZED_DESCENDANTS` exige autorização explícita e owner responsável; não é fallback automático.

Cancelamento é cooperativo. O Orchestrator não muda terminais já confirmados, não converte resultado tardio em sucesso e não afirma `ExecutionCancelled` antes de o Kernel confirmar o estado. Uma falha ao cancelar um descendente é registrada e propagada ao resultado da operação de cancelamento.

## Propagação de falha e retry

Falha de predecessor é representada por terminal `FAILED` e `ExecutionFailed`. O Orchestrator aplica política explícita:

- não materializar dependentes;
- materializar Execution de tratamento com apenas referências de falha sanitizadas;
- cancelar Executions relacionadas ainda não iniciadas;
- permitir ramo independente já autorizado;
- solicitar retry como nova Execution.

Retry nunca reabre terminal. A nova Execution recebe novo `execution_id`, preserva `correlation_id`, aponta a tentativa anterior por causalidade e repete apenas efeitos idempotentes ou reconciliáveis. Limites de retry, custo e deadline são explícitos e monotônicos no plano.

## Fluxo normal

1. Uma intenção autorizada entra com correlação e idempotência.
2. O Orchestrator resolve Agent/configuração e cria plano simples ou composto.
3. Quando dependências e agenda permitem, cria a Execution em `QUEUED`.
4. A `ExecutionControl` confirma `QUEUED` e sua entrada de outbox na mesma transação; depois o Orchestrator solicita despacho mínimo.
5. Worker adquire a tentativa e Runtime governa estados, Context e efeitos.
6. Events terminais acionam avaliação de dependentes.
7. O Orchestrator materializa próximos nós ou conclui o plano com referências de resultados.

## Fluxo de falha

- intenção não autorizada é rejeitada sem plano ou Execution;
- configuração de Agent inválida impede materialização;
- trigger duplicado retorna resultado idempotente;
- dependência inconsistente impede o nó e produz fato auditável;
- falha de dispatch deixa a Execution `QUEUED` e permite nova solicitação, sem duplicar tentativa;
- perda de Worker inicia reconciliação por versão e checkpoint;
- falha terminal aplica propagação declarada, nunca sucesso parcial implícito;
- falha de entrega após commit mantém o Event pendente na outbox para republicação conforme as RFCs 103 e 601.

## Fluxo de cancelamento

1. validar ator, escopo, versão e política;
2. impedir materialização de novos nós alcançáveis;
3. cancelar triggers operacionais sem apagar intenção auditável;
4. solicitar cancelamento para cada Execution não terminal autorizada;
5. observar reconhecimento e reconciliar resultados tardios;
6. confirmar fatos próprios do plano como `OutboxEntry` no mesmo commit de suas mudanças e somente referenciar estados subjacentes já confirmados;
7. retornar resultado parcial explícito se algum alvo não puder ser cancelado.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `OrchestrationPlanCreated` | plano autorizado foi registrado |
| `OrchestrationPlanChanged` | nova versão do plano foi confirmada |
| `PlannedWorkBecameReady` | dependências e agenda de um nó foram satisfeitas |
| `PlannedWorkExpired` | deadline venceu antes da materialização |
| `ExecutionMaterialized` | PlannedWork ganhou uma Execution em `QUEUED` |
| `ExecutionDispatchRequested` | pedido idempotente de despacho foi aceito pela porta |
| `ExecutionRecoveryRequested` | reconciliação concluiu que redispatch seguro deveria ser solicitado |
| `OrchestrationCancellationRequested` | comando de cancelamento do plano foi aceito |
| `OrchestrationPlanFinished` | todos os ramos requeridos chegaram ao resultado previsto |
| `OrchestrationPlanFailed` | política declarou que o plano não pode continuar |

Events pertencentes à máquina de estados, como `ExecutionStarted`, `ExecutionFinished`, `ExecutionFailed` e `ExecutionCancelled`, continuam sob ownership do Kernel. Os Events desta RFC carregam `execution_id` quando vinculados a uma tentativa e sempre preservam owner, Workspace, correlação, causalidade e classificação.

## Segurança

- cada intenção, dependência, resultado e cancelamento é autorizado no momento de uso;
- plano pai não concede acesso aos filhos nem a Workspaces diferentes;
- o Orchestrator não recebe credenciais, cookies, prompts completos ou conteúdo privado desnecessário;
- referências de resultado e handoff não concedem acesso por si;
- dispatch inclui apenas identidade operacional mínima;
- triggers e Events não são comandos nem prova de autorização;
- limites de paralelismo, custo e Resource são aplicados por owner e Workspace;
- o lançamento single-user mantém `user_id` e isolamento estrutural.

## Observabilidade

Logs, métricas e traces permitem reconstruir intenção, plano, prontidão, materialização, despacho, recuperação, cancelamento e propagação por IDs. Métricas incluem atraso de agendamento, nós prontos, materializações duplicadas evitadas, dispatch retry, duração por dependência, deadlines expirados, recuperação, fan-out, cancelamentos e falhas por política.

Conteúdo da Task, prompt, Memory e Artifact não é label. Auditoria registra ator, decisão, versão do plano, política aplicada e referências mínimas.

## Invariantes

- todo trabalho assíncrono materializado é uma Execution;
- PlannedWork descreve intenção e não executa efeito;
- Execution nasce em `QUEUED` somente quando elegível;
- estados e transições pertencem ao Kernel e seguem a RFC 102;
- Orchestrator não executa LLM, Agent, Tool ou Capability;
- Orchestrator depende apenas de portas públicas e não vaza adapters;
- despacho carrega identidade mínima, não Context completo;
- dependência, agendamento e retry não criam estados ocultos;
- retry após terminal cria nova Execution;
- timeout, cancelamento e perda operacional permanecem distinguíveis;
- toda decisão preserva correlação, idempotência, ownership e auditoria;
- Context compartilhado usa referências e é reautorizado pelo ContextManager.

## Extensibilidade

Novas estratégias de agendamento, prioridade, supervisão, retry e planejamento PODEM implementar portas públicas. Extensões DEVEM declarar idempotência, ownership, deadline, cancelamento, falha, eventos e compatibilidade com a máquina de estados. Nenhuma extensão pode adicionar `switch/case` por tecnologia ao Orchestrator ou transportar payload proprietário ao Kernel.

## Futuro

Workflows recorrentes, DAGs dinâmicos, fairness multiusuário, quotas, speculative execution e coordenação distribuída poderão especializar planos e políticas. A evolução deverá manter uma Execution por tentativa, resultados por referência, ausência de ordenação global e terminais imutáveis.
