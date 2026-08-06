# RFC 501 — Provider API

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 401 — Tool Runtime](../400-tools-resources/401-tool-runtime.md), [RFC 502 — Model Catalog](502-model-catalog.md)

## Objetivo

Definir uma porta uniforme e independente de fornecedor para geração de modelos, streaming, entradas multimodais de visão, solicitações de Tool, cancelamento, limites, contabilização de uso e custo, erros normalizados e telemetria. Adapters iniciais de OpenAI, Anthropic e OpenRouter implementam esta porta; seus SDKs, payloads, exceções, objetos de stream, nomes técnicos e semânticas proprietárias nunca atravessam a fronteira do adapter.

## Fora de escopo

- selecionar modelo, perfil ou fallback, responsabilidade da [RFC 502](502-model-catalog.md);
- montar, priorizar ou persistir Context, responsabilidade da [RFC 104](../100-kernel/104-context-pipeline.md);
- executar Tool ou aceitar seus argumentos, responsabilidade do Runtime e do [Tool Runtime](../400-tools-resources/401-tool-runtime.md);
- definir ciclo de vida ou estado terminal de Execution;
- escolher SDK, protocolo HTTP, transporte, mecanismo de credenciais ou estratégia concreta de retry;
- definir endpoint, schema ORM, tabela, fila, configuração executável ou código de backend;
- armazenar prompts, respostas completas ou conteúdo de imagem como telemetria por padrão.

## Responsabilidades e não responsabilidades

A Provider API DEVE:

- receber somente tipos públicos normalizados e uma seleção opaca produzida pelo Model Catalog;
- suportar geração não streaming e streaming com equivalência semântica;
- representar texto, visão e resultados de Tool em partes de conteúdo normalizadas;
- retornar pedidos de Tool como dados não confiáveis, nunca executá-los;
- aceitar cancelamento cooperativo e deadlines explícitos;
- aplicar limites da invocação antes de enviar e durante a resposta quando possível;
- normalizar finish reasons, uso, custo, rate limits, erros e retryability;
- emitir telemetria correlacionada sem conteúdo sensível;
- manter credenciais, IDs externos e objetos de SDK dentro do adapter;
- permitir substituição de adapter sem alterar Runtime, Agent, Context ou Tool Runtime.

A Provider API NÃO DEVE:

- escolher perfil, modelo alternativo ou fallback por conta própria;
- alterar Context, Memory, Execution ou política de autorização;
- transformar texto do modelo em comando autorizado;
- chamar Tool, Capability ou Resource;
- ocultar retry, truncamento, custo ou degradação de capacidade;
- converter timeout, cancelamento, resposta inválida ou recusa em sucesso;
- expor objeto, exceção, enum, callback, header ou payload proprietário fora do adapter.

## Arquitetura e fronteiras

```text
Runtime
  │ ModelSelection + ProviderRequest + CancellationSignal
  ▼
ProviderPort
  ├── ProviderRouter
  ├── contratos públicos de geração e stream
  ├── normalização de erro, uso, custo e telemetria
  └── adapter selecionado por ProviderRef opaca
          ├── OpenAIAdapter ─────> SDK/API OpenAI
          ├── AnthropicAdapter ──> SDK/API Anthropic
          └── OpenRouterAdapter ─> SDK/API OpenRouter
```

O Runtime depende de `ProviderPort`; o adapter depende do fornecedor. `ProviderRouter` resolve uma `ProviderRef` para um adapter registrado sem distribuir condicionais por fornecedor no Runtime. A `ModelSelection` da RFC 502 contém referências públicas e opacas suficientes para rotear, mas não contém objetos de SDK nem obriga o Runtime a conhecer o nome técnico aceito pelo fornecedor.

O adapter é o único responsável por traduzir mensagens, imagens, declarações de Tool, chunks, finish reasons, usage, preços confirmados, headers operacionais e falhas concretas. Qualquer extensão proprietária não representável pelo contrato público é recusada ou fica encapsulada em metadata sanitizada explicitamente permitida; nunca é repassada como objeto arbitrário.

## Entidades e dados

### Contexto de operação sensível

Toda geração, abertura ou leitura de stream, cancelamento, consulta de resultado e inspeção operacional DEVE carregar os seis escopos exigidos, ainda que algum valor seja representado explicitamente como nulo conforme as regras de Workspace:

```text
ProviderOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}
```

`workspace_id` só é nulo para uma Execution explicitamente fora de Workspace. Os demais campos são não nulos. O contexto é revalidado na porta; não é inferido de credencial, modelo, stream ou ID externo.

### Invocação e conteúdo

```text
ProviderRequest {
  invocation_id: ProviderInvocationId
  context: ProviderOperationContext
  selection: ModelSelectionRef
  approved_requirements_ref: ApprovedModelRequirementsRef
  approved_requirements: ApprovedModelRequirementsSnapshot
  messages: ProviderMessage[]
  tools: ToolDeclaration[]
  response_format: ResponseFormat
  sampling: SamplingPolicy
  limits: ProviderInvocationLimits
  cancellation: CancellationSignalRef
  idempotency_key: IdempotencyKey | null
}

ProviderMessage {
  role: SYSTEM | USER | ASSISTANT | TOOL
  parts: ContentPart[]
  tool_call_id: ToolCallId | null
}

ContentPart =
  | TextPart { text: Text }
  | ImagePart { image_ref: AuthorizedContentRef, media_type: MediaType, detail: AUTO | LOW | HIGH }
  | ToolResultPart { tool_call_id: ToolCallId, result_ref: ResultReference, status: SUCCEEDED | FAILED | CANCELLED }
  | RefusalPart { category: RefusalCategory, summary: SanitizedText }
```

Referências de imagem e resultado são opacas. Sua resolução exige o mesmo ownership, finalidade e classificação da invocação. O adapter recebe somente o conteúdo mínimo já autorizado para transmissão; não acessa Artifact Storage, filesystem ou Memory por caminho lateral.

```text
ToolDeclaration {
  tool_ref: ToolRef
  name: PublicToolName
  description: Text
  input_schema: TypeSchema
}

SamplingPolicy {
  temperature: Decimal | null
  top_p: Decimal | null
  stop: Text[]
  seed: Integer | null
}
```

`approved_requirements` é o snapshot imutável emitido pela RFC 502. Antes de qualquer transmissão, a porta verifica sua integridade contra `approved_requirements_ref` e a seleção, confere user, Workspace, Agent, Execution e finalidade, e toma a interseção entre seus constraints e os limites da solicitação. Divergência ou expiração exige nova resolução; configuração corrente, metadata do adapter ou pedido do modelo não podem ampliar o snapshot aprovado.

Campos de sampling são opcionais e só são enviados quando declarados compatíveis pela seleção. O adapter não simula suporte silenciosamente. Argumentos de Tool retornados pelo modelo permanecem `UntrustedStructuredValue` até validação do Runtime e do Tool Runtime.

### Limites

```text
ProviderInvocationLimits {
  maximum_input_tokens: PositiveInteger
  maximum_output_tokens: PositiveInteger
  maximum_total_tokens: PositiveInteger
  maximum_cost: CostAmount | null
  timeout: Duration
  first_chunk_timeout: Duration | null
  idle_stream_timeout: Duration | null
  maximum_tool_calls: NonNegativeInteger
  maximum_images: NonNegativeInteger
  maximum_image_bytes: ByteSize | null
}
```

Os limites da invocação nunca ampliam os limites da Execution. O menor limite aplicável prevalece. Estimativa prévia não substitui medição retornada pelo fornecedor; uso e custo confirmados são acumulados mesmo quando a chamada falha, expira ou é cancelada após consumir recursos.

### Resultado, uso e custo

```text
ProviderOutcome =
  | GenerationSucceeded {
      invocation_id: ProviderInvocationId
      message: ModelMessage
      usage: ProviderUsage
      cost: ProviderCost
      finish_reason: FinishReason
    }
  | ToolCallsRequested {
      invocation_id: ProviderInvocationId
      calls: ToolCallRequest[]
      assistant_content: ContentPart[]
      usage: ProviderUsage
      cost: ProviderCost
      finish_reason: TOOL_CALLS
    }
  | GenerationFailed {
      invocation_id: ProviderInvocationId
      error: ProviderError
      usage: ProviderUsage
      cost: ProviderCost
    }
  | GenerationCancelled {
      invocation_id: ProviderInvocationId
      reason: CancellationReason
      usage: ProviderUsage
      cost: ProviderCost
    }

ToolCallRequest {
  tool_call_id: ToolCallId
  tool_name: PublicToolName
  arguments: UntrustedStructuredValue
}

FinishReason = STOP | LENGTH | TOOL_CALLS | CONTENT_FILTER | REFUSAL | ERROR | UNKNOWN
```

```text
ProviderUsage {
  input_tokens: NonNegativeInteger | null
  output_tokens: NonNegativeInteger | null
  total_tokens: NonNegativeInteger | null
  cached_input_tokens: NonNegativeInteger | null
  reasoning_tokens: NonNegativeInteger | null
  measurement: CONFIRMED | ESTIMATED | UNAVAILABLE
}

ProviderCost {
  amount: Decimal | null
  currency: CurrencyCode | null
  measurement: CONFIRMED | COMPUTED_FROM_CATALOG | ESTIMATED | UNAVAILABLE
  pricing_revision: PricingRevisionRef | null
}
```

Campos não informados pelo fornecedor permanecem nulos; o adapter não fabrica precisão. `total_tokens`, quando presente, DEVE ser compatível com os componentes informados. Custo computado usa o snapshot de preço selecionado pela RFC 502 e registra sua revisão.

### Streaming

```text
ProviderStream {
  stream_id: ProviderStreamId
  invocation_id: ProviderInvocationId
  opened_at: Instant
}

ProviderStreamEvent =
  | StreamOpened { stream_id: ProviderStreamId, sequence: PositiveInteger }
  | ContentDelta { stream_id: ProviderStreamId, sequence: PositiveInteger, delta: ContentDeltaValue }
  | ToolCallDelta { stream_id: ProviderStreamId, sequence: PositiveInteger, tool_call_id: ToolCallId, delta: UntrustedStructuredDelta }
  | UsageUpdated { stream_id: ProviderStreamId, sequence: PositiveInteger, usage: ProviderUsage, cost: ProviderCost }
  | StreamCompleted { stream_id: ProviderStreamId, sequence: PositiveInteger, outcome: ProviderOutcome }
  | StreamFailed { stream_id: ProviderStreamId, sequence: PositiveInteger, error: ProviderError, usage: ProviderUsage, cost: ProviderCost }
  | StreamCancelled { stream_id: ProviderStreamId, sequence: PositiveInteger, reason: CancellationReason, usage: ProviderUsage, cost: ProviderCost }
```

A sequência é positiva, única e estritamente crescente por stream. Exatamente um evento terminal — `StreamCompleted`, `StreamFailed` ou `StreamCancelled` — é observado. Deltas não são Events de domínio da RFC 103 e não devem ser persistidos no Event Bus por padrão; são elementos efêmeros da porta. A montagem determinística dos deltas válidos deve resultar no mesmo conteúdo público de uma geração não streaming equivalente, ressalvadas diferenças inerentes do fornecedor declaradas pelo adapter.

```text
ProviderTerminalSnapshot {
  terminal_ref: ProviderTerminalRef
  invocation_id: ProviderInvocationId
  stream_id: ProviderStreamId | null
  state: SUCCEEDED | FAILED | CANCELLED
  stream_terminal: StreamCompleted | StreamFailed | StreamCancelled | null
  outcome: ProviderOutcome
  usage: ProviderUsage
  cost: ProviderCost
  accounting_finality: FINAL_CONFIRMED | FINAL_ESTIMATED | FINAL_UNAVAILABLE
  finalized_at: Instant
  retained_until: Instant
}
```

O terminal é retido por referência até o prazo de inspeção e pode ser observado tanto por `read_stream` quanto por `await_terminal`. Em invocação streaming, `stream_terminal` é obrigatório e corresponde ao mesmo terminal entregue na sequência; em invocação não streaming, é nulo e `outcome` preserva o terminal normalizado. Após cancelamento, `read_stream` continua aceitando leituras a partir da sequência conhecida para entregar `StreamCancelled`; chunks de conteúdo novos podem ser descartados, mas o terminal não. `await_terminal` fornece o caminho garantido para quem não consome stream ou perdeu a leitura: aguarda até o limite de reconciliação e retorna exatamente um snapshot terminal com custo/uso finais ou com indisponibilidade final explicitamente marcada. “Final” significa que a porta não publicará valor silenciosamente diferente; correção posterior exige registro de ajuste auditável relacionado ao terminal.

### Erros normalizados

```text
ProviderError {
  category: ProviderErrorCategory
  code: PublicErrorCode
  message: SanitizedText
  retryability: NEVER | SAFE | POLICY_DEPENDENT
  retry_after: Duration | null
  request_accepted: YES | NO | UNKNOWN
  partial_output_available: Boolean
  provider_ref: ProviderRef
  cause_ref: InternalDiagnosticRef | null
}

ProviderErrorCategory =
  INVALID_REQUEST | AUTHENTICATION | AUTHORIZATION | MODEL_UNAVAILABLE |
  RATE_LIMITED | QUOTA_EXCEEDED | CONTEXT_LIMIT | CONTENT_REJECTED |
  UNSUPPORTED_CAPABILITY | TIMEOUT | CONNECTION | INVALID_RESPONSE |
  CANCELLED | PROVIDER_INTERNAL | POLICY_REJECTED | UNKNOWN
```

`message` é sanitizada e não contém chave, header, prompt, resposta integral ou payload proprietário. `cause_ref` só é resolvível por operadores autorizados. `retryability` descreve segurança técnica mínima; a decisão de retry continua pertencendo ao Runtime e às políticas da Execution.

## Contratos tipados

Esta RFC é a fonte canônica da assinatura de `ProviderPort` e de todos os tipos usados por ela. A menção a `ProviderPort` na RFC 101 é um alias de composição para esta interface completa, não uma porta distinta ou subconjunto compatível por convenção.

```text
interface ProviderPort {
  generate(request: ProviderRequest) -> ProviderOutcome
  open_stream(request: ProviderRequest) -> ProviderStream
  read_stream(request: ReadProviderStream) -> ProviderStreamEvent[]
  cancel(request: CancelProviderInvocation) -> CancelProviderResult
  await_terminal(request: AwaitProviderTerminal) -> ProviderTerminalSnapshot
  inspect(query: AuthorizedProviderInvocationQuery) -> ProviderInvocationSnapshot

  pre: request.context corresponde ao ownership e ao Agent da Execution
  pre: selection está ativa, é compatível e foi emitida pelo Model Catalog
  pre: approved_requirements é íntegro, vigente e pertence à selection e à mesma Execution
  pre: classificação, região, formato, capabilities, cancelamento, limites e policy satisfazem o snapshot aprovado
  post: nenhum tipo proprietário atravessa a porta
  post: uso e custo já consumidos são retornados em sucesso, falha ou cancelamento quando disponíveis
}
```

```text
ReadProviderStream {
  context: ProviderOperationContext
  stream_id: ProviderStreamId
  after_sequence: NonNegativeInteger
  maximum_events: PositiveInteger
  wait_timeout: Duration
}

CancelProviderInvocation {
  request_id: ProviderCancelRequestId
  context: ProviderOperationContext
  invocation_id: ProviderInvocationId
  reason: CancellationReason
  idempotency_key: IdempotencyKey
}

AwaitProviderTerminal {
  context: ProviderOperationContext
  invocation_id: ProviderInvocationId
  terminal_ref: ProviderTerminalRef
  wait_timeout: Duration
}

CancelProviderResult =
  | CancelAccepted { acknowledged_at: Instant, terminal_ref: ProviderTerminalRef }
  | AlreadyCancelled { acknowledged_at: Instant, terminal_ref: ProviderTerminalRef }
  | AlreadyTerminal { terminal: SUCCEEDED | FAILED | CANCELLED, terminal_ref: ProviderTerminalRef }
  | CancelRejected { reason: RejectionReason }

AuthorizedProviderInvocationQuery {
  context: ProviderOperationContext
  invocation_id: ProviderInvocationId
}
```

Cancelar é idempotente para a mesma chave e payload. Reutilizar a chave com payload incompatível é rejeitado. `AlreadyTerminal` não altera o resultado nem o estado da Execution.

```text
interface ProviderAdapter {
  descriptor() -> ProviderAdapterDescriptor
  generate(request: AdapterGenerationRequest) -> AdapterGenerationResult
  open_stream(request: AdapterGenerationRequest) -> AdapterStreamHandle
  cancel(request: AdapterCancelRequest) -> AdapterCancelResult

  invariant: tipos Adapter* permanecem privados ao módulo de Provider
  invariant: credenciais não aparecem em valores públicos, Events, logs ou traces
}

ProviderAdapterDescriptor {
  provider_ref: ProviderRef
  capabilities: ProviderCapability[]
  contract_version: ProviderContractVersion
  status: ACTIVE | DEGRADED | DISABLED | RETIRED
}
```

O pseudocódigo `Adapter*` delimita tipos internos, não cria API pública nem permite que o Runtime os importe.

`RETIRED` é terminal no registro do adapter e corresponde a `ProviderStatus = RETIRED` da RFC 502. Nesse estado, nenhum binding é resolvido, nenhuma credencial ou rota ativa é usada e nenhuma nova invocação é aceita. O registro mantém identidade e dados mínimos necessários para inspeção e reconciliação históricas; `DISABLED` preserva possibilidade de reativação por nova revisão, enquanto `RETIRED` não.

## Uniformidade de geração, visão e Tool calls

`generate`, `open_stream` e `read_stream` são as operações-base de invocação e transporte. Elas unificam visão e Tool calls deliberadamente:

- visão é modalidade de entrada representada por `ImagePart`, sujeita aos mesmos Context, autorização, limites, custo, cancelamento e modelo selecionado que texto;
- Tool call é variante de outcome (`ToolCallsRequested`) ou delta (`ToolCallDelta`), não operação executada pelo Provider;
- geração não streaming e streaming diferem na entrega, não na autoridade ou no conjunto de modalidades;
- portas separadas `vision` ou `tool_call` duplicariam lifecycle e favoreceriam vazamento de endpoints, blocos e objetos proprietários de SDK.

Assim, suporte é resolvido como capability no Model Catalog e revalidado no `ProviderRequest`; ausência de capability falha antes da transmissão. A Provider API continua sem executar Tool nem resolver imagem por acesso lateral.

## Compatibilidade de recursos

A seleção da RFC 502 declara capacidades suportadas. Antes do envio, a Provider API valida:

- integridade, validade e correspondência de `approved_requirements_ref` com a seleção e o snapshot recebido;
- igualdade de ownership, finalidade e Execution entre snapshot e `ProviderOperationContext`;
- classificação, região, formato, Provider/modelo, policy e limites contra o snapshot aprovado;
- total de tokens estimado e reservas de saída contra a janela resolvida;
- presença e quantidade de imagens, formatos e tamanhos permitidos;
- suporte a Tools, quantidade de declarações e schema representável;
- suporte a streaming e response format solicitado;
- parâmetros de sampling aceitos;
- classificação de dados, Provider permitido, finalidade e região/política quando aplicável.

Incompatibilidade falha com `UNSUPPORTED_CAPABILITY`, `CONTEXT_LIMIT` ou `POLICY_REJECTED` antes do efeito. O adapter NÃO DEVE remover imagem, Tool, response format ou instrução para “tentar funcionar”. Degradação só ocorre quando foi declarada na solicitação, aprovada no snapshot e registrada na seleção.

## Adapters iniciais

| Adapter | Responsabilidade mínima | Traduções obrigatórias |
| --- | --- | --- |
| `OpenAIAdapter` | implementar geração, streaming, visão e Tool calls para modelos habilitados no catálogo | mensagens e partes, tool calls, deltas, finish reasons, usage, rate limits, cancelamento e erros para tipos públicos |
| `AnthropicAdapter` | implementar o mesmo contrato uniforme para modelos habilitados | blocos de conteúdo, tool use/results, deltas, stop reasons, usage, limites, cancelamento e erros para tipos públicos |
| `OpenRouterAdapter` | implementar o contrato sobre o roteamento oferecido pelo serviço sem torná-lo fallback implícito do AgentOS | identificação opaca de rota/modelo, respostas, deltas, usage/custo disponível, rate limits, cancelamento e erros para tipos públicos |

Suporte efetivo é declarado por modelo no catálogo, não presumido pelo nome do adapter. Recursos extras de um fornecedor só ficam disponíveis após ganharem representação pública estável e compatibilidade explícita. OpenRouter não pode trocar silenciosamente de rota quando a política exigir Provider ou modelo fixo; qualquer roteamento permitido deve constar da seleção e da telemetria pública sanitizada.

## Eventos

Eventos de domínio seguem o envelope da [RFC 103](../100-kernel/103-event-system.md) e contêm o contexto sensível completo por seus campos de ownership/correlação e payload mínimo:

| Event | Fato confirmado | Campos específicos mínimos |
| --- | --- | --- |
| `ProviderInvocationStarted` | invocação foi aceita pelo adapter | `invocation_id`, `provider_ref`, `model_selection_ref`, `streaming` |
| `ProviderInvocationFinished` | invocação terminou com outcome bem-sucedido explícito | `invocation_id`, `finish_reason`, `usage`, `cost` |
| `ProviderInvocationFailed` | invocação terminou em falha normalizada | `invocation_id`, `error_category`, `retryability`, `usage`, `cost` |
| `ProviderInvocationCancelled` | cancelamento foi estabilizado | `invocation_id`, `reason_code`, `usage`, `cost` |
| `ProviderRateLimitObserved` | limite público relevante foi observado | `provider_ref`, `limit_kind`, `remaining`, `reset_at` |

Chunks não são publicados individualmente no Event Bus por padrão. Eventos não contêm prompt, imagem, delta, resposta integral, argumentos de Tool, credencial, ID externo sensível ou objeto de SDK. Falha ao entregar Event não muda o fato confirmado e segue a publicação transacional conceitual da RFC 103.

## Fluxo normal

1. O Runtime recebe `ModelSelectionRef`, `ApprovedModelRequirementsRef` e snapshot imutável da RFC 502 e monta `ProviderRequest` com contexto sensível, conteúdo autorizado, limites e sinal de cancelamento.
2. A Provider API revalida integridade e validade do snapshot, ownership, finalidade, classificação, região, formato, capabilities, cancelamento, policy, budget, seleção e registro do adapter.
3. O router escolhe o adapter pela referência opaca e registra `ProviderInvocationStarted` somente após aceitação confirmada.
4. O adapter traduz tipos públicos para seu SDK/API, injeta credencial fora do payload público e realiza a chamada.
5. Em geração comum, o adapter normaliza mensagem ou Tool calls, finish reason, uso, custo e limites observados.
6. Em streaming, o adapter emite sequência pública, valida deltas e termina exatamente uma vez.
7. A porta devolve outcome ao Runtime. Tool calls passam por validação e autorização antes de qualquer Tool; texto e referências atualizam Context pela RFC 104.
8. Uso e custo são registrados mesmo se o Runtime decidir continuar outra iteração.

## Fluxo de falha

- erro anterior ao envio retorna `request_accepted = NO` e não afirma consumo inexistente;
- aceitação incerta retorna `UNKNOWN`, preserva qualquer uso conhecido e exige reconciliação antes de retry quando duplicação puder importar;
- rate limit inclui `retry_after` somente quando confiável e não autoriza retry além do deadline ou budget;
- resposta malformada, sequência inválida, Tool call ambígua ou usage contraditório produz erro público explícito;
- output parcial nunca é promovido a resultado final sem política explícita do Runtime;
- falha de um adapter não seleciona outro modelo ou Provider; fallback é uma nova resolução/invocação governada pela RFC 502 e pelo Runtime;
- retry interno do adapter, se permitido, é limitado, observável e contabilizado; não oculta tentativas ou custo;
- nenhum erro proprietário atravessa a porta.

## Fluxo de cancelamento

1. O Runtime observa `CANCEL_REQUESTED` ou timeout e chama `cancel` com o mesmo contexto sensível.
2. A porta impede novos efeitos e novos chunks de conteúdo para produção de resultado, mas mantém `read_stream` disponível para observar o terminal e propaga o sinal conforme `InvocationCancellationCapability.mode`.
3. Em `COOPERATIVE_REMOTE`, o adapter solicita interrupção ao fornecedor; em `LOCAL_ONLY`, encerra a entrega local sem afirmar interrupção remota; `UNSUPPORTED` só pode ter sido selecionado quando o snapshot aceitou explicitamente essa limitação.
4. Chunks já recebidos podem ser drenados apenas para contabilização e reconciliação; não convertem cancelamento em sucesso.
5. O adapter reconcilia uso e custo até `maximum_reconciliation_time` e grava um `ProviderTerminalSnapshot` com finality confirmada, estimada ou indisponível.
6. A stream termina em `StreamCancelled`, ou preserva terminal anterior quando a corrida já havia sido confirmada. O terminal fica disponível por `read_stream` e `await_terminal` usando `terminal_ref` retornada por `cancel`.
7. `await_terminal` entrega o terminal e o custo/uso finais; ausência definitiva é expressa por `FINAL_UNAVAILABLE`, nunca por zero.
8. Resultado tardio permanece auditável e não reabre Execution `CANCELLED` nem substitui terminal confirmado; eventual ajuste contábil é fato relacionado e explícito.

## Segurança

- toda operação sensível contém `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- authorization e classificação são verificadas antes de resolver conteúdo e antes de transmiti-lo;
- credenciais pertencem ao adapter e nunca entram em Context, request público, checkpoint, Event, log ou resultado;
- Provider, modelo e região devem ser permitidos para classificação e finalidade da Execution;
- imagens e resultados de Tool são reautorizados por referência e minimizados antes do envio;
- conteúdo do modelo e argumentos de Tool são dados não confiáveis e não concedem autoridade;
- erros e telemetria aplicam redaction a headers, URLs assinadas, IDs externos e conteúdo;
- caches do fornecedor só podem ser usados quando política, ownership e classificação permitem;
- conhecer `invocation_id`, `stream_id` ou `selection_ref` não concede acesso.

## Observabilidade e telemetria

Cada invocação produz trace correlacionado por `correlation_id`, `execution_id`, `agent_id`, `invocation_id`, `provider_ref` e referência de seleção. Métricas incluem latência total e até primeiro chunk, duração do stream, tokens, custo, throughput, finish reason, retries, rate limits, cancelamentos, timeouts, falhas por categoria, resposta inválida e capacidade usada.

Labels não contêm prompt, resposta, argumentos, imagem, credencial, nome de usuário ou ID externo de alta cardinalidade. Logs registram somente IDs internos, versões, categorias, limites e contagens sanitizadas. Telemetria de saúde usada pelo Model Catalog é agregada e pública ao domínio (`AVAILABLE`, `DEGRADED`, `UNAVAILABLE`, instante e validade); ela não expõe payload ou exceção do SDK.

Medições distinguem valores confirmados, calculados, estimados e indisponíveis. A ausência de usage/custo é observável e nunca é registrada como zero confirmado.

## Invariantes

- Runtime, Agent, Context e Tool Runtime nunca importam SDK nem tipo proprietário de Provider;
- toda invocação pertence a uma Execution e carrega os seis escopos sensíveis;
- toda invocação recebe e revalida o snapshot imutável dos requirements aprovados antes da transmissão;
- Provider não seleciona modelo ou fallback por decisão local;
- Tool call de modelo é dado não confiável e nunca é executado pelo adapter;
- geração comum e streaming usam os mesmos tipos públicos de conteúdo, uso, custo e erro;
- stream possui sequência monotônica e exatamente um terminal público;
- cancelamento por invocação é capability distinta de streaming, e seu terminal/custo final são observáveis por referência;
- limites da invocação não ampliam limites da Execution;
- uso e custo consumidos nunca diminuem nem desaparecem por falha ou cancelamento;
- retry e degradação nunca são silenciosos;
- erro normalizado preserva categoria e retryability sem vazar segredo;
- cancelamento impede novos efeitos e não converte resultado tardio em sucesso;
- todo adapter é substituível pela mesma porta.
- adapter `RETIRED` não resolve binding nem aceita nova invocação e preserva somente inspeção histórica.

## Extensibilidade

Novo Provider implementa `ProviderAdapter`, registra descriptor e capabilities e passa testes contratuais de geração, streaming, cancelamento, erro, uso, custo, segurança e ausência de vazamento. Nova modalidade ou recurso exige primeiro um tipo público normalizado e metadata correspondente na RFC 502; um campo proprietário arbitrário não é ponto de extensão.

Adapters podem especializar batching, cache, conexão ou otimização internamente, desde que preservem isolamento, limites, observabilidade e outcomes. O registro escolhe adapters por referência e versão, sem `switch/case` no Runtime.

## Futuro

Áudio, vídeo, embeddings, geração de imagem, respostas estruturadas mais ricas, batch assíncrono e speculative decoding poderão ganhar portas ou variantes específicas. A evolução deve preservar seleção explícita, tipos públicos, cancelamento, contabilização e isolamento; capacidades com semântica incompatível não serão comprimidas artificialmente na operação `generate`.
