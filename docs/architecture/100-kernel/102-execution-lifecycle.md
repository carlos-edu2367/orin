# RFC 102 — Ciclo de vida da Execution

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](101-runtime.md), [RFC 103 — Sistema de eventos](103-event-system.md), [RFC 104 — Pipeline de contexto](104-context-pipeline.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir `Execution` como unidade universal de trabalho do AgentOS e formalizar identidade, ownership, rastreabilidade, estados, transições, comandos idempotentes, timeout, cancelamento, pausa, retomada e recuperação.

## Fora de escopo

- endpoint, payload HTTP, protocolo de streaming ou interface visual;
- tabela, schema ORM, transação, fila, lease ou banco concreto;
- política de agendamento e dimensionamento de workers;
- algoritmo do Agent, Provider, Tool, Capability ou ContextManager;
- formato físico de log, trace, checkpoint ou Artifact.

## Responsabilidades e não responsabilidades

Esta RFC DEVE:

- tornar toda tentativa de trabalho identificável, autorizável e observável;
- definir os dados mínimos e a máquina de estados de `Execution`;
- impedir transições ambíguas, regressões e reabertura de terminais;
- especificar semântica de comandos concorrentes e repetidos;
- preservar ownership, correlação, causalidade, custo e resultado.

Uma `Execution` NÃO DEVE:

- representar apenas uma Task abstrata; ela representa uma tentativa concreta;
- funcionar como Worker, Agent, Context, Event ou fila;
- conter credencial, objeto vivo de adapter ou histórico bruto ilimitado;
- autorizar acesso apenas porque um ator conhece seu ID;
- ocultar subtrabalho não trivial fora de uma `Execution` relacionada.

## Entidade e ownership

```text
Execution {
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  task: TaskSnapshot
  state: ExecutionState
  state_version: Version
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  parent_execution_id: ExecutionId | null
  context_manifest_ref: ContextManifestRef | null
  result_ref: ResultReference | null
  failure: ExecutionFailure | null
  limits: ExecutionLimits
  usage: Usage
  iteration_count: NonNegativeInteger
  created_at: Instant
  queued_at: Instant
  started_at: Instant | null
  updated_at: Instant
  finished_at: Instant | null
}

ExecutionState =
  QUEUED | STARTING | RUNNING | WAITING_TOOL | WAITING_USER |
  PAUSED | COMPLETED | FAILED | CANCELLED
```

`user_id` é obrigatório. `workspace_id` é obrigatório quando a Task, Agent, Memory, Resource, Artifact ou resultado pertence a um projeto; nulo só é válido para trabalho explicitamente fora de Workspace. `agent_id` identifica quem assume a Task, não o Worker. Delegação ou retry cria nova `Execution` com identidade própria, relação causal e a mesma correlação lógica quando aplicável.

`task` é um snapshot estável da intenção aceita ou uma referência imutável acompanhada de versão. Alterar a Task de uma tentativa em andamento é proibido; nova intenção cria nova `Execution`.

## Rastreabilidade, contexto, custo e eventos

- `correlation_id` acompanha o fluxo lógico de ponta a ponta;
- `causation_id` aponta para a causa direta quando conhecida;
- `parent_execution_id` expressa delegação ou subtrabalho, sem transferir autorização;
- `context_manifest_ref` referencia a composição reproduzível do Context e não o transforma em Memory;
- `usage` e `iteration_count` são monotônicos;
- `result_ref` só é obrigatório em `COMPLETED` e deve apontar para resultado autorizado;
- `failure` só é preenchida quando a tentativa falha e nunca contém segredo;
- cada transição confirmada gera Event correlacionado com sequência por Execution conforme a RFC 103.

## Arquitetura

```text
Command autorizado
       │ idempotency_key + expected_version
       ▼
ExecutionControl ──valida──> State Machine
       │                         │
       └── TransactionalPersistence.transact
                    │ estado/version + outbox no mesmo commit
                    ▼
             publicador de outbox ──> EventBus
```

A máquina de estados pertence ao Kernel. API, worker, scheduler ou adapter podem solicitar comandos, mas não podem escrever estado contornando o contrato. `ExecutionControl` é a fachada canônica de domínio; `TransactionalPersistence` da RFC 601 é a única fronteira atômica subjacente. O publicador de outbox apenas entrega Events já confirmados e nunca completa ou corrige uma transição.

## Semântica dos estados

| Estado | Significado normativo |
| --- | --- |
| `QUEUED` | tentativa aceita e elegível para despacho, ainda sem Runtime ativo |
| `STARTING` | Worker adquiriu direito de inicialização e valida pré-condições |
| `RUNNING` | Runtime governa o loop e pode produzir novo passo |
| `WAITING_TOOL` | Runtime aguarda resultado ou reconciliação de Tool/Capability já autorizada |
| `WAITING_USER` | falta entrada explícita do usuário; nenhum Worker precisa permanecer ocupado |
| `PAUSED` | execução suspensa em limite seguro por comando ou política, retomável |
| `COMPLETED` | resultado foi confirmado com sucesso; terminal |
| `FAILED` | continuação segura não é possível nesta tentativa; terminal |
| `CANCELLED` | cancelamento foi reconhecido e efeitos novos foram interrompidos; terminal |

`WAITING_TOOL` não autoriza outra ação concorrente na mesma linha de execução. Capabilities que possuam paralelismo futuro devem manter reconciliação e determinismo sob contrato próprio.

`WAITING_CHILD` NÃO é um `ExecutionState`. Quando uma Capability, Skill ou coordenação registra internamente `WAITING_CHILD`, a `Execution` correspondente DEVE estar em `PAUSED`, com checkpoint e conjunto de filhos aguardados confirmados. Ao satisfazer a espera, a retomada canônica é `PAUSED -> QUEUED`; nenhum adapter pode projetar `WAITING_CHILD` como novo estado persistente de `Execution`.

## Transições permitidas

| Origem | Destino | Condição |
| --- | --- | --- |
| `QUEUED` | `STARTING` | despacho adquirido por exatamente um Worker válido |
| `QUEUED` | `CANCELLED` | cancelamento autorizado antes do início |
| `STARTING` | `RUNNING` | pré-condições, ownership e checkpoint validados |
| `STARTING` | `QUEUED` | aquisição perdida ou recuperação segura antes de efeito |
| `STARTING` | `FAILED` | inicialização irrecuperável ou inválida |
| `STARTING` | `CANCELLED` | cancelamento reconhecido em limite seguro |
| `RUNNING` | `WAITING_TOOL` | ação externa autorizada foi iniciada |
| `RUNNING` | `WAITING_USER` | continuação exige nova entrada do usuário |
| `RUNNING` | `PAUSED` | pausa reconhecida em checkpoint seguro |
| `RUNNING` | `QUEUED` | Worker perdido e recuperação segura exige redispatch |
| `RUNNING` | `COMPLETED` | resultado persistido e pós-condições satisfeitas |
| `RUNNING` | `FAILED` | erro ou limite impede continuação segura |
| `RUNNING` | `CANCELLED` | cancelamento reconhecido em limite seguro |
| `WAITING_TOOL` | `RUNNING` | resultado foi confirmado ou reconciliado |
| `WAITING_TOOL` | `PAUSED` | pausa reconhecida após estabilizar a ação pendente |
| `WAITING_TOOL` | `QUEUED` | perda do Worker exige reconciliação por novo processamento |
| `WAITING_TOOL` | `FAILED` | ação falhou sem continuação segura ou ficou irreconciliável |
| `WAITING_TOOL` | `CANCELLED` | cancelamento reconhecido e ação pendente estabilizada |
| `WAITING_USER` | `QUEUED` | entrada autorizada foi anexada e nova execução operacional deve ser despachada |
| `WAITING_USER` | `PAUSED` | pausa explícita aceita enquanto aguardava usuário |
| `WAITING_USER` | `FAILED` | timeout de espera ou entrada definitivamente inválida conforme política |
| `WAITING_USER` | `CANCELLED` | cancelamento autorizado |
| `PAUSED` | `QUEUED` | retomada autorizada solicita novo despacho |
| `PAUSED` | `FAILED` | checkpoint incompatível ou prazo irrecuperável |
| `PAUSED` | `CANCELLED` | cancelamento autorizado |

Transições para `QUEUED` durante recuperação exigem incremento de `state_version`, causa registrada e checkpoint reconciliável. Elas não apagam uso, iterações, eventos nem efeitos anteriores.

## Transições proibidas

Qualquer transição ausente da tabela é proibida. Em especial:

- `QUEUED` não pode saltar diretamente para `RUNNING` ou `COMPLETED`;
- `STARTING` não pode concluir sem entrar em `RUNNING`;
- `WAITING_USER` e `PAUSED` não retomam diretamente em `RUNNING`; primeiro voltam a `QUEUED` para nova aquisição;
- `WAITING_TOOL` não pode ir para `COMPLETED` sem reconciliar o resultado e voltar a `RUNNING`;
- estados terminais não possuem saída;
- `FAILED` não volta a `QUEUED`; retry cria outra `Execution`;
- `CANCELLED` não pode virar `COMPLETED` por resultado tardio;
- nenhuma transição pode reduzir versão, custo, uso ou iteração.

## Contratos de comando e idempotência

```text
ExecutionCommand {
  command_id: CommandId
  idempotency_key: IdempotencyKey
  execution_id: ExecutionId
  actor: ActorRef
  expected_version: Version | null
  correlation_id: CorrelationId
  requested_at: Instant
}

interface ExecutionControl {
  acquire(request: AcquireExecution) -> ExecutionSnapshot
  current_signal(query: ExecutionControlQuery) -> ControlSignal
  request_cancel(command: CancelExecution) -> CommandResult
  request_pause(command: PauseExecution) -> CommandResult
  request_resume(command: ResumeExecution) -> CommandResult
  provide_input(command: ProvideExecutionInput) -> CommandResult
  transition(command: StateTransition) -> TransitionResult
  commit(command: CommitExecutionChanges) -> ExecutionCommitResult

  post: todo resultado mutante Accepted ou Applied corresponde a um único commit COMMITTED da RFC 601
  post: estado, versão, mudanças relacionadas e Events ficam duráveis na mesma transação
  invariant: a fachada nunca confirma estado por uma operação e Event por outra
}

AcquireExecution {
  execution_id: ExecutionId
  worker_ref: WorkerRef
  expected_version: Version | null
}

ExecutionControlQuery {
  execution_id: ExecutionId
  actor: ActorRef
  correlation_id: CorrelationId
}

ControlSignal = CONTINUE | PAUSE_REQUESTED | CANCEL_REQUESTED

CommitExecutionChanges extends ExecutionCommand {
  expected_state: ExecutionState
  changes: ExecutionRelatedChange[]
  events: NonEmptyList<EventEnvelope<EventPayload>>
}

ExecutionRelatedChange =
  | UsageRecorded { delta: UsageDelta }
  | ResultRecorded { result_ref: ResultReference }
  | CheckpointRecorded { checkpoint_ref: CheckpointRef }
  | ControlAcknowledged { signal: ControlSignal, safe_boundary: SafeBoundary }

ExecutionCommitResult =
  | Applied { resulting_version: Version, transaction_id: TransactionId }
  | AlreadyApplied { resulting_version: Version, transaction_id: TransactionId }
  | Rejected { reason: RejectionReason, current_state: ExecutionState }
  | Conflict { current_version: Version }
  | Indeterminate { transaction_id: TransactionId }

CommandResult =
  | Accepted { resulting_version: Version }
  | AlreadyApplied { resulting_version: Version }
  | Rejected { reason: RejectionReason, current_state: ExecutionState }
  | Conflict { current_version: Version }
```

Para o mesmo escopo de ownership e `idempotency_key`, payload semanticamente igual DEVE retornar o mesmo resultado observável. Reutilizar a chave com payload incompatível DEVE ser rejeitado. `expected_version` protege transições concorrentes; conflito exige releitura, não sobrescrita.

Cancelar uma `Execution` já `CANCELLED` retorna `AlreadyApplied`. Cancelar `COMPLETED` ou `FAILED` é rejeitado sem mudar o terminal. Pausar `PAUSED` e retomar uma retomada já aceita são idempotentes conforme a chave. Eventos também podem ser entregues mais de uma vez; idempotência de comando não elimina deduplicação de Event.

## Fluxo normal

1. Uma intenção autorizada cria `Execution` em `QUEUED`, com Task, ownership, limites e correlação.
2. A orquestração despacha somente `execution_id`; o Worker adquire e solicita `QUEUED -> STARTING`.
3. O Runtime valida dependências e solicita `STARTING -> RUNNING`, produzindo `ExecutionStarted`.
4. Cada turno atualiza Context, uso, eventos e checkpoints. Ações externas usam `WAITING_TOOL -> RUNNING`.
5. Necessidade de entrada usa `WAITING_USER`; a entrada autorizada gera novo despacho por `QUEUED`.
6. Resultado confirmado é registrado antes de `RUNNING -> COMPLETED` e `ExecutionFinished`.

## Fluxo de falha e timeout

Falhas recuperáveis do adapter podem ser tratadas dentro dos limites da mesma `Execution`; todo retry consome tempo e uso e permanece observável. Quando a continuação segura termina, a transição é `FAILED` com causa sanitizada.

Timeouts são avaliados com instantes UTC e `Duration` explícita. A política DEVE distinguir:

- timeout total da `Execution`;
- timeout de Provider ou ação;
- timeout de espera do usuário;
- expiração de lease operacional, que indica recuperação e não necessariamente falha da Task.

Timeout de Provider ou ação pode permitir retry idempotente; timeout total ou de espera pode levar a `FAILED`. Timeout NÃO DEVE ser registrado como `CANCELLED` salvo se também houve comando de cancelamento e sua causalidade prevalecer por regra explícita. Corridas são decididas por versão: o primeiro terminal confirmado prevalece.

## Cancelamento

O pedido de cancelamento é comando, não Event. Após ser aceito, torna-se observável e o Runtime coopera em limites seguros. Estados não terminais podem chegar a `CANCELLED` conforme a tabela. Operações subordinadas recebem sinal quando a porta suportar; incapacidade de interrupção não autoriza iniciar novos efeitos.

O terminal `CANCELLED` só é confirmado após estabilização mínima do estado: ação já confirmada é contabilizada, ação incerta é marcada para reconciliação e checkpoint seguro pode ser criado. Resultado que chega depois permanece Event auditável, mas não reabre nem conclui a `Execution`.

## Pausa e retomada

Pausa preserva intenção de continuar e exige checkpoint seguro. Em `PAUSED`, nenhum Worker mantém ownership operacional ativo e nenhum novo efeito é iniciado. Retomada revalida ator, ownership, políticas, validade das referências e compatibilidade do checkpoint; em seguida transiciona para `QUEUED`. Uma alteração material da Task ou do Agent exige nova `Execution`, não retomada.

## Recuperação após falha de Worker

Um mecanismo de supervisão pode detectar expiração de lease ou ausência de progresso por portas operacionais. Ele NÃO muda estado diretamente em storage. A recuperação:

1. confirma que o antigo direito de processamento expirou;
2. lê versão, checkpoint e ação pendente;
3. reconcilia efeito externo por chave idempotente ou consulta autorizada;
4. transiciona `STARTING`, `RUNNING` ou `WAITING_TOOL` para `QUEUED` se a retomada for segura;
5. transiciona para `FAILED` se não for possível provar consistência;
6. registra causa, incrementa versão e publica Events correspondentes.

Dois Workers não podem recuperar a mesma versão com sucesso. Um Worker antigo que reapareça deve falhar por perda de versão/lease e não pode persistir novo terminal.

## Eventos

Eventos mínimos do ciclo:

| Event | Aplicação |
| --- | --- |
| `ExecutionQueued` | criação ou redispatch confirmado |
| `ExecutionStarted` | entrada confirmada em `RUNNING` pela primeira aquisição útil |
| `ExecutionWaitingForTool` | entrada em `WAITING_TOOL` |
| `ExecutionWaitingForUser` | entrada em `WAITING_USER` |
| `ExecutionPaused` | entrada em `PAUSED` |
| `ExecutionResumed` | retomada aceita e retorno a `QUEUED` |
| `ExecutionFinished` | entrada em `COMPLETED` |
| `ExecutionFailed` | entrada em `FAILED` |
| `ExecutionCancelled` | entrada em `CANCELLED` |

Redispatch de recuperação pode publicar `ExecutionQueued` novamente com nova sequência e causa; não deve publicar um segundo `ExecutionStarted` como se apagasse a história. Payloads carregam estado anterior, novo estado, versão e razão sanitizada quando pertinente.

## Segurança

- comandos exigem ator autenticado e autorização no `user_id`, `workspace_id` e Agent aplicáveis;
- relação pai-filho não transfere autorização entre Workspaces;
- anexar entrada, retomar ou consultar resultado exige nova validação;
- `failure`, Task, Context e resultados podem ter classificações distintas e exposição mínima;
- IDs, estados e eventos não devem revelar segredo nem conteúdo privado por labels;
- Workers recebem somente escopo e referências necessários à tentativa.

## Observabilidade

Cada transição gera log estruturado, Event e sinais de trace com `execution_id`, `correlation_id`, versão, origem/destino, razão categórica e duração no estado. Métricas incluem filas, duração por estado, transições rejeitadas, conflitos de versão, timeouts, cancelamentos, recuperações, terminais e custo. Conteúdo da Task e do resultado não é necessário para correlação operacional.

## Extensibilidade

Novos motivos, limites ou metadados podem ser adicionados sem alterar a máquina. Novo estado exige revisão desta RFC, definição de todas as transições, cancelamento, recuperação, eventos e impacto em consumidores. Subexecutions, agendamento e delegação futura usam novas `Execution`s correlacionadas, não estados ocultos.

## Invariantes

- toda ação relevante é uma `Execution` ou Event associado a uma;
- uma Task e uma Execution são conceitos distintos;
- cada `Execution` possui exatamente um `user_id`, um Agent e ownership de Workspace quando aplicável;
- somente transições listadas são válidas;
- `state_version`, uso, custo e iterações são monotônicos;
- `COMPLETED`, `FAILED` e `CANCELLED` são terminais e mutuamente exclusivos;
- apenas `COMPLETED` possui resultado de sucesso final;
- retry após terminal cria nova identidade e preserva causalidade;
- Context continua temporário e referenciado, nunca convertido implicitamente em Memory;
- toda transição confirmada é correlacionada, sequenciada e auditável.

## Futuro

Execuções programadas, recorrentes, delegadas ou distribuídas poderão adicionar relações e políticas. Estados adicionais só serão aceitos quando não puderem ser representados com os estados atuais e quando a evolução definir migração e compatibilidade de consumidores.
