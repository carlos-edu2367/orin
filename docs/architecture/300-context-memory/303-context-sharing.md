# RFC 303 — Compartilhamento de contexto

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 203 — Multi-agent](../200-agents/203-multi-agent.md), [RFC 301 — Memory](301-memory.md), [RFC 302 — Blackboard](302-blackboard.md)

## Objetivo

Definir compartilhamento de contexto entre Agents por referências autorizadas, snapshots mínimos e handoffs estruturados. Esta RFC é a fonte canônica de `StructuredHandoff` e `HandoffRef`; a RFC 203 consome esses contratos por alias e define a coordenação que os utiliza.

Compartilhar Context NÃO significa copiar a janela corrente. O destinatário monta seu próprio Context temporário pela RFC 104 a partir de referências mínimas, versionadas e reautorizadas.

## Fora de escopo

- copiar ou sincronizar Context completo entre Agents;
- enviar centenas de mensagens, histórico bruto de chat, prompts internos ou cadeia de raciocínio;
- transferir ownership de Memory, Artifact, Blackboard ou Execution;
- herdar automaticamente Tools, Skills, Grants, segredos ou configuração do remetente;
- persistir Context como Memory;
- definir endpoints, ORM, transporte, serialização concreta ou algoritmo de sumarização;
- substituir delegação, mensageria ou lifecycle de `Execution` da RFC 203.

## Responsabilidades e não responsabilidades

O subsistema de compartilhamento DEVE:

- validar remetente, destinatário, `user_id`, `workspace_id`, `agent_id`, `execution_id`, finalidade e correlação;
- criar referências opacas e limitadas em vez de duplicar conteúdo;
- permitir snapshots mínimos apenas quando referências isoladas não preservarem coerência suficiente;
- estruturar handoff com objetivo, critérios, limites, orçamento e resultado esperado;
- aplicar filtros de classificação, necessidade, fonte, tempo e tamanho antes de disponibilizar conteúdo;
- reautorizar referências no instante da resolução;
- suportar expiração, revogação, cancelamento e consumo idempotente;
- registrar auditoria e Events no passado sem conteúdo sensível;
- falhar fechado quando uma referência obrigatória não puder ser autorizada.

O subsistema NÃO DEVE:

- montar o Context final do destinatário;
- transformar colaboração ou parentesco em autorização;
- copiar Private Memory, mensagens ou resultados por padrão;
- ampliar classificação, Workspace, Agent set ou duração de uma fonte;
- manter credencial, token, sessão ou handle vivo em referência ou snapshot;
- chamar Provider, Agent, Tool ou adapter de storage concreto;
- reabrir Execution terminal ou governar suas transições.

## Princípios de compartilhamento

1. **Referência primeiro:** Artifact, Memory, Blackboard item, Event e resultado são compartilhados por referências autorizadas.
2. **Mínimo necessário:** o handoff contém somente objetivo, critérios, restrições e dependências essenciais.
3. **Snapshot excepcional e limitado:** snapshot preserva uma visão coerente pequena, nunca o histórico bruto.
4. **Autorização no uso:** emissão de referência não garante resolução futura; o destinatário é revalidado.
5. **Sem herança de poder:** Agent filho recebe seus próprios limites e somente Grants delegados explicitamente.
6. **Context local:** o `ContextManager` do destinatário seleciona, sanitiza e orça os candidatos.
7. **Revogação observável:** novas resoluções cessam após revogação; consumo anterior permanece auditável.

## Arquitetura

```text
Origin Execution / Agent
          │ ShareContext command
          ▼
  ContextSharingService
 ┌────────┼────────────┐
 │        │            │
Policy  Filter     Grant lifecycle
 │        │            │
 └────────┼────────────┘
          │
 Shared refs / minimal snapshot / structured handoff
          │
          ▼
Destination ContextManager
 authorization + sanitation + budget + manifest
          │
          ▼
Destination Execution Context (temporary)
```

O serviço coordena metadados e Grants por portas públicas. As fontes continuam responsáveis por ownership e conteúdo. O `ContextManager` pode excluir um item compartilhado por orçamento ou sanidade mesmo quando o Grant é válido.

## Modos de compartilhamento

| Modo | Uso | Limite normativo |
| --- | --- | --- |
| `REFERENCE` | fonte durável já possui referência estável | preferencial; resolução reautorizada e versionada |
| `MINIMAL_SNAPSHOT` | pequena visão coerente de dados mutáveis é indispensável | itens selecionados, orçamento rígido, validade curta e proveniência por item |
| `STRUCTURED_HANDOFF` | delegação ou continuação precisa de objetivo e contrato de resultado | envelope mínimo que contém refs e, opcionalmente, snapshot mínimo |

`MINIMAL_SNAPSHOT` não é Memory e não vira fonte permanente. Persistem somente metadados de auditoria e, durante a validade necessária, a representação mínima protegida. Conteúdo volumoso deve ser Artifact.

## Entidades e dados conceituais

O pseudocódigo é tipado, contratual e não executável.

```text
ContextShareGrant {
  grant_id: ContextShareGrantId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  purpose: AccessPurpose
  allowed_kinds: SharedContextKind[]
  classification_ceiling: DataClassification
  filters: ContextShareFilter[]
  budget: ContextShareBudget
  redelegation: FORBIDDEN | EXPLICIT_ONLY
  consumption_policy: SINGLE_USE | MULTI_USE_UNTIL_TERMINAL
  status: PENDING | ACTIVE | REVOKED | EXPIRED | CONSUMED | CANCELLED
  issued_by: ActorRef
  authorization_basis_ref: AuthorizationBasisRef
  correlation_id: CorrelationId
  issued_at: Instant
  expires_at: Instant
  consumed_at: Instant | null
  resolution_count: NonNegativeInteger
  revoked_at: Instant | null
}

SharedContextKind = TASK | DECISION | MESSAGE | MEMORY | ARTIFACT |
                    BLACKBOARD_ITEM | EVENT | TOOL_RESULT | CONTROL_STATE

ContextShareFilter {
  field: SOURCE_KIND | SOURCE_VERSION | SOURCE_AGENT_ID | SOURCE_EXECUTION_ID |
         AUTHORED_BY | CREATED_AT | OBSERVED_AT | CLASSIFICATION | SOURCE_REF
  operator: EQUALS | IN | BETWEEN | AT_OR_BEFORE | AT_OR_AFTER | AT_MOST
  value: ContextShareFilterValue
}

ContextShareFilterValue = SharedContextKind | Version | AgentId | ExecutionId |
                          ActorRef | Instant | InstantRange |
                          DataClassification | SourceReference |
                          SharedContextKind[] | Version[] | ActorRef[]
```

Somente os campos enumerados em `ContextShareFilter.field` são filtráveis. `AT_MOST` é reservado a `CLASSIFICATION`, operadores temporais a `CREATED_AT` e `OBSERVED_AT`, e valores de identidade exigem igualdade ou allowlist; filtros livres sobre conteúdo são proibidos.

`target_execution_id` é obrigatório para compartilhamento operacional. Um Grant para Agent sem Execution ativa pode autorizar criação de handoff, mas deve ser limitado e vinculado à Execution de destino antes da resolução de conteúdo.

```text
SharedContextReference {
  shared_ref_id: SharedContextReferenceId
  grant_id: ContextShareGrantId
  source_kind: SharedContextKind
  source_ref: AuthorizedSourceReference
  source_version: Version | null
  source_user_id: UserId
  source_workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  target_execution_id: ExecutionId
  purpose: AccessPurpose
  classification: DataClassification
  integrity_ref: IntegrityRef | null
  created_at: Instant
  expires_at: Instant
}

AuthorizedSourceReference {
  source_kind: SharedContextKind
  source_ref: SourceReference
  source_version: Version | null
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner_agent_id: AgentId | null
  authorization_ref: AuthorizationBasisRef
  permitted_purposes: AccessPurpose[]
  classification: DataClassification
  expires_at: Instant | null
  integrity_ref: IntegrityRef | null
}
```

Uma referência não contém conteúdo nem localização física. A origem e o `ContextSharingService` validam que o Grant não é mais amplo que a autorização da própria fonte.

```text
MinimalContextSnapshot {
  snapshot_id: MinimalContextSnapshotId
  grant_id: ContextShareGrantId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  purpose: AccessPurpose
  items: MinimalSnapshotItem[]
  budget: ContextShareBudget
  source_cutoff_at: Instant
  policy_version: Version
  classification: DataClassification
  correlation_id: CorrelationId
  created_at: Instant
  expires_at: Instant
  integrity_ref: IntegrityRef
}

MinimalSnapshotItem {
  kind: SharedContextKind
  representation: BoundedSummary | SharedContextReference
  provenance: Provenance
  source_version: Version | null
  classification: DataClassification
  estimated_units: PositiveInteger
}

MinimalSnapshotRequest {
  request_id: MinimalSnapshotRequestId
  shared_ref_id: SharedContextReferenceId
  representation: REFERENCE_ONLY | BOUNDED_SUMMARY
  maximum_summary_units: PositiveInteger
  required: Boolean
  summary_purpose: AccessPurpose
}
```

O snapshot não aceita coleção ilimitada de mensagens. A política define quantidade máxima de itens, tamanho por item e total; resumo informa cobertura, lacunas e transformações.

```text
StructuredHandoff {
  handoff_id: HandoffId
  grant_id: ContextShareGrantId
  user_id: UserId
  workspace_id: WorkspaceId | null
  from_agent_id: AgentId
  to_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  objective: TaskSnapshot
  success_criteria: Criterion[]
  constraints: Constraint[]
  expected_output: OutputContractRef
  context_refs: SharedContextReference[]
  minimal_snapshot_ref: MinimalContextSnapshotRef | null
  delegated_grant_refs: DelegatedGrantRef[]
  budget: ContextShareBudget
  purpose: DelegationPurpose
  classification: DataClassification
  correlation_id: CorrelationId
  created_at: Instant
  expires_at: Instant
  integrity_ref: IntegrityRef
}

ContextShareBudget {
  maximum_references: PositiveInteger
  maximum_snapshot_items: NonNegativeInteger
  maximum_summary_units: PositiveInteger
  maximum_resolved_content_units: PositiveInteger
  per_kind_limits: SharedKindBudget[]
}

SharedKindBudget {
  kind: SharedContextKind
  maximum_references: NonNegativeInteger
  maximum_snapshot_items: NonNegativeInteger
  maximum_summary_units: NonNegativeInteger
  maximum_resolved_content_units: NonNegativeInteger
}

MinimalContextSnapshotRef {
  snapshot_id: MinimalContextSnapshotId
  grant_id: ContextShareGrantId
  target_agent_id: AgentId
  target_execution_id: ExecutionId
  policy_version: Version
  expires_at: Instant
  integrity_ref: IntegrityRef
}

HandoffRef {
  handoff_id: HandoffId
  grant_id: ContextShareGrantId
  from_agent_id: AgentId
  to_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  purpose: DelegationPurpose
  classification: DataClassification
  version: Version
  expires_at: Instant
  integrity_ref: IntegrityRef
}

DelegatedGrantRef {
  delegated_grant_id: DelegatedGrantId
  parent_grant_id: ContextShareGrantId
  from_agent_id: AgentId
  to_agent_id: AgentId
  target_execution_id: ExecutionId
  allowed_kinds: SharedContextKind[]
  purpose: AccessPurpose
  redelegation: FORBIDDEN
  expires_at: Instant
  authorization_ref: AuthorizationBasisRef
  integrity_ref: IntegrityRef
}

TaskSnapshot {
  task_id: TaskId | null
  objective: BoundedText
  source_version: Version
  captured_at: Instant
  integrity_ref: IntegrityRef
}

Criterion {
  criterion_id: CriterionId
  description: BoundedText
  required: Boolean
  verification_ref: SourceReference | null
}

Constraint {
  constraint_id: ConstraintId
  kind: TIME | RESOURCE | SCOPE | SECURITY | OUTPUT
  description: BoundedText
  source_ref: SourceReference | null
}

OutputContractRef {
  output_contract_id: OutputContractId
  version: Version
  expected_kind: OutputKind
  schema_ref: SourceReference | null
  authorization_ref: AuthorizationBasisRef
  integrity_ref: IntegrityRef
}

OutputKind = TEXT | STRUCTURED_DATA | ARTIFACT | DECISION | PATCH | REPORT
DelegationPurpose = TASK_DELEGATION | CONTINUATION | REVIEW | SYNTHESIS
```

`StructuredHandoff`, `HandoffRef`, `DelegatedGrantRef`, `TaskSnapshot`, `Criterion`, `Constraint` e `OutputContractRef` são os contratos canônicos também usados pela RFC 203. Essas referências carregam somente contrato e autoridade mínimos; não incorporam Context nem concedem acesso transitivo. `expires_at` é obrigatório no handoff e na referência, DEVE ser igual em ambos e não pode exceder a expiração do `ContextShareGrant`, de qualquer `DelegatedGrantRef` necessário nem do snapshot referenciado. Uma `HandoffRef` expirada ou cuja versão/integridade não corresponda ao `StructuredHandoff` falha fechado.

## Contratos públicos

```text
interface ContextSharingService {
  authorize(command: AuthorizeContextShare) -> ContextShareGrant
  create_reference(command: CreateSharedContextReference) -> SharedContextReference
  create_snapshot(command: CreateMinimalContextSnapshot) -> MinimalContextSnapshotRef
  create_handoff(command: CreateStructuredHandoff) -> HandoffRef
  resolve(query: ResolveSharedContext) -> ResolvedContextSeed
  revoke(command: RevokeContextShare) -> RevocationReceipt
  expire(command: ExpireContextShare) -> ExpirationReceipt

  pre: origem, destino, finalidade e ownership foram validados
  post: conteúdo resolvido respeita filtros, orçamento e classificação
  post: nenhuma autorização da fonte foi ampliada
  post: resolução bem-sucedida incrementa resolution_count exatamente uma vez por idempotency_key
  post: primeira resolução bem-sucedida transiciona ACTIVE para CONSUMED
}
```

```text
AuthorizeContextShare {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  purpose: AccessPurpose
  requested_kinds: SharedContextKind[]
  filters: ContextShareFilter[]
  budget: ContextShareBudget
  classification_ceiling: DataClassification
  consumption_policy: SINGLE_USE | MULTI_USE_UNTIL_TERMINAL
  expires_at: Instant
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

CreateSharedContextReference {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  grant_id: ContextShareGrantId
  source_ref: AuthorizedSourceReference
  source_kind: SharedContextKind
  expected_source_version: Version | null
  purpose: AccessPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}
```

```text
CreateMinimalContextSnapshot {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  grant_id: ContextShareGrantId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  candidate_refs: SharedContextReference[]
  requested_items: MinimalSnapshotRequest[]
  source_cutoff_at: Instant
  purpose: AccessPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

CreateStructuredHandoff {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  grant_id: ContextShareGrantId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  objective: TaskSnapshot
  success_criteria: Criterion[]
  constraints: Constraint[]
  expected_output: OutputContractRef
  context_refs: SharedContextReference[]
  minimal_snapshot_ref: MinimalContextSnapshotRef | null
  delegated_grant_refs: DelegatedGrantRef[]
  purpose: DelegationPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}
```

```text
ResolveSharedContext {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  grant_id: ContextShareGrantId
  handoff_ref: HandoffRef
  requested_ref_ids: SharedContextReferenceId[]
  purpose: AccessPurpose
  remaining_budget: ContextShareBudget
  expected_resolution_count: NonNegativeInteger
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ResolvedContextSeed {
  grant_id: ContextShareGrantId
  target_execution_id: ExecutionId
  authorized_candidates: ContextCandidate[]
  excluded: SharedContextExclusion[]
  policy_version: Version
  grant_status: CONSUMED
  resolution_count: PositiveInteger
  truncated: Boolean
  correlation_id: CorrelationId
}

SharedContextExclusion {
  shared_ref_id: SharedContextReferenceId | null
  source_kind: SharedContextKind
  required: Boolean
  reason: NOT_AUTHORIZED | REVOKED | EXPIRED | VERSION_MISMATCH |
          INTEGRITY_FAILED | FILTERED | BUDGET_EXCEEDED | SOURCE_UNAVAILABLE
  source_version: Version | null
}
```

`ResolvedContextSeed` é entrada candidata para a RFC 104, não um Context pronto. O `ContextManager` ainda aplica sanidade, prioridade e orçamento do modelo.

Uma resolução confirmada compara `expected_resolution_count`, deduplica por `idempotency_key` e, na mesma mudança conceitual, incrementa `resolution_count` em exatamente uma unidade. A primeira resolução transiciona `ACTIVE -> CONSUMED`; resoluções subsequentes autorizadas por `MULTI_USE_UNTIL_TERMINAL` mantêm `CONSUMED` e incrementam o contador. Falha, negação, cancelamento ou repetição da mesma chave não incrementam o contador. Grant `SINGLE_USE` em `CONSUMED` rejeita nova chave de resolução.

```text
RevokeContextShare {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  grant_id: ContextShareGrantId
  reason: RevocationReason
  purpose: RevocationPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ExpireContextShare {
  actor: ActorRef
  execution_id: ExecutionId
  user_id: UserId
  workspace_id: WorkspaceId | null
  source_agent_id: AgentId
  target_agent_id: AgentId
  source_execution_id: ExecutionId
  target_execution_id: ExecutionId
  grant_id: ContextShareGrantId
  policy_cutoff_at: Instant
  reason: ExpirationReason
  purpose: RetentionPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

RevocationReceipt {
  grant_id: ContextShareGrantId
  previous_status: ACTIVE | CONSUMED
  status: REVOKED
  target_execution_id: ExecutionId
  correlation_id: CorrelationId
}

ExpirationReceipt {
  grant_id: ContextShareGrantId
  previous_status: PENDING | ACTIVE | CONSUMED
  status: EXPIRED
  target_execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

## Permissões e filtros

Autorização considera simultaneamente:

- identidade e status de origem e destino;
- `user_id`, `workspace_id`, `agent_id` e `execution_id` exatos;
- finalidade e duração;
- tipo e classificação do conteúdo;
- autorização vigente da fonte;
- orçamento e necessidade para a Task;
- possibilidade de redelegação, negada por padrão.

Filtros são aplicados antes de ler conteúdo quando possível e novamente após resolução. Podem restringir tipos, versões, intervalo temporal, autores, campos, classificação e quantidade. Um filtro nunca torna elegível conteúdo que a fonte não autorizou.

Cross-workspace e cross-user são negados por padrão. Um futuro contrato explícito pode permitir compartilhamento, mas deverá usar Grant dedicado, fonte autorizada e auditoria; correlação ou owner comum não bastam. Compartilhamento cross-agent no mesmo Workspace também exige autorização explícita, sobretudo para Private Memory.

## Orçamento e minimização

O orçamento de compartilhamento é um teto adicional ao orçamento de Context. A criação:

1. remove duplicatas;
2. prefere referência a conteúdo inline;
3. seleciona apenas dependências necessárias;
4. resume sequências pequenas com cobertura declarada;
5. exclui itens opcionais por prioridade;
6. falha se um item obrigatório não couber de forma íntegra.

É PROIBIDO copiar centenas de mensagens ou histórico bruto, mesmo se o orçamento técnico permitir. O remetente deve produzir resumo estruturado e referências para fontes específicas. Prompts de sistema, cadeia de raciocínio, segredos e ContextSnapshot completo são sempre proibidos.

## Ciclo de vida e revogação

```text
PENDING -> ACTIVE -> CONSUMED
    |        |         |-> REVOKED
    |        |         \-> EXPIRED
    |        |-> REVOKED
    |        |-> EXPIRED
    |        \-> CANCELLED
    |-> EXPIRED
    \-> CANCELLED
```

- `PENDING` ainda não pode resolver conteúdo;
- `ACTIVE` permite resolução conforme Grant;
- `CONSUMED` confirma ao menos uma entrega. Em `SINGLE_USE`, novas resoluções são proibidas; em `MULTI_USE_UNTIL_TERMINAL`, novas resoluções ainda são permitidas dentro do orçamento cumulativo até revogação ou expiração;
- `REVOKED` impede novas resoluções e invalida caches derivados;
- `EXPIRED` resulta do prazo e falha fechado;
- `CANCELLED` interrompe preparação antes de entrega confirmada.

`CONSUMED` é terminal para entrega de Grant `SINGLE_USE`, mas não para sua governança: tanto Grant `SINGLE_USE` quanto `MULTI_USE_UNTIL_TERMINAL` podem transicionar de `CONSUMED` para `REVOKED` ou `EXPIRED`. Revogação pode ocorrer por decisão autorizada antes de `expires_at`; expiração ocorre quando o prazo ou política vence. Nenhum estado retorna a `ACTIVE`, e `CANCELLED` só é válido antes de consumo confirmado. Repetições idempotentes de revoke/expire retornam o terminal já confirmado; uma tentativa concorrente preserva o primeiro terminal e registra a segunda como no-op auditável.

Revogação não apaga o que um destinatário autorizado já processou, mas impede novos usos e sinaliza ao `ContextManager` que o item deve ser removido na próxima montagem. Material temporário e caches são eliminados; auditoria mínima permanece. Se política exigir apagar derivados persistentes, lineage identifica Memories, Artifacts ou itens criados, e cada domínio executa sua própria remoção autorizada.

## Fluxo normal

1. A origem define objetivo, destino e itens necessários para uma `Execution` alvo.
2. O serviço valida ownership, finalidade, classificação, prazo e orçamento.
3. Um `ContextShareGrant` ativo é confirmado.
4. Fontes são convertidas em referências opacas; snapshot mínimo é criado apenas se necessário.
5. O handoff estruturado é confirmado e entregue por referência.
6. O destino revalida Grant, fonte, versão e integridade.
7. O serviço retorna candidatos autorizados e registra exclusões.
8. O `ContextManager` do destino monta Context próprio e registra manifesto.

## Fluxo de falha

- origem ou destino incompatível com usuário/Workspace causa negação sem fallback;
- Grant expirado, revogado ou de finalidade divergente não resolve conteúdo;
- referência quebrada, versão divergente ou integridade inválida é excluída ou falha se obrigatória;
- item acima da classificação permitida não é resumido para contornar política;
- excesso de orçamento resulta em redução rastreável ou falha explícita;
- snapshot não confirmado nunca é entregue como parcial;
- falha de uma fonte opcional não autoriza copiar histórico bruto;
- retry com mesma chave não duplica Grant, snapshot ou handoff;
- destino suspenso ou Execution terminal impede nova entrega.

## Fluxo de cancelamento

1. O Runtime ou coordenador autorizado solicita cancelamento pela `Execution` correspondente.
2. Novas resoluções são bloqueadas e o Grant entra em `CANCELLED` quando elegível.
3. Preparação de snapshot interrompe em limite seguro e descarta material não confirmado.
4. Handoff não consumido deixa de ser resolvível.
5. Conteúdo já consumido é sinalizado para remoção na próxima remontagem, sem alegar que foi apagado do modelo.
6. Auditoria e fatos confirmados permanecem; sucesso não é fabricado.

## Eventos

Todos usam o envelope da RFC 103 e descrevem fatos passados.

| Event | Fato confirmado |
| --- | --- |
| `ContextShareAuthorized` | um Grant mínimo tornou-se ativo |
| `SharedContextReferenceCreated` | uma referência autorizada e versionada foi confirmada |
| `MinimalContextSnapshotCreated` | um snapshot limitado e íntegro foi confirmado |
| `StructuredHandoffCreated` | um handoff com objetivo, limites e refs foi confirmado |
| `SharedContextResolved` | o destino recebeu candidatos autorizados e limitados |
| `SharedContextConsumed` | o uso previsto pela política foi confirmado |
| `ContextShareRevoked` | novas resoluções deixaram de ser permitidas |
| `ContextShareExpired` | o prazo do Grant ou material compartilhado venceu |
| `ContextShareCancelled` | preparação ou entrega deixou de prosseguir |
| `ContextShareAccessDenied` | uma tentativa foi recusada por política |
| `ContextShareFailed` | a operação terminou sem entrega utilizável |

Payloads incluem IDs, origem, destino, `execution_id`, `agent_id`, contagens, classificação categórica, razões, `correlation_id` e versões. Não incluem conteúdo, resumos livres, prompts, histórico ou segredos.

## Segurança

- autorização da fonte e Grant de compartilhamento são ambos necessários;
- Grants são mínimos, temporais, revogáveis e vinculados a finalidade e destino;
- redelegação é proibida salvo permissão explícita e nunca pode ampliar escopo;
- referências e snapshots são protegidos contra enumeração e adulteração;
- origem, destino e conteúdo são tratados como não confiáveis em cada fronteira;
- Private Memory requer autorização específica do owner e do `MemoryManager`;
- Workspace, Agent e usuário são validados antes e depois da resolução;
- segredos, credenciais, handles vivos, prompts internos e cadeia de raciocínio não são compartilhados;
- classificação e redaction acompanham transformações e não podem ser reduzidas silenciosamente;
- logs, Events e manifests usam metadados mínimos.

## Observabilidade

Logs e traces permitem reconstruir autorização, criação de referência, snapshot, handoff, resolução, consumo, revogação, expiração e cancelamento. Métricas incluem volume por modo, latência, refs por handoff, tamanho de snapshots, exclusões por filtro, orçamento utilizado, referências quebradas, negações cross-workspace, revogações, expirações e resoluções após revogação bloqueadas.

Auditoria registra ator, origem, destino, finalidade, política, filtros, orçamento, fontes por ID, versões e resultado. Conteúdo e texto livre não são labels.

## Extensibilidade

Novos tipos de referência, filtros, formatos de snapshot e handoff podem entrar por contratos versionados. Cada extensão DEVE declarar ownership, finalidade, orçamento, classificação, revogação, compatibilidade, eventos e comportamento de falha. Nenhuma extensão pode copiar Context completo, criar autorização transitiva ou contornar o `ContextManager` do destino.

## Invariantes

- cada Agent monta Context próprio, temporário e específico da sua `Execution`.
- compartilhamento usa referências primeiro e snapshots mínimos apenas quando necessários.
- copiar centenas de mensagens, histórico bruto ou Context completo é proibido.
- todo handoff declara objetivo, critérios, restrições, resultado esperado, orçamento e validade.
- origem, destino, `user_id`, `workspace_id`, `agent_id`, `execution_id` e `correlation_id` permanecem explícitos.
- conhecer referência, participar de Collaboration ou compartilhar correlação não concede acesso.
- nenhuma autorização é herdada entre Agents ou ampliada por resumo.
- toda referência é reautorizada no uso e limitada por finalidade.
- cross-user, cross-workspace e cross-agent são negados sem autorização explícita.
- revogação impede novas resoluções e invalida material temporário derivado.
- snapshot mínimo não é Memory nem fonte transacional de verdade.
- resolução retorna candidatos; somente o `ContextManager` monta Context.
- falha e cancelamento não entregam pacote parcial como completo.
- Events são fatos passados e não carregam conteúdo sensível.

## Futuro

Poderão ser adicionados handoffs multimodais, grants entre organizações, políticas de data residency, attestations de integridade, revisão humana e negociação automática de orçamento. Qualquer evolução manterá compartilhamento mínimo, referencial, revogável, reautorizado e separado de Memory persistente e Context temporário.
