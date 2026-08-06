# RFC 301 — Memory

**Estado:** Proposta arquitetural  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 201 — Agent](../200-agents/201-agent.md), [RFC 203 — Multi-agent](../200-agents/203-multi-agent.md)

## Objetivo

Definir o `MemoryManager` como domínio responsável por conhecimento persistente e recuperável. Esta RFC estabelece tipos, ownership, proveniência, escrita, leitura, retenção, invalidamento, consolidação, busca, auditoria, proteção de dados e eventos de Memory sem escolher tecnologia de persistência ou busca.

Context é temporário, específico de uma `Execution` e de um turno; Memory é persistente, possui ciclo de vida próprio e sobrevive ao descarte do Context. A passagem entre ambos ocorre somente por comandos explícitos ou referências autorizadas.

## Fora de escopo

- montar, compactar ou descartar Context;
- armazenar automaticamente mensagens, prompts ou Context completo;
- definir modelos ORM, tabelas, migrations ou endpoints;
- escolher banco, índice, algoritmo de embeddings, modelo de embedding ou implementação de busca vetorial;
- armazenar Artifacts volumosos, arquivos ou credenciais;
- decidir política visual de consentimento ou interface de usuário;
- substituir a fonte transacional de verdade de outro domínio.

## Responsabilidades e não responsabilidades

O `MemoryManager` DEVE:

- validar `user_id`, `workspace_id`, `agent_id`, finalidade e autorização em toda operação;
- registrar Memory somente por escrita explícita e observável;
- preservar ownership, escopo, proveniência, classificação, versão e política de retenção;
- oferecer leitura e busca por portas públicas, com filtros obrigatórios antes da recuperação;
- controlar invalidamento, expiração, consolidação e lineage entre registros;
- permitir referências estáveis e versionadas para o `ContextManager`;
- auditar quem escreveu, leu, alterou, invalidou, consolidou ou tentou acessar Memory;
- emitir Events no passado, mínimos e correlacionáveis;
- manter adapters de persistência e recuperação substituíveis.

O `MemoryManager` NÃO DEVE:

- assumir responsabilidades do `ContextManager`;
- decidir quais itens cabem na janela de um modelo;
- gravar todo resultado, mensagem ou Context por conveniência;
- conceder acesso por conhecimento de `memory_id` ou por parentesco entre Agents;
- promover conteúdo não confiável a instrução de sistema;
- cruzar usuário, Workspace ou Agent sem autorização explícita e verificável;
- guardar segredo, token, credencial ou handle vivo como conteúdo comum;
- chamar Provider, Tool ou Agent, nem governar estado de `Execution`.

## Fronteira entre Context e Memory

```text
Execution / Agent
      │ comando explícito de escrita
      ▼
 MemoryManager ──porta──> MemoryStore / SearchAdapter
      │
      └── MemoryReference autorizada
                    │
                    ▼
              ContextManager
              seleção temporária
```

As regras desta fronteira são normativas:

- inclusão de uma Memory em Context é leitura temporária, não cópia nem mudança de ownership;
- descarte de Context não apaga Memory;
- resumo criado apenas para caber no Context não se torna Memory sem nova escrita explícita;
- manifesto de Context referencia a versão lida, mas não duplica o conteúdo persistente;
- escrita em Memory que produz trabalho pertence a uma `Execution` e carrega `execution_id`;
- Memory pode expirar ou ser invalidada mesmo que um manifesto antigo ainda a referencie; nova montagem revalida o acesso.

## Tipos e escopos de Memory

Private, Workspace e User Memory definem ownership e visibilidade. Semantic Memory define a natureza do conhecimento e o modo de recuperação; ela sempre está subordinada a um dos escopos de ownership e nunca constitui um escopo global implícito.

| Tipo | Ownership e uso | Regra de acesso |
| --- | --- | --- |
| `PRIVATE` | pertence a um `agent_id` dentro de um usuário e, quando aplicável, Workspace | somente o Agent proprietário; compartilhamento exige Grant explícito, mínimo e revogável |
| `WORKSPACE` | conhecimento do projeto identificado por `workspace_id` | Agents e atores autorizados naquele Workspace e finalidade |
| `USER` | preferências e conhecimento pertencentes a `user_id`, não vinculados a um projeto específico | acesso por política do usuário; exposição a um Workspace é explícita e filtrada |
| `SEMANTIC` | fatos, conceitos, relações ou sínteses recuperáveis por significado | mantém `base_scope` `PRIVATE`, `WORKSPACE` ou `USER`; indexação jamais remove ownership ou classificação |

User Memory NÃO DEVE ser usada como ponte silenciosa entre Workspaces. Quando conteúdo de User Memory for disponibilizado a uma `Execution` de Workspace, a autorização declara finalidade, campos permitidos e duração. Private Memory de um Agent não é herdada por Agent filho, colaborador ou Orchestrator.

## Arquitetura

```text
Memory commands / authorized queries
                 │
                 ▼
           MemoryManager
   ┌─────────────┼─────────────┐
   │             │             │
Policy & ACL  Provenance   Lifecycle
   │             │       retention / invalidation
   └─────────────┼─────────────┘
                 │ portas públicas
       ┌─────────┴──────────┐
       ▼                    ▼
  MemoryStore        MemorySearchAdapter
       │                    │
       └──── audit / Events ┘
```

`MemoryStore` preserva registros e revisões; `MemorySearchAdapter` oferece capacidades declaradas de recuperação. A política de domínio pertence ao `MemoryManager`, não aos adapters. O Runtime e o `ContextManager` dependem apenas das portas públicas.

## Entidades e dados conceituais

O pseudocódigo é tipado, contratual e não executável.

```text
MemoryRecord {
  memory_id: MemoryId
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner_agent_id: AgentId | null
  scope: MemoryScope
  kind: MemoryKind
  base_scope: PRIVATE | WORKSPACE | USER
  content: BoundedMemoryContent | ArtifactReference
  provenance: MemoryProvenance
  classification: DataClassification
  retention_policy_ref: RetentionPolicyRef
  status: ACTIVE | INVALIDATED | EXPIRED | SUPERSEDED
  version: Version
  created_by: ActorRef
  created_execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
  valid_from: Instant
  expires_at: Instant | null
  invalidated_at: Instant | null
  superseded_by: MemoryId | null
}

MemoryScope = PRIVATE | WORKSPACE | USER
MemoryKind = EPISODIC | PROCEDURAL | PREFERENCE | FACT | SEMANTIC
```

Para `PRIVATE`, `owner_agent_id` é obrigatório. Para `WORKSPACE`, `workspace_id` é obrigatório. Para `USER`, `workspace_id` é nulo na origem; uma exposição posterior a Workspace não altera o registro original. `SEMANTIC` usa `kind` e `base_scope` para conservar o limite real de autorização.

```text
MemoryProvenance {
  source_kind: USER_STATEMENT | AGENT_OBSERVATION | TOOL_RESULT |
               ARTIFACT | BLACKBOARD_ITEM | CONSOLIDATION | IMPORT
  source_refs: SourceReference[]
  authored_by: ActorRef | null
  observed_at: Instant | null
  confidence: Confidence | null
  transformation_chain: TransformationRef[]
  integrity_ref: IntegrityRef | null
}

MemoryReference {
  memory_id: MemoryId
  version: Version
  user_id: UserId
  workspace_id: WorkspaceId | null
  permitted_agent_id: AgentId | null
  authorization_ref: MemoryGrantRef
  purpose: AccessPurpose
  expires_at: Instant | null
  integrity_ref: IntegrityRef
}
```

Uma `MemoryReference` é opaca, limitada e não contém caminho físico nem conteúdo secreto. Sua resolução revalida status, versão, ownership, finalidade e Grant.

```text
MemoryRevision {
  memory_id: MemoryId
  version: Version
  previous_version: Version | null
  changed_by: ActorRef
  execution_id: ExecutionId
  correlation_id: CorrelationId
  change_reason: ChangeReason
  changed_at: Instant
}

MemoryConsolidation {
  consolidation_id: MemoryConsolidationId
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  source_memory_refs: MemoryReference[]
  output_memory_id: MemoryId
  policy_version: Version
  execution_id: ExecutionId
  correlation_id: CorrelationId
  created_at: Instant
}
```

## Contratos públicos

```text
interface MemoryManager {
  save(command: SaveMemory) -> MemoryWriteReceipt
  get(query: GetMemory) -> AuthorizedMemory
  search(query: SearchMemory) -> MemorySearchResult
  invalidate(command: InvalidateMemory) -> MemoryWriteReceipt
  consolidate(command: ConsolidateMemory) -> MemoryConsolidationReceipt
  apply_retention(command: ApplyMemoryRetention) -> RetentionReceipt

  pre: ator, finalidade e escopo foram autenticados e autorizados
  post: resultado nunca contém Memory de outro escopo não autorizado
  post: toda mutação confirmada possui versão, auditoria e Event correspondente
}
```

```text
SaveMemory {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  scope: MemoryScope
  kind: MemoryKind
  content: BoundedMemoryContent | ArtifactReference
  provenance: MemoryProvenance
  classification: DataClassification
  retention_policy_ref: RetentionPolicyRef
  expected_version: Version | null
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

MemoryWriteReceipt {
  memory_id: MemoryId
  version: Version
  status: ACTIVE | INVALIDATED | EXPIRED | SUPERSEDED
  correlation_id: CorrelationId
}
```

`save` não aceita ContextSnapshot ou histórico bruto como conteúdo. Atualização usa `expected_version`; concorrência divergente produz conflito explícito e não sobrescrita silenciosa.

```text
GetMemory {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  memory_ref: MemoryReference
  purpose: AccessPurpose
  correlation_id: CorrelationId
}

AuthorizedMemory {
  memory_ref: MemoryReference
  version: Version
  content: BoundedMemoryContent | ArtifactReference
  provenance: MemoryProvenance
  classification: DataClassification
  status: ACTIVE
  authorized_scope: AuthorizedScope
  purpose: AccessPurpose
  policy_version: Version
  retrieved_at: Instant
  correlation_id: CorrelationId
}

MemoryFilter {
  scopes: MemoryScope[]
  kinds: MemoryKind[]
  statuses: (ACTIVE | INVALIDATED | EXPIRED | SUPERSEDED)[]
  source_kinds: (USER_STATEMENT | AGENT_OBSERVATION | TOOL_RESULT |
                 ARTIFACT | BLACKBOARD_ITEM | CONSOLIDATION | IMPORT)[]
  authored_by: ActorRef[]
  source_refs: SourceReference[]
  created_from: Instant | null
  created_to: Instant | null
  valid_at: Instant | null
  minimum_confidence: Confidence | null
  classification_ceiling: DataClassification
}

SearchMemory {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  allowed_scopes: MemoryScope[]
  query: BoundedSearchIntent
  filters: MemoryFilter[]
  maximum_results: PositiveInteger
  maximum_content_units: PositiveInteger
  purpose: AccessPurpose
  correlation_id: CorrelationId
}

MemoryMatch {
  memory_ref: MemoryReference
  version: Version
  kind: MemoryKind
  scope: MemoryScope
  excerpt: BoundedMemoryExcerpt | null
  relevance: RelevanceScore
  match_reasons: MemoryMatchReason[]
  provenance: MemoryProvenance
  classification: DataClassification
}

MemoryMatchReason = SEMANTIC_RELEVANCE | TERM_MATCH | FILTER_MATCH |
                    PROVENANCE_MATCH | RECENCY

MemorySearchResult {
  matches: MemoryMatch[]
  applied_scope: AuthorizedScope
  policy_version: Version
  truncated: Boolean
  correlation_id: CorrelationId
}
```

Busca aplica filtros de ownership e classificação antes de ranking. O resultado retorna referências, trechos mínimos e proveniência; não exporta coleções completas por padrão. Capacidade semântica é opcional e substituível, sem alterar este contrato.

```text
InvalidateMemory {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  memory_ref: MemoryReference
  expected_version: Version
  reason: InvalidationReason
  purpose: WritePurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

ConsolidateMemory {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  source_refs: MemoryReference[]
  target_scope: MemoryScope
  policy_ref: ConsolidationPolicyRef
  execution_id: ExecutionId
  purpose: ConsolidationPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

MemoryConsolidationReceipt {
  consolidation_id: MemoryConsolidationId
  output_memory_id: MemoryId
  output_version: Version
  source_memory_ids: MemoryId[]
  source_versions: Version[]
  status: CONSOLIDATED
  execution_id: ExecutionId
  correlation_id: CorrelationId
}

ApplyMemoryRetention {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  agent_id: AgentId
  execution_id: ExecutionId
  scope: MemoryScope
  memory_refs: MemoryReference[]
  retention_policy_ref: RetentionPolicyRef
  policy_cutoff_at: Instant
  purpose: RetentionPurpose
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

RetentionReceipt {
  retention_run_id: RetentionRunId
  evaluated_count: NonNegativeInteger
  expired_count: NonNegativeInteger
  invalidated_count: NonNegativeInteger
  retained_count: NonNegativeInteger
  policy_version: Version
  execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

`ApplyMemoryRetention` revalida cada `memory_ref` contra o escopo explícito do comando. A política pode expirar, invalidar ou manter versões, mas não pode ampliar ownership, mover conteúdo entre Workspaces nem excluir fora do conjunto autorizado. Um recibo só é confirmado após os efeitos e a auditoria correspondentes; execução parcial termina como falha ou cancelamento com progresso auditável, nunca como aplicação completa.

## Escrita e leitura

Uma escrita é permitida somente quando a finalidade, a fonte e o escopo estão explícitos. Política PODE exigir consentimento, revisão humana ou confiança mínima. Conteúdo derivado preserva todas as fontes e transformações. Se a evidência for contraditória, a Memory registra a incerteza ou referências conflitantes; não inventa consenso.

Leitura valida o ator antes de resolver conteúdo. O `ContextManager` recebe `AuthorizedMemory` ou referências já filtradas, ainda sujeito ao seu próprio orçamento e sanidade. Leitura não renova retenção automaticamente, não muda confiança e não promove a Memory a instrução.

## Retenção, invalidamento e exclusão

Políticas de retenção são versionadas e podem considerar tipo, classificação, origem, consentimento e obrigação legal. `expires_at` limita uso futuro; expiração não autoriza acesso ao conteúdo e mantém apenas auditoria mínima conforme política.

Invalidamento marca conhecimento como não utilizável sem apagar lineage. Motivos incluem incorreção, revogação de consentimento, fonte removida, mudança de escopo ou supersessão. Exclusão física, quando exigida, pertence ao adapter e à política de proteção de dados; tombstones mínimos impedem ressurreição por retry, cache ou consolidação.

Uma referência expirada, revogada ou invalidada falha fechada. Caches e índices recebem invalidação por contrato e nunca podem retornar uma versão já proibida.

## Consolidação

Consolidação transforma Memories redundantes ou episódicas em uma Memory nova e versionada. Ela:

1. reautoriza cada fonte no instante da operação;
2. impede promoção a escopo mais amplo sem autorização específica;
3. preserva lineage, classificação mais restritiva e incertezas;
4. grava saída como nova Memory, sem mutar fontes retroativamente;
5. marca fontes como superseded somente se a política autorizar;
6. executa como `Execution` cancelável e auditável.

Uma consolidação parcial não é publicada como sucesso. Se fontes mudarem durante o processo, conflito de versão exige nova tentativa.

## Auditoria e proteção de dados

A auditoria registra ator, operação, finalidade, escopo, IDs, versão, decisão de autorização, resultado e `correlation_id`. Ela não duplica conteúdo de Memory nem segredo. Acesso negado é observável por razão categórica sem revelar existência ou metadados ao solicitante não autorizado.

Proteções obrigatórias incluem minimização, classificação, retenção limitada, redaction, criptografia por adapters quando aplicável, separação lógica por ownership, propagação de revogação e capacidade de localizar derivados por lineage. Exportação ou remoção solicitada por usuário deve respeitar obrigações de auditoria e será detalhada pela segurança da plataforma.

## Fluxo normal

1. Uma `Execution` produz intenção explícita de salvar conhecimento.
2. O `MemoryManager` valida ator, Agent, usuário, Workspace, finalidade e classificação.
3. Proveniência, retenção e versão são normalizadas.
4. O store confirma a revisão de forma idempotente.
5. Auditoria e `MemorySaved` são confirmados.
6. Busca posterior fixa escopo autorizado antes da recuperação.
7. O `ContextManager` recebe referências e trechos mínimos, aplica orçamento e monta Context temporário.

## Fluxo de falha

- divergência de ownership ou Grant resulta em negação sem fallback cross-workspace;
- proveniência ausente, conteúdo proibido ou escopo incoerente rejeita escrita;
- `expected_version` divergente produz conflito explícito;
- falha de store não emite `MemorySaved` nem retorna recibo confirmado;
- índice indisponível PODE degradar para outra capacidade declarada, nunca para outro escopo;
- referência inválida, expirada ou revogada não é resolvida;
- falha de auditoria obrigatória impede mutação confirmada;
- retry com a mesma chave idempotente não duplica Memory.

## Fluxo de cancelamento

1. O Runtime propaga cancelamento da `Execution` ao trabalho de Memory.
2. Novas leituras, fontes ou transformações deixam de ser iniciadas.
3. Escrita não confirmada é descartada; escrita já confirmada permanece válida e observável.
4. Consolidação interrompe em limite seguro e não publica saída parcial.
5. Material temporário é eliminado sem apagar auditoria confirmada.
6. O resultado volta ao Runtime como cancelamento, sem fabricar sucesso.

## Eventos

Todos usam o envelope da RFC 103 e nomes no passado.

| Event | Fato confirmado |
| --- | --- |
| `MemorySaved` | uma versão de Memory foi persistida com política e proveniência válidas |
| `MemoryRead` | uma versão autorizada foi entregue a um consumidor |
| `MemorySearched` | uma busca autorizada terminou com escopo e limites aplicados |
| `MemoryInvalidated` | uma versão deixou de ser elegível para uso |
| `MemorySuperseded` | uma Memory foi substituída por referência explícita |
| `MemoryConsolidated` | uma nova Memory foi criada a partir de fontes rastreáveis |
| `MemoryExpired` | a retenção tornou a Memory indisponível para novas leituras |
| `MemoryAccessDenied` | uma tentativa foi recusada por política |
| `MemoryOperationFailed` | uma operação terminou sem efeito confirmado |

Payloads incluem IDs, versão, escopo, razões categóricas, `agent_id`, `execution_id` aplicável e `correlation_id`; não incluem conteúdo, consultas livres, segredos ou vetores.

## Segurança

- negar por padrão qualquer acesso cross-user, cross-workspace ou cross-agent;
- exigir autorização explícita para Private Memory compartilhada e User Memory exposta a Workspace;
- revalidar Grants em leitura, busca, consolidação e resolução de referência;
- particionar stores, caches e índices conceituais por ownership e classificação;
- tratar Memory recuperada como dado não confiável, nunca como instrução de maior autoridade;
- impedir que resumo, consolidação ou indexação reduzam classificação;
- não incluir segredos em conteúdo, logs, Events ou metadados pesquisáveis;
- limitar consultas e resultados para reduzir enumeração e exfiltração.

## Observabilidade

Logs e traces permitem reconstruir escrita, leitura, busca, invalidamento, consolidação, expiração e negação por IDs. Métricas incluem latência, volume por tipo e escopo, taxa de acerto, resultados truncados, conflitos de versão, negações, referências expiradas, consolidações, backlog de retenção e falhas de propagação de revogação.

Conteúdo, consulta livre e atributos sensíveis não são labels. Auditoria e telemetria preservam `correlation_id`, `execution_id`, `agent_id`, `user_id` e `workspace_id` aplicável sem expor payload.

## Extensibilidade

Novos stores, mecanismos de busca, rankers e políticas entram por adapters ou estratégias versionadas. Um novo tipo de Memory DEVE declarar escopo base, ownership, proveniência, retenção, classificação, autorização, eventos e comportamento de invalidamento. Extensões não podem introduzir escrita implícita de Context nem busca global sem filtro prévio.

## Invariantes

- Context é temporário; Memory é persistente e possui ciclo de vida próprio.
- `MemoryManager` e `ContextManager` são componentes separados.
- nenhuma inclusão em Context grava, altera ou renova Memory automaticamente.
- toda Memory possui `user_id`, escopo, proveniência, classificação, retenção e versão.
- `workspace_id` é obrigatório para Workspace Memory; `agent_id` é obrigatório para Private Memory.
- Semantic Memory sempre conserva um escopo base autorizado.
- conhecer um ID ou participar de colaboração não concede acesso.
- nenhuma operação cruza Workspace ou Agent sem autorização explícita.
- busca filtra ownership antes de recuperar ou ranquear.
- invalidamento e revogação propagam a caches, referências e índices.
- consolidação cria lineage e nunca amplia escopo implicitamente.
- mutações confirmadas são idempotentes, auditáveis e correlacionáveis.
- eventos são fatos no passado e não contêm conteúdo sensível.

## Futuro

Poderão ser adicionados consentimento granular, revisão humana, políticas jurisdicionais, memória temporal, avaliação de qualidade, deduplicação avançada e recuperação híbrida. Busca semântica e embeddings permanecem capacidades substituíveis; qualquer escolha concreta exigirá decisão própria sem alterar ownership, autorização ou a separação entre Context e Memory.
