# RFC 702 — Segurança

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 601 — Persistência](../600-platform-data/601-persistence.md), [RFC 603 — Workspaces](../600-platform-data/603-workspaces.md), [RFC 604 — Configuração](../600-platform-data/604-configuration.md), [RFC 701 — API e SSE](701-api-sse.md)

## Objetivo

Definir as fronteiras de confiança do AgentOS para identidade, sessão server-side, CSRF, Personal Access Tokens, autorização por escopo, proteção de segredos, auditoria, isolamento, revogação e controles de rate/abuse, preservando o lançamento single-user sem remover os limites multiusuário.

## Fora de escopo

- fornecedor de identidade, tela de login, fluxo OAuth/OIDC ou método MFA concreto;
- biblioteca criptográfica, vault, KMS, HSM, WAF ou produto de SIEM específico;
- endpoint, middleware, schema de tabela, configuração executável ou código;
- política jurídica de retenção, residência ou resposta a incidente de uma organização específica;
- segurança física do host e gestão de contas do sistema operacional;
- autorização interna de cada Tool/Capability além das garantias de contexto e escopo.

## Responsabilidades e não responsabilidades

O subsistema de segurança DEVE:

- autenticar identidades e vincular credenciais a atores revogáveis;
- manter sessão web somente server-side em Redis, com cookie opaco e protegido;
- bloquear CSRF em operações autenticadas por cookie;
- armazenar PAT somente como verificador hash e revelar o token bruto uma única vez;
- decidir autorização por ação, recurso, ownership, Workspace, purpose e policy versionada;
- proteger segredos com AES-256-GCM e chave raiz externa;
- produzir auditoria íntegra, minimizada e correlacionável;
- revogar sessão, PAT, grants e segredo sem depender apenas de expiração;
- limitar custo e abuso por múltiplas dimensões sem tratar rate limit como autorização.

O subsistema NÃO DEVE:

- confiar em `user_id`, `workspace_id`, roles ou scopes declarados pelo cliente;
- guardar sessão inteira, PAT bruto, segredo, `APP_MASTER_KEY` ou chave de dados em cookie;
- usar criptografia como substituto de autorização e isolamento;
- entregar segredo a frontend, Agent, Memory, log, Event ou telemetria;
- permitir que modo single-user omita ownership, tenancy ou checagens de escopo;
- conceder acesso transitivo por correlação, relação pai-filho ou conhecimento de ID;
- falhar em modo aberto quando Redis, policy, auditoria crítica ou chave estiver indisponível.

## Arquitetura e fronteiras de confiança

```text
Credencial externa
      │
      ▼
AuthenticationService ──> SessionManager / PatVerifier
      │ ActorPrincipal
      ▼
AuthorizationService ──> policy + ownership + revocation
      │ AuthorizedOperation
      ├──> Gateway / portas de aplicação
      ├──> SecretProtector / SecretResolver
      └──> AuditRecorder

AbuseProtection observa tentativas e custo em todas as fronteiras.
```

Autenticação prova uma identidade; autorização decide uma ação concreta. Nenhuma camada posterior aceita o contexto do cliente sem a assinatura/atestado interno da decisão. A autorização é refeita em toda fronteira sensível, inclusive consulta, SSE, Artifact, Workspace, configuração e resolução de segredo.

Redis guarda coordenação efêmera e sessões conforme a [RFC 601](../600-platform-data/601-persistence.md), mas não é fonte de verdade de identidade, PAT ou policy. Indisponibilidade de Redis invalida o uso de sessão até recuperação segura; o sistema não reconstrói privilégio a partir do cookie.

## Contexto completo de operação sensível

```text
SecurityOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
  credential_ref: CredentialRef
  request_origin: TrustedRequestOrigin
  policy_version: Version
}
```

Toda operação sensível confirmada inclui esses campos. Para autenticação ainda não resolvida, tentativas recusadas usam `AuthenticationSubjectRef` minimizada; depois que a identidade é resolvida, o contexto completo é obrigatório antes de criar sessão, PAT, grant ou acesso. Operações administrativas são `Execution`s próprias com `agent_id`, `execution_id`, correlação e finalidade explícitos, não atalhos fora do modelo.

## Sessão server-side e cookie

```text
ServerSession {
  session_id: SessionId
  user_id: UserId
  actor_id: ActorId
  authentication_strength: AuthenticationStrength
  granted_scope_refs: ScopeRef[]
  csrf_binding_hash: CsrfBindingHash
  credential_version: Version
  policy_version: Version
  created_at: Instant
  last_seen_at: Instant
  idle_expires_at: Instant
  absolute_expires_at: Instant
  revoked_at: Instant | null
  revocation_reason: RevocationReason | null
}

SessionCookie {
  opaque_session_handle: OpaqueSessionHandle
  attributes: HTTP_ONLY + SECURE + RESTRICTIVE_SAME_SITE + HOST_SCOPED + PATH_SCOPED
}
```

O cookie contém somente handle aleatório de alta entropia. Sessão, scopes e dados de usuário permanecem server-side em Redis com TTL limitado ao menor prazo entre expiração ociosa e absoluta. O handle é rotacionado no login, elevação/redução de privilégio e evento de risco; o anterior é invalidado para impedir fixation. Logout, revogação, mudança de credencial e encerramento administrativo removem ou marcam a sessão como inválida imediatamente.

Cookies são `HttpOnly`, `Secure`, host-scoped, path-scoped e usam a política `SameSite` mais restritiva compatível. Exceção de domínio ou cross-site exige análise explícita e nunca remove CSRF. Conteúdo do cookie não é lido por script, usado em URL, persistido em Artifact ou enviado a outro domínio.

## CSRF

```text
CsrfProof {
  session_id_ref: SessionIdRef
  token: CsrfToken
  request_origin: TrustedRequestOrigin
  issued_at: Instant
  expires_at: Instant
}
```

Toda operação que muda estado e usa autenticação por cookie exige token CSRF não-cookie vinculado à sessão, validação de origem e método não seguro explicitamente permitido. O servidor compara o token em tempo constante contra binding server-side, aplica expiração e rotação e rejeita ausência ou divergência antes do domínio.

PAT enviado explicitamente em header não depende de cookie e, portanto, não usa CSRF; se cookie e PAT forem apresentados juntos, a política escolhe uma única credencial sem mesclar privilégios. `SameSite`, CORS e checagem de origem são camadas adicionais, não substitutos do token CSRF.

## Personal Access Tokens

```text
PersonalAccessTokenRecord {
  pat_id: PatId
  user_id: UserId
  selector: PatSelector
  verifier_hash: PatVerifierHash
  hash_parameters: HashParametersRef
  scopes: AuthorizationScope[]
  workspace_constraints: WorkspaceId[]
  purpose_constraints: PurposePattern[]
  created_by: ActorRef
  created_execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
  last_used_at: Instant | null
  expires_at: Instant
  revoked_at: Instant | null
  credential_version: Version
}

IssuedPersonalAccessToken {
  pat_id: PatId
  raw_token_once: SecretDisplayValue
  expires_at: Instant
  scopes: AuthorizationScope[]
}
```

O PAT bruto é gerado com entropia criptográfica, exibido uma única vez e nunca persistido. O store mantém somente selector não secreto e hash unidirecional resistente a ataque offline, com salt único e parâmetros versionados; logs preservam no máximo `pat_id` ou fingerprint não reversível. Comparação do verificador é constante. Rotação emite novo PAT e revoga o anterior de forma explícita; não existe recuperação do token bruto.

Scopes de PAT são allowlists de menor privilégio, com expiração obrigatória e constraints por Workspace e purpose. PAT não pode elevar os privilégios do usuário, adquirir novos scopes por uso nem atuar em Workspace diferente. Uso suspeito pode revogar o PAT ou toda a versão de credencial associada.

## Contratos tipados de identidade e credenciais

```text
interface AuthenticationService {
  authenticate(request: AuthenticationRequest) -> AuthenticationResult
  establish_session(command: EstablishSession) -> SessionReceipt
  revoke_session(command: RevokeSession) -> RevocationReceipt
  issue_pat(command: IssuePersonalAccessToken) -> PatIssuanceReceipt
  revoke_pat(command: RevokePersonalAccessToken) -> RevocationReceipt
}

AuthenticationRequest {
  operation_id: SecurityOperationId
  presented_credential: PresentedCredential
  request_origin: TrustedRequestOrigin
  correlation_id: CorrelationId
  purpose: AUTHENTICATE
}

EstablishSession {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  authentication_result_ref: AuthenticationResultRef
  requested_idle_ttl: Duration
  requested_absolute_ttl: Duration
  idempotency_key: IdempotencyKey
}

RevokeSession {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  target_session_id: SessionId
  expected_credential_version: Version
  reason: RevocationReason
  idempotency_key: IdempotencyKey
}

IssuePersonalAccessToken {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  requested_scopes: AuthorizationScope[]
  workspace_constraints: WorkspaceId[]
  purpose_constraints: PurposePattern[]
  expires_at: Instant
  idempotency_key: IdempotencyKey
}

RevokePersonalAccessToken {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  pat_id: PatId
  expected_credential_version: Version
  reason: RevocationReason
  idempotency_key: IdempotencyKey
}
```

```text
PatIssuanceReceipt =
  | PatIssued { token: IssuedPersonalAccessToken, correlation_id: CorrelationId }
  | PatIssuanceRejected { reason: PublicSecurityReason }
  | PatIssuanceConflicted { actual_credential_version: Version }
  | PatIssuanceIndeterminate { operation_id: SecurityOperationId, idempotency_key: IdempotencyKey }

RevocationReceipt =
  | CredentialRevoked { credential_ref: CredentialRef, revoked_at: Instant, correlation_id: CorrelationId }
  | CredentialAlreadyRevoked { credential_ref: CredentialRef, revoked_at: Instant }
  | RevocationRejected { reason: PublicSecurityReason }
  | RevocationConflicted { actual_credential_version: Version }
  | RevocationIndeterminate { operation_id: SecurityOperationId, idempotency_key: IdempotencyKey }
```

Criação, rotação e revogação são idempotentes no ownership e na versão esperada. Estado indeterminado exige reconciliação pela mesma chave; nunca se emite outro PAT bruto automaticamente após possível confirmação. Como o valor bruto só existe no recibo inicial, perda do valor exige revogar o registro e emitir uma credencial nova.

## Autorização por escopo

```text
AuthorizationRequest {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  action: SecurityAction
  resource: ResourceAuthorizationRef
  requested_scopes: AuthorizationScope[]
  data_classification: DataClassification
  current_resource_version: Version
}

AuthorizationDecision =
  | AuthorizationGranted {
      authorization_basis_ref: AuthorizationBasisRef
      effective_scopes: AuthorizationScope[]
      constraints: AuthorizationConstraint[]
      policy_version: Version
      valid_until: Instant
    }
  | AuthorizationDenied {
      public_reason: PublicSecurityReason
      policy_version: Version
    }

interface AuthorizationService {
  authorize(request: AuthorizationRequest) -> AuthorizationDecision
}
```

A decisão calcula a interseção entre privilégios do ator, scopes da credencial, ownership do recurso, vínculo com Workspace, purpose, classificação, estado de revogação e policy vigente. Ausência de regra resulta em negação. `user_id` é sempre validado; `workspace_id` é obrigatório para recurso de projeto e não é substituído por compartilhamento de `agent_id` ou `execution_id`.

Decisões possuem validade curta e referência auditável; caches são vinculados a versões de policy, credencial e recurso. Revogação ou mudança de ownership invalida a decisão. Cada ação encadeada reautoriza no menor recurso possível; a autorização para uma `Execution` não concede automaticamente Artifact, Memory, segredo, filho ou Workspace inteiro.

## Segredos e AES-256-GCM

```text
ProtectedSecretEnvelope {
  secret_id: SecretId
  secret_version: SecretVersion
  ciphertext: Ciphertext
  algorithm: AES_256_GCM
  nonce: UniqueNonce
  authentication_tag: AuthenticationTag
  wrapped_data_key: WrappedDataEncryptionKey
  master_key_version: MasterKeyVersion
  aad_descriptor: SecretAadDescriptor
  created_at: Instant
}

SecretAadDescriptor {
  user_id: UserId
  workspace_id: WorkspaceId | null
  secret_id: SecretId
  secret_version: SecretVersion
  classification: DataClassification
  purpose_boundary: SecretPurposeBoundary
}
```

Cada versão de segredo usa AES-256-GCM com nonce criptograficamente seguro e único para a mesma chave. Associated Authenticated Data vincula ciphertext a ownership, identidade, versão, classificação e purpose boundary; divergência falha fechada. O material usa data-encryption key dedicada ou escopo equivalente, protegida por `APP_MASTER_KEY`; plaintext e chave de dados existem apenas pelo tempo mínimo no consumer autorizado.

`APP_MASTER_KEY` é fornecida por bootstrap externo autorizado, sem default, e permanece fora de banco, Redis, Artifact Storage, Workspace, backup de dados, configuração distribuída, logs e código. Ela nunca é entregue a frontend, Agent, Tool, Provider ou Browser. Ambientes usam roots diferentes. Ausência, versão desconhecida, tag inválida ou falha de unwrap torna a operação indisponível e auditável; não tenta chave vazia, fallback silencioso ou plaintext legado.

A rotação de segredo muda a credencial de negócio. A rotação de `APP_MASTER_KEY` rewrapa data keys de forma versionada, idempotente, checkpointed e verificável, sem reutilizar nonce nem alterar secret plaintext. Revogação impede novas resoluções; handles já emitidos têm TTL curto e recebem sinal de invalidação quando suportado. A [RFC 604](../600-platform-data/604-configuration.md) continua responsável pelo lifecycle e pelas referências de segredo.

## Contratos tipados de proteção

```text
interface SecretProtector {
  protect(command: ProtectSecret) -> SecretProtectionReceipt
  unprotect(request: UnprotectSecret) -> EphemeralSecretHandle
  rewrap(command: RewrapProtectedSecrets) -> RewrapReceipt
}

ProtectSecret {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  secret_id: SecretId
  secret_version: SecretVersion
  plaintext_input: EphemeralSecretInput
  classification: DataClassification
  expected_previous_version: SecretVersion | null
  idempotency_key: IdempotencyKey
}

UnprotectSecret {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  secret_ref: SecretReference
  authorized_consumer: AuthorizedSecretConsumer
  required_purpose: SecretPurpose
}

RewrapProtectedSecrets {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  source_master_key_version: MasterKeyVersion
  target_master_key_version: MasterKeyVersion
  checkpoint_ref: RewrapCheckpointRef | null
  batch_limit: PositiveInteger
  idempotency_key: IdempotencyKey
}
```

Nenhum receipt público contém plaintext, chave, nonce reutilizável ou token. `EphemeralSecretHandle` não é serializável, persistível nem transferível entre consumers. Operações classificadas como críticas passam pelo gate de auditoria definido abaixo; indisponibilidade desse gate bloqueia a operação antes de confirmar mutação ou liberar material.

## Auditoria

```text
SecurityAuditRecord {
  audit_id: AuditId
  occurred_at: Instant
  action: SecurityAction
  outcome: ALLOWED | DENIED | REVOKED | LIMITED | FAILED | INDETERMINATE
  user_id: UserId | null
  workspace_id: WorkspaceId | null
  agent_id: AgentId | null
  execution_id: ExecutionId | null
  correlation_id: CorrelationId
  purpose: Purpose
  actor_ref: RedactedActorRef
  credential_ref: RedactedCredentialRef | null
  resource_ref: RedactedResourceRef | null
  policy_version: Version | null
  reason_code: SecurityReasonCode
  integrity_ref: AuditIntegrityRef
}

AuditAvailabilityClass = REQUIRED_PRECOMMIT | REQUIRED_PREDELIVERY |
                         REQUIRED_DECISION | BEST_EFFORT

AuditGateRequest {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  action: SecurityAction
  availability_class: AuditAvailabilityClass
  resource_ref: RedactedResourceRef
  intended_outcome: SecurityOutcome
  idempotency_key: IdempotencyKey | null
}

AuditGateReceipt {
  audit_reservation_id: AuditReservationId
  integrity_ref: AuditIntegrityRef
  reserved_at: Instant
  expires_at: Instant
}

interface SecurityAuditGate {
  reserve(request: AuditGateRequest) -> AuditGateReceipt
  finalize(reservation_id: AuditReservationId, outcome: SecurityOutcome) -> AuditFinalizeReceipt
}
```

Auditoria registra sucesso e negação de autenticação, criação/revogação de sessão e PAT, decisões privilegiadas, acesso e lifecycle de segredo, mudanças de policy, violações de isolamento e acionamento de abuso. O registro é append-oriented, versionado, protegido contra adulteração e retido por policy. Acesso à auditoria também é autorizado e auditado.

Nunca entram em auditoria: PAT bruto ou hash completo, cookie, CSRF token, senha, plaintext, chave, ciphertext completo, prompt, conteúdo de Artifact ou stack com dados sensíveis. Para tentativa pré-autenticação, o identificador é minimizado/pseudonimizado e limitado por retenção.

### Classes de disponibilidade

| Operação | Classe | Semântica de falha |
| --- | --- | --- |
| emitir, rotacionar ou revogar sessão, PAT ou outra credencial | `REQUIRED_PRECOMMIT` | reservar auditoria antes da mutação; indisponibilidade rejeita sem emitir/revogar |
| criar, alterar, ativar ou revogar policy, grant ou role | `REQUIRED_PRECOMMIT` | policy e registro de auditoria confirmam na mesma fronteira conceitual; indisponibilidade preserva versão anterior |
| proteger, rotacionar, revogar ou rewrapar segredo/chave | `REQUIRED_PRECOMMIT` | nenhuma nova versão ou lifecycle é confirmado sem reserva íntegra |
| `unprotect` ou resolver segredo para consumer | `REQUIRED_PREDELIVERY` | reservar auditoria antes de liberar `EphemeralSecretHandle`; falha não entrega material |
| mutação administrativa, break-glass ou alteração de ownership | `REQUIRED_PRECOMMIT` | falha fechada antes do efeito; break-glass não contorna auditoria |
| decisão privilegiada de autorização e acesso à própria auditoria | `REQUIRED_DECISION` | não concede decisão nem conteúdo se não puder registrar o outcome |
| métricas operacionais, heartbeat e telemetria redundante | `BEST_EFFORT` | pode degradar com alerta, sem mudar autorização ou expor conteúdo |

`reserve` grava intenção mínima íntegra antes do efeito e devolve referência de uso único. `finalize` registra outcome confirmado ou indeterminado sem reusar a reserva para outra ação. Para `REQUIRED_PRECOMMIT`, mutação e finalização pertencem à mesma fronteira atômica conceitual quando o store permitir; caso não compartilhem transação, uma intenção durável idempotente precede o efeito e reconciliação obrigatória fecha o outcome. Para `REQUIRED_PREDELIVERY`, nenhum byte ou handle de segredo sai antes da reserva confirmada, e a entrega finaliza imediatamente o outcome. Timeout depois da reserva não autoriza repetir efeito com nova chave.

Falha fechada de auditoria é requisito de segurança, não mera observabilidade. Filas ou buffers somente em memória não satisfazem classes `REQUIRED_*`; se integridade, durabilidade ou capacidade do audit store não puderem ser comprovadas, a operação retorna indisponível/indeterminada conforme o ponto de falha. `BEST_EFFORT` nunca inclui emissão/revogação de credencial, alteração de policy, `unprotect` de segredo ou mutação administrativa.

## Isolamento entre usuário e Workspace

- toda entidade pertencente a pessoa carrega `user_id` e toda entidade de projeto carrega `workspace_id` conforme a [RFC 603](../600-platform-data/603-workspaces.md);
- queries sempre aplicam ownership no store ou porta autorizada, não filtragem posterior no cliente;
- referências cross-user e cross-workspace são negadas por padrão;
- relação entre Agent, `Execution`, Artifact, Memory ou Event não transfere autorização;
- caches, sessões, cursores, rate buckets e locks incluem namespace de tenancy;
- nomes, contagens, timing detalhado e diferenças de erro não devem permitir enumeração de outro tenant;
- o modo single-user mantém os mesmos campos, checagens e testes de isolamento.

## Revogação

Revogação é estado durável ou policy versionada propagada aos caches e stores efêmeros. Ela cobre sessão individual, todas as sessões de um usuário, PAT, grant, policy, segredo e versão de credencial. Novas decisões falham imediatamente após confirmação; streams e handles vivos são encerrados ou expiram no menor prazo contratual.

Uma lista de revogação ou versão de credencial pode ser distribuída em Redis para resposta rápida, mas sua indisponibilidade não autoriza acesso. Logout local revoga a sessão atual; incidente pode incrementar versão de credencial e invalidar todas. Revogação não apaga uso já confirmado: preserva auditoria e inicia cleanup/rotação conforme o domínio proprietário.

## Rate limits e proteção contra abuso

```text
AbuseCheck {
  operation_id: SecurityOperationId
  context: SecurityOperationContext
  action_class: AbuseActionClass
  cost_units: PositiveInteger
  dimensions: USER + WORKSPACE + CREDENTIAL + NETWORK_ORIGIN + RESOURCE
}

AbuseDecision =
  | WithinLimit { remaining_units: NonNegativeInteger, resets_at: Instant }
  | Limited { reason: LimitReason, retry_after: Duration | null }
  | Challenged { challenge_ref: ChallengeRef, expires_at: Instant }
  | Blocked { reason: AbuseReason, review_ref: ReviewRef | null }
```

Limites são aplicados antes de trabalho caro e novamente conforme custo real para criação/controle de `Execution`, consultas, SSE, autenticação, PAT, segredo e operações administrativas. Dimensões combinadas evitam que troca de IP, PAT ou Workspace contorne quota. Origem de rede auxilia detecção, mas não define identidade nem ownership.

Políticas distinguem burst, taxa sustentada, concorrência, bytes, conexões, fan-out, falhas de autenticação e custo estimado. Respostas não revelam thresholds sensíveis. Limitação nunca muda `DENIED` para `ALLOWED`, e indisponibilidade do limiter adota ceiling local seguro ou falha fechada para operações de alto risco. Exceções administrativas são limitadas, expiráveis, justificadas e auditadas.

## Eventos

Eventos de segurança relatam fatos passados e contêm somente metadata minimizada:

| Event | Fato confirmado |
| --- | --- |
| `SessionCreated` | sessão server-side foi confirmada |
| `SessionRevoked` | sessão deixou de ser utilizável |
| `PersonalAccessTokenIssued` | registro hash e scopes foram confirmados |
| `PersonalAccessTokenRevoked` | PAT deixou de ser utilizável |
| `AuthorizationDenied` | ação foi negada por policy |
| `SecretProtected` | envelope autenticado foi persistido |
| `SecretRotated` | nova versão de segredo foi ativada |
| `SecretRevoked` | versão não aceita mais novas resoluções |
| `MasterKeyRewrapCompleted` | lote de data keys foi rewrapado e verificado |
| `AbuseLimitTriggered` | limite ou bloqueio foi acionado |
| `IsolationViolationDetected` | tentativa cross-scope foi bloqueada |

Events seguem envelope e entrega da [RFC 103](../100-kernel/103-event-system.md). Hash, token, cookie, CSRF, plaintext, ciphertext, nonce e chave são proibidos no payload.

## Fluxo normal

1. A borda valida forma, origem e rate limit inicial.
2. AuthenticationService verifica credencial e resolve identidade sem aceitar ownership do cliente.
3. Sessão server-side ou PAT válido produz principal com versão e scopes limitados.
4. AuthorizationService cruza principal, recurso, ownership, Workspace, purpose, classificação e policy.
5. Decisão autorizada e limitada acompanha a chamada à porta de aplicação.
6. Operação crítica reserva auditoria íntegra conforme sua classe antes de mutação, decisão ou entrega.
7. Para segredo, o resolver reautoriza, verifica envelope AES-256-GCM e somente então entrega handle efêmero ao consumer exato sob reserva `REQUIRED_PREDELIVERY`.
8. O gate finaliza o outcome confirmado ou indeterminado sem registrar material secreto.

## Fluxo de falha

- credencial inválida usa resposta genérica e tentativa auditada sem enumerar usuário;
- Redis indisponível impede aceitar sessão baseada apenas em cookie;
- CSRF ausente ou divergente bloqueia antes do domínio;
- PAT expirado, revogado ou com hash divergente falha sem expor qual condição;
- policy indisponível ou versão inconsistente falha fechada em operação sensível;
- acesso cross-user/workspace é negado e sinalizado sem revelar o alvo;
- tag GCM inválida, nonce inconsistente ou key version ausente não retorna plaintext;
- auditoria obrigatória indisponível impede emissão/revogação de credencial, alteração de policy/grant/role, `unprotect` de segredo e toda mutação administrativa;
- rate limit indisponível aplica ceiling local seguro ou bloqueio conforme classe de risco.

## Fluxo de cancelamento

Cancelar uma `Execution` não revoga automaticamente a credencial que solicitou o comando. Cancelar operação administrativa interrompe novos lotes e preserva checkpoint idempotente; envelopes já confirmados permanecem válidos. Cancelar emissão de PAT antes da confirmação não cria registro; após confirmação indeterminada exige reconciliação e, se o token bruto não puder ser entregue com segurança, revogação explícita. Cancelamento nunca restaura sessão, PAT, grant ou segredo já revogado.

## Segurança

- TLS e transporte seguro são obrigatórios fora de loopback controlado;
- algoritmos, parâmetros, tamanhos e rotação seguem baseline criptográfico versionado;
- comparações de segredo e verifier são constantes onde aplicável;
- entradas têm limites de tamanho, cardinalidade, tempo e custo;
- clocks e expirações usam UTC, com tolerância explícita e sem prolongamento por falha;
- mensagens públicas minimizam enumeração e side channels operacionais;
- dependências e backups têm acesso mínimo e separação por ambiente;
- break-glass, se futuro, exige identidade forte, prazo curto, escopo estreito e auditoria independente.

## Observabilidade

Métricas cobrem autenticações, sessões ativas/expiradas/revogadas, falhas CSRF, verificações e revogações de PAT, decisões por outcome, latência de policy, acessos de segredo, falhas de tag/unwrap, progresso de rewrap, violações de isolamento e limits por dimensão. Logs e traces usam IDs redacted, `correlation_id`, `execution_id`, purpose, policy version e razão categórica. Alertas distinguem indisponibilidade, brute force, credential stuffing, enumeração, exfiltração, fan-out e acesso anômalo a segredos sem registrar o material observado.

## Invariantes

- sessão web é server-side em Redis; cookie é somente handle opaco `HttpOnly` e `Secure`;
- operação mutável autenticada por cookie exige CSRF válido;
- PAT bruto nunca é persistido e seu hash nunca é tratado como token reutilizável;
- autenticação não implica autorização;
- autorização é deny-by-default, por ação, recurso, ownership, Workspace e purpose;
- single-user não elimina `user_id` nem isolamento de Workspace;
- segredo persistido usa AES-256-GCM com nonce único e AAD vinculada ao escopo;
- `APP_MASTER_KEY` é externa a todos os stores protegidos e nunca chega ao domínio ou cliente;
- revogação impede novos usos e invalida decisões/cache dentro do prazo contratual;
- rate limit não concede acesso nem substitui autorização;
- auditoria é minimizada, íntegra, autorizada e não contém segredo;
- emissão/revogação de credenciais, alterações de policy/grant/role, `unprotect` de segredo e mutações administrativas falham fechado sem auditoria durável disponível;
- toda operação sensível carrega contexto completo e finalidade explícita.

## Extensibilidade

Novos fatores de autenticação, tipos de credencial, engines de policy, vaults e serviços de key management podem implementar as mesmas portas. A migração preserva revogação, versões, auditabilidade e criptografia autenticada. Novos scopes são negados por clientes e policies antigos até suporte explícito; nunca são concedidos por compatibilidade implícita.

## Futuro

OIDC, passkeys, MFA adaptativo, identidades de serviço, organizações, compartilhamento entre Workspaces, KMS/HSM e detecção comportamental poderão ser adicionados. Cada evolução deverá definir bootstrap, recuperação, revogação, tenancy, migração criptográfica e degradação segura antes de ativação.
