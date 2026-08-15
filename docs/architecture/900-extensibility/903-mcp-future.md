# RFC 903 — MCP: posição futura e critérios de adoção

**Estado:** Parcialmente adotada — v1 expõe somente tools; ver [docs/MCP.md](../../MCP.md) e "Estado da implementação" abaixo  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 401 — Tool Runtime](../400-tools-resources/401-tool-runtime.md), [RFC 402 — Resource Manager](../400-tools-resources/402-resource-manager.md), [RFC 501 — Provider API](../500-providers-models/501-provider-api.md), [RFC 602 — Artifact Storage](../600-platform-data/602-artifact-storage.md), [RFC 702 — Segurança](../700-api-security/702-security.md), [RFC 803 — Observabilidade](../800-operations/803-observability.md), [RFC 901 — Plugin SDK](901-plugin-sdk.md)

## Objetivo

Registrar a posição do AgentOS sobre uma integração futura com Model Context Protocol (MCP): MCP pode ser adotado como protocolo de interoperabilidade na borda, por adapters versionados e limitados, mas não como modelo de domínio, barramento interno ou fonte de autorização. Esta RFC define portas, traduções, limites de segurança, lifecycle, falhas, cancelamento e critérios objetivos que devem ser satisfeitos antes de habilitar a integração sem quebrar os contratos atuais.

## Decisão e estado atual

O lançamento inicial NÃO depende de MCP e não expõe MCP como caminho alternativo para executar trabalho. Nenhuma RFC vigente precisa de MCP para cumprir seu contrato. Uma adoção futura será incremental, opt-in e reversível.

Quando adotado, MCP ficará atrás de `McpGatewayAdapter`. O adapter traduz descoberta e invocações remotas para contratos públicos do AgentOS. Internamente, continuam canônicos:

- `Execution` para lifecycle e ownership do trabalho;
- Tool Runtime para operação atômica;
- Resource Manager para recursos e leases;
- Context Manager para seleção de contexto;
- Artifact Storage para conteúdo durável;
- Event System para fatos internos;
- Security Policy para identidade, autorização, secrets e auditoria.

## Estado da implementação

Esta seção registra o que da posição normativa acima já tem uma implementação
pragmática (`src/agentos/mcp/`, documentada em [docs/MCP.md](../../MCP.md))
e o que permanece adiado.

**Atendido:**

- opt-in explícito por servidor: nenhum servidor conecta sem `approve()`
  explícito do usuário (`McpServerState.PENDING_APPROVAL → ACTIVE`);
- descoberta não cria grant nem Tool automaticamente: o schema de cada tool
  remota é saneado (`agentos/mcp/sanitize.py`) antes de virar `ToolDefinition`;
- servidor, descriptor e resultado remoto são tratados como não confiáveis:
  nome, schema e conteúdo passam por validação e limite antes de chegar ao
  modelo;
- credencial como referência cifrada: um segredo nunca é argumento de tool
  nem aparece em resposta pública (`ProviderSecretCipher`, a mesma usada para
  credenciais de provider);
- egress controlado: `stdio` usa allowlist de launcher e ambiente mínimo;
  `http` reusa a política de rede pública do `fetch_url` e a reaplica a cada
  chamada;
- cancelamento e fail-closed: uma falha de conexão nunca ativa parcialmente
  um servidor, e uma falha de servidor configurado nunca bloqueia o turno;
- eventos auditáveis: aprovação, teste e remoção passam pelas mesmas rotas
  versionadas e autenticadas do restante do gateway.

**Adiado deliberadamente** (não implementado nesta versão):

- `binding_version` por tool — hoje a identidade é o par (servidor, nome da
  tool); mudança de schema reescreve o cache sem versionamento explícito;
- quarentena automática por violação repetida de schema;
- reconciliação de efeito `UNKNOWN` após cancelamento de uma chamada de tool
  em andamento;
- resources e prompts do MCP — exigem uma porta local proprietária própria
  (ver "Mapeamento para contratos atuais" abaixo) que ainda não existe;
- IP pinning na sessão HTTP — o guard de SSRF revalida a cada chamada, mas
  ainda há uma corrida estreita contra a resolução DNS interna do `httpx`
  (ver docs/MCP.md, "The HTTP network policy").

## Fora de escopo

- escolher versão concreta da especificação MCP, SDK, transporte, implementação de servidor ou cliente;
- habilitar MCP no lançamento inicial;
- definir endpoint, deployment, configuração executável, banco ou código de adapter;
- aceitar remote code execution, plugins arbitrários ou scripts enviados por servidor;
- federar identidade, autorização, Event log, Memory ou fonte de verdade pelo protocolo;
- mapear toda funcionalidade MCP futura antecipadamente;
- garantir compatibilidade com servidores que não atendam aos critérios desta RFC.

## Responsabilidades e não responsabilidades

Uma integração MCP futura DEVE:

- negociar versão e capabilities explicitamente e fixá-las por sessão;
- registrar servidor por identidade interna, endpoint normalizado, trust policy e versão de binding;
- mapear cada operação remota para uma porta pública e um contrato local versionado;
- revalidar autorização, schema, limites, ownership e finalidade em cada chamada;
- tratar servidor, descriptors, prompts, recursos e resultados como não confiáveis;
- manter credenciais em handles efêmeros e dados volumosos por ArtifactReference;
- propagar deadline e cancelamento quando suportado e reconciliar efeitos incertos;
- preservar Events, auditoria, métricas e traces do AgentOS independentemente da telemetria remota;
- falhar fechado quando negociação, identidade, integridade ou tradução for incompatível.

Uma integração MCP NÃO DEVE:

- permitir que servidor MCP crie, conclua, pause ou cancele `Execution` diretamente;
- interpretar capability anunciada como grant ou aprovação;
- publicar mensagem remota diretamente como Event interno;
- injetar prompt/template remoto como instrução privilegiada ou policy;
- expor Context integral, Memory, cookie, PAT, chave ou filesystem por conveniência;
- aceitar URI, redirect, schema ou nome remoto sem normalização e validação;
- usar metadata aberta para contornar tipos, filtros, limites ou autorização;
- introduzir dependência obrigatória de MCP nas interfaces públicas existentes.

## Arquitetura e portas

```text
AgentOS Domain Port
  Tool Runtime | Resource Manager | Context Manager | Artifact Manager
                         |
                         v
                 McpGatewayAdapter
                 + Translation Registry
                 + Policy Enforcement
                 + Session Manager
                 + Transport Port
                         |
                         v
                    MCP Server
```

`McpGatewayAdapter` é uma coleção de adapters de borda, não um super-runtime. A porta dona valida a operação antes e depois da tradução. Um Tool remoto aparece ao Tool Registry como implementação de `AtomicTool`; um Resource remoto aparece por adapter especializado do Resource Manager. Conteúdo remoto destinado ao Context passa pelo Context Manager. Blob ou resultado durável passa pelo Artifact Manager.

## Contratos tipados

```text
interface McpTransportPort {
  negotiate(request: McpNegotiationRequest) -> McpNegotiationResult
  request(request: McpTransportRequest) -> McpTransportOutcome
  request_cancel(request: McpCancelRequest) -> McpCancelReceipt
  close(request: McpCloseSession) -> McpCloseReceipt

  invariant: transporte não decide autorização nem estado de domínio
}

interface McpTranslationRegistry {
  resolve(request: ResolveMcpBinding) -> McpBindingResolution
  validate_input(request: ValidateMcpInput) -> ValidationResult
  validate_output(request: ValidateMcpOutput) -> ValidationResult

  invariant: toda tradução aponta para contrato público local e versão exata
}

interface McpGatewayAdapter {
  open_session(request: OpenMcpSession) -> McpSessionReceipt
  discover(request: DiscoverMcpCapabilities) -> McpDiscoveryReceipt
  invoke_tool(request: InvokeMcpTool) -> ToolInvocationOutcome
  close_session(request: CloseMcpSession) -> McpCloseReceipt

  pre: binding, contexto, policy e negociação são vigentes
  post: outcome remoto foi traduzido e validado antes de chegar ao domínio
}
```

Essas portas não substituem `AtomicTool`, Resource ports ou `ExecutionControl`. Elas encapsulam protocolo e tradução para que remoção de MCP não altere consumidores de domínio. O Gateway não oferece uma operação genérica `read_resource`: uma superfície MCP de resource só pode implementar uma porta especializada já pertencente ao AgentOS, depois de `ResourceManager.acquire` e `ResourceManager.authorize` produzirem `AuthorizedResourceHandle` para a operação. Sem esse encaixe, a superfície remota permanece indisponível.

## Dados, identidade e contexto sensível

```text
McpServerRef {
  server_id: McpServerId
  binding_version: Version
}

McpServerDescriptor {
  server_ref: McpServerRef
  endpoint_ref: NormalizedEndpointRef
  transport_kind: McpTransportKind
  trust_policy_ref: TrustPolicyRef
  credential_ref: SecretReference | null
  allowed_protocol_versions: VersionRange
  allowed_capabilities: McpCapabilityKind[]
  network_policy_ref: NetworkPolicyRef
  data_policy_ref: DataPolicyRef
  status: DISABLED | VALIDATING | ACTIVE | DRAINING | QUARANTINED
}

McpOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  server_ref: McpServerRef
}

McpAdministrativeContext {
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

`server_id` é identidade interna opaca e não é inferido do hostname. Mudança de endpoint, transport, trust policy, credential binding ou conjunto permitido cria nova versão de binding. `workspace_id` nulo só é válido para servidor global explicitamente administrado; nunca significa acesso a todos os Workspaces.

```text
McpSession {
  session_id: McpSessionId
  server_ref: McpServerRef
  context: McpOperationContext
  negotiated_protocol_version: ProtocolVersion
  negotiated_capabilities: McpCapability[]
  state: NEGOTIATING | ACTIVE | DRAINING | CLOSED | FAILED | QUARANTINED
  state_version: Version
  credential_lease_ref: CredentialLeaseRef | null
  opened_at: Instant
  expires_at: Instant
  last_activity_at: Instant
}

McpBinding {
  binding_id: McpBindingId
  binding_version: Version
  user_id: UserId
  workspace_id: WorkspaceId | null
  server_ref: McpServerRef
  remote_capability_ref: OpaqueRemoteCapabilityRef
  local_contract_ref: PublicContractRef
  local_contract_version: Version
  remote_schema_digest: Digest
  translation_policy_ref: TranslationPolicyRef
  required_permissions: PermissionRequirement[]
  allowed_purposes: Purpose[]
  limits: McpInvocationLimits
  state: DRAFT | VALIDATING | ACTIVE | SUSPENDED | DISABLED | QUARANTINED
  state_version: Version
  validation_evidence_ref: McpBindingValidationEvidenceRef | null
  validation_policy_snapshot_ref: ValidationPolicySnapshotRef
  validated_at: Instant | null
  revalidate_after: Instant | null
  activated_at: Instant | null
  suspended_at: Instant | null
  disabled_at: Instant | null
}

McpBindingState = DRAFT | VALIDATING | ACTIVE | SUSPENDED | DISABLED | QUARANTINED
```

Sessão é operacional e limitada; não é owner nem fonte durável de verdade. Capabilities negociadas não sobrevivem automaticamente à reconexão. Binding é entidade administrativa persistente, versionada e pertencente a `user_id`/`workspace_id`; exige allowlist explícita, purposes, evidence vigente e digest de schema. Nome remoto semelhante não produz binding automático. `workspace_id = null` significa apenas binding global explicitamente autorizado, nunca wildcard.

## Registro e lifecycle administrativo

```text
interface McpServerRegistry {
  register(request: RegisterMcpServer) -> McpServerLifecycleReceipt
  validate(request: ValidateMcpServer) -> McpValidationReport
  activate(request: ActivateMcpServer) -> McpServerLifecycleReceipt
  begin_drain(request: DrainMcpServer) -> McpServerLifecycleReceipt
  disable(request: DisableMcpServer) -> McpServerLifecycleReceipt
  quarantine(request: QuarantineMcpServer) -> McpServerLifecycleReceipt
  inspect(query: InspectMcpServer) -> AuthorizedMcpServerView
}

interface McpBindingRegistry {
  register(request: RegisterMcpBinding) -> McpBindingLifecycleReceipt
  validate(request: ValidateMcpBinding) -> McpBindingValidationReport
  activate(request: ActivateMcpBinding) -> McpBindingLifecycleReceipt
  suspend(request: SuspendMcpBinding) -> McpBindingLifecycleReceipt
  disable(request: DisableMcpBinding) -> McpBindingLifecycleReceipt
  quarantine(request: QuarantineMcpBinding) -> McpBindingLifecycleReceipt
  resolve(request: ResolveMcpBinding) -> McpBindingResolution
  inspect(query: InspectMcpBinding) -> AuthorizedMcpBindingView

  invariant: mudança de contrato, schema, owner, Workspace, purpose ou policy cria nova binding_version
  invariant: somente binding ACTIVE com validação vigente pode resolver
}

RegisterMcpServer {
  operation_id: McpAdministrativeOperationId
  context: McpAdministrativeContext
  descriptor: McpServerDescriptor
  expected_descriptor_digest: Digest
  idempotency_key: IdempotencyKey
}

McpServerLifecycleCommand {
  operation_id: McpAdministrativeOperationId
  context: McpAdministrativeContext
  server_ref: McpServerRef
  expected_state_version: Version
  reason: McpLifecycleReason
  drain_deadline: Instant | null
  idempotency_key: IdempotencyKey
}

McpServerLifecycleReceipt =
  | McpServerStateChanged { state: McpServerState, resulting_version: Version }
  | McpServerStateAlreadyApplied { state: McpServerState, resulting_version: Version }
  | McpServerStateConflict { state: McpServerState, current_version: Version }
  | McpServerStateRejected { reason: McpLifecycleRejection }
  | McpServerStateIndeterminate { reconciliation_ref: ReconciliationRef }

RegisterMcpBinding {
  operation_id: McpAdministrativeOperationId
  context: McpAdministrativeContext
  binding: McpBinding
  expected_binding_digest: Digest
  idempotency_key: IdempotencyKey
}

ValidateMcpBinding {
  operation_id: McpAdministrativeOperationId
  context: McpAdministrativeContext
  binding_id: McpBindingId
  binding_version: Version
  expected_state_version: Version
  validation_policy_snapshot_ref: ValidationPolicySnapshotRef
  idempotency_key: IdempotencyKey
}

McpBindingValidationReport {
  binding_id: McpBindingId
  binding_version: Version
  outcome: VALID | INVALID | INDETERMINATE
  remote_schema_digest: Digest
  local_contract_version: Version
  evidence_ref: McpBindingValidationEvidenceRef
  validated_at: Instant
  revalidate_after: Instant
}

McpBindingLifecycleCommand {
  operation_id: McpAdministrativeOperationId
  context: McpAdministrativeContext
  binding_id: McpBindingId
  binding_version: Version
  expected_state_version: Version
  reason: McpBindingLifecycleReason
  idempotency_key: IdempotencyKey
}

ActivateMcpBinding = McpBindingLifecycleCommand
SuspendMcpBinding = McpBindingLifecycleCommand
DisableMcpBinding = McpBindingLifecycleCommand
QuarantineMcpBinding = McpBindingLifecycleCommand

McpBindingLifecycleReceipt =
  | McpBindingStateChanged {
      state: DRAFT | VALIDATING | ACTIVE | SUSPENDED | DISABLED | QUARANTINED
      resulting_version: Version
    }
  | McpBindingStateAlreadyApplied { state: McpBindingState, resulting_version: Version }
  | McpBindingStateConflict { state: McpBindingState, current_version: Version }
  | McpBindingStateRejected { reason: McpBindingLifecycleRejection }
  | McpBindingStateIndeterminate { reconciliation_ref: ReconciliationRef }

ResolveMcpBinding {
  context: McpOperationContext
  binding_id: McpBindingId
  required_binding_version: Version
  required_local_contract_ref: PublicContractRef
  required_local_contract_version: Version
}

McpBindingResolution =
  | McpBindingResolved {
      binding: McpBinding
      validation_evidence_ref: McpBindingValidationEvidenceRef
    }
  | McpBindingResolutionDenied { reason: AuthorizationDenial }
  | McpBindingUnavailable { state: DRAFT | VALIDATING | SUSPENDED | DISABLED | QUARANTINED }
  | McpBindingIncompatible { reason: McpBindingCompatibilityFailure }
  | McpBindingValidationStale { revalidate_after: Instant | null }
  | McpBindingResolutionIndeterminate { reason: McpBindingResolutionFailure }
```

Ativação de servidor e de binding são decisões distintas. Binding só entra em `ACTIVE` após `McpBindingValidationReport.outcome = VALID`, evidence íntegra, `validated_at/revalidate_after` vigentes e auditoria precommit. `SUSPENDED` bloqueia novas resoluções de forma reversível; `DISABLED` bloqueia de forma administrativa até nova versão ou reativação explicitamente permitida; `QUARANTINED` também inicia contenção de invocações ativas. Registro, validação, ativação, suspensão, desativação e quarentena geram receipts e Events auditáveis.

| Origem | Destino | Condição |
| --- | --- | --- |
| `DRAFT` | `VALIDATING` | registro íntegro e validação iniciada |
| `VALIDATING` | `ACTIVE` | report `VALID`, evidence vigente e ativação autorizada |
| `VALIDATING` | `SUSPENDED` | report `INVALID`, `INDETERMINATE` ou prazo expirado |
| `ACTIVE` | `SUSPENDED` | revalidação devida, mudança de dependência ou pausa administrativa |
| `ACTIVE` | `DISABLED` | desativação administrativa confirmada |
| `ACTIVE` | `QUARANTINED` | risco ou violação confirmados |
| `SUSPENDED` | `VALIDATING` | revalidação explicitamente iniciada |
| `SUSPENDED` | `DISABLED` | desativação administrativa confirmada |
| `QUARANTINED` | `VALIDATING` | contenção encerrada e nova versão/evidence exigidas |

Mudança no server binding, protocolo negociável, remote descriptor/schema digest, contrato local, translation policy, permissions, owner, Workspace, purposes ou validation policy exige revalidação e suspende resolução até nova evidence. Expiração de `revalidate_after`, resultado `INVALID/INDETERMINATE` ou indisponibilidade do gate falha fechado. `McpTranslationRegistry.resolve` delega ao `McpBindingRegistry.resolve`; somente `McpBindingResolved` permite criar sessão ou enviar request.

`DRAINING` no servidor bloqueia novas sessões; sessões existentes terminam até deadline se policy permitir. `QUARANTINED` bloqueia sessões e novas solicitações imediatamente, revoga credential leases e inicia reconciliação de invocações ativas. Histórico e evidência permanecem após disable.

## Negociação, descoberta e binding

```text
McpNegotiationRequest {
  request_id: McpRequestId
  context: McpOperationContext
  server_ref: McpServerRef
  supported_protocol_versions: ProtocolVersion[]
  requested_capabilities: McpCapabilityKind[]
  limits: McpSessionLimits
  idempotency_key: IdempotencyKey
}

McpNegotiationResult =
  | McpNegotiated {
      session_id: McpSessionId
      protocol_version: ProtocolVersion
      capabilities: McpCapability[]
      expires_at: Instant
    }
  | McpNegotiationRejected { reason: McpNegotiationFailure }
  | McpNegotiationIndeterminate { reconciliation_ref: ReconciliationRef }

DiscoverMcpCapabilities {
  request_id: McpRequestId
  context: McpOperationContext
  session_id: McpSessionId
  allowed_kinds: McpCapabilityKind[]
  page: PageRequest
  maximum_descriptors: PositiveInteger
}

McpDiscoveryReceipt {
  session_id: McpSessionId
  descriptors: UntrustedMcpDescriptor[]
  rejected_descriptor_count: NonNegativeInteger
  discovery_digest: Digest
}
```

Descoberta não cria Tool, Resource ou grant automaticamente. Descriptors remotos são inputs não confiáveis para o `McpBindingRegistry`. O binding somente é ativado após validação de schema, semântica, limites, permission mapping, cancelamento, errors e compatibilidade. Mudança do descriptor digest suspende o binding até revalidação e nova evidence.

## Mapeamento para contratos atuais

| Superfície MCP futura | Contrato canônico do AgentOS | Regra de tradução |
| --- | --- | --- |
| tool remota | RFC 401 `AtomicTool` | uma chamada atômica, schema fixo, ToolRef/version, idempotência e terminal normalizado |
| resource remota | RFC 402 `ResourceManager.acquire/authorize` + porta especializada existente | requer `AuthorizedResourceHandle` e contrato concreto das RFCs 403/404/405; sem porta local compatível, não há binding |
| conteúdo para contexto | RFC 104 Context Pipeline | dado não confiável, selecionado, classificado, limitado e com proveniência |
| blob ou output durável | RFC 602 Artifact Storage | staging, checksum, classificação, ownership, seal e ArtifactReference |
| prompt/template remoto | input não confiável para template autorizado | nunca instrução de sistema, policy, grant ou aprovação |
| evento/notificação remota | input do adapter | validação e tradução antes de qualquer Event local; não preserva autoridade remota |

Não haverá binding genérico “MCP capability” nem tipo público solto de leitura remota. Cada categoria necessita adapter específico e contrato local. Para Resource, o adapter MCP pode ficar atrás de uma porta especializada existente, mas a autorização continua sendo `ResourceManager.authorize` e o handle continua `AuthorizedResourceHandle`; o `McpTransportPort` é detalhe do adapter. Superfície sem mapeamento seguro permanece indisponível mesmo que negociada pelo protocolo.

## Invocação e resultados

```text
InvokeMcpTool {
  request_id: McpRequestId
  context: McpOperationContext
  session_id: McpSessionId
  binding_id: McpBindingId
  tool_invocation_id: ToolInvocationId
  arguments: StructuredValue
  deadline: Instant
  limits: McpInvocationLimits
  idempotency_key: IdempotencyKey | null
}

McpTransportOutcome =
  | McpTransportSucceeded { payload: UntrustedStructuredValue, remote_receipt_ref: RemoteReceiptRef | null }
  | McpTransportFailed { error: McpNormalizedError, effect: NONE | POSSIBLE | CONFIRMED }
  | McpTransportCancelled { effect: NONE | POSSIBLE | CONFIRMED }
  | McpTransportIndeterminate { reconciliation_ref: ReconciliationRef }
```

Antes do envio, Tool Runtime valida Tools. Uma operação de Resource passa primeiro por `ResourceManager.acquire/authorize` e depois pela porta especializada correspondente; não chama uma operação MCP pública genérica. O adapter traduz somente campos allowlisted. Depois da resposta, valida schema, tamanho, media type, URIs, refs e classificação. Payload volumoso passa por Artifact Storage; conteúdo inline é limitado e marcado não confiável.

Receipt remoto é evidência e não Event interno nem prova automática de idempotência. O domínio registra seu próprio outcome. Idempotency key só é enviada se o binding declarar semântica compatível e proteção contra vazamento entre tenants.

## Limites de segurança

### Identidade, autenticação e secrets

Servidor MCP não autentica o usuário no AgentOS e não concede roles. Credencial de conexão é `SecretReference` vinculada a server, owner, Workspace e purpose; é resolvida no último momento, expira e não é encaminhada a outro servidor. Tokens recebidos do servidor não viram credenciais locais.

### Rede e transporte

Endpoint, scheme, porta, DNS e endereço resolvido obedecem à Network Policy. Redirect exige nova validação. Loopback, link-local, metadata endpoints, redes privadas e downgrade de transporte são negados por padrão. Conexão local não implica confiança. Limites cobrem handshake, duração, bytes, frames, mensagens, concorrência e idle timeout.

### Conteúdo e schema

Nome, descrição, prompt, URI, mensagem de erro e resultado remoto são não confiáveis. Eles não alteram `purpose`, permissions, ToolRef, destino, modelo, Context policy ou instruções de sistema. Schema recursivo, excessivo, mutável ou ambíguo é rejeitado. URI não é dereferenciada fora da porta e allowlist aplicáveis.

### Isolamento e exfiltração

Cada sessão é vinculada ao contexto autorizado e não pode ser reutilizada entre usuários, Workspaces ou purposes incompatíveis. O adapter não envia Context completo: transmite somente argumentos já aprovados. Campos sensíveis requerem regra explícita de egress e auditabilidade. Resposta remota não pode referenciar livremente arquivos locais, handles, Artifacts ou outras sessões.

### Confused deputy e prompt injection

Toda solicitação é autorizada segundo a ação efetiva e o destino final, não segundo a intenção alegada pelo servidor. O servidor não pode induzir o AgentOS a chamar outra Tool, pedir credencial, aprovar ação ou ampliar escopo. Recomendações remotas são dados; nova ação exige nova decisão e autorização local.

## Fluxo normal

1. Administrador autorizado registra descriptor e binding versionado sem abrir conexão privilegiada.
2. Validação testa endpoint, transporte, identidade, negociação, schemas, limites, cancelamento e tradução em ambiente controlado.
3. Ativação confirma allowlists, grants e evidência; nenhuma capability é criada automaticamente.
4. Durante uma Execution, a porta proprietária resolve binding e autoriza ação e egress.
5. Adapter abre sessão limitada, negocia versão/capabilities e fixa o snapshot.
6. Request é traduzido, enviado com deadline e correlacionado por IDs internos não sensíveis.
7. Resultado é limitado, validado, armazenado por referência quando necessário e traduzido em outcome local.
8. A porta proprietária confirma efeitos e Events; sessão é fechada ou expira.

## Falhas, timeout e recuperação

Falhas são normalizadas em negociação, autenticação remota, policy, transporte, protocol, schema, limite, indisponibilidade e efeito incerto. Detalhe remoto sensível fica em Artifact restrito. Retry automático só ocorre para operação declarada idempotente, dentro do budget, quando `effect = NONE` ou reconciliação prova segurança.

Desconexão antes da resposta não prova ausência de efeito. `POSSIBLE` ou `INDETERMINATE` bloqueia retry e solicita inspeção/reconciliação quando o servidor oferecer contrato confiável; sem isso, o domínio falha com efeito desconhecido. Reconexão sempre renegocia e não presume que session IDs, capabilities ou descriptors permanecem válidos.

Violação de framing, schema, limite, identidade ou policy pode quarentenar servidor/binding. Indisponibilidade do servidor nunca autoriza fallback para servidor diferente com dados ou credenciais equivalentes sem nova decisão local. Falha de MCP não compromete o funcionamento de portas nativas não dependentes do binding.

## Cancelamento

Pedido de cancelamento nasce em `ExecutionControl` ou Tool/Resource owner e é propagado ao adapter. Se o protocolo negociado suportar cancelamento, o adapter envia request correlacionado e aguarda receipt até o deadline. Se não suportar, encerra a espera/conexão conforme policy e bloqueia novos efeitos locais.

Cancel acknowledgement remoto não prova reversão. Resultado tardio é registrado e reconciliado, mas não reabre `Execution` terminal. Fechar sessão não substitui cancelamento da operação; cancelar operação não desativa servidor. Drain administrativo bloqueia novas sessões e respeita as invocações ativas salvo revogação de segurança.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `McpServerRegistered` | descriptor administrativo versionado do servidor foi persistido |
| `McpServerValidated` | validação terminou com evidência e outcome |
| `McpServerActivated` | servidor tornou-se elegível no escopo aprovado |
| `McpServerDrainStarted` | novas sessões foram bloqueadas |
| `McpServerDisabled` | servidor deixou de aceitar novas sessões |
| `McpServerQuarantined` | contenção por risco foi confirmada |
| `McpBindingRegistered` | binding versionado, owner, Workspace e purposes foram persistidos |
| `McpBindingValidated` | validação terminou com evidence, vigência e outcome explícitos |
| `McpBindingActivated` | binding tornou-se resolvível no escopo aprovado |
| `McpBindingSuspended` | novas resoluções foram bloqueadas reversivelmente |
| `McpBindingDisabled` | binding deixou de aceitar resoluções administrativas |
| `McpBindingQuarantined` | binding entrou em contenção por risco confirmado |
| `McpSessionOpened` | negociação compatível foi confirmada |
| `McpCapabilityDiscovered` | descriptor remoto foi observado como dado não confiável |
| `McpBindingResolved` | capability remota foi vinculada a contrato local exato |
| `McpRequestFinished` | request terminou com outcome e effect state normalizados |
| `McpSessionClosed` | sessão deixou de aceitar requests |

Eventos administrativos usam correlação administrativa quando fora de Execution. Eventos de sessão/request usam `execution_id` e sequência. Notificação do servidor não é republicada diretamente: ela só gera Event local após validação, deduplicação e confirmação de fato pertencente ao AgentOS.

## Observabilidade

Logs e traces incluem `server_id`, binding version, session/request IDs, contrato local, `execution_id`, `correlation_id`, protocol version, duração, bytes, retries, effect state e código sanitizado. Métricas cobrem negociação, sessões, requests, latência, tamanho, cancelamento, incompatibilidade, schema violations, policy denials, efeitos incertos e quarentena. Endpoint completo, argumentos, conteúdo e tokens não são labels.

O sistema deve responder qual servidor/binding foi usado, quais protocol capabilities foram negociadas, qual contrato local autorizou a operação, quais dados deixaram a fronteira e como o outcome foi determinado. Telemetria remota é complementar e não substitui receipts ou Events locais.

## Critérios de adoção

MCP só pode sair do estado “futuro” após todos os critérios abaixo terem evidência reproduzível:

1. **Estabilidade:** versão da especificação e SDK escolhidos possuem lifecycle, versionamento e compatibilidade documentados.
2. **Mapeamento completo:** cada superfície habilitada possui contrato local proprietário, tradução tipada e semântica de erro, timeout, idempotência e cancelamento.
3. **Segurança:** threat model cobre SSRF, confused deputy, prompt injection, exfiltração, credential forwarding, schema abuse, servidor malicioso e resultado tardio.
4. **Isolamento:** testes provam separação por usuário, Workspace, Execution, purpose, sessão e servidor.
5. **Conformance:** suíte testa negociação, limites, malformed frames, capabilities mutáveis, reconexão, backpressure, cancelamento e efeito incerto.
6. **Observabilidade:** auditoria local reconstrói descoberta, egress, invocação e outcome sem depender do servidor.
7. **Operação:** disable, drain, quarantine, rotação de credencial, recovery e rollback para adapters nativos são exercitados.
8. **Compatibilidade:** testes de regressão provam que interfaces, Events e máquinas de estado existentes não mudaram.
9. **Opt-in:** ativação é explícita por servidor, capability, owner/Workspace e purpose; default permanece deny.
10. **Reversibilidade:** remover ou desabilitar MCP não invalida dados duráveis, Executions históricas ou consumidores das portas públicas.

Uma prova de conceito não satisfaz adoção. Falha em qualquer critério mantém a superfície desabilitada. A aprovação deve registrar RFC/ADR complementar com versão concreta do protocolo, threat model, matriz de bindings e plano de rollback.

## Compatibilidade com contratos atuais

Nenhum tipo MCP pode aparecer em contratos de Kernel, Agent, Execution, Event, Context, Tool, Resource, Provider, Artifact ou Scheduler. IDs e receipts remotos permanecem encapsulados em refs opacas do adapter. Erros MCP são traduzidos para taxonomia pública sem adicionar branches obrigatórios a consumidores existentes.

Adicionar MCP é compatível quando registra novas implementações nos registries atuais. Mudança incompatível em uma superfície local exige nova versão desse contrato independentemente de MCP. Desabilitar MCP torna somente os bindings associados indisponíveis; não altera semântica de implementações nativas nem de plugins não relacionados.

## Invariantes

- MCP é protocolo de borda opcional, não modelo de domínio ou dependência do Kernel;
- toda ação remota pertence a uma `Execution` e passa por porta pública proprietária;
- servidor, descriptor, prompt, URI e resultado remotos são não confiáveis;
- capability anunciada não é grant, Event, Tool ou Resource registrado;
- binding só resolve em `ACTIVE`, na versão exata, para owner, Workspace e purpose compatíveis e com evidence vigente;
- identidade e autorização permanecem locais e deny-by-default;
- sessão é vinculada a um contexto e não cruza owner, Workspace ou purpose;
- segredo nunca entra em payload, log, Event, checkpoint ou descriptor;
- versão, capability e schema negociados são fixados por sessão/binding;
- cancelamento remoto é cooperativo e não prova reversão;
- efeito incerto é reconciliado antes de retry;
- telemetria e receipts remotos não substituem Events e auditoria locais;
- remoção de MCP não quebra os contratos públicos vigentes.

## Extensibilidade

Novos transports, autenticações e superfícies MCP podem implementar portas específicas após atender os mesmos gates. Cada binding adicional declara contrato local, versionamento, egress, limites, errors, idempotência, cancelamento, observabilidade e estratégia de disable. Extension fields desconhecidos permanecem inertes e nunca influenciam autorização.

MCP server fornecido por plugin também obedece à RFC 901; uma camada não reduz as garantias da outra. Adapters alternativos podem coexistir por versão, sem `switch/case` no domínio e sem binding genérico que exponha o protocolo ao Kernel.

## Futuro

Após adoção comprovada, poderão ser avaliados servidores locais isolados, catálogos administrados, discovery federado, streaming, subscriptions e interoperabilidade entre instalações. Cada expansão requer ameaça, retenção, backpressure, ordering, revogação e compatibilidade próprios. Federação de identidade, Event log ou Memory permanece fora da direção recomendada até RFC específica demonstrar que preserva ownership, causalidade, minimização e autoridade local.
