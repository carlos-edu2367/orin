# RFC 401 — Tool Runtime

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 402 — Resource Manager](402-resource-manager.md), [RFC 406 — Capabilities](406-capabilities.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir o registro, o contrato uniforme e o ciclo de invocação de Tools atômicas. O Tool Runtime valida entrada, autorização, limites e disponibilidade de Resource antes de executar, normaliza streaming, resultado, falha e cancelamento e confirma fatos correlacionados pela outbox canônica sem expor adapters ao Kernel.

## Fora de escopo

- composição, repetição, branching ou compensação entre Tools, que pertencem a Capabilities, Agents ou Orchestrator;
- implementação concreta de filesystem, terminal, browser, fila, banco ou Artifact Storage;
- protocolo de Provider para solicitar Tools e formato de transporte para clientes;
- descoberta, instalação e confiança de plugins, Skills ou MCP;
- linguagem, framework, serialização ou schema de persistência.

## Responsabilidades e não responsabilidades

O Tool Runtime DEVE:

- manter um `ToolRegistry` versionado, consultável e independente de adapters concretos;
- validar descriptor, versão, argumentos, resultado e compatibilidade antes e depois da execução;
- avaliar autorização por usuário, Workspace, Agent, Execution, finalidade e permissões declaradas;
- reservar Resources por meio do Resource Manager e aplicar timeout, orçamento e limites;
- controlar uma invocação por identidade, idempotência, estado e sinal de cancelamento;
- normalizar streaming com sequência, backpressure e término explícito;
- traduzir falhas para tipos públicos estáveis e emitir Events mínimos conforme a RFC 103;
- devolver conteúdo volumoso ou sensível por referência autorizada.

Uma Tool e o Tool Runtime NÃO DEVEM:

- chamar outra Tool, acessar o registro para compor trabalho ou iniciar um fluxo composto;
- criar trabalho fora de uma `Execution`;
- confiar em argumentos produzidos por modelo, página, arquivo ou usuário sem nova validação;
- acessar storage, banco, cache ou Resource concreto fora de uma porta declarada;
- conceder autorização por nome de Tool, posse de ID ou sucesso anterior;
- registrar credenciais, segredos ou conteúdo integral em Event, log ou métrica.

## Arquitetura

```text
Runtime / Capability
        │ ToolInvocationRequest
        ▼
    Tool Runtime
        ├── ToolRegistry
        ├── Input / Output Validator
        ├── AuthorizationPolicy
        ├── ResourceManager
        ├── ToolExecutor ──> Tool registrada ──porta──> Resource adapter
        └── InvocationControl (fachada de estado)
                 │ DomainChange + OutboxEntry
                 ▼
          TransactionalPersistence.transact
                 │ após COMMITTED
                 ▼
             outbox ──> EventBus
```

Consumidores conhecem `ToolRef` e contratos públicos. A Tool recebe somente o contexto operacional e os Resources já autorizados; ela não recebe o registro, o Runtime do Kernel nem um mecanismo para invocar Tools. `InvocationControl` valida a máquina da invocação e prepara `DomainChange` e `OutboxEntry`; a confirmação ocorre exclusivamente por `TransactionalPersistence.transact` da RFC 601. O Tool Runtime não possui publicador direto, e a entrega posterior da outbox segue a RFC 103.

## Dados

Toda operação sensível usa o contexto abaixo. `purpose` é uma finalidade estável, específica e auditável; texto livre não substitui uma política registrada.

```text
SensitiveOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

ToolRef {
  tool_id: ToolId
  version: ToolVersion
}

ToolDescriptor {
  tool_ref: ToolRef
  name: ToolName
  description: Text
  input_schema: TypeSchema
  output_schema: TypeSchema
  permissions: PermissionRequirement[]
  resource_requirements: ResourceRequirement[]
  limits: ToolLimits
  supports_streaming: Boolean
  supports_cancellation: Boolean
  idempotency: IDEMPOTENT | IDEMPOTENT_WITH_KEY | NON_IDEMPOTENT
  result_classification: DataClassification
  status: ACTIVE | DEPRECATED | DISABLED
}

ToolLimits {
  timeout: Duration
  maximum_input_bytes: NonNegativeInteger
  maximum_output_bytes: NonNegativeInteger
  maximum_stream_items: NonNegativeInteger
  maximum_resource_usage: ResourceBudget
}
```

Operações administrativas do registro usam um contexto próprio, tão explícito quanto o contexto de execução:

```text
ToolRegistryOperationContext {
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

`workspace_id` nulo só é válido para Tool global. `agent_id` e `execution_id` podem ser nulos apenas para manutenção administrativa ou bootstrap controlado; isso não permite omitir `user_id`, `correlation_id`, `administrative_correlation_id`, `purpose` ou `actor` aplicáveis.

```text
ToolInvocation {
  invocation_id: ToolInvocationId
  tool_ref: ToolRef
  context: SensitiveOperationContext
  arguments: StructuredValue
  idempotency_key: IdempotencyKey | null
  limits: EffectiveToolLimits
  state: ToolInvocationState
  resource_lease_refs: ResourceLeaseRef[]
  started_at: Instant | null
  finished_at: Instant | null
}

ToolInvocationState =
  REQUESTED | VALIDATED | AUTHORIZED | RUNNING |
  SUCCEEDED | FAILED | CANCELLED
```

Estados terminais são mutuamente exclusivos. A mesma `invocation_id` não pode executar duas vezes; repetição segura depende da política declarada e de `idempotency_key`, nunca de suposição do chamador.

## Contratos tipados

```text
interface ToolRegistry {
  register(request: RegisterTool) -> RegistrationResult
  resolve(tool_ref: ToolRef, context: SensitiveOperationContext) -> ToolDescriptor
  list(query: AuthorizedToolQuery) -> ToolDescriptor[]
  disable(request: DisableTool) -> RegistrationResult

  invariant: uma versão publicada é imutável
  invariant: nomes não concedem permissão nem selecionam versão implicitamente
}

RegisterTool {
  request_id: RegistryRequestId
  context: ToolRegistryOperationContext
  descriptor: ToolDescriptor
  factory: ToolFactoryRef
  package_integrity_ref: IntegrityRef
  idempotency_key: IdempotencyKey
}

DisableTool {
  request_id: RegistryRequestId
  context: ToolRegistryOperationContext
  tool_ref: ToolRef
  expected_status: ACTIVE | DEPRECATED
  reason: DisableReason
  idempotency_key: IdempotencyKey
}

AuthorizedToolQuery {
  context: ToolRegistryOperationContext
  tool_ref: ToolRef | null
  status: (ACTIVE | DEPRECATED | DISABLED)[]
  permission_filter: Permission[]
  page: PageRequest
}
```

```text
interface ToolRuntime {
  invoke(request: ToolInvocationRequest) -> ToolInvocationOutcome
  stream(request: ToolStreamRequest, sink: ToolStreamSink) -> ToolInvocationOutcome
  request_cancel(request: CancelToolInvocation) -> CancelToolResult
  inspect(query: AuthorizedToolInvocationQuery) -> ToolInvocationSnapshot

  pre: contexto corresponde ao ownership da Execution e do Agent
  pre: Tool exata está registrada, ativa e permitida para a finalidade
  post: resultado terminal foi validado e é observável
}

ToolInvocationRequest {
  invocation_id: ToolInvocationId
  tool_ref: ToolRef
  context: SensitiveOperationContext
  arguments: StructuredValue
  idempotency_key: IdempotencyKey | null
  requested_limits: ToolLimitRequest
}

ToolInvocationOutcome =
  | ToolSucceeded { invocation_id: ToolInvocationId, result: InlineResult | ResultReference, usage: ResourceUsage }
  | ToolFailed { invocation_id: ToolInvocationId, error: ToolError, retryability: Retryability }
  | ToolCancelled { invocation_id: ToolInvocationId, reason: CancellationReason, partial_result_ref: ResultReference | null }
```

```text
interface AtomicTool<TInput, TOutput> {
  execute(call: AtomicToolCall<TInput>) -> ToolExecution<TOutput>

  pre: input, permissões, limites e leases já foram validados
  post: realiza uma única responsabilidade operacional
  invariant: não recebe ToolRuntime, ToolRegistry ou outra Tool
}

AtomicToolCall<TInput> {
  invocation_id: ToolInvocationId
  context: SensitiveOperationContext
  input: TInput
  resources: AuthorizedResourceHandle[]
  cancellation: CancellationSignal
  deadline: Instant
}
```

```text
ToolStreamItem<T> {
  invocation_id: ToolInvocationId
  sequence: PositiveInteger
  occurred_at: Instant
  kind: PROGRESS | DATA | WARNING
  value: T
}

interface ToolStreamSink {
  emit(item: ToolStreamItem<StructuredValue>) -> StreamDisposition
  close(terminal: ToolInvocationOutcome) -> Unit
}

StreamDisposition = ACCEPTED | BACKPRESSURE | REJECTED
```

Streaming é parte da mesma invocação e não cria `Execution` paralela. Itens intermediários não são sucesso final, devem respeitar limites e classificação e podem ser descartáveis; o terminal confirmado continua sendo a fonte do resultado.

## Registro, validação e autorização

O registro rejeita descriptor sem versão exata, schema fechado, limites, política de idempotência, permissões ou Resources declarados. Atualização incompatível cria nova versão; uma versão desabilitada não aceita novas invocações, mas continua identificável para auditoria.

Registro, listagem administrativa e desabilitação exigem `ToolRegistryOperationContext`, autorização e registro auditável antes do efeito. Bootstrap é a única exceção ao vínculo com Agent/Execution: ele só pode ocorrer enquanto o registro inicial estiver vazio, a partir de manifesto allowlisted com integridade verificada, `purpose = SYSTEM_BOOTSTRAP`, `user_id` responsável e `administrative_correlation_id` não nulos. Cada entrada de bootstrap é auditada antes de ficar `ACTIVE`; o caminho é desabilitado após a inicialização e não pode atualizar ou substituir versão existente.

A autorização efetiva é a interseção entre ator, usuário, Workspace, Agent, Execution, finalidade, Tool, argumentos e Resource. Ela ocorre antes de resolver conteúdo sensível e é reavaliada se lease, política ou credencial de curta duração expirar. Validar schema não sanitiza significado: caminhos, comandos, URLs, seletores e referências passam também pela política do Resource responsável.

## Fluxo normal

1. O chamador envia `ToolInvocationRequest` com versão exata e contexto completo.
2. O Tool Runtime confirma Execution ativa, ownership, finalidade, idempotência e limites.
3. O registro resolve o descriptor; argumentos são validados e sanitizados.
4. A autorização calcula permissões efetivas e o Resource Manager concede leases mínimos.
5. `InvocationControl` confirma `RUNNING` e a entrada de outbox de `ToolStarted` no mesmo commit.
6. A Tool executa sua única operação, verificando cancelamento em limites seguros e emitindo itens sequenciados quando aplicável.
7. O Runtime valida saída, contabiliza uso, grava conteúdo volumoso por referência e libera leases.
8. O estado terminal, outcome, uso, referência de resultado e entrada de outbox de `ToolFinished` são confirmados no mesmo commit.

## Fluxo de falha

Falha de schema, autorização ou Resource termina antes de qualquer efeito. Falha após efeito externo registra se o efeito foi `NOT_APPLIED`, `APPLIED` ou `UNKNOWN`, de acordo com evidência do adapter. Retry automático só é permitido quando a operação é idempotente ou reconciliável, a política autoriza e o orçamento comporta; a mesma chave não pode aceitar payload diferente. Saída inválida nunca é promovida a sucesso. Falha de entrega após `COMMITTED` não desfaz o fato: a mesma `OutboxEntry` permanece pendente e é republicada conforme as RFCs 103 e 601.

## Fluxo de cancelamento

`request_cancel` é idempotente e autoriza pelo mesmo contexto da invocação. Antes de iniciar, a invocação pode terminar sem efeito. Durante execução, o Tool Runtime sinaliza a Tool e os Resources, impede novos efeitos e aguarda somente até o limite seguro. Resultado já confirmado é reconciliado; resultado tardio não transforma `CANCELLED` em `SUCCEEDED`. Tool que não suporta interrupção ainda deve declarar limites e permitir isolamento ou término pelo Resource responsável.

## Eventos

| Event | Fato confirmado | Dados mínimos específicos |
| --- | --- | --- |
| `ToolStarted` | invocação autorizada entrou em execução | `tool_ref`, `invocation_id`, `purpose`, resumo sanitizado dos argumentos |
| `ToolProgressed` | marco de progresso observável foi confirmado | `tool_ref`, `invocation_id`, `stream_sequence`, `progress_kind` |
| `ToolFinished` | invocação terminou | `tool_ref`, `invocation_id`, `outcome`, `result_ref`, `usage`, `effect_state` |
| `ToolRegistrationChanged` | registro de uma versão foi ativado, desabilitado ou depreciado | `tool_ref`, `change`, `reason_code` |

Eventos ligados a trabalho carregam `execution_id`, sequência, `user_id`, `workspace_id` e `correlation_id` conforme a RFC 103. Cada envelope é preparado como `OutboxEntry` e confirmado atomicamente com a mudança correspondente pela RFC 601; publicação no `EventBus` é posterior. Eventos não contêm argumentos, chunks ou resultados integrais.

## Segurança

- negação é o padrão para Tool, versão, finalidade, argumento e Resource não declarados;
- `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose` são obrigatórios em operações sensíveis;
- operações administrativas de registro carregam ainda `administrative_correlation_id` quando não pertencem a Execution; bootstrap não pode omitir usuário responsável, finalidade, integridade e auditoria;
- credenciais chegam ao adapter por handle efêmero e não entram em argumentos, resultado, checkpoint ou Event;
- conteúdo externo permanece não confiável e não pode alterar política, permissões ou finalidade;
- limites de tamanho, tempo, volume de stream e uso são aplicados antes e durante a execução;
- referências de resultado são opacas e reautorizadas em toda resolução;
- Tools de plugins executam sob a mesma validação e isolamento, sem privilégio por origem.

## Observabilidade

Logs e traces usam `invocation_id`, `tool_ref`, IDs de ownership, `execution_id`, `correlation_id`, finalidade, estado, duração e códigos de erro sanitizados. Métricas incluem invocações, latência por fase, rejeições de schema/política, timeouts, cancelamentos, retries, backpressure, bytes/chunks, uso de Resource e resultados por categoria. Argumentos, cookies, comandos sensíveis e conteúdo de saída não são labels.

## Invariantes

- toda Tool realiza exatamente uma responsabilidade operacional dentro de uma `Execution`;
- Tool nunca chama Tool e nunca recebe registro ou Runtime como dependência;
- somente Capability, Agent ou Orchestrator compõe Tools;
- registro usa versão exata e descriptor imutável;
- validação e autorização precedem efeitos e são específicas à finalidade;
- streaming pertence a uma única invocação e possui sequência e terminal explícito;
- estados terminais são exclusivos e nunca reabertos;
- cancelamento, timeout e falha não são reportados como sucesso;
- todo Resource é obtido por porta e lease autorizado;
- fatos relevantes são auditáveis sem expor conteúdo sensível.

## Extensibilidade

Novas Tools implementam `AtomicTool`, publicam descriptor completo e usam somente Resources declarados. Novos validadores, registries e executores entram por portas substituíveis. Compatibilidade exige schemas e semântica estáveis por versão; extensões não podem introduzir chamadas Tool-para-Tool, acesso direto a banco ou desvio de autorização.

## Futuro

Assinatura e attestação de pacotes, execução sandboxed por nível de confiança, schema registry, dry-run e políticas distribuídas poderão especializar o contrato. Essas evoluções devem preservar atomicidade, versão exata, contexto sensível explícito, terminalidade e observabilidade.
