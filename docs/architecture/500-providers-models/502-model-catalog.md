# RFC 502 — Model Catalog

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 201 — Agent](../200-agents/201-agent.md), [RFC 202 — Orchestrator](../200-agents/202-orchestrator.md), [RFC 501 — Provider API](501-provider-api.md)

## Objetivo

Definir o Model Catalog como fonte pública e versionada de metadados, perfis e compatibilidade de modelos, além da porta que resolve requisitos abstratos em uma seleção explícita para a Provider API. O catálogo mantém Provider, nome público, janela de contexto, custo, visão, Tools, streaming, status e metadata; oferece os perfis `CODING`, `REASONING`, `ORCHESTRATOR`, `VISION`, `CHEAP` e `BALANCED`; e governa resolução, fallback explícito e descontinuação sem expor SDKs ou nomes técnicos proprietários ao Runtime.

## Fora de escopo

- executar geração, stream, visão, Tool calls ou cancelamento, responsabilidade da [RFC 501](501-provider-api.md);
- montar Context ou escolher seu conteúdo;
- definir comportamento do Agent ou estratégia de planejamento do Orchestrator;
- executar benchmark, avaliação ou descoberta automática de modelos;
- decidir preços comerciais, disponibilidade contratual ou termos do fornecedor;
- armazenar credencial, objeto de SDK, payload proprietário ou configuração executável;
- definir endpoint, tabela, schema ORM, cache, job, linguagem ou framework;
- tornar fallback automático e implícito obrigatório.

## Responsabilidades e não responsabilidades

O Model Catalog DEVE:

- manter descriptors públicos, versionados e auditáveis de Providers e modelos;
- registrar contexto, custo, visão, Tools, streaming, status e metadata compatível;
- representar vínculos técnicos do fornecedor por referência opaca resolvida somente na camada de Provider;
- definir perfis sem transformar um perfil em nome de modelo;
- validar requisitos obrigatórios antes de ranquear preferências;
- produzir seleção primária e plano de fallback explícito, ordenado e explicável;
- congelar versão do catálogo, política, preço e compatibilidade em cada seleção;
- governar depreciação, desabilitação e retirada sem substituição silenciosa;
- consumir apenas sinais públicos e sanitizados de disponibilidade e telemetria;
- preservar ownership, finalidade, correlação e autorização em operações sensíveis.

O Model Catalog NÃO DEVE:

- invocar Provider ou Tool;
- alterar estado de Execution;
- ampliar orçamento, classificação permitida ou permissões do Agent;
- escolher com base em objeto, header, erro ou capability não normalizada de SDK;
- garantir qualidade subjetiva ou disponibilidade futura;
- permitir que metadata arbitrária substitua campos tipados;
- ocultar razão, custo previsto, status depreciado ou fallback usado;
- modificar descriptor publicado em lugar de criar nova revisão.

## Arquitetura e fronteiras

```text
Runtime / Agent / Orchestrator
        │ ModelRequirements + contexto sensível
        ▼
     ModelResolver
        ├── ModelCatalogPort
        ├── ProfileRegistry
        ├── CompatibilityPolicy
        ├── CostPolicy
        ├── AvailabilitySnapshotPort
        └── FallbackPolicy
                 │
                 ▼
      ModelSelection + fallback explícito
                 │
                 ▼
         ProviderPort (RFC 501)
```

O consumidor conhece atributos públicos. O `ProviderPort` recebe `ModelSelectionRef` e resolve o binding opaco dentro de sua própria fronteira. Runtime, Agent, Orchestrator e Context nunca decompõem a referência nem leem nome técnico aceito por SDK.

Catálogo e resolução são responsabilidades distintas: o catálogo registra fatos e políticas versionados; o resolver filtra e ordena candidatos para uma solicitação concreta. Uma implementação pode reuni-los fisicamente, mas deve conservar contratos e ownership separados.

## Entidades e dados

### Contexto de operação sensível

Toda resolução, consulta, registro, alteração de status, publicação de preço, inspeção de seleção e decisão de fallback DEVE carregar os seis escopos exigidos:

```text
ModelCatalogOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}
```

`workspace_id` só é nulo para Execution explicitamente fora de Workspace. Operação de manutenção deve ser executada por uma Execution administrativa própria; não existe bypass sem `agent_id` ou `execution_id`. A finalidade diferencia resolução de produção, avaliação, auditoria e administração, mas nunca substitui autorização.

### Provider e modelo

```text
ProviderDescriptor {
  provider_ref: ProviderRef
  name: ProviderName
  status: ProviderStatus
  supported_data_classifications: DataClassification[]
  allowed_regions: RegionCode[]
  metadata: PublicProviderMetadata
  revision: ProviderRevision
  created_at: Instant
  updated_at: Instant
}

ProviderStatus = ACTIVE | DEGRADED | DISABLED | RETIRED
```

```text
ModelDescriptor {
  model_ref: ModelRef
  provider_ref: ProviderRef
  name: ModelName
  provider_binding_ref: ProviderModelBindingRef
  context: ModelContextLimits
  cost: ModelCost
  vision: VisionCapability
  tools: ToolCapability
  streaming: StreamingCapability
  cancellation: InvocationCancellationCapability
  status: ModelStatus
  metadata: PublicModelMetadata
  profiles: ModelProfile[]
  compatibility: ModelCompatibility
  deprecation: ModelDeprecation | null
  revision: ModelRevision
  created_at: Instant
  updated_at: Instant
}

ModelStatus = ACTIVE | DEPRECATED | DISABLED | RETIRED
```

`name` é um nome público estável do catálogo. `provider_binding_ref` é opaca e só pode ser resolvida pelo adapter autorizado; não é decomposta pelo domínio e impede vazamento do nome técnico ou objeto do SDK. IDs externos ficam dentro do binding privado.

```text
ModelContextLimits {
  maximum_total_tokens: PositiveInteger
  maximum_input_tokens: PositiveInteger
  maximum_output_tokens: PositiveInteger
  tokenizer_profile: TokenizerProfileRef
}

ModelCost {
  currency: CurrencyCode
  input_per_million_tokens: Decimal | null
  output_per_million_tokens: Decimal | null
  cached_input_per_million_tokens: Decimal | null
  image_unit_cost: Decimal | null
  minimum_charge: Decimal | null
  pricing_revision: PricingRevisionRef
  effective_from: Instant
  effective_until: Instant | null
  measurement_basis: PROVIDER_PUBLISHED | CONTRACTED | ESTIMATED
}
```

Valores nulos significam preço indisponível, nunca zero. Resolução com restrição de custo rejeita candidato sem preço comparável, salvo política explícita `ALLOW_UNKNOWN_COST` autorizada.

```text
VisionCapability {
  supported: Boolean
  media_types: MediaType[]
  maximum_images: NonNegativeInteger | null
  maximum_image_bytes: ByteSize | null
  detail_modes: VisionDetailMode[]
}

ToolCapability {
  supported: Boolean
  parallel_calls: Boolean
  strict_schema: Boolean
  maximum_declarations: NonNegativeInteger | null
  maximum_calls_per_turn: NonNegativeInteger | null
}

StreamingCapability {
  supported: Boolean
  usage_during_stream: Boolean
  tool_call_deltas: Boolean
}

InvocationCancellationCapability {
  mode: COOPERATIVE_REMOTE | LOCAL_ONLY | UNSUPPORTED
  terminal_observation: GUARANTEED
  accounting_finality: CONFIRMED | ESTIMATED_OR_UNAVAILABLE
  maximum_reconciliation_time: Duration
}
```

Cancelamento é capability da invocação, não do transporte de streaming: a mesma chamada pode ser cancelável em modo não streaming, e um stream pode existir sobre fornecedor sem interrupção remota. Os modos têm esta semântica:

- `COOPERATIVE_REMOTE`: o adapter propaga o pedido ao fornecedor, interrompe novos efeitos quando reconhecido e reconcilia o terminal e a contabilização;
- `LOCAL_ONLY`: o adapter encerra entrega e produção de resultado localmente, não promete interromper processamento remoto e reconcilia uso/custo até `maximum_reconciliation_time`;
- `UNSUPPORTED`: a invocação não satisfaz requisito de cancelamento e só é elegível quando a solicitação aceita explicitamente essa limitação.

`terminal_observation = GUARANTEED` exige que a RFC 501 retenha e entregue um terminal público após o cancelamento. `accounting_finality` declara se o fornecedor permite valor confirmado ou apenas conclusão explícita estimada/indisponível; valor ausente nunca é convertido em zero.

```text
PublicModelMetadata {
  release_family: Text | null
  quality_tier: LOW | MEDIUM | HIGH | FRONTIER | null
  latency_tier: LOW | MEDIUM | HIGH | null
  reasoning_mode: NONE | OPTIONAL | REQUIRED | null
  structured_output: NONE | JSON | SCHEMA_CONSTRAINED
  training_data_cutoff: Instant | null
  regions: RegionCode[]
  data_residency: DataResidencyClass[]
  tags: PublicMetadataTag[]
}
```

`metadata` contém somente chaves registradas e tipos públicos. Mapas livres com payload proprietário são proibidos. Metadata informa compatibilidade e ordenação; não concede autorização nem contradiz campos tipados.

### Perfis

```text
ModelProfile = CODING | REASONING | ORCHESTRATOR | VISION | CHEAP | BALANCED

ProfileDefinition {
  profile: ModelProfile
  required: ModelConstraint[]
  preferences: WeightedPreference[]
  default_fallback_policy_ref: FallbackPolicyRef | null
  revision: ProfileRevision
  status: ACTIVE | DEPRECATED | DISABLED
}
```

| Perfil | Intenção | Requisitos e preferências normativas |
| --- | --- | --- |
| `CODING` | edição, análise e geração de código | prioriza Tool use, structured output, contexto adequado e qualidade em código; não presume Provider |
| `REASONING` | tarefas com raciocínio deliberado | exige modo de reasoning compatível quando solicitado e prioriza qualidade sob budget |
| `ORCHESTRATOR` | planejamento, delegação e decisão entre ações | prioriza Tools confiáveis, structured output, contexto e latência previsível |
| `VISION` | compreensão de imagens | exige `vision.supported = true` e formatos/tamanhos compatíveis |
| `CHEAP` | minimizar custo dentro dos requisitos | ordena custo elegível após todos os constraints obrigatórios; preço desconhecido não vence por ausência |
| `BALANCED` | equilibrar qualidade, custo e latência | usa pesos versionados sem relaxar requisitos obrigatórios |

Um modelo pode pertencer a vários perfis. Associação é declaração versionada baseada em avaliação e capabilities públicas; não é inferida do nome. Perfil define intenção reutilizável, não garantia absoluta de qualidade.

### Compatibilidade

```text
ModelCompatibility {
  supported_input_kinds: InputKind[]
  supported_response_formats: ResponseFormat[]
  supported_sampling_parameters: SamplingParameter[]
  minimum_provider_contract: ProviderContractVersion
  incompatible_tool_schema_features: SchemaFeature[]
  allowed_purposes: PurposePattern[]
  maximum_data_classification: DataClassification
}
```

Compatibilidade é a interseção de descriptor, política de Provider, classificação, finalidade, região, Agent, Workspace e limites da Execution. Um campo `supported = true` nunca supera proibição de segurança ou status indisponível.

### Requisitos e seleção

```text
ModelRequirements {
  context: ModelCatalogOperationContext
  requested_profile: ModelProfile | null
  preferred_model_ref: ModelRef | null
  allowed_provider_refs: ProviderRef[]
  denied_provider_refs: ProviderRef[]
  required_capabilities: RequiredCapability[]
  input_kinds: InputKind[]
  response_format: ResponseFormat
  cancellation_requirement: ANY | LOCAL_OR_REMOTE | REMOTE_REQUIRED
  minimum_context_tokens: PositiveInteger
  maximum_input_tokens: PositiveInteger
  maximum_output_tokens: PositiveInteger
  maximum_total_tokens: PositiveInteger
  maximum_cost: CostAmount | null
  latency_preference: LOWEST | BALANCED | QUALITY_FIRST | null
  data_classification: DataClassification
  region: RegionCode | null
  fallback: FallbackRequest
  catalog_version: CatalogVersion | null
  policy_version: ModelPolicyVersion | null
  cancellation: CancellationSignalRef
}
```

```text
ModelSelection {
  selection_id: ModelSelectionId
  selection_ref: ModelSelectionRef
  context: ModelCatalogOperationContext
  primary: SelectedModel
  fallbacks: SelectedModel[]
  catalog_version: CatalogVersion
  policy_version: ModelPolicyVersion
  profile_revision: ProfileRevision | null
  pricing_revisions: PricingRevisionRef[]
  approved_requirements_ref: ApprovedModelRequirementsRef
  availability_snapshot_ref: AvailabilitySnapshotRef
  explanation: SelectionExplanation
  resolved_at: Instant
  valid_until: Instant
}

SelectedModel {
  model_ref: ModelRef
  provider_ref: ProviderRef
  provider_binding_ref: ProviderModelBindingRef
  descriptor_revision: ModelRevision
  capabilities: ResolvedCapabilities
  estimated_cost_ceiling: CostAmount | null
  rank: PositiveInteger
  role: PRIMARY | FALLBACK
}
```

```text
ApprovedModelRequirementsSnapshot {
  approved_requirements_ref: ApprovedModelRequirementsRef
  selection_id: ModelSelectionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  purpose: Purpose
  data_classification: DataClassification
  region: RegionCode | null
  input_kinds: InputKind[]
  response_format: ResponseFormat
  required_capabilities: RequiredCapability[]
  cancellation_requirement: ANY | LOCAL_OR_REMOTE | REMOTE_REQUIRED
  minimum_context_tokens: PositiveInteger
  maximum_input_tokens: PositiveInteger
  maximum_output_tokens: PositiveInteger
  maximum_total_tokens: PositiveInteger
  maximum_cost: CostAmount | null
  allowed_provider_refs: ProviderRef[]
  allowed_model_refs: ModelRef[]
  fallback_policy_ref: FallbackPolicyRef | null
  catalog_version: CatalogVersion
  policy_version: ModelPolicyVersion
  issued_at: Instant
  valid_until: Instant
  integrity_ref: IntegrityRef
}
```

O snapshot aprovado é imutável, pertence à seleção e materializa os constraints autorizados que não podem ser reconstruídos por “configuração atual”. `approved_requirements_ref` identifica seu conteúdo íntegro; qualquer alteração de classificação, região, formato, capabilities, cancelamento, limites, Provider/modelo ou política exige nova resolução e nova seleção. A Provider API recebe o snapshot e a referência e revalida ambos antes da transmissão.

`provider_binding_ref` é transportada opacamente ao ProviderPort. O Runtime pode comparar referências e capabilities públicas, mas não resolve o binding.

## Contratos tipados

Esta RFC é a fonte canônica da assinatura de `ModelResolver` e de seus tipos de resolução. A menção a `ModelResolver` na RFC 101 é um alias de composição para esta interface completa; o Runtime não define uma sobrecarga abreviada nem recebe `ModelSelection` sem o `ModelResolutionOutcome` contratual.

```text
interface ModelCatalogPort {
  register_provider(request: RegisterProvider) -> CatalogMutationResult
  register_model(request: RegisterModel) -> CatalogMutationResult
  publish_profile(request: PublishModelProfile) -> CatalogMutationResult
  publish_pricing(request: PublishModelPricing) -> CatalogMutationResult
  change_model_status(request: ChangeModelStatus) -> CatalogMutationResult
  get_model(query: AuthorizedModelQuery) -> ModelDescriptor
  list_models(query: AuthorizedModelListQuery) -> ModelPage
  inspect_selection(query: AuthorizedModelSelectionQuery) -> ModelSelection

  invariant: toda revisão publicada é imutável
  invariant: toda operação recebe ModelCatalogOperationContext completo
}
```

```text
CatalogMutationRequest {
  request_id: CatalogRequestId
  context: ModelCatalogOperationContext
  expected_catalog_version: CatalogVersion
  idempotency_key: IdempotencyKey
}

RegisterProvider extends CatalogMutationRequest {
  descriptor: ProviderDescriptor
}

RegisterModel extends CatalogMutationRequest {
  descriptor: ModelDescriptor
}

PublishModelProfile extends CatalogMutationRequest {
  profile: ProfileDefinition
}

PublishModelPricing extends CatalogMutationRequest {
  model_ref: ModelRef
  cost: ModelCost
}

ChangeModelStatus extends CatalogMutationRequest {
  model_ref: ModelRef
  expected_status: ModelStatus
  new_status: ModelStatus
  deprecation: ModelDeprecation | null
  reason: StatusChangeReason
}
```

```text
AuthorizedModelQuery {
  context: ModelCatalogOperationContext
  model_ref: ModelRef
  revision: ModelRevision | null
}

AuthorizedModelListQuery {
  context: ModelCatalogOperationContext
  profiles: ModelProfile[]
  providers: ProviderRef[]
  statuses: ModelStatus[]
  required_capabilities: RequiredCapability[]
  page: PageRequest
}

AuthorizedModelSelectionQuery {
  context: ModelCatalogOperationContext
  selection_ref: ModelSelectionRef
}
```

```text
interface ModelResolver {
  resolve(request: ResolveModel) -> ModelResolutionOutcome
  resolve_fallback(request: ResolveFallback) -> ModelResolutionOutcome

  pre: contexto corresponde ao ownership e Agent da Execution
  post: todo candidato satisfaz requisitos obrigatórios e política vigente
  post: seleção registra versões, razões e fallback explicitamente permitido
}

ResolveModel {
  request_id: ModelResolutionRequestId
  requirements: ModelRequirements
  idempotency_key: IdempotencyKey
}

ResolveFallback {
  request_id: ModelResolutionRequestId
  context: ModelCatalogOperationContext
  prior_selection_ref: ModelSelectionRef
  failed_model_ref: ModelRef
  failure_category: ProviderErrorCategory
  consumed_usage: ProviderUsage
  consumed_cost: ProviderCost
  remaining_limits: ExecutionLimits
  cancellation: CancellationSignalRef
  idempotency_key: IdempotencyKey
}

ModelResolutionOutcome =
  | ModelResolved { selection: ModelSelection }
  | NoCompatibleModel { error: ModelResolutionError, considered: CandidateRejection[] }
  | ModelResolutionCancelled { reason: CancellationReason }
```

Resolver a mesma solicitação com mesma chave, versões e snapshot retorna resultado observacionalmente equivalente. Mudança de contexto, policy ou payload com a mesma chave é rejeitada.

## Resolução

A resolução segue esta ordem normativa:

1. validar contexto sensível, ownership, finalidade, autorização e cancelamento;
2. fixar `catalog_version`, `policy_version`, revisão de perfil, preços e snapshot de disponibilidade;
3. formar candidatos pelo modelo preferido ou perfil solicitado;
4. remover Provider ou modelo com status não elegível;
5. aplicar hard constraints: classificação, região, contexto, input/output/total, visão, Tools, streaming, cancelamento por invocação, response format, Provider allow/deny e budget;
6. calcular custo comparável com preço da revisão fixada;
7. aplicar preferências versionadas de perfil, qualidade, custo e latência apenas aos candidatos ainda compatíveis;
8. ordenar determinística e estavelmente, usando `model_ref` somente como desempate final opaco;
9. materializar `ApprovedModelRequirementsSnapshot` imutável com classificação, região, formato, capabilities, cancelamento, limites, Providers/modelos e policy aprovados;
10. montar primário e fallbacks permitidos, registrar rejeições e explicação sanitizada;
11. persistir seleção e snapshot antes de entregá-los ao Runtime.

Hard constraint nunca é convertido em preferência. `preferred_model_ref` incompatível falha ou entra no plano explícito de fallback conforme `FallbackRequest`; não é silenciosamente ignorado.

```text
SelectionExplanation {
  requested_profile: ModelProfile | null
  satisfied_constraints: ConstraintCode[]
  applied_preferences: PreferenceScore[]
  rejected_candidates: CandidateRejection[]
  fallback_policy_ref: FallbackPolicyRef | null
  warnings: SelectionWarning[]
}
```

A explicação usa códigos, referências e métricas sanitizadas; não contém segredo, lógica proprietária de fornecedor nem conteúdo da Task.

## Fallback explícito

```text
FallbackRequest {
  mode: DISABLED | EXPLICIT_ORDER | POLICY
  ordered_model_refs: ModelRef[]
  policy_ref: FallbackPolicyRef | null
  allowed_failure_categories: ProviderErrorCategory[]
  maximum_attempts: PositiveInteger
  allow_cross_provider: Boolean
}

FallbackPolicy {
  policy_ref: FallbackPolicyRef
  revision: FallbackPolicyRevision
  ordered_profiles: ModelProfile[]
  constraints: ModelConstraint[]
  allowed_failure_categories: ProviderErrorCategory[]
  maximum_attempts: PositiveInteger
  allow_cross_provider: Boolean
}
```

Fallback é proibido quando `mode = DISABLED`. Em `EXPLICIT_ORDER`, somente referências listadas e compatíveis podem ser usadas. Em `POLICY`, a política versionada deve estar nomeada e seus candidatos ficam materializados na `ModelSelection`; não existe busca ilimitada após a falha.

Fallback:

- só ocorre após outcome normalizado elegível da RFC 501;
- revalida cancelamento, orçamento restante, classificação, finalidade, status e disponibilidade;
- preserva `execution_id` e `correlation_id`, mas cria nova `ProviderInvocationId`;
- contabiliza todas as tentativas e custos;
- não pode cruzar Provider se `allow_cross_provider = false`;
- não pode reduzir requisito de visão, Tools, contexto, formato ou segurança;
- publica fato próprio quando selecionado;
- nunca substitui modelo em resposta a erro de conteúdo, autenticação ou política salvo se a lista de categorias permitir e a política continuar segura.

OpenRouter ou outro serviço roteador não transforma roteamento interno em fallback implícito. A rota permitida deve estar representada pelo binding/descriptor e obedecer à seleção congelada.

## Descontinuação e status

```text
ModelDeprecation {
  announced_at: Instant
  deprecated_at: Instant
  sunset_at: Instant | null
  successor_model_ref: ModelRef | null
  reason: DeprecationReason
  migration_guidance: SanitizedText | null
}
```

Transições válidas são:

```text
ACTIVE -> DEPRECATED -> DISABLED -> RETIRED
ACTIVE -> DISABLED
DEPRECATED -> ACTIVE
DISABLED -> ACTIVE | DEPRECATED
```

`RETIRED` é terminal. Reativação de `DEPRECATED` ou `DISABLED` exige nova revisão, causa e Event; não reescreve histórico. `DEPRECATED` continua elegível apenas quando política permite, com warning explícito. `DISABLED` não entra em nova seleção. `RETIRED` conserva metadata mínima e referências históricas, mas não possui binding invocável.

### Lifecycle do Provider e do adapter

`ProviderStatus` mapeia normativamente para o registro da RFC 501:

| `ProviderStatus` | Estado do adapter/registro | Novas invocações |
| --- | --- | --- |
| `ACTIVE` | adapter `ACTIVE`, binding resolvível | permitidas conforme modelo e política |
| `DEGRADED` | adapter `DEGRADED`, saúde e validade explícitas | permitidas somente se política aceitar degradação |
| `DISABLED` | adapter `DISABLED`, registro preservado para inspeção/reconciliação | proibidas |
| `RETIRED` | adapter `RETIRED`; credenciais e rota ativa removidas após drenar/reconciliar chamadas | proibidas permanentemente |

Ao entrar em `RETIRED`, nenhum binding desse Provider pode ser resolvido para nova chamada. Seleções ainda válidas falham na revalidação pré-transmissão e só usam fallback previamente materializado. O registro preserva descriptor e referências históricas para auditoria; remover o código físico do adapter é decisão operacional posterior e não pode eliminar inspeção de invocações, uso ou custo já confirmados.

Selection já emitida é revalidada antes da invocação. Se o modelo foi desabilitado ou retirado, o Runtime recebe falha explícita e só usa fallback materializado e permitido. `successor_model_ref` é orientação, não substituição automática. Executions longas podem fixar seleção até `valid_until`; depois desse instante devem resolver novamente.

## Eventos

Eventos seguem a [RFC 103](../100-kernel/103-event-system.md), pertencem à Execution sensível que realizou a operação e usam payload mínimo:

| Event | Fato confirmado | Campos específicos mínimos |
| --- | --- | --- |
| `ProviderRegistered` | revisão de Provider foi publicada | `provider_ref`, `revision`, `status` |
| `ModelRegistered` | revisão de modelo foi publicada | `model_ref`, `provider_ref`, `revision`, `status` |
| `ModelProfilePublished` | revisão de perfil foi publicada | `profile`, `revision` |
| `ModelPricingPublished` | revisão de preço passou a existir | `model_ref`, `pricing_revision`, `effective_from` |
| `ModelStatusChanged` | novo status foi confirmado | `model_ref`, `previous_status`, `new_status`, `revision` |
| `ModelDeprecated` | depreciação com prazo e orientação foi confirmada | `model_ref`, `deprecated_at`, `sunset_at`, `successor_model_ref` |
| `ModelResolved` | seleção explícita foi persistida | `selection_ref`, `primary_model_ref`, `fallback_model_refs`, versões |
| `ModelFallbackSelected` | próximo candidato explícito foi escolhido | `selection_ref`, `failed_model_ref`, `selected_model_ref`, `failure_category`, `attempt` |
| `ModelResolutionFailed` | nenhum candidato compatível foi encontrado | `request_id`, `reason_code`, `candidate_counts` |

Eventos administrativos continuam carregando `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e finalidade no contexto/payload mínimo apropriado. Não contêm binding, preço contratual secreto, conteúdo da Task ou dado de SDK.

## Fluxo normal

1. Runtime, Agent ou Orchestrator formula `ModelRequirements` com contexto sensível, capabilities, classificação, budget e fallback desejado.
2. O resolver valida autorização e fixa versões do catálogo, perfil, política, preços e disponibilidade.
3. Candidatos são filtrados por hard constraints antes de qualquer score.
4. Preferências do perfil ordenam apenas candidatos compatíveis.
5. O resolver materializa primário, fallbacks, revisões, validade e explicação.
6. O resolver materializa e persiste o snapshot imutável dos requirements autorizados junto da seleção, e `ModelResolved` é publicado.
7. O Runtime passa `selection_ref`, `approved_requirements_ref` e o snapshot à Provider API, que revalida integridade e constraints antes da transmissão.
8. Se houver falha elegível, o Runtime solicita `resolve_fallback`; o resolver revalida limites e escolhe somente o próximo candidato já autorizado.

## Fluxo de falha

- catálogo, perfil ou policy version inexistente falha sem usar “mais recente” silenciosamente;
- nenhum candidato após hard constraints retorna `NoCompatibleModel` com rejeições categóricas;
- custo desconhecido sob teto obrigatório torna o candidato incompatível;
- metadata contraditória ou binding ausente impede publicação/seleção;
- snapshot de disponibilidade expirado exige atualização ou falha explícita, não suposição de saúde;
- conflito de versão em mutação exige releitura e nova tentativa;
- tentativa de selecionar modelo `DISABLED` ou `RETIRED` falha antes do Provider;
- falha do Provider não altera descriptor nem status por si só; sinais agregados podem produzir novo snapshot ou operação administrativa auditada;
- falha de fallback preserva uso e custo das tentativas anteriores e nunca retorna sucesso inexistente.

## Fluxo de cancelamento

Resolução e fallback verificam o `CancellationSignalRef` antes de carregar catálogo, após filtrar candidatos e antes de persistir seleção. Ao cancelar:

- nenhuma nova seleção ou tentativa de fallback é iniciada;
- operação administrativa ainda não confirmada é abortada em limite seguro;
- revisão já confirmada não é revertida nem ocultada;
- seleção incompleta não é publicada como utilizável;
- trabalho temporário é descartado e metadados mínimos de auditoria são preservados;
- `ModelResolutionCancelled` é retornado ao Runtime, que governa o terminal da Execution.

Cancelamento de uma invocação já iniciada pertence à RFC 501. O catálogo não usa cancelamento para marcar modelo como indisponível.

## Segurança

- toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- manutenção do catálogo ocorre por Execution administrativa autorizada, sem bypass de bootstrap permanente;
- seleção respeita classificação, residência, região, Provider allow/deny, finalidade e política do Workspace;
- binding e IDs externos são opacos fora do adapter de Provider;
- catálogo não armazena credenciais, tokens, headers, payloads ou objetos de SDK;
- metadata, explicações e Events são sanitizados e não contêm conteúdo da Task;
- conhecer `model_ref`, `selection_ref` ou `provider_ref` não concede acesso;
- fallback revalida todos os escopos e nunca amplia permissão;
- preço confidencial pode ser representado por classe/referência autorizada, sem exposição a consumidores sem clearance;
- sinais de saúde são agregados e não podem carregar prompts, respostas ou identificadores sensíveis de chamadas.

## Observabilidade

Métricas incluem resoluções por perfil, candidatos antes/depois de constraints, razão de rejeição, modelo/Provider selecionados por referência interna controlada, custo estimado, latência da resolução, cache por versão, depreciações, seleções expiradas, fallbacks, ausência de candidato e divergência entre custo estimado e confirmado.

Traces ligam `request_id`, `selection_id`, `execution_id`, `correlation_id`, versões de catálogo/policy/perfil/preço e posterior `invocation_id`. Logs registram códigos de constraint, scores normalizados e referências; não registram Task, prompt, binding, credencial ou resposta. Métricas de modelos devem controlar cardinalidade e acesso, sobretudo quando nomes ou preços forem sensíveis.

Toda seleção deve ser explicável posteriormente a partir das revisões retidas e do snapshot de disponibilidade. Reprodutibilidade significa repetir a decisão sob os mesmos inputs e snapshots, não garantir resposta idêntica do modelo.

## Invariantes

- Provider e Model são conceitos distintos; modelo é resolvido por atributos públicos;
- Runtime não conhece SDK, binding concreto nem nome técnico proprietário;
- toda operação sensível possui os seis escopos requeridos;
- descriptor e revisão publicados são imutáveis;
- toda seleção referencia snapshot imutável dos requirements e constraints aprovados;
- hard constraints precedem score e nunca são relaxados por perfil;
- preço ausente não equivale a custo zero;
- seleção fixa versões, primário, fallback, explicação e validade;
- fallback é desabilitado ou explicitamente materializado e limitado;
- fallback nunca amplia Provider, classificação, finalidade, capacidade ou budget;
- todas as tentativas preservam e acumulam uso e custo;
- modelo desabilitado ou retirado não entra em nova invocação;
- Provider `RETIRED` corresponde a adapter `RETIRED`, sem binding ou nova invocação;
- depreciação não substitui modelo silenciosamente;
- metadata arbitrária não contorna campos tipados;
- eventos e telemetria não vazam binding, credencial ou conteúdo.

## Extensibilidade

Novos Providers e modelos entram por descriptors e bindings opacos. Novos perfis registram constraints e pesos versionados sem exigir alteração do Runtime. Nova capability exige campo público tipado, regra de compatibilidade e tradução correspondente na RFC 501 antes de participar da resolução.

Estratégias alternativas de ranking, custo e disponibilidade podem implementar portas compatíveis, desde que hard constraints, explicação, versionamento e determinismo sob snapshot sejam preservados. Plugins não podem adicionar metadata livre que altere segurança ou invocação fora desses contratos.

## Futuro

Benchmarks versionados, roteamento por residência, portfolios por Workspace, limites contratuais, modelos locais, fine-tunes, alias controlado e seleção baseada em avaliações poderão especializar o catálogo. Aprendizado online ou roteamento adaptativo só será aceito com snapshot explicável, limites rígidos e fallback explícito; decisão opaca que impeça auditoria não substitui este contrato.
