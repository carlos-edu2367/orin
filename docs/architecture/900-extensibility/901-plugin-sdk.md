# RFC 901 — Plugin SDK

**Estado:** Normativa para o contrato de extensão; implementação futura  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](../400-tools-resources/401-tool-runtime.md), [RFC 402 — Resource Manager](../400-tools-resources/402-resource-manager.md), [RFC 406 — Capabilities](../400-tools-resources/406-capabilities.md), [RFC 604 — Configuração](../600-platform-data/604-configuration.md), [RFC 702 — Segurança](../700-api-security/702-security.md), [RFC 803 — Observabilidade](../800-operations/803-observability.md)

## Objetivo

Definir o contrato de empacotamento, descoberta, registro, resolução, ativação, execução isolada, compatibilidade, desativação e observabilidade de plugins do AgentOS. Um plugin adiciona implementações atrás de portas públicas; ele não altera o Kernel, não cria uma segunda arquitetura de autorização e não recebe confiança implícita por estar instalado.

## Fora de escopo

- escolher linguagem, formato físico de pacote, registry remoto, sistema de assinatura, sandbox ou processo de distribuição;
- definir marketplace, cobrança, reputação, interface visual ou política comercial;
- permitir patching, monkey patching, reflexão irrestrita ou importação de módulos internos;
- definir contratos particulares de Tool, Resource, Provider, Skill ou MCP além de seus pontos públicos;
- executar instalação, atualização ou remoção no processo da API ou de uma `Execution` de usuário;
- fornecer código de backend, schema de banco, endpoint, CLI ou configuração executável.

## Responsabilidades e não responsabilidades

O Plugin SDK DEVE:

- exigir manifesto imutável por versão com identidade, integridade, compatibilidade, contribuições, permissões, isolamento, limites e lifecycle;
- separar descoberta de registro, registro de ativação e ativação de invocação;
- resolver contribuições apenas por contratos públicos e versões compatíveis;
- validar integridade, origem, policy e conflitos antes de tornar uma versão elegível;
- aplicar privilégio mínimo, isolamento e quotas por contribuição e invocação;
- preservar ownership, finalidade, correlação, causalidade e `Execution` em toda operação sensível;
- suportar desativação segura sem apagar histórico nem quebrar inspeção de Executions antigas;
- produzir Events, auditoria, métricas e traces sanitizados em cada mudança relevante.

O Plugin SDK NÃO DEVE:

- conceder acesso a banco, broker, outbox, filesystem do host, segredo, sessão, cookie, Runtime ou adapter concreto;
- permitir que um plugin registre estados de `Execution`, tipos de Event ou permissões arbitrários sem contrato proprietário explícito;
- interpretar metadata livre como autorização, roteamento ou comportamento executável;
- permitir dependência implícita, circular ou não versionada entre plugins;
- carregar pacote incompatível e tentar degradação silenciosa;
- assumir que contribuição instalada está autorizada para todo usuário, Workspace, Agent ou finalidade;
- descarregar uma versão enquanto houver invocação ativa sem protocolo de drain ou isolamento que preserve essa invocação.

## Arquitetura e fronteiras

```text
Plugin Source -> Discovery -> Package Verifier -> Plugin Registry
                                                    |
                                                    v
                                             Compatibility Gate
                                                    |
                                      +-------------+-------------+
                                      |                           |
                               Activation Policy            Runtime Resolver
                                      |                           |
                                      v                           v
                                Plugin Host                porta pública dona
                                isolado                    da contribuição
```

`PluginDiscovery` apenas encontra candidatos e coleta evidência. `PluginRegistry` é a fonte durável de identidade, versões e estados administrativos. `PluginHost` aplica isolamento operacional. O domínio proprietário continua validando a contribuição: Tool pelo Tool Runtime, Resource pelo Resource Manager, Provider pela porta de Provider e assim por diante.

Um plugin não é uma Tool, Capability ou Skill. Ele é o pacote que pode contribuir com uma ou mais implementações desses contratos. A origem comum não permite que uma contribuição chame outra por caminho privado; composição continua usando as portas arquiteturais existentes.

## Manifesto e dados

```text
PluginRef {
  plugin_id: PluginId
  version: SemanticVersion
}

PluginManifest {
  manifest_version: ManifestVersion
  plugin_ref: PluginRef
  name: PluginName
  publisher: PublisherIdentity
  description: Text
  license_ref: LicenseRef | null
  package_integrity: IntegrityDescriptor
  host_compatibility: VersionRange
  sdk_compatibility: VersionRange
  contributions: PluginContribution[]
  dependencies: PluginDependency[]
  requested_permissions: PluginPermissionRequest[]
  isolation_profile: IsolationProfile
  resource_limits: PluginResourceLimits
  configuration_schema_ref: TypeSchemaRef | null
  lifecycle_hooks: PluginLifecycleHook[]
}

PluginContribution =
  | ToolContribution { public_ref: ToolRef, contract_version: Version }
  | CapabilityContribution { public_ref: CapabilityRef, contract_version: Version }
  | ResourceAdapterContribution { public_ref: ResourceAdapterRef, contract_version: Version }
  | ProviderAdapterContribution { public_ref: ProviderAdapterRef, contract_version: Version }
  | SkillContribution { public_ref: SkillRef, contract_version: Version }
  | ObservabilityExporterContribution { public_ref: ExporterRef, contract_version: Version }

PluginDependency {
  plugin_id: PluginId
  accepted_versions: VersionRange
  required_contributions: PublicContributionRef[]
  optional: Boolean
}
```

O manifesto é declarativo, não contém segredo, código executável embutido, endpoint privilegiado ou instrução capaz de ampliar policy. `requested_permissions` é limite superior solicitado, nunca concessão. Contribuição desconhecida para a versão de manifesto é rejeitada; campos opcionais desconhecidos podem ser preservados apenas como metadata inerte.

```text
PluginAdministrativeContext {
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

PluginRuntimeContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  contribution_ref: PublicContributionRef
  invocation_id: InvocationId
}

RegisteredPluginVersion {
  plugin_ref: PluginRef
  manifest_digest: Digest
  package_digest: Digest
  trust_tier: PluginTrustTier
  state: DISCOVERED | VALIDATING | REGISTERED | ACTIVE | DRAINING |
    DISABLED | QUARANTINED | REJECTED | RETIRED
  state_version: Version
  effective_grants: PluginGrant[]
  compatibility_evidence_ref: ArtifactReference | null
  installed_at: Instant
  activated_at: Instant | null
  disabled_at: Instant | null
}
```

`workspace_id` nulo identifica instalação global explicitamente permitida, nunca wildcard. Operação administrativa fora de `Execution` exige correlação administrativa e usuário responsável. Invocação de contribuição sempre ocorre em uma `Execution` e carrega o contexto sensível completo.

## Descoberta, registro e resolução

Descoberta é read-only: encontra pacote, lê manifesto e calcula evidência sem importar ou executar o payload. Fonte local, registry ou bundle embutido são adapters de descoberta, não autoridades de confiança. Registro só aceita conteúdo endereçado por digest e mantém a versão publicada imutável.

## Contratos tipados

```text
interface PluginDiscovery {
  discover(query: DiscoverPlugins) -> PluginCandidate[]
  inspect(candidate_ref: PluginCandidateRef) -> UntrustedPluginManifest

  post: nenhum payload do candidato foi executado
}

interface PluginRegistry {
  register(request: RegisterPluginVersion) -> PluginRegistrationReceipt
  activate(request: ActivatePluginVersion) -> PluginLifecycleReceipt
  begin_drain(request: DrainPluginVersion) -> PluginLifecycleReceipt
  disable(request: DisablePluginVersion) -> PluginLifecycleReceipt
  quarantine(request: QuarantinePluginVersion) -> PluginLifecycleReceipt
  retire(request: RetirePluginVersion) -> PluginLifecycleReceipt
  resolve(request: ResolvePluginContribution) -> PluginResolution
  inspect(query: InspectPluginVersion) -> AuthorizedPluginView

  invariant: PluginRef publicado e digests associados são imutáveis
}

RegisterPluginVersion {
  operation_id: PluginOperationId
  context: PluginAdministrativeContext
  candidate_ref: PluginCandidateRef
  expected_manifest_digest: Digest
  expected_package_digest: Digest
  requested_activation_scope: PluginActivationScope
  idempotency_key: IdempotencyKey
}

ResolvePluginContribution {
  context: PluginRuntimeContext
  public_ref: PublicContributionRef
  required_contract_version: VersionRange
  configuration_snapshot_ref: ConfigurationSnapshotRef
}

PluginResolution =
  | PluginResolved {
      plugin_ref: PluginRef
      contribution_ref: PublicContributionRef
      binding_plan_ref: PluginBindingPlanRef
      effective_grants: PluginGrant[]
    }
  | PluginUnavailable { reason: DISABLED | DRAINING | QUARANTINED | RETIRED }
  | PluginIncompatible { reason: CompatibilityFailure }
  | PluginResolutionDenied { reason: AuthorizationDenial }
```

O mesmo comando idempotente e payload retorna o mesmo receipt; a mesma chave com payload divergente é rejeitada. Resolução fixa versão exata para a duração da invocação e nunca seleciona automaticamente uma versão major incompatível.

```text
interface PluginHost {
  bind(request: BindPluginContribution) -> PluginBindingResult
  invoke(request: InvokePluginBinding) -> PluginInvocationOutcome
  cancel(request: CancelPluginInvocation) -> PluginCancellationReceipt
  heartbeat(request: RecordPluginHeartbeat) -> PluginHeartbeatReceipt
  cleanup(request: CleanupPluginBinding) -> PluginCleanupReceipt

  pre: binding_plan_ref veio de PluginResolved vigente para a mesma Execution
  pre: contexto, contrato, grants, limites e cancelamento foram revalidados
  post: toda invocação chega a terminal tipado ou a UNKNOWN reconciliável
  invariant: cleanup operacional não apaga receipt, terminal, Event ou evidência durável
}

BindPluginContribution {
  binding_id: PluginBindingId
  context: PluginRuntimeContext
  plugin_ref: PluginRef
  contribution_ref: PublicContributionRef
  binding_plan_ref: PluginBindingPlanRef
  effective_grants: PluginGrant[]
  isolation_profile: IsolationProfileRef
  limits: PluginInvocationLimits
  cancellation: CancellationSignalRef
  idempotency_key: IdempotencyKey
}

PluginBindingPlanRef {
  resolution_id: PluginResolutionId
  plugin_ref: PluginRef
  contribution_ref: PublicContributionRef
  plugin_version: SemanticVersion
  package_digest: Digest
  issued_at: Instant
  valid_until: Instant
  integrity_ref: IntegrityRef
}

PluginInvocationLimits {
  timeout: Duration
  cancellation_grace: Duration
  maximum_cpu_time: Duration
  maximum_memory: ByteSize
  maximum_output_bytes: ByteSize
  maximum_subprocesses: NonNegativeInteger
}

PluginBindingResult =
  | PluginBound { binding: PluginBinding }
  | PluginBindingAlreadyExists { binding: PluginBinding }
  | PluginBindingDenied { reason: AuthorizationDenial }
  | PluginBindingFailed { error: PluginHostError }

PluginBinding {
  binding_id: PluginBindingId
  binding_ref: IsolatedBindingRef
  plugin_ref: PluginRef
  contribution_ref: PublicContributionRef
  execution_id: ExecutionId
  contract_version: SemanticVersion
  state: STARTING | ACTIVE | CANCELLING | TERMINAL | CLEANED
  started_at: Instant
  heartbeat_deadline_at: Instant
}

InvokePluginBinding {
  invocation_id: PluginInvocationId
  context: PluginRuntimeContext
  binding_ref: IsolatedBindingRef
  operation: PublicContributionOperation
  input_ref: InputReference
  expected_contract_version: SemanticVersion
  deadline_at: Instant
  cancellation: CancellationSignalRef
  idempotency_key: IdempotencyKey
}

PublicContributionOperation {
  operation_name: PublicOperationName
  contract_version: SemanticVersion
  input_schema_ref: TypeSchemaRef
  output_schema_ref: TypeSchemaRef
}

PluginInvocationOutcome =
  | PluginInvocationSucceeded { invocation_id: PluginInvocationId, result_ref: ResultReference, usage: ResourceUsage }
  | PluginInvocationFailed { invocation_id: PluginInvocationId, error: PluginHostError, effect_state: NOT_APPLIED | APPLIED | UNKNOWN, usage: ResourceUsage }
  | PluginInvocationCancelled { invocation_id: PluginInvocationId, reason: CancellationReason, effect_state: NOT_APPLIED | APPLIED | UNKNOWN, usage: ResourceUsage }

CancelPluginInvocation {
  request_id: PluginCancellationRequestId
  context: PluginRuntimeContext
  binding_ref: IsolatedBindingRef
  invocation_id: PluginInvocationId
  reason: CancellationReason
  deadline_at: Instant
  idempotency_key: IdempotencyKey
}

PluginCancellationReceipt =
  | PluginCancellationAccepted { invocation_id: PluginInvocationId, acknowledged_at: Instant }
  | PluginInvocationAlreadyTerminal { invocation_id: PluginInvocationId, terminal: SUCCEEDED | FAILED | CANCELLED | UNKNOWN }
  | PluginCancellationForced { invocation_id: PluginInvocationId, effect_state: UNKNOWN, terminated_at: Instant }
  | PluginCancellationRejected { reason: RejectionReason }

RecordPluginHeartbeat {
  binding_ref: IsolatedBindingRef
  invocation_id: PluginInvocationId | null
  sequence: PositiveInteger
  observed_at: Instant
  health: HEALTHY | DEGRADED | TERMINATING
  usage: ResourceUsage
}

PluginHeartbeatReceipt =
  | PluginHeartbeatRecorded { next_deadline_at: Instant }
  | PluginHeartbeatDuplicate { next_deadline_at: Instant }
  | PluginHeartbeatRejected { reason: RejectionReason }

CleanupPluginBinding {
  request_id: PluginCleanupRequestId
  context: PluginRuntimeContext
  binding_ref: IsolatedBindingRef
  expected_terminal: SUCCEEDED | FAILED | CANCELLED | UNKNOWN
  force_after: Instant | null
  idempotency_key: IdempotencyKey
}

PluginCleanupReceipt =
  | PluginBindingCleaned { binding_ref: IsolatedBindingRef, cleaned_at: Instant }
  | PluginBindingAlreadyCleaned { binding_ref: IsolatedBindingRef, cleaned_at: Instant }
  | PluginCleanupDeferred { active_invocation_ids: PluginInvocationId[], retry_after: Duration }
  | PluginCleanupFailed { error: PluginHostError }

PluginHostError {
  category: STARTUP | ISOLATION | CONTRACT | TIMEOUT | CRASH | QUOTA | IPC | CLEANUP | UNKNOWN
  code: PublicErrorCode
  message: SanitizedText
  retryability: NEVER | SAFE | POLICY_DEPENDENT
  diagnostic_ref: InternalDiagnosticRef | null
}
```

`bind` materializa o plano resolvido em isolamento ativo; `invoke` usa apenas operação pública e referências autorizadas; `cancel` é cooperativo até o deadline e explicita encerramento forçado; `heartbeat` é monotônico por `sequence`; `cleanup` só libera processo, handles, workspace temporário e leases após terminal ou política de força. Nenhuma dessas operações substitui o contrato proprietário da contribuição nem permite callable arbitrário.

## Versionamento e compatibilidade

O SDK distingue quatro versões: formato do manifesto, API do SDK, contrato público da contribuição e versão do pacote. Compatibilidade do host ou SDK não implica compatibilidade do contrato contribuído. Versão publicada é imutável; mudança de código, manifesto, schema, permissão, dependência ou digest exige nova versão.

Compatibilidade é avaliada antes da ativação e novamente na resolução quando policy, dependência ou host mudou. Uma mudança é incompatível quando altera sem transição explícita:

- schema de entrada ou saída aceito por consumidores existentes;
- semântica de erro, idempotência, cancelamento ou efeito;
- permissões, Resources, destinos de rede ou classificação de dados;
- eventos públicos ou interpretação de metadata;
- requisito de host, SDK ou dependência fora da faixa declarada.

```text
interface PluginCompatibilityGate {
  evaluate(request: EvaluatePluginCompatibility) -> CompatibilityReport
}

EvaluatePluginCompatibility {
  operation_id: PluginOperationId
  context: PluginAdministrativeContext
  plugin_ref: PluginRef
  host_version: SemanticVersion
  sdk_version: SemanticVersion
  installed_dependencies: ResolvedPluginDependency[]
  contract_catalog_snapshot: ContractCatalogSnapshotRef
}

CompatibilityReport {
  outcome: COMPATIBLE | INCOMPATIBLE | INDETERMINATE
  checked_constraints: CompatibilityCheck[]
  evidence_ref: ArtifactReference
}
```

`INDETERMINATE` falha fechado para ativação. Testes contratuais e attestations são evidência, não substituem validação de autorização em runtime.

## Permissões, isolamento e configuração

A permissão efetiva é a interseção de owner, Workspace, Agent, Execution, `purpose`, manifesto, grants administrativos, contrato da contribuição, policy vigente e limites do Resource. Instalação não concede uso; ativação não concede acesso a dados; resolver uma contribuição não transfere grants para outra.

Perfis de isolamento declaram, no mínimo, separação de processo ou equivalente, filesystem visível, política de rede, ambiente permitido, CPU, memória, tempo, subprocessos e canais IPC. O host expõe apenas portas tipadas e handles efêmeros. Falha ou escape do sandbox põe a versão em quarentena e encerra novas resoluções.

Configuração usa schema versionado e snapshot da RFC 604. Segredos aparecem apenas como `SecretReference`, são resolvidos pela borda proprietária no último momento e chegam por handle efêmero quando necessário. Manifesto, logs, Events, erro, checkpoint e Artifact de diagnóstico não contêm material secreto.

## Lifecycle, desativação e remoção

Transições válidas são:

| Origem | Destino | Condição |
| --- | --- | --- |
| `DISCOVERED` | `VALIDATING` | digests e contexto aceitos |
| `VALIDATING` | `REGISTERED` | integridade, policy e compatibilidade confirmadas |
| `VALIDATING` | `REJECTED` | validação terminal negativa |
| `REGISTERED` | `ACTIVE` | grants e activation scope confirmados |
| `ACTIVE` | `DRAINING` | desativação graciosa aceita |
| `ACTIVE` | `QUARANTINED` | risco de segurança ou integridade confirmado |
| `DRAINING` | `DISABLED` | nenhuma invocação ativa ou deadline encerrado |
| `QUARANTINED` | `DISABLED` | contenção e evidência preservadas |
| `DISABLED` | `ACTIVE` | reativação explícita com revalidação completa |
| `DISABLED` | `RETIRED` | retenção e dependências verificadas |

`DRAINING` recusa novas resoluções e permite que bindings já fixados terminem sob os mesmos grants e limites, salvo revogação de segurança. `QUARANTINED` recusa novas resoluções e sinaliza cancelamento às invocações ativas; efeitos incertos são reconciliados pelo domínio proprietário. `RETIRED` impede execução e preserva manifesto, digests, Events e bindings históricos necessários à auditoria. Remoção física nunca antecede retenção, legal hold, investigação ou referência histórica.

```text
PluginLifecycleCommand {
  operation_id: PluginOperationId
  context: PluginAdministrativeContext
  plugin_ref: PluginRef
  expected_state_version: Version
  reason: PluginLifecycleReason
  drain_deadline: Instant | null
  idempotency_key: IdempotencyKey
}

ActivatePluginVersion extends PluginLifecycleCommand {
  expected_state: REGISTERED | DISABLED
  target_state: ACTIVE
  activation_scope: PluginActivationScope
  approved_grants: PluginGrant[]
  drain_deadline: null
}

DrainPluginVersion extends PluginLifecycleCommand {
  expected_state: ACTIVE
  target_state: DRAINING
  drain_deadline: Instant
}

DisablePluginVersion extends PluginLifecycleCommand {
  expected_state: DRAINING | QUARANTINED
  target_state: DISABLED
  active_binding_policy: REQUIRE_NONE | FORCE_CLEANUP_AFTER_DEADLINE
}

QuarantinePluginVersion extends PluginLifecycleCommand {
  expected_state: ACTIVE
  target_state: QUARANTINED
  evidence_ref: SecurityEvidenceRef
  active_invocation_policy: REQUEST_CANCEL
  drain_deadline: null
}

RetirePluginVersion extends PluginLifecycleCommand {
  expected_state: DISABLED
  target_state: RETIRED
  retention_verification_ref: RetentionVerificationRef
  active_binding_policy: REQUIRE_NONE
  drain_deadline: null
}

PluginLifecycleReceipt =
  | PluginLifecycleChanged { state: PluginLifecycleState, resulting_version: Version }
  | PluginLifecycleAlreadyApplied { state: PluginLifecycleState, resulting_version: Version }
  | PluginLifecycleConflict { state: PluginLifecycleState, current_version: Version }
  | PluginLifecycleRejected { reason: PluginLifecycleRejection }
  | PluginLifecycleIndeterminate { reconciliation_ref: ReconciliationRef }
```

Os cinco tipos são especializações fechadas de `PluginLifecycleCommand`, não aliases sem restrição. Eles herdam contexto administrativo, identidade, versão esperada, razão e idempotência, e fixam origem/destino permitidos pela tabela. `DrainPluginVersion` exige deadline; ativação fixa escopo e Grants; quarentena exige evidência e solicita cancelamento; desabilitação e retirada declaram política para bindings ativos. O Registry rejeita qualquer especialização cujo estado, deadline ou campos discriminadores contradigam a transição nomeada.

## Fluxo normal

1. Discovery encontra candidato e lê manifesto como dado não confiável, sem executar payload.
2. Registro verifica digests, identidade, assinatura quando exigida, schema, dependências, permissões e compatibilidade.
3. Evidência é persistida; `PluginRegistered` confirma versão imutável em `REGISTERED`.
4. Ativação aprova escopo e grants mínimos e move a versão para `ACTIVE`.
5. Um domínio solicita contribuição por referência e faixa contratual; Registry fixa plugin e versão exatos.
6. `PluginHost.bind` cria binding isolado e entrega somente portas e handles autorizados.
7. O domínio proprietário valida cada operação, chama `PluginHost.invoke`, registra o outcome tipado e solicita `cleanup` no terminal.

## Falhas, timeout e recuperação

Falha de descoberta não muda registro. Digest, assinatura, schema ou dependência inválidos levam a `REJECTED`; incompatibilidade indeterminada não ativa. Crash, timeout, quota ou resposta malformada do plugin viram erro normalizado do contrato proprietário, sem expor stack, caminho ou segredo.

O host registra heartbeat monotônico e terminal do binding pelos contratos tipados acima. Heartbeat ausente após `heartbeat_deadline_at` dispara contenção, consulta de terminal e, quando necessário, cancelamento seguido de cleanup; não prova falha nem ausência de efeito por si só. Após crash, a fonte durável decide se a invocação pode ser repetida: efeito confirmado não é repetido; efeito `UNKNOWN` exige reconciliação; operação sem idempotência falha sem retry automático. Falha do Registry impede novas resoluções, mas binding já isolado pode terminar somente se policy e grants continuarem localmente verificáveis. Falha de observabilidade crítica segue as classes da RFC 803 e nunca autoriza execução cega.

## Cancelamento

Cancelamento pertence à `Execution` ou ao contrato público invocado, não ao pacote. O host propaga o sinal ao binding, bloqueia novos efeitos, aplica deadline e encerra o processo isolado quando necessário. Encerramento forçado não prova ausência de efeito externo; o domínio registra `UNKNOWN` e reconcilia antes de retry.

Drain administrativo não cancela por padrão Executions existentes. Quarentena ou revogação de grant pode cancelá-las quando policy exigir, sempre por comando autorizado e observável. Resultado tardio permanece auditável e não reabre `Execution` terminal.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `PluginDiscovered` | candidato e digests foram observados sem execução |
| `PluginValidationFinished` | validação terminou com outcome e evidência |
| `PluginRegistered` | versão imutável entrou no Registry |
| `PluginActivated` | versão tornou-se elegível no escopo aprovado |
| `PluginDrainStarted` | novas resoluções foram bloqueadas |
| `PluginDisabled` | versão deixou de aceitar invocações |
| `PluginQuarantined` | contenção de segurança foi confirmada |
| `PluginRetired` | execução futura foi encerrada preservando histórico |
| `PluginBindingStarted` | binding isolado iniciou para contribuição e invocação |
| `PluginBindingFinished` | binding terminou com outcome categórico |

Eventos administrativos podem usar `execution_id` nulo com correlação administrativa. Eventos de binding usam `execution_id` não nulo e sequência da RFC 103. Payloads contêm IDs, refs, digests, versões, estado, duração e razões sanitizadas; manifesto integral, configuração, argumentos e outputs ficam por referência autorizada.

## Segurança

- manifesto e pacote são conteúdo não confiável até validação completa;
- digest verificado identifica os bytes registrados e impede substituição silenciosa;
- assinatura ou publisher conhecido complementa, mas não substitui, sandbox e autorização;
- contribuições falham fechado diante de grant, versão, schema ou dependência ausente;
- rede, filesystem, subprocessos, ambiente e IPC são negados por padrão e liberados granularmente;
- plugins não recebem credencial bruta, Context integral, Memory privada ou conteúdo de Artifact sem necessidade e grant;
- saída do plugin é não confiável e revalidada antes de virar argumento, contexto, Event ou efeito;
- operações administrativas críticas exigem auditoria precommit e não podem ser disparadas por texto de conteúdo externo;
- isolamento é aplicado entre plugins, versões, usuários, Workspaces e invocações.

## Observabilidade

Logs e traces correlacionam `plugin_id`, versão, digest abreviado seguro, contribuição, binding, `execution_id`, `correlation_id`, estado e outcome. Métricas incluem descoberta, validação, ativação, resolução, latência, crashes, timeouts, quotas, recusas, incompatibilidades, drains, quarentenas e consumo por trust tier. Nome de usuário, argumento, segredo, path e conteúdo não são labels.

O sistema deve explicar qual versão foi resolvida, quais constraints foram avaliadas, quais grants efetivos foram aplicados e por que uma ativação ou invocação foi recusada. Diagnóstico volumoso ou sensível vira Artifact classificado e autorizado.

## Invariantes

- toda versão publicada de plugin e seu manifesto são imutáveis e identificados por digest;
- descoberta nunca executa payload;
- instalação, ativação e invocação são decisões separadas;
- plugin só contribui atrás de porta pública e não altera o Kernel;
- contribuição de plugin recebe a mesma validação que implementação nativa;
- invocação sensível pertence a uma `Execution` e usa contexto completo;
- permissão efetiva nunca excede a interseção das políticas aplicáveis;
- versão incompatível ou indeterminada falha fechado;
- desativação não apaga histórico, Events, receipts ou referências necessárias;
- cancelamento e crash não transformam efeito incerto em ausência de efeito;
- metadata livre nunca controla autorização ou execução.

## Extensibilidade

Novos tipos de contribuição exigem contrato público proprietário, versionamento, autorização, isolamento, eventos, cancelamento e testes de compatibilidade antes de integrar o manifesto. Novos discovery sources, verificadores, sandboxes e registries implementam as portas desta RFC sem alterar a semântica de lifecycle.

Campos adicionais de manifesto são permitidos somente como extensões namespaced e inertes até RFC reconhecê-los. Nenhuma extensão pode criar acesso genérico ao container, registrar callable arbitrário ou contornar Tool Runtime, Resource Manager, Provider Port, Scheduler ou Kernel.

## Futuro

Assinaturas com transparência, attestations de build, SBOM, marketplace, reputação, sandbox remoto, aprovação organizacional e rollout progressivo poderão especializar esta RFC. A adoção deve preservar digests imutáveis, compatibilidade explícita, privilégio mínimo, isolamento por invocação, auditabilidade, drain e desativação recuperável.
