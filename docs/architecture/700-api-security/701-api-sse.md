# RFC 701 — API e SSE

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md), [RFC 602 — Artifact Storage](../600-platform-data/602-artifact-storage.md), [RFC 603 — Workspaces](../600-platform-data/603-workspaces.md), [RFC 702 — Segurança](702-security.md)

## Objetivo

Definir a borda HTTP/SSE do AgentOS como um Gateway adapter sem regra de negócio, com contratos para criar e controlar `Execution`s de forma idempotente, consultar estado e acompanhar eventos autorizados com cursor, reconexão, projeção segura e erros estáveis.

## Fora de escopo

- caminhos, verbos, status HTTP, headers concretos, OpenAPI ou framework web;
- componentes de frontend, renderização, WebSocket ou protocolo binário;
- máquina de estados, despacho, scheduling, execução de Agent ou loop de Runtime;
- schema de banco, fila, broker, outbox, Redis ou cache concreto;
- autenticação, emissão de credenciais e criptografia, definidos pela [RFC 702](702-security.md);
- entrega exactly-once ou retenção ilimitada de eventos para clientes.

## Responsabilidades e não responsabilidades

O Gateway DEVE:

- autenticar a credencial por porta de segurança e construir contexto de ator confiável;
- validar forma, tamanho, versão do contrato e campos obrigatórios antes da tradução;
- autorizar cada comando, consulta e abertura ou retomada de stream no recurso concreto;
- traduzir contratos de transporte para portas públicas de aplicação;
- preservar `idempotency_key`, versão esperada, ownership, correlação e finalidade;
- projetar somente campos autorizados e classificar erros sem vazar detalhes internos;
- impor limites de request, conexão e abuso antes de consumir recursos desproporcionais.

O Gateway NÃO DEVE:

- conter transições de domínio, decidir próximo passo, montar Context ou escolher Provider;
- escrever estado diretamente no banco, outbox ou broker;
- ler eventos diretamente de infraestrutura para contornar autorização;
- considerar posse de um ID como autorização;
- executar Agent, Tool, Capability, Browser ou qualquer efeito de domínio;
- manter Worker ocupado ou iniciar Runtime dentro do processo de API;
- serializar segredo, credencial, cookie, token, prompt ou conteúdo não autorizado.

## Arquitetura e fronteiras

```text
Cliente
  │ comando, consulta ou assinatura
  ▼
Gateway adapter
  ├── Authentication / CSRF / AbuseProtection
  ├── validação e tradução de transporte
  ├── AuthorizationService
  └── projeção e mapeamento de erro
          │ portas de aplicação
          ├──> ExecutionApplication ──> Kernel / Persistence / despacho
          ├──> ExecutionQuery ────────> projeção autorizada
          └──> ClientEventStream ─────> Event archive / live feed
```

O Gateway termina o protocolo e depende somente de portas públicas. `ExecutionApplication` cria intenção durável e solicita comandos ao contrato da [RFC 102](../100-kernel/102-execution-lifecycle.md); Workers executam o trabalho fora da API. `ClientEventStream` recebe projeções autorizadas da camada de aplicação, nunca acesso irrestrito ao `EventBus` ou à outbox.

## Contexto e dados

Toda operação sensível usa contexto completo:

```text
ApiOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  credential_ref: CredentialRef
  authorization_basis_ref: AuthorizationBasisRef
}

ApiContractVersion {
  major: PositiveInteger
  minor: NonNegativeInteger
}
```

Para criação, `execution_id` é alocado pelo servidor antes da autorização final e da persistência; a mesma `idempotency_key` no mesmo ownership resolve para a mesma identidade. `workspace_id` só pode ser nulo para trabalho explicitamente fora de Workspace. IDs vindos do cliente são tratados como opacos e não sobrescrevem ownership resolvido pela credencial e pelos stores autorizados.

Streams que observam mais de uma `Execution` não reutilizam implicitamente o `execution_id` singular. Eles usam um contexto próprio que preserva a operação iniciadora e modela o conjunto completo de recursos:

```text
StreamOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  credential_ref: CredentialRef
  resource_selection: ExplicitStreamResourceSelection
}

ExplicitStreamResourceSelection {
  selection: EXPLICIT_EXECUTIONS
  execution_ids: NonEmptyList<ExecutionId>
}

AuthorizedStreamScope {
  resources: NonEmptyList<AuthorizedStreamResource>
  resource_set_digest: ResourceSetDigest
  resource_count: PositiveInteger
  filter_digest: FilterDigest
  authorization_version: StreamAuthorizationVersion
  authorized_at: Instant
  reauthorization_due_at: Instant
}

AuthorizedStreamResource {
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  authorization_basis_ref: AuthorizationBasisRef
  resource_authorization_version: Version
}

StreamBinding {
  stream_id: StreamId
  initiating_context_digest: OperationContextDigest
  resource_selection: ExplicitStreamResourceSelection
  client_filter: ClientEventFilter
  authorized_scope: AuthorizedStreamScope
  stream_binding_digest: StreamBindingDigest
  created_at: Instant
}

StreamContinuationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  credential_ref: CredentialRef
}
```

`StreamOperationContext.execution_id` identifica a `Execution` administrativa ou de produto que iniciou e audita a assinatura; não representa todas as Executions observadas. `resource_selection.execution_ids` é a única fonte canônica do conjunto solicitado. O servidor normaliza essa lista, resolve ownership de cada item, rejeita duplicatas e valida individualmente ação, classificação, `user_id`, `workspace_id`, `agent_id` e purpose. O conjunto normalizado, seu digest, sua cardinalidade e as bases de autorização integram `AuthorizedStreamScope` e a auditoria. Falha de qualquer item rejeita a abertura inteira com erro não enumerável; não existe sucesso parcial silencioso.

Após a abertura, o servidor persiste `StreamBinding` em storage efêmero autorizado e vincula de forma imutável `stream_id`, contexto iniciador, seleção canônica, filtro, escopo autorizado e digests. `stream_id` não concede acesso: somente localiza o binding que deve ser revalidado contra a credencial e o `StreamContinuationContext`. Alterar seleção ou filtro exige fechar e abrir outro stream com novo `stream_id`; reautorização do mesmo stream só atualiza versões, prazo, epoch e a expansão autorizada de descendentes da seleção original, sempre com novo registro de auditoria.

```text
ExecutionRepresentation {
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  state: ExecutionState
  state_version: Version
  correlation_id: CorrelationId
  result_ref: AuthorizedArtifactReference | null
  failure: PublicFailure | null
  usage_summary: RedactedUsageSummary
  created_at: Instant
  updated_at: Instant
  finished_at: Instant | null
}
```

A representação é uma projeção, não a entidade interna. `result_ref` exige autorização própria ao ser resolvida conforme a [RFC 602](../600-platform-data/602-artifact-storage.md). Falhas, uso e metadata são minimizados para o clearance do ator.

## Contratos tipados de comandos

```text
interface ExecutionApplication {
  create(command: CreateExecutionCommand) -> ApiCommandReceipt
  control(command: ControlExecutionCommand) -> ApiCommandReceipt
  provide_input(command: ProvideExecutionInputCommand) -> ApiCommandReceipt

  pre: contexto, credencial, CSRF quando aplicável e escopos foram validados
  post: o Gateway não executa a Execution e não confirma fato antes do domínio
}

CreateExecutionCommand {
  operation_id: ApiOperationId
  context: ApiOperationContext
  task: TaskSubmission
  limits: RequestedExecutionLimits
  expected_agent_version: Version
  idempotency_key: IdempotencyKey
  requested_at: Instant
}

ControlExecutionCommand {
  operation_id: ApiOperationId
  context: ApiOperationContext
  action: PAUSE | RESUME | CANCEL
  expected_state_version: Version | null
  reason: ControlReason
  idempotency_key: IdempotencyKey
  requested_at: Instant
}

ProvideExecutionInputCommand {
  operation_id: ApiOperationId
  context: ApiOperationContext
  input: UserInput | AuthorizedArtifactReference
  expected_state_version: Version
  idempotency_key: IdempotencyKey
  requested_at: Instant
}
```

```text
ApiCommandReceipt =
  | CommandAccepted {
      operation_id: ApiOperationId
      execution_id: ExecutionId
      resulting_state_version: Version
      correlation_id: CorrelationId
      accepted_at: Instant
    }
  | CommandAlreadyApplied {
      operation_id: ApiOperationId
      execution_id: ExecutionId
      resulting_state_version: Version
      correlation_id: CorrelationId
    }
  | CommandRejected {
      operation_id: ApiOperationId
      error: PublicApiError
    }
  | CommandConflicted {
      operation_id: ApiOperationId
      current_state_version: Version
      error: PublicApiError
    }
  | CommandIndeterminate {
      operation_id: ApiOperationId
      idempotency_key: IdempotencyKey
      reconciliation_ref: ReconciliationRef
    }
```

A chave idempotente é vinculada ao `user_id`, `workspace_id`, tipo de operação e digest semântico do comando. Repetição compatível retorna o mesmo outcome; reutilização com payload diferente falha. Timeout depois de envio à porta não autoriza nova chave: o cliente reconcilia pela mesma chave ou consulta o estado. `CommandAccepted` significa intenção confirmada, não início nem conclusão da `Execution`.

## Contratos tipados de consulta

```text
interface ExecutionQuery {
  get(query: GetExecutionQuery) -> ExecutionQueryResult
  list(query: ListExecutionsQuery) -> ExecutionPage
}

GetExecutionQuery {
  operation_id: ApiOperationId
  context: ApiOperationContext
  requested_fields: ExecutionFieldSet
  minimum_state_version: Version | null
}

ListExecutionsQuery {
  operation_id: ApiOperationId
  context: ApiOperationContext
  filter: AuthorizedExecutionFilter
  page_cursor: OpaquePageCursor | null
  page_size: PositiveInteger
  sort: UPDATED_DESC | CREATED_DESC
}

ExecutionQueryResult =
  | ExecutionFound { representation: ExecutionRepresentation }
  | ExecutionNotVisible { error: PublicApiError }

ExecutionPage {
  items: ExecutionRepresentation[]
  next_cursor: OpaquePageCursor | null
  snapshot_at: Instant
}
```

Filtros recebidos nunca ampliam o escopo derivado da autorização. Paginação usa cursor opaco vinculado ao ator, filtro, ordenação e validade; adulteração ou uso em outro escopo é rejeitado. Recursos inexistentes e não visíveis PODEM compartilhar a mesma categoria pública para reduzir enumeração.

## Contratos tipados de SSE

```text
interface ClientEventStream {
  open(request: OpenEventStream) -> StreamOpenResult
  read(request: ReadEventBatch) -> ClientEventBatch
  close(command: CloseEventStream) -> StreamCloseReceipt
}

OpenEventStream {
  operation_id: ApiOperationId
  context: StreamOperationContext
  filter: ClientEventFilter
  cursor: StreamCursor | null
  accepted_event_versions: EventVersionRange[]
  heartbeat_preference: Duration | null
}

ClientEventFilter {
  event_types: ClientEventType[]
  include_descendants: Boolean
}

StreamCursor {
  opaque_value: OpaqueCursorValue
}

StreamOpenResult =
  | StreamOpened {
      stream_id: StreamId
      stream_binding_ref: StreamBindingRef
      stream_binding_digest: StreamBindingDigest
      effective_cursor: StreamCursor
      retention_floor: StreamCursor
      heartbeat_interval: Duration
      authorized_scope: AuthorizedStreamScope
      authorization_version: StreamAuthorizationVersion
      authorization_valid_until: Instant
      revocation_epoch: RevocationEpoch
    }
  | StreamResyncRequired {
      reason: CURSOR_EXPIRED | CURSOR_INVALID | FILTER_CHANGED
      state_reconciliation_ref: ReconciliationRef
    }
  | StreamRejected { error: PublicApiError }
```

```text
ReadEventBatch {
  operation_id: ApiOperationId
  context: StreamContinuationContext
  stream_id: StreamId
  stream_binding_ref: StreamBindingRef
  expected_stream_binding_digest: StreamBindingDigest
  after_cursor: StreamCursor
  expected_authorization_version: StreamAuthorizationVersion
  maximum_events: PositiveInteger
  wait_timeout: Duration
}

ClientEventBatch {
  stream_id: StreamId
  events: ClientEventEnvelope[]
  next_cursor: StreamCursor
  authorization_version: StreamAuthorizationVersion
  authorized_at: Instant
  authorization_valid_until: Instant
  heartbeat_due_at: Instant
}

ClientEventEnvelope {
  event_id: EventId
  event_type: ClientEventType
  event_version: PositiveInteger
  occurred_at: Instant
  correlation_id: CorrelationId
  execution_id: ExecutionId | null
  sequence: ExecutionSequence | null
  cursor: StreamCursor
  payload: AuthorizedClientPayload
}

CloseEventStream {
  operation_id: ApiOperationId
  context: StreamContinuationContext
  stream_id: StreamId
  stream_binding_ref: StreamBindingRef
  last_acknowledged_cursor: StreamCursor | null
  reason: CLIENT_DISCONNECTED | AUTHORIZATION_REVOKED | SERVER_SHUTDOWN
}
```

O cursor é opaco, autenticado contra adulteração e vinculado a versão de filtro e escopo; não é autorização nem offset de infraestrutura exposto. Cada evento entregue possui cursor retomável. Reconexão apresenta o último cursor processado; repetição na fronteira é permitida e o cliente deduplica por `event_id`. Para uma mesma `Execution`, `sequence` preserva a ordem lógica da [RFC 103](../100-kernel/103-event-system.md), embora a entrega física possa repetir.

`resource_selection.execution_ids` é obrigatoriamente não vazio e `selection` só aceita `EXPLICIT_EXECUTIONS`. Lista vazia é erro de validação e nunca significa “todas as Executions”, Workspace inteiro ou feed global. `ClientEventFilter` não contém seleção de recurso e, portanto, não pode divergir do contexto. `include_descendants` expande um conjunto limitado a partir da seleção canônica; cada descendente é resolvido, autorizado e incluído no digest antes da entrega. Descendente criado depois exige nova expansão autorizada e nova `authorization_version`.

`ReadEventBatch` e `CloseEventStream` não aceitam filtro nem lista de recursos. O servidor resolve o `StreamBinding` por `stream_id` e `stream_binding_ref`, exige igualdade entre `expected_stream_binding_digest` e o digest persistido e compara todos os campos do `StreamContinuationContext` ao contexto iniciador. Cursor, autorização e batch também são vinculados ao mesmo digest. Qualquer divergência encerra ou rejeita a continuação; nunca executa uma leitura do conjunto B sob autorização ou auditoria do conjunto A.

SSE não promete exatamente uma vez, não recebe comandos e não mantém estado de domínio. Heartbeats não são Events, não avançam cursor e não transportam dados. Backpressure limita batch, bytes, tempo de conexão e buffers; cliente lento pode ser desconectado com cursor seguro para retomada.

## Autorização, reconexão e retenção

- toda abertura reautoriza o filtro; toda reconexão autentica novamente;
- `resource_selection` é a fonte canônica única; o binding imutável preserva seleção e filtro durante toda a vida do `stream_id`;
- cada `execution_id` e cada descendente expandido é autorizado individualmente; o conjunto efetivo e seu digest ficam auditáveis;
- a reautorização completa ocorre antes de `authorization_valid_until` e, em qualquer caso, no máximo 30 segundos após a última decisão bem-sucedida;
- mudança de escopo, versão de policy/credencial/recurso, expansão de descendentes ou material sensível força reautorização antes do próximo write, sem aguardar os 30 segundos;
- a propagação de invalidação de revogação tem limite operacional máximo de 5 segundos para encerrar o stream, mas o fencing de entrega bloqueia imediatamente novos writes após o commit da revogação;
- revogação encerra a conexão inteira sem revelar eventos posteriores; não se reduz silenciosamente o conjunto autorizado;
- cursor não contorna retenção, classificação, redaction ou ownership;
- cursor anterior ao piso de retenção produz `StreamResyncRequired`; o cliente consulta estado atual e abre novo stream;
- lacuna percebida pode resultar de filtro ou autorização e não autoriza consulta irrestrita;
- `include_descendants` só alcança `Execution`s individualmente visíveis; causalidade não transfere acesso;
- eventos de alta classificação são omitidos ou projetados para um tipo público seguro.

`StreamAuthorizationVersion` é um token opaco e monotônico no escopo do stream, derivado das versões de credencial, policy, grants, ownership e de cada recurso do `AuthorizedStreamScope`; não é uma simples cópia de uma policy global. A versão muda sempre que qualquer componente que possa alterar acesso muda, mesmo que o conjunto de IDs permaneça igual. Cliente deve ecoar a versão esperada em cada leitura; divergência suspende entrega e exige reautorização. A versão aparece na abertura e em cada batch para tornar auditável sob qual decisão cada cursor foi admitido.

O gate de entrega mantém um `RevocationEpoch` monotônico e ordena atomicamente admissão de batch e commit de revogação. “Entrega” nesta RFC significa admissão server-side para escrita na conexão, não o instante de chegada pela rede. Antes de admitir cada batch, o Gateway verifica que a versão continua vigente, que `authorization_valid_until` não venceu e que o epoch observado é o atual. O commit de revogação incrementa o epoch e estabelece uma fence: nenhum evento é admitido sob versão anterior depois desse commit. Batch ainda não admitido é descartado; a conexão é encerrada antes do próximo write. Se o Gateway não puder provar freshness do epoch, suspende writes e falha fechado. Bytes admitidos antes do commit podem chegar depois por latência de rede, mas nenhum fato novo é admitido após a revogação.

## Mapeamento de erros

```text
PublicApiError {
  code: PublicErrorCode
  category: VALIDATION | AUTHENTICATION | AUTHORIZATION | NOT_VISIBLE |
            CONFLICT | RATE_LIMITED | RETRYABLE_DEPENDENCY |
            INDETERMINATE | INTERNAL
  message_key: PublicMessageKey
  correlation_id: CorrelationId
  retryable: Boolean
  retry_after: Duration | null
  field_issues: PublicFieldIssue[]
}
```

| Origem interna | Categoria pública | Regra |
| --- | --- | --- |
| contrato malformado ou incompatível | `VALIDATION` | expor somente campos e limites públicos |
| credencial ausente, inválida ou expirada | `AUTHENTICATION` | não revelar identidade resolvida |
| escopo insuficiente | `AUTHORIZATION` ou `NOT_VISIBLE` | evitar enumeração conforme política |
| versão concorrente | `CONFLICT` | retornar versão pública atual quando autorizado |
| idempotency key com payload divergente | `CONFLICT` | não executar nem substituir o resultado anterior |
| quota ou abuso | `RATE_LIMITED` | fornecer espera apenas quando seguro |
| dependência temporária antes de efeito | `RETRYABLE_DEPENDENCY` | retry limitado com a mesma intenção |
| confirmação desconhecida após possível efeito | `INDETERMINATE` | exigir reconciliação, nunca alegar falha definitiva |
| falha interna | `INTERNAL` | mensagem genérica e correlação, sem stack ou segredo |

Erros de Provider, banco, Redis, broker, Worker ou SDK não atravessam a borda em sua forma nativa. Mensagens públicas são estáveis, localizáveis e não incluem queries, paths, ciphertext, tokens ou payloads internos.

## Eventos

O Gateway não publica fatos de domínio por conta própria. Mudanças confirmadas produzem Events pelas portas responsáveis, como `ExecutionQueued`, `ExecutionPaused`, `ExecutionResumed` e `ExecutionCancelled`. A borda PODE registrar eventos operacionais e de segurança no passado:

| Event | Fato observado | Payload mínimo |
| --- | --- | --- |
| `ApiCommandAccepted` | comando foi aceito pela porta de aplicação | operação, tipo, `execution_id`, correlação |
| `ApiCommandRejected` | comando foi rejeitado | operação, categoria sanitizada, correlação |
| `ClientEventStreamOpened` | stream autorizado foi aberto | stream, digest/cardinalidade do conjunto, versão de autorização |
| `ClientEventStreamClosed` | stream foi encerrado | stream, razão categórica, último cursor seguro |
| `ClientEventStreamResyncRequired` | cursor não permitiu continuidade | stream/filtro, razão, correlação |
| `ClientEventStreamReauthorized` | conjunto foi revalidado antes do prazo | stream, versão anterior/nova, digest, instante |
| `ClientEventStreamRevocationFenced` | revogação bloqueou novas entregas | stream, epoch, versão, instante de fence |

Esses Events não contêm credencial, token CSRF, conteúdo de Task, payload completo do Event entregue ou valor do cursor. Métricas de conexão que não sejam fatos duráveis podem permanecer apenas em telemetria.

## Fluxo normal

1. O Gateway autentica a credencial, valida CSRF quando aplicável e resolve o ator.
2. Fixa `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose` sem confiar em ownership fornecido pelo cliente.
3. Autoriza a ação e aplica limites de custo e abuso.
4. Valida o contrato e chama a porta de aplicação com idempotência e versão esperada.
5. A porta confirma intenção e Event de domínio; o Gateway devolve recibo sanitizado.
6. Para leitura, a projeção autorizada retorna estado atual, não reconstrução improvisada no Gateway.
7. Para SSE, o stream resolve e autoriza individualmente a seleção canônica não vazia, persiste seu binding imutável com filtro e digests, fixa versão e epoch, e avança cursor somente após admissão do batch pelo gate de entrega.
8. Cada leitura resolve o binding por `stream_id`, compara contexto e digest e não recebe uma nova seleção ou filtro.
9. Antes de cada write, o gate verifica versão, prazo e revocation epoch; no máximo a cada 30 segundos executa reautorização completa.

## Fluxo de falha

- falha de autenticação, CSRF ou autorização termina antes de acessar o recurso;
- entrada inválida não alcança o domínio;
- conflito de versão não é convertido em retry automático destrutivo;
- timeout com efeito possível retorna outcome indeterminado e referência de reconciliação;
- cursor inválido, expirado ou de outro escopo não faz fallback para início irrestrito;
- filtro vazio, recurso duplicado ou qualquer Execution não autorizada rejeita toda a abertura;
- `stream_id`, binding, contexto ou digest divergente rejeita a leitura sem substituir seleção ou filtro persistidos;
- indisponibilidade do feed não muda estado da `Execution`; reconexão usa cursor e backoff;
- indisponibilidade de policy, revocation epoch ou fence suspende writes e encerra sem entregar novo evento;
- payload incompatível com a versão aceita é omitido com sinal explícito ou encerra para resync conforme policy;
- falha de redaction bloqueia a entrega em vez de expor o payload original.

## Fluxo de cancelamento e desconexão

Cancelar uma `Execution` é comando idempotente traduzido para `ExecutionControl`; fechar o request ou o stream não cancela a `Execution`. Se o cliente desconecta após enviar comando, a porta continua segundo o estado confirmado e a reconciliação usa a mesma chave. Fechar SSE libera somente recursos da conexão, registra o último cursor seguro e não altera domínio. Shutdown do Gateway drena ou encerra conexões sem inventar Events e permite reconexão em outra instância.

## Segurança

- sessão, PAT, CSRF, revogação e autorização seguem a [RFC 702](702-security.md);
- cookies e credenciais nunca entram em query, cursor, Event, log ou trace;
- CORS, origem permitida, limites de conteúdo e política de transporte são deny-by-default;
- downloads e resultados usam referências autorizadas, expiráveis e restritas ao purpose;
- projeção de evento aplica classificação, field-level redaction e ownership antes da serialização;
- filtros e cursores são limitados em cardinalidade e vinculados ao ator, ao digest do conjunto e à `authorization_version`;
- conexões contam contra quotas de usuário, Workspace, credencial e origem de rede;
- autorização é contínua: uma conexão já aberta não preserva privilégio revogado, reautoriza no máximo a cada 30 segundos e não escreve após a fence de revogação.

## Observabilidade

Logs, métricas e traces usam `operation_id`, `execution_id`, `correlation_id`, categoria de ator, versão do contrato, outcome, latência, bytes, contagem de eventos e razão de encerramento. Para SSE registram `resource_set_digest`, cardinalidade, `authorization_version`, idade da decisão, `authorization_valid_until`, `revocation_epoch`, reautorizações, fences e tempo de encerramento após revogação; a lista detalhada permanece em auditoria autorizada. Registram-se taxa de aceitação/rejeição, conflitos, idempotency replays, outcomes indeterminados, conexões ativas, reconexões, lag, cursores expirados, backpressure, redactions e limites acionados. Não se registram credenciais, cookies, tokens CSRF, payload sensível, query livre, corpo da Task ou valor completo de cursor.

## Invariantes

- a API é Gateway adapter e não contém regra de negócio;
- a API nunca executa agentes, Tools, Capabilities ou Runtime;
- criação e controle de `Execution` são idempotentes e versionados;
- aceite de comando não significa início ou conclusão;
- toda consulta e entrega SSE é autorizada no recurso concreto;
- stream observa somente conjunto explícito não vazio, normalizado e auditado; nunca interpreta vazio como wildcard;
- `resource_selection` é a única fonte canônica de recursos; `ClientEventFilter` não pode declarar Executions;
- cada `stream_id` preserva seleção, filtro e digests do `StreamBinding`; leitura não pode trocar A por B;
- posse de ID ou cursor nunca concede acesso;
- SSE é unidirecional, ao-menos-uma-vez na observação e deduplicável por `event_id`;
- reautorização completa ocorre no máximo a cada 30 segundos e mudança relevante força validação anterior ao próximo write;
- nenhum evento é admitido para entrega depois do commit de revogação; falta de freshness falha fechado;
- cursor é opaco, limitado por retenção e não substitui estado atual;
- disconnect de transporte não cancela trabalho de domínio;
- erros públicos não vazam tecnologia, segredo ou existência protegida;
- toda operação sensível carrega contexto completo e finalidade explícita.

## Extensibilidade

Novas versões de contrato, filtros, projeções e tipos públicos de evento podem coexistir quando a negociação for explícita e compatível. Outro transporte de cliente poderá implementar as mesmas portas, autorização, cursor, redaction e semântica de erro sem alterar Kernel. Mudança incompatível de envelope ou idempotência exige nova versão principal e janela de migração.

## Futuro

WebSocket bidirecional, GraphQL, webhooks, exportações assíncronas e streams agregados poderão ser avaliados por RFC/ADR próprios. Nenhum deles poderá transformar transporte em Runtime, enfraquecer autorização por evento, usar cursor como credencial ou prometer exactly-once sem novo contrato durável.
