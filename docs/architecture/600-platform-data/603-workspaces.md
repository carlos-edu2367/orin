# RFC 603 — Workspaces

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 402 — Resource Manager](../400-tools-resources/402-resource-manager.md), [RFC 403 — Filesystem](../400-tools-resources/403-filesystem.md), [RFC 601 — Persistência](601-persistence.md), [RFC 602 — Artifact Storage](602-artifact-storage.md), [RFC 604 — Configuração](604-configuration.md)

## Objetivo

Definir Workspace como limite lógico de projeto, ownership e isolamento operacional, com raiz de filesystem confiável, lifecycle, quotas, locks, canonicalização e limpeza recuperável. Nenhuma Tool, Agent, Resource ou rotina de manutenção pode escapar da raiz nem atravessar ownership por caminho, symlink, corrida ou reutilização de identificador.

## Fora de escopo

- definir UI de projetos, colaboração, convite ou billing;
- escolher layout físico, volume, container, sistema operacional ou serviço remoto;
- repetir operações de arquivo da RFC 403 ou bytes duráveis da RFC 602;
- permitir que chamador forneça raiz física ou monte diretório arbitrário;
- definir VCS, sincronização, backup ou merge de arquivos;
- tratar lock de Workspace como transação distribuída ou autorização.

## Responsabilidades e não responsabilidades

O `WorkspaceManager` DEVE:

- criar identidade persistente e ownership antes de provisionar recursos;
- resolver cada `workspace_id` para exatamente uma raiz ativa e canonicalizada;
- separar raízes entre usuários e Workspaces, inclusive no modo single-user;
- definir lifecycle explícito, quotas e políticas de classificação;
- emitir leases e locks limitados para mutações administrativas;
- impedir path traversal, symlink/junction/reparse escape e troca de raiz em corrida;
- suspender novas operações antes de arquivar ou limpar;
- executar limpeza em etapas recuperáveis, idempotentes e auditáveis;
- reconciliar roots órfãs, metadata divergente e operações interrompidas.

O `WorkspaceManager` NÃO DEVE:

- executar Task, Tool ou Capability;
- aceitar caminho físico, `..`, drive, UNC, URL ou namespace de device como identidade;
- autorizar acesso porque processo atual está dentro da raiz;
- usar lock Redis como prova de ownership ou de validade da raiz;
- apagar recursivamente alvo não canonicalizado, root vazia, ampla ou divergente;
- mover silenciosamente dados para outro usuário ou Workspace;
- expor raiz física a Agent, Provider, frontend, Event ou log.

## Arquitetura

```text
Runtime / Resource Manager / manutenção
             │ WorkspaceOperationContext
             ▼
        WorkspaceManager
  ┌──────────┼────────────┐
  │ Registry & ownership  │──> PostgreSQL
  │ Lifecycle & policy    │
  │ Root provisioner      │──> raiz isolada
  │ Quota accounting      │
  │ Lease / lock          │──> coordenação efêmera
  │ Cleanup reconciler    │
  └──────────┴────────────┘
```

O registro durável é autoridade sobre identidade, owner, estado e root ref opaca. A raiz física é resolvida apenas pelo `WorkspaceRootResolver`. O Resource Manager fornece leases operacionais; FilesystemPort valida cada path. O lock coordena transições administrativas, mas toda transição compara versão durável.

## Dados e lifecycle

```text
WorkspaceOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

CreateWorkspaceContext {
  user_id: UserId
  requested_workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

invariant: requested_workspace_id é preferência opaca ou nulo para alocação pelo servidor
invariant: nenhum Workspace pré-existente é exigido para autorizar a criação

WorkspaceRecord {
  workspace_id: WorkspaceId
  user_id: UserId
  display_name: BoundedText
  state: WorkspaceState
  root_ref: OpaqueWorkspaceRootRef | null
  root_identity: FilesystemObjectIdentity | null
  quota_policy_ref: WorkspaceQuotaPolicyRef
  configuration_ref: WorkspaceConfigurationRef
  classification: DataClassification
  version: Version
  created_at: Instant
  activated_at: Instant | null
  archived_at: Instant | null
  deletion_requested_at: Instant | null
  deleted_at: Instant | null
}

WorkspaceState = PROVISIONING | ACTIVE | SUSPENDING | SUSPENDED |
                 ARCHIVING | ARCHIVED | DELETING | DELETED |
                 RECOVERY_REQUIRED | FAILED
```

`CreateWorkspaceContext` é a única exceção de bootstrap ao `WorkspaceOperationContext`: antes da criação ainda não existe `workspace_id` pertencente ao projeto. A autorização parte de `user_id`, ator, Agent, Execution, correlação e finalidade; o servidor aloca um ID ou aceita `requested_workspace_id` somente se a política permitir e ele estiver livre. O ID final é vinculado ao mesmo `user_id` na primeira transação e passa a ser o `workspace_id` obrigatório de todas as operações seguintes.

Transições permitidas são explícitas e versionadas. `PROVISIONING` não aceita operações normais. `ACTIVE` aceita leases segundo política. `SUSPENDING` bloqueia novos leases e drena os existentes. `SUSPENDED` preserva dados sem permitir trabalho. `ARCHIVED` é somente leitura quando a política autorizar. `DELETING` nunca retorna a `ACTIVE` sem recuperação administrativa explícita. `DELETED` é terminal e o ID não é reutilizado.

```text
WorkspaceRootDescriptor {
  workspace_id: WorkspaceId
  root_ref: OpaqueWorkspaceRootRef
  root_identity: FilesystemObjectIdentity
  storage_class: WorkspaceStorageClass
  containment_policy_version: Version
  provisioned_at: Instant
  health: READY | DEGRADED | QUARANTINED | MISSING
}

WorkspaceQuota {
  maximum_bytes: NonNegativeInteger
  maximum_entries: NonNegativeInteger
  maximum_file_bytes: NonNegativeInteger
  maximum_depth: NonNegativeInteger
  maximum_active_leases: NonNegativeInteger
  reserved_bytes: NonNegativeInteger
}

WorkspaceUsage {
  accounted_bytes: NonNegativeInteger
  accounted_entries: NonNegativeInteger
  reserved_bytes: NonNegativeInteger
  active_leases: NonNegativeInteger
  measured_at: Instant
  reconciliation_state: CURRENT | STALE | IN_PROGRESS | DIVERGENT
}
```

Quota lógica cobre root operacional. Artifacts podem ter quota separada na RFC 602; políticas agregadas evitam bypass entre stores. Uso `STALE` não autoriza exceder limite: novas reservas usam o valor mais conservador até reconciliação.

## Contratos tipados

```text
interface WorkspaceManager {
  create(command: CreateWorkspace) -> CreateWorkspaceResult
  activate(command: ActivateWorkspace) -> WorkspaceSnapshot
  inspect(query: InspectWorkspace) -> WorkspaceSnapshot
  acquire_lease(request: AcquireWorkspaceLease) -> WorkspaceLease
  renew_lease(request: RenewWorkspaceLease) -> WorkspaceLease
  release_lease(request: ReleaseWorkspaceLease) -> ReleaseWorkspaceLeaseResult
  transition(command: TransitionWorkspace) -> WorkspaceSnapshot
  delete(command: DeleteWorkspace) -> WorkspaceDeletionReceipt
  reconcile(command: ReconcileWorkspace) -> WorkspaceReconciliationReceipt

  pre: operações sobre Workspace existente usam contexto completo correspondente ao ownership durável
  pre: create usa CreateWorkspaceContext autorizado e não pressupõe Workspace existente
  post: nenhuma operação devolve root físico ou handle nativo
  post: create vincula o workspace_id alocado ao user_id antes de provisionamento externo
}
```

```text
CreateWorkspace {
  operation_id: WorkspaceOperationId
  context: CreateWorkspaceContext
  display_name: BoundedText
  quota_policy_ref: WorkspaceQuotaPolicyRef
  configuration_ref: WorkspaceConfigurationRef
  classification: DataClassification
  idempotency_key: IdempotencyKey
}

CreateWorkspaceResult =
  | WorkspaceCreationAccepted {
      workspace: WorkspaceSnapshot
      allocated_workspace_id: WorkspaceId
      ownership_version: Version
    }
  | WorkspaceCreationRejected { reason: WorkspaceCreationRejectionReason }
  | WorkspaceCreationConflicted {
      requested_workspace_id: WorkspaceId
      reason: ID_UNAVAILABLE | IDEMPOTENCY_CONFLICT
    }
  | WorkspaceCreationIndeterminate {
      operation_id: WorkspaceOperationId
      idempotency_key: IdempotencyKey
    }

pre: actor pode criar Workspace para user_id e purpose informados
pre: requested_workspace_id, quando presente, não representa ownership nem autorização
post: resultado aceito contém ID duravelmente reservado, único e pertencente a user_id
post: retry com a mesma idempotency_key retorna o mesmo ID ou conflito explícito

ActivateWorkspace {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  expected_version: Version
  expected_root_identity: FilesystemObjectIdentity
  idempotency_key: IdempotencyKey
}

InspectWorkspace {
  context: WorkspaceOperationContext
  include_usage: Boolean
  purpose: ReadPurpose
}

WorkspaceSnapshot {
  workspace_id: WorkspaceId
  user_id: UserId
  state: WorkspaceState
  classification: DataClassification
  quota: WorkspaceQuota
  usage: WorkspaceUsage | null
  version: Version
  policy_version: Version
}
```

Criação primeiro autoriza `CreateWorkspaceContext`, aloca ou reserva a preferência de ID e grava ownership e estado `PROVISIONING` na mesma transação; somente depois o provisioner cria uma root privada. A identidade física é registrada e só então `activate` torna o Workspace utilizável. Falha deixa estado reconciliável, nunca root ativa sem ownership. Se a confirmação da reserva for incerta, o chamador consulta pela mesma `idempotency_key` antes de tentar outro ID.

```text
AcquireWorkspaceLease {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  permissions: WorkspacePermission[]
  requested_duration: Duration
  budget: WorkspaceOperationBudget
  expected_workspace_version: Version
  idempotency_key: IdempotencyKey
}

WorkspaceLease {
  lease_id: WorkspaceLeaseId
  workspace_id: WorkspaceId
  context: WorkspaceOperationContext
  permissions: WorkspacePermission[]
  root_handle_ref: OpaqueRootHandleRef
  root_identity: FilesystemObjectIdentity
  workspace_version: Version
  expires_at: Instant
  state: ACTIVE | REVOKING | RELEASED | EXPIRED
}

RenewWorkspaceLease {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  lease_id: WorkspaceLeaseId
  expected_expires_at: Instant
  requested_extension: Duration
  idempotency_key: IdempotencyKey
}

ReleaseWorkspaceLease {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  lease_id: WorkspaceLeaseId
  reason: ReleaseReason
  idempotency_key: IdempotencyKey
}
```

`root_handle_ref` só pode ser resolvida no adapter autorizado do mesmo worker e lease; não é serializável nem durável. Lease expira, não transfere ownership e não sobrevive a mudança de root identity, Workspace version ou estado incompatível.

```text
TransitionWorkspace {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  target_state: SUSPENDING | SUSPENDED | ARCHIVING | ARCHIVED
  expected_version: Version
  drain_deadline: Instant
  reason: WorkspaceTransitionReason
  idempotency_key: IdempotencyKey
}

DeleteWorkspace {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  expected_version: Version
  expected_root_identity: FilesystemObjectIdentity
  deletion_policy_ref: WorkspaceDeletionPolicyRef
  recovery_window: Duration
  reason: WorkspaceDeletionReason
  idempotency_key: IdempotencyKey
}

ReconcileWorkspace {
  operation_id: WorkspaceOperationId
  context: WorkspaceOperationContext
  expected_version: Version
  scope: ROOT | USAGE | LEASES | CLEANUP | ALL
  maximum_entries: PositiveInteger
  idempotency_key: IdempotencyKey
}
```

## Raiz, canonicalização e containment

A raiz é criada sob uma base aprovada pelo adapter, com permissões e identidade próprias. `workspace_id` não é convertido em caminho por chamadores. Antes de emitir lease, o Manager:

1. resolve `root_ref` durável por porta interna;
2. abre a raiz sem seguir redirect não autorizado;
3. canonicaliza segundo semântica da plataforma;
4. compara identidade física com `root_identity` registrada;
5. rejeita symlink, junction, mount, reparse point ou hard-link ambiguity na própria root;
6. fixa um handle opaco e aplica policy version;
7. revalida state/version imediatamente antes da concessão.

A RFC 403 repete containment para cada operação e componente. Defesa em profundidade é obrigatória: validação do Workspace não substitui validação descriptor-relative do Filesystem. Comparação textual por prefixo é insuficiente. Case, Unicode, separadores, alternate streams, device namespaces e links são tratados pela política específica de plataforma; incerteza falha fechada.

## Ownership, autorização e colaboração

Todo Workspace possui exatamente um `user_id` proprietário no lançamento. Grants futuros podem delegar ações específicas, mas não mudam ownership nem permitem reuso da raiz. Agent autorizado permanece limitado à Execution e purpose declarados. Clone, importação ou transferência criam novo Workspace/root e lineage; não reatribuem diretório existente silenciosamente.

Conhecer `workspace_id`, nome, correlação ou caminho lógico não concede acesso. Erros para ator não autorizado não distinguem ausência, suspensão ou exclusão. Processos de manutenção usam Agent e Execution administrativos no mesmo ownership alvo.

## Locks, concorrência e quotas

Locks administrativos coordenam provisionamento, mudança de lifecycle e limpeza. São leases efêmeros com fencing token monotônico associado à versão durável. Um worker atrasado não pode aplicar efeito após outro adquirir token mais novo. Perda do lock não decide a transição; comparação de versão no PostgreSQL decide.

Operações comuns usam leases compartilhados compatíveis. Suspensão e exclusão solicitam barreira exclusiva, bloqueiam novos leases, revogam ou drenam ativos e só avançam após deadline e reconciliação. Quota reserva antes do efeito e contabiliza depois. Criação concorrente não pode exceder limite por leitura stale; usa reserva durável ou mecanismo de concorrência equivalente.

## Limpeza recuperável

Exclusão é workflow, não `rm -rf` direto:

1. confirmar alvo exato, ownership, estado, versão e root identity;
2. transicionar para `DELETING` e registrar fence;
3. proibir novos leases e drenar/revogar os existentes;
4. criar manifesto limitado das categorias a limpar;
5. mover ou marcar raiz para quarentena recuperável dentro do mesmo boundary, se suportado;
6. limpar por handles relativos, sem seguir links e com checkpoints;
7. reconciliar Artifacts, metadata, temporários e auditoria conforme suas políticas;
8. confirmar ausência ou tombstone da root esperada;
9. transicionar para `DELETED`, preservar tombstone e impedir reuso do ID.

Cada retry usa o mesmo `operation_id`, fence e identidade esperada. Divergência põe o Workspace em `RECOVERY_REQUIRED`; nunca amplia o alvo. A janela de recuperação pode restaurar somente antes da remoção física, com autorização e nova verificação de integridade.

## Fluxo normal

1. Comando usa `CreateWorkspaceContext`; o Manager autoriza o usuário e aloca ou valida a preferência de ID.
2. A transação cria o registro `PROVISIONING`, fixa ownership, quota e idempotência e devolve o `workspace_id` final.
3. Provisioner cria root isolada e retorna referência/identidade opacas.
4. Manager confirma root e ativa o Workspace transacionalmente.
5. Execution solicita lease limitado a permissões, orçamento e purpose.
6. Manager revalida estado, versão, root identity, quota e autorização.
7. Resource usa handle opaco; Filesystem valida cada caminho.
8. Release fecha handles, contabiliza uso e registra outcome.

## Fluxo de falha

- preferência de ID indisponível ou conflito idempotente falha antes de criar ownership;
- commit de criação indeterminado é inspecionado pela chave idempotente, sem alocar um segundo ID;
- provisionamento parcial deixa `PROVISIONING` ou `FAILED` e root em quarentena;
- root ausente, trocada ou não canonicalizável muda para `RECOVERY_REQUIRED`;
- quota divergente bloqueia novas reservas até reconciliação;
- lease expirado ou versão antiga não é renovado;
- lock perdido impede efeito administrativo por fencing;
- symlink escape, path traversal ou identidade divergente falham antes do efeito;
- cleanup parcial preserva checkpoint e `DELETING`, sem alegar exclusão.

## Fluxo de cancelamento

Criação cancelada antes de ativação mantém registro e root staging para cleanup; não publica Workspace ativo. Operação de arquivo segue a RFC 403. Suspensão cancelada antes da barreira pode retornar a `ACTIVE` por transição versionada; após revogar leases, não os restaura automaticamente. Exclusão cancelada antes de `DELETING` não tem efeito; após esse estado, a reconciliação termina em root recuperável ou exclusão confirmada, nunca reativa por impulso tardio.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `WorkspaceProvisioningStarted` | identidade e intenção de provisionar foram persistidas |
| `WorkspaceActivated` | root íntegra tornou-se utilizável |
| `WorkspaceSuspended` | novos leases foram bloqueados e barrier confirmada |
| `WorkspaceArchived` | política de arquivo foi aplicada |
| `WorkspaceDeletionStarted` | estado `DELETING` e fence foram confirmados |
| `WorkspaceDeleted` | exclusão lógica terminal foi confirmada |
| `WorkspaceRecoveryRequired` | divergência impede operação segura |
| `WorkspaceQuotaExceeded` | reserva foi recusada pelo limite aplicável |

Payloads incluem Workspace, ownership, versão, política, `execution_id`, correlação, purpose e razão categórica. Nunca incluem raiz física, handle, manifesto de nomes, conteúdo ou segredo.

## Segurança

- toda operação sensível sobre Workspace existente carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`; criação carrega `requested_workspace_id` ou alocação server-side junto dos demais campos e fixa o `workspace_id` no resultado;
- raiz vem de registro e adapter confiáveis, jamais do chamador;
- path traversal e escape por symlink são bloqueados na raiz e por operação;
- roots possuem isolamento e permissões distintas mesmo no modo single-user;
- locks e leases são limitados, revogáveis e não equivalem a autorização;
- cleanup exige alvo exato, identidade, fence, estado e versão;
- logs e Events não revelam path físico ou conteúdo;
- dados sensíveis seguem classificação, retenção e configuração própria;
- mount externo, share, device ou root ampla são negados por padrão.

## Observabilidade

Métricas incluem Workspaces por estado, provisionamento, leases, renovações, revogações, saturação, quota, drift de uso, roots ausentes/divergentes, violações de containment, locks vencidos, tempo de drain, cleanup e recuperação. Logs e traces usam IDs, state/version, policy, purpose e códigos sanitizados. Auditoria registra ator, lifecycle, concessão, mutação administrativa e resultado sem path físico.

## Invariantes

- cada Workspace possui ownership durável e uma única root ativa identificável.
- criação não exige Workspace pré-existente e fixa ID e ownership atomicamente antes do provisionamento.
- root física nunca é escolhida nem observada pelo chamador.
- nenhuma operação ocorre sem state compatível, lease válido e autorização.
- validação lexical jamais substitui containment por identidade física.
- symlink, junction, mount ou corrida não permitem escape da root.
- lock efêmero não substitui versão, fence ou verdade durável.
- quotas incluem reservas e não podem ser contornadas por concorrência.
- Workspace suspenso, arquivado ou em exclusão não aceita novos writes.
- exclusão nunca usa alvo vazio, amplo, não canonicalizado ou divergente.
- cleanup parcial permanece recuperável e auditável.
- `workspace_id` e root de Workspace excluído nunca são reutilizados.

## Extensibilidade

Adapters podem provisionar roots locais, volumes isolados ou Workspaces remotos, desde que implementem identidade, canonicalização, containment, leases, quota, lifecycle e cleanup equivalentes. Grants colaborativos, templates e clones entram por contratos versionados sem enfraquecer ownership.

## Futuro

Snapshots, clones copy-on-write, migração entre storage classes, Workspaces remotos, criptografia por Workspace e colaboração multiusuário poderão ser adicionados. Nenhuma evolução pode permitir root fornecida pelo chamador, lock sem fencing, escape por link ou exclusão irrecuperável sem verificação.
