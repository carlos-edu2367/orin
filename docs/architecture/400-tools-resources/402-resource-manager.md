# RFC 402 — Resource Manager

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 401 — Tool Runtime](401-tool-runtime.md), [RFC 403 — Filesystem](403-filesystem.md), [RFC 404 — Terminal](404-terminal.md), [RFC 405 — Browser](405-browser.md)

## Objetivo

Definir o Resource Manager como autoridade de alocação, isolamento, leasing, renovação, revogação, limpeza e auditoria de Resources operacionais. Filesystem, Terminal e Browser permanecem atrás de portas especializadas, mas compartilham um ciclo de vida e um contexto de segurança uniformes.

## Fora de escopo

- execução de Tools, Capabilities, Agents ou regras do Kernel;
- semântica interna de arquivo, processo, shell, página ou protocolo de browser;
- tecnologia de container, sistema operacional, fila, banco, cache ou descoberta de hosts;
- armazenamento durável de Artifacts, Memory ou Context;
- dimensionamento de pools, agendamento e topologia de deploy.

## Responsabilidades e não responsabilidades

O Resource Manager DEVE:

- manter catálogo tipado de Resource e adapter compatível;
- autorizar e alocar o menor Resource necessário para uma finalidade explícita;
- emitir leases limitados por usuário, Workspace, Agent, Execution, correlação, propósito, tempo e orçamento;
- garantir isolamento lógico e exigir isolamento físico quando o tipo ou política determinar;
- renovar, liberar, revogar e expirar leases de forma idempotente;
- limpar estado temporário, encerrar handles e reconciliar vazamentos após falha ou cancelamento;
- auditar solicitações, decisões, uso e limpeza sem guardar segredos;
- disponibilizar snapshots e sinais de saúde por portas públicas.

O Resource Manager NÃO DEVE:

- interpretar Task, decidir fluxo de Agent ou compor Tools;
- expor handle nativo, PID, contexto Playwright ou caminho físico a consumidores não autorizados;
- permitir acesso direto ao adapter contornando lease;
- persistir handle vivo em checkpoint, Event ou Context;
- assumir que o modo single-user elimina partição por `user_id` e `workspace_id`;
- transformar expiração ou falha de limpeza em sucesso silencioso.

## Arquitetura

```text
Tool Runtime / serviço autorizado
        │ ResourceLeaseRequest
        ▼
    Resource Manager
        ├── ResourceCatalog
        ├── AuthorizationPolicy
        ├── Quota / Budget Policy
        ├── LeaseCoordinator
        ├── IsolationController
        ├── CleanupSupervisor
        └── Audit / Events
             ├── Filesystem adapter
             ├── Terminal adapter
             └── Browser broker ──> Browser Workers
```

O catálogo descreve capacidades; adapters materializam Resources. Um `AuthorizedResourceHandle` é opaco, limitado ao processo ou worker autorizado e verificável contra o lease. Ele não é uma referência durável.

## Dados

```text
ResourceOperationContext {
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  correlation_id: CorrelationId
  purpose: Purpose
  actor: ActorRef
}

ResourceDescriptor {
  resource_type: ResourceType
  adapter_ref: ResourceAdapterRef
  capabilities: ResourceCapability[]
  isolation_modes: IsolationMode[]
  limits: ResourceLimits
  health: AVAILABLE | DEGRADED | UNAVAILABLE
}

ResourceType = FILESYSTEM | TERMINAL | BROWSER
IsolationMode = PROCESS | WORKSPACE | USER | SESSION | HOST
```

```text
ResourceLease {
  lease_id: ResourceLeaseId
  resource_ref: ResourceRef
  resource_type: ResourceType
  context: ResourceOperationContext
  permissions: EffectivePermission[]
  isolation_key: IsolationKey
  budget: ResourceBudget
  state: ResourceLeaseState
  acquired_at: Instant
  expires_at: Instant
  last_renewed_at: Instant | null
  released_at: Instant | null
}

ResourceLeaseState = REQUESTED | LEASED | REVOKING | RELEASED | EXPIRED | FAILED
```

`isolation_key` é derivada internamente de ownership e política e não pode ser fornecida pelo chamador. O lease dá direito limitado de solicitar operações; não transfere ownership do Resource nem autoriza outra finalidade.

```text
ResourceUsageRecord {
  lease_id: ResourceLeaseId
  execution_id: ExecutionId
  operation_id: ResourceOperationId
  resource_type: ResourceType
  purpose: Purpose
  started_at: Instant
  finished_at: Instant | null
  usage: ResourceUsage
  outcome: ResourceOperationOutcome | null
}
```

## Contratos tipados

```text
interface ResourceManager {
  acquire(request: ResourceLeaseRequest) -> ResourceLeaseGrant
  renew(request: RenewResourceLease) -> ResourceLeaseSnapshot
  authorize(request: AuthorizeResourceOperation) -> AuthorizedResourceHandle
  release(request: ReleaseResourceLease) -> ReleaseResult
  revoke(request: RevokeResourceLease) -> RevokeResult
  inspect(query: AuthorizedResourceQuery) -> ResourceLeaseSnapshot

  pre: contexto corresponde à Execution, Agent e ownership informados
  post: nenhum handle é entregue antes de lease e permissões serem confirmados
}

ResourceLeaseRequest {
  request_id: ResourceRequestId
  context: ResourceOperationContext
  resource_type: ResourceType
  required_capabilities: ResourceCapability[]
  requested_permissions: Permission[]
  requested_budget: ResourceBudget
  requested_duration: Duration
  idempotency_key: IdempotencyKey
}

ResourceLeaseGrant =
  | LeaseGranted { lease: ResourceLeaseSnapshot }
  | LeaseRejected { reason: ResourceRejectionReason }
  | LeaseUnavailable { retryability: Retryability, reason: AvailabilityReason }
```

```text
RenewResourceLease {
  request_id: ResourceRequestId
  lease_id: ResourceLeaseId
  context: ResourceOperationContext
  requested_extension: Duration
  expected_expires_at: Instant
  idempotency_key: IdempotencyKey
}

ReleaseResourceLease {
  request_id: ResourceRequestId
  lease_id: ResourceLeaseId
  context: ResourceOperationContext
  cleanup_mode: NORMAL | FORCE_IF_EXPIRED
  reason: ReleaseReason
  idempotency_key: IdempotencyKey
}

RevokeResourceLease {
  request_id: ResourceRequestId
  lease_id: ResourceLeaseId
  context: ResourceOperationContext
  reason: RevocationReason
  cleanup_deadline: Instant
  idempotency_key: IdempotencyKey
}

AuthorizedResourceQuery {
  context: ResourceOperationContext
  lease_id: ResourceLeaseId | null
  resource_ref: ResourceRef | null
  resource_type: ResourceType | null
  states: ResourceLeaseState[]
  include_usage: Boolean
  page: PageRequest
}

invariant: query informa lease_id, resource_ref ou filtros limitados ao contexto
invariant: renew, release, revoke e inspect revalidam user, Workspace, Agent, Execution, correlação e purpose
```

```text
AuthorizeResourceOperation {
  lease_id: ResourceLeaseId
  operation_id: ResourceOperationId
  context: ResourceOperationContext
  capability: ResourceCapability
  requested_usage: ResourceUsageEstimate
}

AuthorizedResourceHandle {
  handle_ref: EphemeralHandleRef
  lease_id: ResourceLeaseId
  operation_id: ResourceOperationId
  capabilities: ResourceCapability[]
  expires_at: Instant
}
```

```text
interface ResourceAdapter {
  allocate(grant: AdapterAllocationGrant) -> AdapterResourceHandle
  inspect(handle: AdapterResourceHandle) -> AdapterResourceState
  signal_cancel(handle: AdapterResourceHandle, reason: CancellationReason) -> Unit
  cleanup(handle: AdapterResourceHandle, mode: CleanupMode) -> CleanupResult

  invariant: adapter valida lease e isolation_key em cada operação
  invariant: tipos nativos não atravessam a porta pública
}

interface CleanupSupervisor {
  sweep(cutoff_at: Instant) -> CleanupBatchResult
  reconcile(resource_ref: ResourceRef, context: ResourceOperationContext) -> ReconciliationResult
}
```

Renovação exige lease ainda renovável, mesma identidade operacional e nova avaliação de política. O prazo nunca ultrapassa os limites da Execution ou da credencial associada. `release` e `revoke` repetidos devolvem resultado idempotente sem reativar o Resource.

`RenewResourceLease`, `ReleaseResourceLease`, `RevokeResourceLease` e `AuthorizedResourceQuery` não são atalhos administrativos: cada um carrega `ResourceOperationContext` completo. Uma revogação operacional de supervisão ainda pertence a uma Execution autorizada, usa `purpose` específico e preserva sua própria correlação; conhecer o lease não basta.

## Isolamento, leasing e limpeza

- Filesystem é particionado por raiz canonicalizada de Workspace e permissões de operação.
- Terminal é particionado por owner, Workspace, sessão e processo ou sandbox aplicável.
- Browser é particionado por perfil, sessão e Browser Worker dedicado ao job conforme política.
- orçamento, contadores, caches e filas internas são particionados pela mesma identidade efetiva;
- Resource compartilhável exige descriptor e política explícitos; compartilhamento implícito por host é proibido;
- lease expirado não aceita nova operação, mesmo que o handle nativo ainda exista;
- cleanup fecha handles, processos, páginas e áreas temporárias conforme o adapter, preservando somente referências duráveis autorizadas;
- falha de cleanup entra em reconciliação e alerta; não devolve o Resource ao pool como saudável.

## Fluxo normal

1. O Tool Runtime solicita Resource com contexto, capacidades, permissões, orçamento, duração e chave idempotente.
2. O Manager valida ownership, finalidade, quota, saúde, compatibilidade e isolamento.
3. O adapter aloca o Resource e devolve handle nativo somente ao Manager.
4. O Manager confirma o lease e publica `ResourceLeaseGranted`.
5. Cada operação pede autorização, recebe handle efêmero e registra uso.
6. Renovação revalida política; cancelamento ou conclusão impedem novas operações.
7. `release` aciona limpeza, confirma descarte e publica `ResourceLeaseReleased`.

## Fluxo de falha

Falha antes da alocação não cria lease. Falha entre alocação e confirmação aciona cleanup compensatório. Resource indisponível não provoca fallback para outro Workspace, usuário ou tipo mais privilegiado. Se a saúde ficar incerta, o Manager marca o Resource como não alocável, reconcilia handles e classifica efeito e uso já ocorridos. Cleanup incompleto produz `ResourceCleanupFailed`, retry supervisionado e quarentena; nunca libera capacidade fantasma.

## Fluxo de cancelamento

Cancelamento da Tool ou Execution revoga a capacidade de iniciar operações, sinaliza o adapter, estabiliza operações em curso e executa cleanup no limite seguro. Efeitos já confirmados são auditados. Resultado tardio não renova lease nem reabre Resource liberado. Recursos não interrompíveis são isolados e encerrados no nível mais estreito possível; escalada não pode atingir processo, sessão ou Workspace alheio.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `ResourceLeaseGranted` | Resource isolado foi alocado e lease tornou-se utilizável |
| `ResourceLeaseRenewed` | prazo e política do lease foram revalidados |
| `ResourceLeaseReleased` | lease foi encerrado e limpeza obrigatória foi confirmada |
| `ResourceLeaseRevoked` | novas operações foram proibidas por cancelamento ou política |
| `ResourceLeaseExpired` | prazo terminou e lease deixou de autorizar operações |
| `ResourceCleanupFailed` | limpeza não pôde ser confirmada e Resource entrou em reconciliação |

Payloads incluem `resource_type`, `lease_id`, `execution_id`, ownership, finalidade, razão categórica e uso agregado, mas não handle, PID, caminho físico, cookie ou segredo.

## Segurança

- todas as operações sensíveis declaram `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`;
- autorização é negada por padrão e limitada por capacidade, duração, quota e classificação;
- handles são opacos, efêmeros, não serializáveis e vinculados ao lease e à operação;
- conhecer `resource_ref` ou `lease_id` não concede acesso;
- adapters revalidam isolamento em cada chamada, inclusive após renovação;
- secrets são resolvidos somente na fronteira autorizada e nunca entram em lease ou auditoria;
- políticas de rede, filesystem e processo continuam sendo aplicadas pelo Resource especializado.

## Observabilidade

Métricas incluem alocações, rejeições, espera, utilização, saturação, leases ativos/expirados, renovações, revogações, vazamentos, tempo de cleanup e Resources em quarentena. Logs e traces usam IDs, tipo, adapter lógico, finalidade, estado e códigos sanitizados. Auditoria permite responder quem, em qual Workspace e Execution, para qual finalidade, obteve qual capacidade e quando a liberou, sem registrar conteúdo operado.

## Invariantes

- nenhum Resource é usado sem lease válido e autorização da operação;
- lease é limitado por ownership, Agent, Execution, correlação, finalidade, tempo, permissões e orçamento;
- handle nativo não atravessa a fronteira pública nem é persistido;
- isolamento do modo single-user é idêntico ao esperado para múltiplos usuários;
- lease expirado, revogado ou liberado nunca é reaberto;
- falha e cancelamento sempre iniciam estabilização e limpeza;
- Resource incerto ou sujo não retorna ao pool saudável;
- adapters são substituíveis e não alteram a política de domínio;
- uso e fatos relevantes são auditáveis e correlacionáveis.

## Extensibilidade

Novo tipo de Resource registra descriptor, capacidades, modos de isolamento, limites, adapter, semântica de cancelamento e cleanup. Políticas de quota e placement podem ser substituídas por portas. Extensões não podem introduzir acesso sem lease, handle durável ou isolamento mais fraco que o declarado.

## Futuro

Pools distribuídos, leases federados, attestação de sandbox, placement por afinidade e preempção poderão especializar a coordenação. A evolução deve manter autorização por operação, isolamento, limpeza confirmável, auditabilidade e ausência de handles vivos em estado durável.
