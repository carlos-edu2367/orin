# RFC 101 — Runtime

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](103-event-system.md), [RFC 104 — Pipeline de contexto](104-context-pipeline.md), [RFC 501 — Provider API](../500-providers-models/501-provider-api.md), [RFC 502 — Model Catalog](../500-providers-models/502-model-catalog.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md)

## Objetivo

Definir o Runtime como Kernel de execução do AgentOS. O Runtime governa uma `Execution` por vez, aplica seu ciclo de vida e coordena Context, seleção de modelo, Provider, Tools, Capabilities, checkpoints, cancelamento e eventos exclusivamente por portas públicas.

## Fora de escopo

- endpoints, protocolos de cliente e autenticação de transporte;
- escolha de fila, worker, banco, cache, ORM ou formato de persistência;
- SDK, payload ou política proprietária de Provider;
- implementação de Tool, Capability, Resource, Browser ou Memory;
- algoritmo concreto de seleção de modelo, compactação de Context ou retry de infraestrutura;
- sintaxe, linguagem ou framework de implementação.

## Responsabilidades e não responsabilidades

O Runtime DEVE:

- receber uma `Execution` válida e autorizada por uma porta de entrada;
- adquirir e respeitar controle exclusivo ou lease conceitual antes de executar;
- validar estado, ownership, limites, política e sinal de cancelamento;
- montar e atualizar Context por meio do `ContextManager`;
- resolver um modelo por atributos públicos e invocar um `Provider` abstrato;
- despachar pedidos de Tool ou Capability por uma porta controlada;
- aplicar custo, timeout e limite de iterações antes e depois de cada efeito externo;
- persistir mudanças e checkpoints por portas públicas;
- emitir eventos correlacionados para toda mudança relevante;
- produzir um resultado ou falha explícita e um estado terminal compatível com a RFC 102.

O Runtime NÃO DEVE:

- autenticar HTTP, renderizar interface ou transmitir SSE;
- enfileirar ou supervisionar workers diretamente;
- acessar banco, cache, filesystem, fila ou Artifact Storage diretamente;
- controlar Playwright, Browser Worker ou outro Resource concreto;
- importar SDKs ou tipos proprietários de Provider;
- implementar uma Tool, compor Tools dentro de outra Tool ou persistir Context como Memory;
- decidir autorização com base apenas na posse de um ID;
- converter falha ou cancelamento em sucesso.

## Arquitetura e fronteiras

```text
Worker / Orquestração
        │ ExecutionId + identidade de processamento
        ▼
     Runtime (Kernel)
        ├── ExecutionControl
        ├── ContextManager
        ├── ModelResolver (alias da RFC 502)
        ├── ProviderPort (alias da RFC 501)
        ├── ToolCapabilityPort
        ├── CheckpointPort
        └── Clock / BudgetPolicy
                │
                ▼
      adapters escolhidos na composição
```

As setas representam dependência do Runtime para contratos, nunca para adapters. A composição externa fornece implementações compatíveis; sua tecnologia não altera o loop nem aparece nos tipos públicos.

### Dependências proibidas

O Runtime NÃO DEVE importar nem conhecer FastAPI, React, HTTP, SSE, Playwright, ORM, banco de dados, Redis, fila concreta, filesystem concreto ou SDK de Provider. Também NÃO DEVE consultar tabelas, controlar Browser diretamente ou escolher adapters por nome de tecnologia. Esses mecanismos permanecem nas bordas, workers especializados e adapters que implementam as portas públicas.

## Contratos públicos

O pseudocódigo abaixo é contratual, tipado e não executável.

```text
interface Runtime {
  execute(request: RuntimeRequest) -> RuntimeOutcome

  pre: request.actor está autorizado para o ownership da Execution
  pre: a Execution está em estado elegível conforme RFC 102
  post: toda mudança relevante é persistida e observável por Event
  post: resultado terminal não contradiz o estado persistido
}

RuntimeRequest {
  execution_id: ExecutionId
  actor: ActorRef
  worker_ref: WorkerRef
  correlation_id: CorrelationId
  resume_from: CheckpointId | null
}

RuntimeOutcome =
  | CompletedOutcome { execution_id: ExecutionId, result: ResultReference, usage: Usage }
  | WaitingOutcome { execution_id: ExecutionId, state: WAITING_USER | PAUSED }
  | FailedOutcome { execution_id: ExecutionId, error: RuntimeError }
  | CancelledOutcome { execution_id: ExecutionId, reason: CancellationReason }
```

```text
alias ExecutionControl = RFC102.ExecutionControl
```

`ExecutionControl` é a única fachada de mutação de `Execution` consumida pelo Runtime. Sua assinatura e semântica canônicas pertencem à RFC 102. A fachada valida a máquina de estados e confirma cada mudança, inclusive resultado, uso ou checkpoint associado, por uma única chamada a `TransactionalPersistence.transact` da RFC 601, com o Event correspondente na outbox da mesma transação. O Runtime NÃO DEVE chamar `TransactionalPersistence`, `EventBus` ou um publicador de outbox diretamente, nem separar “salvar estado” de “registrar Event”.

```text
interface ContextManager {
  assemble(request: ContextAssemblyRequest) -> ContextSnapshot
  apply_turn(request: ContextTurnUpdate) -> ContextSnapshot
  finalize(execution_id: ExecutionId, disposition: ContextDisposition) -> Unit
}

alias ModelResolver = RFC502.ModelResolver
alias ProviderPort = RFC501.ProviderPort
```

Esses aliases são fachadas canônicas, não versões reduzidas nem contratos paralelos. `ModelResolver` incorpora exatamente `resolve(ResolveModel) -> ModelResolutionOutcome` e `resolve_fallback(ResolveFallback) -> ModelResolutionOutcome` da RFC 502. `ProviderPort` incorpora exatamente `generate(ProviderRequest) -> ProviderOutcome`, streaming, cancelamento, espera de terminal e inspeção da RFC 501. Tipos como `ResolveModel`, `ModelResolutionOutcome`, `ProviderRequest` e `ProviderOutcome` são definidos somente nessas RFCs; qualquer evolução de assinatura ocorre em sua fonte canônica e é automaticamente exigida aqui.

```text
interface ToolCapabilityPort {
  invoke(request: ActionInvocation) -> ActionOutcome

  pre: ação está registrada, autorizada e permitida para Agent, user e workspace
  post: Tool permanece atômica; Capability pode coordenar Tools por seu contrato próprio
}

ActionRequest =
  | ToolRequest { tool_ref: ToolRef, arguments: SanitizedArguments }
  | CapabilityRequest { capability_ref: CapabilityRef, input: SanitizedInput }

ActionOutcome =
  | ActionSucceeded { result: ResultReference, usage: ResourceUsage }
  | ActionFailed { error: ActionError, retryability: Retryability }
  | ActionCancelled { reason: CancellationReason }
```

```text
interface CheckpointPort {
  load(checkpoint_id: CheckpointId, ownership: OwnershipScope) -> CheckpointSnapshot
  latest_safe(execution_id: ExecutionId) -> CheckpointRef | null
}

CheckpointSnapshot {
  checkpoint_id: CheckpointId
  execution_id: ExecutionId
  state_version: Version
  iteration: NonNegativeInteger
  context_manifest_ref: ContextManifestRef
  accumulated_usage: Usage
  pending_action: PendingAction | null
  created_at: Instant
}
```

Um checkpoint NÃO DEVE conter segredo, handle vivo de Resource, objeto de SDK ou estado interno de adapter. Referências só podem ser resolvidas após nova validação de ownership e autorização.

## Loop normativo de execução

1. **Receber e adquirir.** O Runtime recebe `RuntimeRequest`, carrega a `Execution` pela porta, valida ownership, elegibilidade, versão e controle exclusivo. Uma aquisição duplicada não inicia um segundo loop.
2. **Iniciar ou retomar.** O Runtime restaura apenas checkpoint seguro e compatível quando aplicável e solicita à `ExecutionControl` a transição `STARTING` para `RUNNING` junto do fato correspondente; `ExecutionStarted` ocorre somente na primeira entrada útil em `RUNNING`, sem apagar retomadas ou recuperações anteriores.
3. **Montar Context.** O `ContextManager` seleciona task, instruções, mensagens, resumos, referências, decisões, eventos, memórias autorizadas e resultados necessários dentro do orçamento da RFC 104.
4. **Selecionar modelo.** O `ModelResolver` recebe requisitos de capacidade, política, orçamento e Agent; o Runtime não escolhe adapter por condicionais de fornecedor.
5. **Chamar Provider.** O Runtime envia somente o Context autorizado e registra uso retornado em tipos públicos. Antes e depois da chamada verifica timeout, custo, iterações e controle.
6. **Tratar resposta.** Uma resposta final atualiza Context e pode concluir a `Execution`. Pedidos de ação são validados e encaminhados à porta de Tool/Capability.
7. **Executar ação.** Antes da ação, o Runtime solicita a mudança para `WAITING_TOOL` e o Event aplicável na mesma fronteira de commit. Após o resultado, solicita uso, referência, Event e retorno a `RUNNING` como mudanças consistentes; nenhum Event é publicado diretamente pelo Runtime.
8. **Checkpoint.** O Runtime prepara checkpoint em limites seguros definidos por política, inclusive após efeitos externos confirmados e antes de nova iteração quando necessário. A confirmação do checkpoint e `CheckpointCreated` entram juntos pela `ExecutionControl`; a `CheckpointPort` é usada somente para leitura e recuperação.
9. **Repetir ou aguardar.** O loop continua enquanto houver trabalho, orçamento e autorização. Necessidade de informação externa transiciona para `WAITING_USER`; pausa solicitada transiciona para `PAUSED` em limite seguro.
10. **Finalizar.** Resultado válido é registrado antes de `COMPLETED` e `ExecutionFinished`. Falha termina em `FAILED`; cancelamento reconhecido termina em `CANCELLED` e `ExecutionCancelled`. O Context temporário é finalizado conforme política, sem virar Memory implicitamente.

## Limites, custo e iterações

Cada `Execution` DEVE possuir limites resolvidos antes do loop:

```text
ExecutionLimits {
  maximum_iterations: PositiveInteger
  execution_timeout: Duration
  provider_timeout: Duration
  action_timeout: Duration
  maximum_cost: CostAmount | null
  maximum_provider_tokens: NonNegativeInteger | null
}
```

- a iteração é incrementada uma vez por invocação de Provider aceita;
- uso e custo são acumulados a partir de medições públicas, nunca estimados a partir de payload proprietário quando houver medição confirmada;
- o Runtime DEVE verificar limites antes de iniciar e após concluir Provider ou ação;
- atingir limite impede novo efeito externo e produz falha explícita classificada, salvo se já existir resultado concluído e persistido;
- timeout não é cancelamento voluntário: ele produz falha de timeout, podendo solicitar cancelamento de operações subordinadas;
- retries internos permitidos por política contam em uso, tempo e observabilidade e não podem ultrapassar o limite da `Execution`.

## Checkpoints e recuperação

Checkpoints representam limites seguros de retomada, não snapshots arbitrários de processo. Um checkpoint DEVE registrar versão do estado, iteração, uso acumulado, manifesto do Context e estado de ação suficiente para impedir repetição cega de efeito externo.

Após falha de worker, a orquestração pode redispatchar a mesma `Execution`. O novo Runtime:

1. adquire controle por versão ou lease expirado;
2. carrega o último checkpoint seguro e valida ownership e compatibilidade;
3. reconcilia ação pendente por chave idempotente ou estado observável;
4. retoma sem repetir efeito confirmado;
5. falha explicitamente se não puder provar uma retomada segura.

Uma `Execution` em estado terminal nunca é reaberta. Nova tentativa cria outra `Execution`, preserva `correlation_id` e registra a relação causal.

## Fluxo de falha

Falhas são traduzidas na porta em que surgem e classificadas ao menos por categoria, retryability e segurança de retomada. O Runtime DEVE:

- preservar `correlation_id`, `execution_id` e causa sem publicar segredo;
- contabilizar uso já consumido;
- distinguir rejeição de política, indisponibilidade, timeout, resultado inválido e falha interna;
- aplicar retry somente quando a política permitir e a operação for idempotente ou reconciliável;
- criar checkpoint de falha apenas se representar estado seguro;
- transicionar para `FAILED` quando não houver continuação segura;
- confirmar `FAILED` e a entrada de outbox de `ExecutionFailed` na mesma transação pela `ExecutionControl`.

Falha de entrega posterior não desfaz o fato: o Event já está durável na outbox do mesmo commit e será republicado segundo as RFCs 103 e 601. Não existe uma segunda operação de `publish` dentro do Runtime.

## Fluxo de cancelamento e pausa

Cancelamento é cooperativo e idempotente. O Runtime verifica `ExecutionControl`:

- antes de Provider, Tool ou Capability;
- durante operações longas quando a porta suportar sinal cooperativo;
- imediatamente após cada retorno externo;
- antes de persistir novo efeito ou iniciar nova iteração.

Ao receber `CANCEL_REQUESTED`, o Runtime não inicia novos efeitos, propaga o sinal às operações subordinadas, espera apenas até um limite seguro, reconcilia resultados já confirmados e prepara checkpoint se útil. A `ExecutionControl` confirma `CANCELLED`, checkpoint e entrada de outbox de `ExecutionCancelled` em uma unidade atômica. Se uma operação não puder ser interrompida, seu resultado tardio deve ser reconciliado e não pode mudar o terminal `CANCELLED` para sucesso.

Pausa segue limites seguros equivalentes, mas preserva possibilidade de retomada em `PAUSED`. Pausa não substitui cancelamento nem timeout.

## Eventos

O Runtime produz ou coordena, conforme o fato, pelo menos:

| Event | Momento do fato |
| --- | --- |
| `ExecutionStarted` | primeira entrada útil em `RUNNING` confirmada |
| `ToolStarted` | invocação autorizada de Tool aceita |
| `ToolFinished` | resultado da Tool confirmado, inclusive com status explícito |
| `CheckpointCreated` | checkpoint seguro confirmado |
| `ExecutionFinished` | resultado e estado `COMPLETED` confirmados |
| `ExecutionFailed` | estado `FAILED` confirmado |
| `ExecutionCancelled` | estado `CANCELLED` confirmado |

Eventos emitidos por Browser, Memory, Agent ou Capability pertencem ao módulo que confirma o fato e seguem a RFC 103. O Runtime não fabrica `BrowserOpened`, `MemorySaved` ou `AgentCreated` sem confirmação da porta responsável.

## Segurança

- toda entrada e referência DEVE ser revalidada no escopo de `user_id`, `workspace_id` e `agent_id` aplicável;
- permissões de Tool, Capability, Resource, modelo, Memory e Artifact são avaliadas antes do uso;
- Context e eventos carregam somente conteúdo necessário e autorizado;
- credenciais permanecem nos adapters e nunca entram em Context, checkpoint, log ou Event;
- conteúdo de Provider e Tool é dado não confiável: solicitações de ação passam por validação estrutural, política e autorização;
- referências não concedem acesso por si próprias;
- uma retomada não herda autoridade expirada sem revalidação.

## Observabilidade

Logs, métricas e traces DEVEM permitir reconstruir estado, iteração, duração, uso, custo, chamadas abstratas e transições por IDs. Métricas recomendadas incluem duração por estado, iterações, custo acumulado, retries, checkpoints, cancelamentos, timeouts e falhas por categoria. Conteúdo sensível, prompts completos, argumentos secretos e resultados privados NÃO DEVEM ser usados como labels ou chaves de correlação.

## Extensibilidade

Novos Providers, estratégias de modelo, gerenciadores de Context, stores, Tools, Capabilities e políticas de budget PODEM ser adicionados por adapters ou registros compatíveis. Extensões DEVEM declarar capabilities, permissões, limites, eventos, tipos públicos e comportamento de cancelamento. O Runtime NÃO DEVE ganhar `switch/case` por fornecedor ou tecnologia.

## Invariantes

- toda execução de trabalho ocorre dentro de exatamente uma `Execution` principal;
- somente um proprietário de processamento válido governa uma versão ativa da `Execution`;
- estados e transições seguem a RFC 102;
- o Runtime depende apenas de portas públicas;
- Context é temporário e Memory só é alterada por decisão explícita e observável;
- nenhum efeito externo é repetido após recuperação sem idempotência ou reconciliação;
- custo, tempo e iterações nunca diminuem;
- terminal confirmado não é reaberto;
- falha, timeout e cancelamento permanecem distinguíveis;
- todo fato relevante é observável e correlacionável conforme a RFC 103.

## Futuro

Políticas de execução distribuída, modelos concorrentes, delegação multi-agent, speculative execution e migração entre workers PODEM especializar as portas. Qualquer evolução DEVE preservar ownership, causalidade, limite de custo, terminalidade e independência de adapters.
