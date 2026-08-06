# RFC 803 — Observabilidade, auditoria e reconstrução

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 501 — Provider API](../500-providers-models/501-provider-api.md), [RFC 502 — Model Catalog](../500-providers-models/502-model-catalog.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md), [RFC 602 — Artifact Storage](../600-platform-data/602-artifact-storage.md), [RFC 702 — Segurança](../700-api-security/702-security.md), [RFC 801 — Workers](801-workers.md), [RFC 802 — Scheduler](802-scheduler.md)

## Objetivo

Definir logs estruturados, métricas, tracing, correlação, auditoria orientada a eventos, contabilização de custo e reconstrução integral e autorizada de uma `Execution`, inclusive após falhas e incidentes.

## Fora de escopo

- escolher vendor, collector, protocolo, dashboard, storage, formato físico ou serviço de alerta;
- transformar log, métrica ou span em fonte de verdade de domínio;
- adotar Event Sourcing obrigatório para toda entidade;
- gravar prompt, segredo, credencial ou conteúdo bruto para facilitar diagnóstico;
- prometer replay de efeitos externos a partir da reconstrução;
- definir SLOs e thresholds de produção específicos.

## Responsabilidades e não responsabilidades

O subsistema DEVE:

- correlacionar sinais de API, Scheduler, Worker, Runtime, Provider, Tool, Resource e persistência;
- preservar Events duráveis e registros de auditoria conforme retenção e integridade;
- oferecer métricas de saúde, saturação, fila, latência, erro, uso e custo sem cardinalidade insegura;
- permitir reconstruir estado, sequência causal, decisões, tentativas, efeitos e custos de uma Execution;
- aplicar classificação, minimização, redaction, controle de acesso e legal hold;
- detectar lacunas, duplicatas, clocks inconsistentes e degradação de exportação.

O subsistema NÃO DEVE:

- alterar resultado ou autorização do domínio por causa de telemetria comum;
- aceitar Event como comando;
- inferir sucesso pela ausência de erro em log ou pelo fechamento de span;
- usar `user_id`, `workspace_id`, `execution_id`, URL ou texto livre como label métrica irrestrita;
- permitir que acesso à observabilidade atravesse ownership;
- confundir auditoria de segurança obrigatória com logging best-effort.

## Arquitetura e fontes de verdade

```text
Componentes ── logs / métricas / spans ──> TelemetryPipeline ──> backends substituíveis
     │
     ├── estado + versões ───────────────> PostgreSQL
     ├── Events / outbox ────────────────> EventArchive
     ├── conteúdo durável por referência > ArtifactStorage
     └── auditoria crítica ──────────────> SecurityAuditGate

Estado + Events + receipts + manifests ──> ExecutionReconstructor
                                                  │
                                       visão autorizada + relatório de lacunas
```

Estado durável e Events confirmados são autoridades sobre fatos. Logs e spans explicam operação, mas podem chegar atrasados, duplicados ou ser descartados. Métricas são agregados e nunca comprovam uma ocorrência individual. Auditoria crítica segue as classes `REQUIRED_*` da RFC 702 e falha fechado; telemetria comum é `BEST_EFFORT` com buffer, limites e alerta.

## Contexto sensível de observabilidade

```text
ObservabilityOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  authorization_ref: AuthorizationDecisionRef
  classification_ceiling: DataClassification
  redaction_policy_ref: RedactionPolicyRef
}
```

Emitir sinal ligado a trabalho e consultar/reconstruir uma Execution exige os seis campos de escopo. Operações globais são `Execution`s administrativas com Agent de sistema. Conhecer um ID não concede leitura; a consulta revalida ownership, purpose e classificação.

## Correlação e tempo

`correlation_id` acompanha o fluxo lógico; `execution_id` delimita uma tentativa; `parent_execution_id` liga subtrabalho; `causation_id` aponta a causa direta; `event_id`, `command_id`, `dispatch_id`, `occurrence_id`, `span_id` e `resource_operation_id` preservam identidades próprias.

Events de uma Execution usam `sequence` estritamente crescente como ordem canônica de fatos confirmados. `occurred_at` usa UTC e ordena apenas quando sequence não se aplica. Logs e spans carregam timestamps UTC, monotonic duration e `clock_source`; divergência de relógio é sinalizada, nunca corrigida reordenando Event histórico.

## Logs estruturados

```text
StructuredLogRecord {
  log_id: LogRecordId
  occurred_at: Instant
  severity: TRACE | DEBUG | INFO | WARN | ERROR | FATAL
  service: ServiceName
  component: ComponentName
  environment: EnvironmentRef
  event_name: LogEventName
  message_template: MessageTemplateId
  outcome: SUCCESS | FAILURE | DENIED | CANCELLED | INDETERMINATE | NOT_APPLICABLE
  reason_code: ReasonCode | null
  correlation: CorrelationFields
  ownership_ref: RedactedOwnershipRef
  attempt: PositiveInteger | null
  duration: Duration | null
  attributes: BoundedRedactedAttributes
  classification: DataClassification
  redaction_policy_version: Version
}

CorrelationFields {
  correlation_id: CorrelationId
  execution_id: ExecutionId | null
  parent_execution_id: ExecutionId | null
  event_id: EventId | null
  causation_id: EventId | CommandId | null
  dispatch_id: DispatchId | null
  span_id: SpanId | null
}
```

Campos são tipados, limitados e registrados por catálogo. Texto livre é exceção sanitizada, não o lugar para payload. Stack trace é armazenada somente após redaction e pode virar Artifact restrito. Sampling PODE reduzir sucesso repetitivo, mas nunca remove erro terminal, negação relevante, decisão de auditoria, custo confirmado ou marco necessário à reconstrução.

## Métricas

```text
MetricDescriptor {
  metric_name: MetricName
  kind: COUNTER | GAUGE | HISTOGRAM
  unit: MetricUnit
  description: String
  allowed_labels: LowCardinalityLabel[]
  max_series: PositiveInteger
  aggregation_temporality: DELTA | CUMULATIVE
  classification: PUBLIC | INTERNAL
  owner: ComponentName
  version: Version
}
```

Catálogo inicial cobre RED/USE, filas, tempo por estado de Execution, Scheduler lag/misfire, leases/retries/recovery, Provider/Tool/Resource latency, timeouts, cancelamento, outbox, auditoria indisponível, ingestão/exportação de telemetria e custo. Labels permitidas incluem ambiente, componente, operação catalogada, pool, classe de resultado, reason code controlado, model profile e bucket de duração/custo.

IDs, nomes de usuário/Workspace/Agent, prompt, URL, path, mensagem de erro e valores fornecidos por usuário NÃO DEVEM ser labels. Análise por entidade usa logs/Events autorizados; métricas usam agregação ou bucket pseudonimizado com retenção curta quando indispensável.

## Tracing distribuído

```text
TraceContext {
  trace_id: TraceId
  span_id: SpanId
  parent_span_id: SpanId | null
  trace_flags: TraceFlags
  trace_state: BoundedTraceState
  correlation_id: CorrelationId
  execution_id: ExecutionId | null
}

SpanRecord {
  context: TraceContext
  operation: OperationName
  kind: INTERNAL | SERVER | PRODUCER | CONSUMER | CLIENT
  started_at: Instant
  ended_at: Instant
  status: OK | ERROR | UNSET
  outcome_ref: DurableOutcomeRef | null
  attributes: BoundedRedactedAttributes
  links: TraceLink[]
}
```

Contexto de trace atravessa API, outbox, fila, Worker, Runtime e adapters somente em campos autenticados/limitados. Uma nova entrega de fila cria span `CONSUMER` novo ligado ao span produtor e ao mesmo `dispatch_id`; não finge continuidade síncrona. Subexecution pode compartilhar `trace_id` enquanto preserva novo `execution_id`. Headers externos não podem escolher ownership, autorização ou IDs internos sem validação.

Span `OK` significa operação técnica concluída, não necessariamente domínio bem-sucedido. `outcome_ref` aponta para Event/receipt durável quando existe. Sampling é parent-aware e prioriza erro, lentidão, custo anômalo e incidentes, sem capturar conteúdo sensível.

## Auditoria orientada a eventos

```text
AuditProjectionRecord {
  audit_projection_id: AuditProjectionId
  source_event_id: EventId
  event_type: EventType
  event_version: Version
  occurred_at: Instant
  sequence: ExecutionSequence | null
  ownership_ref: RedactedOwnershipRef
  execution_id: ExecutionId | null
  correlation_id: CorrelationId
  action: AuditAction
  actor_ref: RedactedActorRef
  outcome: AuditOutcome
  resource_ref: RedactedResourceRef | null
  integrity_ref: AuditIntegrityRef
  retention_policy_ref: RetentionPolicyRef
}
```

A projeção consome Events imutáveis, preserva `source_event_id` e deduplica por identidade. Replay reconstrói a projeção sem criar novo fato auditado. Eventos tardios entram na posição de `sequence`/`occurred_at` original e geram revisão da projeção, não reescrita do Event.

Auditoria de operação inclui criação/controle de Execution, Schedule, configuração, acesso privilegiado, Tools/Resources, mutações, cancelamento, recovery, custos, retenção e consultas à própria auditoria. Eventos de segurança críticos usam primeiro `SecurityAuditGate`; a projeção não substitui reserva/finalização `REQUIRED_PRECOMMIT`, `REQUIRED_PREDELIVERY` ou `REQUIRED_DECISION`.

## Uso e custos de modelo

```text
ModelUsageRecord {
  usage_id: UsageId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  provider_invocation_id: ProviderInvocationId
  provider_terminal_ref: ProviderTerminalRef
  provider_ref: ProviderRef
  model_profile: ModelProfileId
  resolved_model_ref: RedactedModelRef
  input_tokens: NonNegativeInteger | null
  output_tokens: NonNegativeInteger | null
  cached_input_tokens: NonNegativeInteger | null
  tool_units: UsageUnit[]
  usage_status: CONFIRMED | ESTIMATED | UNAVAILABLE
  cost_status: CONFIRMED | COMPUTED_FROM_CATALOG | ESTIMATED | UNAVAILABLE
  accounting_finality: FINAL_CONFIRMED | FINAL_ESTIMATED | FINAL_UNAVAILABLE
  pricing_revision_ref: PricingRevisionRef | null
  currency: CurrencyCode | null
  estimated_cost: Money | null
  confirmed_cost: Money | null
  record_status: ORIGINAL | RECONCILED
  version: Version
  occurred_at: Instant
  source_event_id: EventId
}

ExecutionCostSummary {
  execution_id: ExecutionId
  usage_record_ids: UsageId[]
  total_by_currency: Money[]
  unpriced_usage: UsageUnit[]
  status: ESTIMATED | PARTIAL | CONFIRMED | RECONCILED | UNAVAILABLE
  version: Version
}
```

Cada terminal público da RFC 501 produz no máximo um registro lógico, identificado por `provider_terminal_ref` e ligado ao `provider_invocation_id`. O índice único conceitual é `(provider_invocation_id, provider_terminal_ref)`; o histórico de correções é único por `(usage_id, version)`. Somente a versão vigente de `usage_id` contribui ao agregado, portanto replay, redelivery e reconciliação não duplicam unidades nem custo.

`usage_status` deriva de `ProviderUsage.measurement`; `cost_status`, de `ProviderCost.measurement`; `accounting_finality` preserva a garantia do `ProviderTerminalSnapshot`. `ESTIMATED` permanece estimativa, e `UNAVAILABLE` mantém tokens/custo desconhecidos como nulos — nunca como zero. `FINAL_ESTIMATED` e `FINAL_UNAVAILABLE` são terminais contábeis explícitos; correção posterior cria nova versão `RECONCILED`, preserva a anterior e referencia o mesmo terminal. Preço usa `PricingRevisionRef` versionada da moeda/unidade vigentes no instante, não consulta retroativa ao preço atual. Conversão cambial, se usada, registra fonte e snapshot separados. Prompt e resposta não são necessários para contabilizar tokens.

## Reconstrução integral de Execution

Reconstrução integral significa explicar, dentro da retenção e autorização aplicáveis: identidade e Task snapshot/ref; estados e versões; Events em ordem; comandos e decisions; Scheduler occurrence; despachos, tentativas, leases e recovery; Agent/model/profile; Context manifest; Tools/Capabilities/Resources; referências de Artifact; uso/custos; cancelamento, falhas e resultado. Não significa reproduzir segredo, credencial, conteúdo expirado ou efeito externo.

```text
interface ExecutionReconstructor {
  reconstruct(query: ReconstructExecution) -> ExecutionReconstruction
  verify(query: VerifyExecutionReconstruction) -> ReconstructionVerification

  pre: acesso foi autorizado para purpose e classification_ceiling
  post: toda afirmação referencia fonte durável ou é marcada como inferência
  post: lacunas, redactions e retenções são explícitas
}

ReconstructExecution {
  operation_id: ObservabilityOperationId
  context: ObservabilityOperationContext
  target_execution_id: ExecutionId
  include_descendants: Boolean
  projection: TIMELINE | STATE | COST | INCIDENT | COMPLETE
  as_of: Instant | null
  classification_ceiling: DataClassification
  idempotency_key: IdempotencyKey
}

ExecutionReconstruction {
  reconstruction_id: ReconstructionId
  execution_snapshot: AuthorizedExecutionSnapshot
  timeline: ReconstructionEntry[]
  causal_graph_ref: CausalGraphRef
  state_transitions: StateTransitionEvidence[]
  dispatch_attempts: DispatchEvidence[]
  scheduled_origin: ScheduleEvidence | null
  runtime_decisions: DecisionEvidence[]
  resource_effects: ResourceEffectEvidence[]
  context_manifest_ref: ContextManifestRef | null
  artifact_refs: AuthorizedArtifactRef[]
  usage: ExecutionCostSummary
  terminal_evidence: DurableOutcomeRef | null
  completeness: COMPLETE | COMPLETE_WITH_REDACTIONS | PARTIAL
  gaps: ReconstructionGap[]
  source_watermarks: SourceWatermark[]
  integrity_ref: IntegrityRef
  generated_at: Instant
}

ReconstructionEntry {
  sequence: ExecutionSequence | null
  occurred_at: Instant
  fact_type: ReconstructionFactType
  source_ref: EventId | RecordVersionRef | ReceiptRef | ManifestRef
  causation_ref: CausationRef | null
  summary: RedactedStructuredSummary
  evidence_level: CONFIRMED | CORROBORATED | INFERRED
}
```

O reconstrutor lê snapshot/version history, EventArchive, receipts idempotentes, manifests e referências; logs/spans apenas corroboram. Verifica sequência duplicada, lacunas, versões regressivas, terminal conflitante, dispatch sem Execution, custo sem receipt e efeito sem reconciliação. `PARTIAL` nunca é promovido a completo. `as_of` usa watermarks consistentes e não mistura dado futuro. Descendentes são autorizados individualmente; uma relação causal não transfere acesso.

Reconstrução é leitura forense, não replay: não publica Events, não reexecuta Tool/Provider, não restaura Context e não altera estado. Conteúdo ainda retido é buscado somente por referência, com autorização do store proprietário; redaction registra motivo e digest/ref quando permitido.

## Contratos de emissão e consulta

```text
interface TelemetrySink {
  emit_log(record: StructuredLogRecord) -> TelemetryReceipt
  record_metric(measurement: MetricMeasurement) -> TelemetryReceipt
  end_span(record: SpanRecord) -> TelemetryReceipt

  pre: schema, cardinalidade, classificação e redaction foram validados
  post: falha best-effort é contada/sinalizada sem mascarar resultado do domínio
}

interface ObservabilityQuery {
  query_logs(query: AuthorizedLogQuery) -> LogPage
  query_metrics(query: AuthorizedMetricQuery) -> MetricSeries
  query_traces(query: AuthorizedTraceQuery) -> TraceView
  query_audit(query: AuthorizedAuditQuery) -> AuditPage
  query_cost(query: AuthorizedCostQuery) -> ExecutionCostSummary

  pre: escopo, purpose, retenção e classificação foram autorizados
  post: resultados são redacted, paginados e limitados ao ownership
}
```

Queries têm janela, page limit, custo e timeout. Cursores são opacos e vinculados ao filtro/autorização. Exportação em massa é privilegiada, rate-limited e auditada.

## Fluxo normal

1. Um componente confirma mudança e Event/outbox no store proprietário.
2. No mesmo limite lógico, emite log/span com correlação e métrica agregada.
3. Pipeline valida schema, redaction, tamanho e cardinalidade e encaminha sinal.
4. Audit projector consome Event, deduplica e atualiza projeção íntegra.
5. Provider receipt atualiza uso/custo idempotente e a Execution agrega o resumo.
6. Consulta autorizada combina apenas fontes necessárias e registra acesso sensível.
7. Reconstrução ordena evidências, verifica integridade e declara lacunas/redactions.

## Falhas, timeout e recuperação

- **collector/backend indisponível:** buffers limitados priorizam sinais críticos; descarte por classe é contado localmente e gera `TelemetryDegraded` quando possível;
- **buffer cheio:** aplica shedding a debug/sucesso amostrado antes de erro, terminal ou custo; nunca bloqueia indefinidamente o Runtime;
- **audit store obrigatório indisponível:** operação `REQUIRED_*` falha fechado conforme RFC 702;
- **evento duplicado:** projeção deduplica por `event_id`; métrica derivada usa identidade de contribuição;
- **evento atrasado ou fora de ordem:** sequence posiciona o fato; gap permanece aberto até watermark/reconciliação;
- **reinício de projector:** retoma cursor durável e replay idempotente;
- **custo ausente:** summary fica `PARTIAL` ou `UNAVAILABLE`; reconciliador consulta `ProviderTerminalSnapshot` por `provider_terminal_ref` sem inventar zero;
- **trace incompleto:** reconstrução usa Event/estado e marca trace gap;
- **timeout de consulta:** retorna resultado paginado/incompleto explicitamente, não snapshot silenciosamente truncado;
- **corrupção/integridade divergente:** isola fonte, preserva evidência e abre `Execution` administrativa de incidente.

Após incidente, o operador cria reconstrução `INCIDENT`, congela watermarks e aplica legal hold quando autorizado. A análise registra consultas, redactions, versões de policy e integridade. Correções são novos Events/registros compensatórios; evidência original não é editada.

## Cancelamento

Cancelar uma `Execution` produz sinais e terminal próprios; não apaga telemetria nem auditoria. Cancelar consulta/reconstrução interrompe novas leituras e libera recursos, preservando acesso já auditado e artefato forense já confirmado. Cancelar exportação não revoga bytes já entregues; política de acesso e incidente trata o destino. Pipeline em shutdown faz flush limitado por deadline e registra perda contabilizada.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `TelemetryDegraded` | perda, atraso ou rejeição ultrapassou limite declarado |
| `TelemetryRecovered` | pipeline voltou a satisfazer sua condição operacional |
| `AuditProjectionRebuilt` | replay idempotente chegou a watermark conhecido |
| `ExecutionCostConfirmed` | uso e preço confirmados produziram custo versionado |
| `ExecutionCostUnavailable` | terminal confirmou indisponibilidade final de uso ou custo |
| `CostReconciled` | custo anterior recebeu correção preservando histórico |
| `ExecutionReconstructionCompleted` | reconstrução autorizada foi confirmada com nível de completude |
| `ObservabilityIntegrityViolationDetected` | verificação encontrou divergência de evidência |

Emissão de eventos sobre a própria telemetria usa rate limit e agregação para evitar recursão. `TelemetryDegraded` não é escrito no pipeline que está comprovadamente indisponível sem fallback durável.

## Retenção, integridade e acesso

Cada classe declara duração, legal hold, exportação e descarte seguro. Events/auditoria duráveis seguem policy de domínio e segurança; logs detalhados e traces normalmente têm retenção menor; métricas agregadas podem ter retenção maior sem labels pessoais; uso/custo segue obrigação financeira aplicável. Remoção de conteúdo preserva tombstone ou gap explícito quando necessário à integridade.

Registros duráveis são append-oriented, versionados e protegidos contra adulteração por `integrity_ref`. Acesso, busca, reconstrução, exportação, hold e descarte são autorizados e auditados. Índices conceituais cobrem execution/correlation/event/dispatch/occurrence IDs, sequence, occurred_at, tipo/versão, outcome, reason code, `provider_invocation_id`, unicidade `(provider_invocation_id, provider_terminal_ref)`, histórico `(usage_id, version)`, retention e integrity refs.

## Segurança e privacidade

- proíbe-se segredo, PAT/hash completo, cookie, CSRF, senha, chave, nonce, ciphertext, prompt, resposta, DOM, arquivo e conteúdo de Artifact em log/métrica/span/Event de observabilidade;
- referências e IDs externos são redacted ou pseudonimizados conforme purpose;
- mensagens de erro usam códigos catalogados; entrada não confiável é sanitizada;
- cada backend, exportador e operador recebe privilégio mínimo e isolamento por ambiente;
- queries aplicam ownership no store, não filtro posterior no cliente;
- acesso cross-user/workspace é negado sem enumerar existência;
- debug temporário exige prazo, aprovação, escopo estreito, redaction e auditoria;
- dados de observabilidade não treinam modelo nem alimentam Context sem autorização e contrato próprios.

## Observabilidade da observabilidade

O pipeline mede ingestão, validação, redaction, filas, bytes, atraso, sampling, drops por razão, cardinalidade, exportação, falha de backend, watermark, replay, integridade e custo do próprio sistema. Heartbeats sintéticos e canários verificam o caminho sem conter dados reais. Alertas distinguem ausência de tráfego legítima de pipeline cego.

## Invariantes

- Event/estado durável prova fato; log, métrica e span não o substituem;
- toda Execution é correlacionável ponta a ponta sem registrar seu conteúdo sensível;
- auditoria crítica não degrada para best-effort;
- duplicata não duplica projeção, custo ou contagem baseada em fato;
- métricas não têm labels de alta cardinalidade ou dados pessoais;
- custo distingue estimado, confirmado e reconciliado e usa preço versionado;
- reconstrução declara fonte, inferência, gap e redaction, e nunca reexecuta efeitos;
- relação causal não transfere autorização;
- degradação da observabilidade é ela própria detectável e auditável.

## Extensibilidade

Backends, exporters, schemas, métricas e views podem ser substituídos por adapters. Novos sinais entram em catálogo versionado com owner, cardinalidade, unidade, classificação, retenção e redaction. Novas fontes do reconstrutor declaram autoridade, watermark, integridade e comportamento de lacuna antes de participar de `COMPLETE`.

## Futuro

OpenTelemetry interoperável, detecção de anomalias, budgets/SLOs, chargeback, assinatura externa de evidências, cold storage e investigação multi-Execution poderão ser adicionados. Automação de resposta nunca executará remediação apenas por métrica: criará comando autorizado e `Execution` auditável.
