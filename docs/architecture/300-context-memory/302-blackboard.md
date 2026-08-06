# RFC 302 — Blackboard

**Estado:** Proposta arquitetural  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 203 — Multi-agent](../200-agents/203-multi-agent.md), [RFC 301 — Memory](301-memory.md)

## Objetivo

Definir o Blackboard como espaço governado de conhecimento compartilhado para decisões, descobertas, bugs, tarefas, contratos e arquitetura. O Blackboard permite que Agents e usuários publiquem, relacionem, revisem e consultem itens estruturados com autoria, proveniência, versão, visibilidade, conflitos, auditoria e expiração.

O Blackboard complementa coordenação multi-agent e Memory, mas não substitui a fonte transacional de verdade de nenhum domínio nem torna Private Memory compartilhada.

## Fora de escopo

- ser fila, scheduler, issue tracker, catálogo de contratos executáveis ou banco transacional;
- governar estados de `Execution`, Task, Agent, Tool, Artifact ou recurso externo;
- armazenar histórico bruto de chat, Context completo, arquivos volumosos ou credenciais;
- replicar automaticamente Private Memory;
- definir modelos ORM, endpoints, migrations ou mecanismo de indexação;
- resolver semanticamente conflitos de negócio sem ator ou política autorizada;
- garantir que uma afirmação publicada seja verdadeira apenas por estar no Blackboard.

## Responsabilidades e não responsabilidades

O Blackboard DEVE:

- limitar cada board por `user_id` e `workspace_id` quando aplicável;
- representar itens tipados, versões imutáveis e um head atual explícito;
- registrar autoria, proveniência, classificação, visibilidade e referências;
- validar autorização em publicação, consulta, revisão, conflito, resolução e expiração;
- aplicar concorrência otimista e preservar versões divergentes;
- oferecer referências estáveis para Context, handoffs, Memory e Artifacts;
- registrar auditoria e emitir Events no passado;
- permitir expiração e arquivamento sem apagar lineage obrigatório.

O Blackboard NÃO DEVE:

- executar Tasks, transicionar bugs, aprovar contratos ou alterar arquitetura em sistemas de origem;
- tratar o item como autoridade maior que a fonte referenciada;
- conceder acesso a Memory privada, Artifact ou dado externo apenas porque há uma referência;
- compartilhar automaticamente entre Workspaces, usuários ou Agents;
- resolver referências sem reautorizar o consumidor;
- manter locks ou coordenação de Worker como regra de domínio;
- substituir `MemoryManager`, `ContextManager`, Orchestrator ou Event Bus.

## Papel arquitetural e limites de verdade

```text
Agents / Users / Executions
          │ comandos autorizados
          ▼
      Blackboard
  itens + versões + relações
          │ referências
   ┌──────┼───────────┐
   ▼      ▼           ▼
Context  Handoff    Memory candidate
          │
          └── fonte transacional continua externa
```

Um item `TASK` descreve ou referencia trabalho, mas não é a máquina de estado da `Execution`. Um item `BUG` registra conhecimento sobre defeito, mas não substitui o issue tracker autorizado. Um item `CONTRACT` ou `ARCHITECTURE` comunica proposta ou decisão, mas sua validade normativa depende do documento ou repositório referenciado. Um item `DECISION` deve distinguir `PROPOSED`, `ACCEPTED` e `SUPERSEDED`; presença no board não equivale a aprovação.

## Categorias de conhecimento

| Tipo | Conteúdo compartilhado | Fonte de verdade preservada |
| --- | --- | --- |
| `DECISION` | decisão proposta, aceita, rejeitada ou substituída | ADR, documento ou autoridade indicada |
| `DISCOVERY` | observação, hipótese, evidência ou aprendizado | Artifact, Tool result, fonte externa ou Memory referenciada |
| `BUG` | sintomas, impacto, reprodução e evidências | issue tracker, código e resultados de execução |
| `TASK` | objetivo, dependências e estado informativo | sistema de Tasks/Executions autorizado |
| `CONTRACT` | interface, premissa ou compatibilidade acordada | RFC, schema ou repositório de contrato referenciado |
| `ARCHITECTURE` | restrição, proposta ou consequência arquitetural | RFC/ADR/documento canônico referenciado |

O Blackboard PODE conter resumo pequeno para descoberta. Conteúdo durável ou volumoso permanece em Artifact e é acessado por referência.

## Arquitetura

```text
Blackboard commands / queries
             │
             ▼
       BlackboardService
  ┌──────────┼───────────┐
  │          │           │
Policy    Versioning   ConflictResolver
  │          │           │
  └──────────┼───────────┘
             │ portas públicas
      ┌──────┴────────┐
      ▼               ▼
 BlackboardStore   Audit / EventBus
```

O serviço contém regras de domínio; o store preserva itens, revisões e relações sem decidir autorização. Consulta por Context ou Agent passa por uma porta pública e retorna somente referências autorizadas e resumos limitados.

## Entidades e dados conceituais

O pseudocódigo é tipado, contratual e não executável.

```text
Blackboard {
  blackboard_id: BlackboardId
  user_id: UserId
  workspace_id: WorkspaceId | null
  name: BoundedText
  policy_ref: BlackboardPolicyRef
  classification_ceiling: DataClassification
  created_by: ActorRef
  created_execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
  version: Version
}
```

Boards de projeto exigem `workspace_id`. Board estritamente do usuário pode ter `workspace_id` nulo, mas a exposição de qualquer item a uma `Execution` de Workspace exige Grant explícito e filtro de classificação.

```text
BlackboardItem {
  item_id: BlackboardItemId
  blackboard_id: BlackboardId
  user_id: UserId
  workspace_id: WorkspaceId | null
  type: BlackboardItemType
  title: BoundedText
  summary: BoundedText
  status: BlackboardItemStatus
  author: ActorRef
  author_agent_id: AgentId | null
  source_execution_id: ExecutionId
  correlation_id: CorrelationId
  provenance: BlackboardProvenance
  visibility: BlackboardVisibility
  classification: DataClassification
  reference_ids: BlackboardReferenceId[]
  conflict_set_id: ConflictSetId | null
  current_version: Version
  created_at: Instant
  updated_at: Instant
  expires_at: Instant | null
}

BlackboardItemType = DECISION | DISCOVERY | BUG | TASK | CONTRACT | ARCHITECTURE
BlackboardItemStatus = PROPOSED | ACTIVE | RESOLVED | REJECTED | SUPERSEDED | ARCHIVED | EXPIRED
```

```text
BlackboardItemVersion {
  item_id: BlackboardItemId
  version: Version
  previous_version: Version | null
  title: BoundedText
  summary: BoundedText
  status: BlackboardItemStatus
  provenance: BlackboardProvenance
  visibility: BlackboardVisibility
  classification: DataClassification
  authorization_basis_ref: AuthorizationBasisRef
  reference_ids: BlackboardReferenceId[]
  changed_by: ActorRef
  changed_by_agent_id: AgentId | null
  execution_id: ExecutionId
  correlation_id: CorrelationId
  change_reason: ChangeReason
  created_at: Instant
  integrity_ref: IntegrityRef
}

BlackboardProvenance {
  source_refs: SourceReference[]
  authored_at: Instant
  observed_at: Instant | null
  confidence: Confidence | null
  transformation_chain: TransformationRef[]
}
```

Versões são imutáveis. O item aponta para o head atual; mudar conteúdo cria versão nova. Cada `BlackboardItemVersion` captura `provenance`, `visibility`, `classification` e a base de autorização vigentes, em vez de herdá-las implicitamente do head mutável. Remover referência em revisão não apaga o objeto referenciado nem a revisão anterior.

```text
BlackboardVisibility {
  mode: PRIVATE_TO_AUTHOR | AGENT_SET | WORKSPACE | USER
  permitted_agent_ids: AgentId[]
  authorization_ref: BlackboardGrantRef
  purpose_constraints: AccessPurpose[]
}

BlackboardReference {
  reference_id: BlackboardReferenceId
  kind: ARTIFACT | MEMORY | EXECUTION | EVENT | DOCUMENT | EXTERNAL_SOURCE | ITEM
  target_ref: AuthorizedReference
  target_version: Version | null
  label: BoundedText
  added_by: ActorRef
  added_at: Instant
}
```

Uma referência não transfere autorização. `MEMORY` privada só pode ser resolvida pelo consumidor que também possua um `MemoryGrantRef` válido.

```text
BlackboardConflictSet {
  conflict_set_id: ConflictSetId
  blackboard_id: BlackboardId
  item_ids: BlackboardItemId[]
  basis: SAME_SUBJECT | CONTRADICTORY_CLAIM | CONCURRENT_EDIT | DUPLICATE
  status: OPEN | RESOLVED | DISMISSED
  detected_by: ActorRef
  detected_execution_id: ExecutionId
  resolution_ref: BlackboardResolutionRef | null
  created_at: Instant
  resolved_at: Instant | null
}

BlackboardResolution {
  resolution_id: BlackboardResolutionId
  conflict_set_id: ConflictSetId
  selected_item_ids: BlackboardItemId[]
  superseded_item_ids: BlackboardItemId[]
  rationale_ref: ArtifactReference | null
  decided_by: ActorRef
  execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
}
```

## Contratos públicos

```text
interface BlackboardService {
  publish(command: PublishBlackboardItem) -> BlackboardWriteReceipt
  revise(command: ReviseBlackboardItem) -> BlackboardWriteReceipt
  get(query: GetBlackboardItem) -> AuthorizedBlackboardItem
  query(query: QueryBlackboard) -> BlackboardQueryResult
  link(command: LinkBlackboardItems) -> BlackboardWriteReceipt
  declare_conflict(command: DeclareBlackboardConflict) -> ConflictReceipt
  resolve_conflict(command: ResolveBlackboardConflict) -> ResolutionReceipt
  expire(command: ExpireBlackboardItem) -> BlackboardWriteReceipt

  pre: ator, Agent, usuário, Workspace e finalidade são autorizados
  post: revisão confirmada é imutável e auditável
  post: referências retornadas preservam autorização própria
}
```

```text
PublishBlackboardItem {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  type: BlackboardItemType
  title: BoundedText
  summary: BoundedText
  status: BlackboardItemStatus
  provenance: BlackboardProvenance
  visibility: BlackboardVisibility
  classification: DataClassification
  references: BlackboardReference[]
  expires_at: Instant | null
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ReviseBlackboardItem {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  item_id: BlackboardItemId
  expected_version: Version
  patch: BlackboardItemPatch
  change_reason: ChangeReason
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

BlackboardItemPatch {
  title: PatchValue<BoundedText>
  summary: PatchValue<BoundedText>
  status: PatchValue<BlackboardItemStatus>
  provenance: PatchValue<BlackboardProvenance>
  visibility: PatchValue<BlackboardVisibility>
  classification: PatchValue<DataClassification>
  reference_additions: BlackboardReference[]
  reference_removals: BlackboardReferenceId[]
  expires_at: PatchValue<Instant | null>
}

PatchValue<T> = UNCHANGED | SET<T> | CLEAR
```

`patch` é uma alteração conceitual tipada, não formato HTTP. Somente os campos listados em `BlackboardItemPatch` são mutáveis; identidade, board, ownership, autor original e instante de criação não podem ser alterados. `CLEAR` só é válido para campo anulável. Se `expected_version` divergir, a revisão é recusada ou preservada como candidato de conflito segundo política; nunca sobrescreve o head silenciosamente.

```text
GetBlackboardItem {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  item_id: BlackboardItemId
  requested_version: Version | null
  purpose: AccessPurpose
  correlation_id: CorrelationId
}

AuthorizedBlackboardItem {
  item_ref: BlackboardItemReference
  authorized_version: Version
  applied_visibility: BlackboardVisibility
  policy_version: Version
  correlation_id: CorrelationId
}

BlackboardItemReference {
  blackboard_id: BlackboardId
  item_id: BlackboardItemId
  version: Version
  user_id: UserId
  workspace_id: WorkspaceId | null
  type: BlackboardItemType
  status: BlackboardItemStatus
  author: ActorRef
  classification: DataClassification
  authorization_ref: BlackboardGrantRef
  expires_at: Instant | null
  integrity_ref: IntegrityRef
}

QueryBlackboard {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  item_types: BlackboardItemType[]
  statuses: BlackboardItemStatus[]
  reference_filter: ReferenceFilter | null
  maximum_items: PositiveInteger
  maximum_summary_units: PositiveInteger
  purpose: AccessPurpose
  correlation_id: CorrelationId
}

BlackboardQueryResult {
  items: BlackboardItemReference[]
  applied_scope: AuthorizedScope
  policy_version: Version
  truncated: Boolean
  correlation_id: CorrelationId
}
```

Consulta retorna referências, metadados e resumos mínimos. O `ContextManager` decide inclusão temporária e orçamento; Blackboard não monta Context.

```text
BlackboardRelationType = SUPPORTS | CONTRADICTS | DUPLICATES | BLOCKS |
                         DEPENDS_ON | SUPERSEDES | REFINES | REFERENCES

ItemExpectedVersion {
  item_id: BlackboardItemId
  expected_version: Version
}

LinkBlackboardItems {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  source_item_id: BlackboardItemId
  target_item_id: BlackboardItemId
  source_expected_version: Version
  target_expected_version: Version
  relation: BlackboardRelationType
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

DeclareBlackboardConflict {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  item_ids: BlackboardItemId[]
  expected_versions: ItemExpectedVersion[]
  basis: SAME_SUBJECT | CONTRADICTORY_CLAIM | CONCURRENT_EDIT | DUPLICATE
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ResolveBlackboardConflict {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  conflict_set_id: ConflictSetId
  expected_versions: ItemExpectedVersion[]
  selected_item_ids: BlackboardItemId[]
  superseded_item_ids: BlackboardItemId[]
  rationale_ref: ArtifactReference | null
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ExpireBlackboardItem {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  blackboard_id: BlackboardId
  item_id: BlackboardItemId
  expected_version: Version
  reason: ExpirationReason
  purpose: RetentionPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

BlackboardWriteReceipt {
  blackboard_id: BlackboardId
  item_id: BlackboardItemId
  version: Version
  status: BlackboardItemStatus
  execution_id: ExecutionId
  correlation_id: CorrelationId
}

ConflictReceipt {
  conflict_set_id: ConflictSetId
  status: OPEN | RESOLVED | DISMISSED
  execution_id: ExecutionId
  correlation_id: CorrelationId
}

ResolutionReceipt {
  resolution_id: BlackboardResolutionId
  conflict_set_id: ConflictSetId
  status: RESOLVED
  execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

## Versionamento e conflitos

Cada mutação usa `expected_version`. Edições concorrentes não são mescladas implicitamente. A política PODE:

- rejeitar a segunda revisão com a versão atual;
- criar branch de revisão e `BlackboardConflictSet`;
- aceitar alteração em campos comprovadamente independentes, registrando transformação determinística.

Contradição entre itens não elimina nenhum deles. Resolução cria registro explícito, aponta selecionados e superseded, preserva fontes e exige autoridade compatível com o tipo do item. Um Agent pode detectar conflito sem possuir autoridade para decidir arquitetura ou contrato.

## Autoria, visibilidade e referências

Autoria nunca é reescrita por transformação. Revisões registram o novo autor sem apagar o original. `author_agent_id` identifica Agent quando aplicável; ações humanas ou de serviço usam `ActorRef`.

Visibilidade é aplicada por item e limitada pelo teto do board. `WORKSPACE` significa somente atores autorizados naquele Workspace, não publicação global. `AGENT_SET` enumera Agents e não transfere acesso aos dados referenciados. Conteúdo de outro Workspace não pode ser anexado como forma de contornar isolamento.

## Expiração e arquivamento

`expires_at` torna o item inelegível para novas consultas após o prazo. Expiração cria versão/status `EXPIRED`, revoga referências de compartilhamento derivadas quando aplicável e mantém auditoria mínima. Arquivamento remove o item da consulta padrão, mas permite acesso histórico autorizado.

Itens normativos ou sob retenção legal podem exigir substituição explícita em vez de expiração automática. A expiração do item não apaga Artifact, Memory ou fonte externa; cada domínio mantém seu próprio ciclo de vida.

## Fluxo normal

1. Uma `Execution` produz item estruturado com tipo, resumo, fontes e visibilidade.
2. O serviço valida board, ownership, Agent, classificação e referências.
3. A versão inicial é persistida de forma idempotente.
4. Auditoria e `BlackboardItemPublished` são confirmados.
5. Outro Agent consulta o board com escopo e orçamento limitados.
6. O serviço devolve referências autorizadas; cada alvo é reautorizado na resolução.
7. O `ContextManager` inclui somente os itens relevantes no Context temporário.

## Fluxo de falha

- board ou item de outro Workspace é negado sem revelar conteúdo;
- referência não autorizada rejeita publicação ou fica indisponível conforme política explícita, nunca é copiada inline;
- fonte obrigatória ausente ou proveniência inválida impede publicação;
- conflito de versão não sobrescreve revisão existente;
- falha do store não emite Event de sucesso;
- consulta excedente é truncada por limites declarados, não ampliada automaticamente;
- status incompatível com tipo produz erro contratual;
- retry idempotente retorna o mesmo recibo e não duplica item.

## Fluxo de cancelamento

1. O Runtime propaga cancelamento da `Execution` responsável.
2. O serviço deixa de resolver novas referências e interrompe transformações em limite seguro.
3. Revisão não confirmada é descartada; versão confirmada não é revertida.
4. Resolução de conflito parcial não altera heads nem status.
5. Consultas canceladas não entregam conjunto parcial como completo.
6. Auditoria confirmada é preservada e o Runtime recebe resultado de cancelamento explícito.

## Eventos

Todos usam o envelope da RFC 103 e descrevem fatos passados.

| Event | Fato confirmado |
| --- | --- |
| `BlackboardCreated` | um board governado foi registrado |
| `BlackboardItemPublished` | a primeira versão de um item foi confirmada |
| `BlackboardItemRevised` | uma nova versão tornou-se o head do item |
| `BlackboardItemsLinked` | uma relação tipada foi confirmada |
| `BlackboardConflictDetected` | versões ou afirmações incompatíveis foram agrupadas |
| `BlackboardConflictResolved` | uma resolução autorizada foi registrada |
| `BlackboardItemSuperseded` | um item passou a apontar para substituto explícito |
| `BlackboardItemArchived` | um item saiu da consulta padrão |
| `BlackboardItemExpired` | um item deixou de ser elegível para novas consultas |
| `BlackboardAccessDenied` | uma operação foi recusada por política |
| `BlackboardOperationFailed` | uma operação terminou sem efeito confirmado |

Payloads incluem IDs, tipo, status, versão, `agent_id`, `execution_id` aplicável, `correlation_id` e razões categóricas. Não incluem resumos livres, conteúdo referenciado, segredo ou histórico.

## Segurança

- acesso é negado por padrão entre usuários, Workspaces e Agents sem Grant explícito;
- conhecer `blackboard_id`, `item_id` ou referência não autoriza leitura;
- publicação e resolução revalidam classificação e autoridade do ator;
- referências são opacas, limitadas por finalidade e reautorizadas no destino;
- Private Memory não se torna compartilhada por ser citada em item;
- conteúdo de Agent, Tool, Artifact ou fonte externa permanece não confiável e não altera hierarquia de instruções;
- redaction não pode reduzir classificação nem apagar proveniência;
- limites de consulta, fan-out e tamanho reduzem enumeração e exfiltração;
- logs, Events e métricas não contêm conteúdo privado.

## Observabilidade

Logs e traces correlacionam publicação, revisão, consulta, link, conflito, resolução, expiração e negação. Métricas incluem itens por tipo/status, latência, conflitos de versão, conflitos abertos, expirações, referências quebradas, consultas truncadas, negações, fan-out e idade de itens ativos.

Auditoria registra ator, `agent_id`, `execution_id`, `user_id`, `workspace_id`, finalidade, política, versões observadas, referências e resultado. Conteúdo livre não é usado como label.

## Extensibilidade

Novos tipos de item, relações, políticas de conflito e visibilidades podem entrar por contratos versionados. Cada extensão DEVE declarar autoridade, fonte transacional preservada, campos mínimos, compatibilidade, expiração, eventos e comportamento de referência. Plugins não podem criar boards globais implícitos nem resolver conflito normativo sem autorização.

## Invariantes

- Blackboard compartilha conhecimento; não executa trabalho nem substitui fonte transacional de verdade.
- Blackboard não substitui Private Memory nem concede acesso a ela.
- todo board e item possuem ownership, autoria, proveniência, classificação, versão e auditoria.
- board de projeto usa `workspace_id`; acesso cross-workspace é negado por padrão.
- toda publicação e revisão que produz trabalho pertence a uma `Execution`.
- versões são imutáveis; mutações criam versões novas.
- concorrência divergente nunca causa sobrescrita silenciosa.
- conflito permanece explícito até resolução autorizada.
- referência não transfere autorização e é revalidada em cada resolução.
- consulta retorna conjunto mínimo, filtrado e limitado.
- Context recebe referências autorizadas, não histórico bruto do board.
- expiração não apaga fonte externa nem lineage obrigatório.
- Events descrevem fatos passados e não carregam conteúdo sensível.

## Futuro

Poderão ser adicionados subscriptions, views por equipe, aprovação humana, votação, regras de quorum, sincronização com issue trackers e análise de inconsistências. Integrações futuras manterão a fonte transacional externa, o isolamento por Workspace e a distinção entre informação compartilhada, Memory privada e Context temporário.
