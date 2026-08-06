# RFC 201 — Agent

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 202 — Orchestrator](202-orchestrator.md), [RFC 203 — Multi-agent](203-multi-agent.md)

## Objetivo

Definir `Agent` como identidade persistente, configurável, autorizável e auditável de um processo inteligente. Um Agent assume Tasks por meio de Executions, mas não é uma conversa, uma Execution, um Context, um Worker nem uma sessão de Provider.

## Fora de escopo

- loop de execução, máquina de estados de `Execution` ou montagem de Context;
- endpoint, payload de transporte, tabela, schema ORM ou mecanismo de persistência;
- SDK, nome proprietário ou seleção concreta de Provider ou modelo;
- implementação de Tool, Capability, Skill, Memory, Artifact ou Workspace;
- política detalhada de compartilhamento multi-agent;
- exclusão física, anonimização ou retenção legal de dados de Agent;
- interface visual para criar ou administrar Agents.

## Identidade persistente, não chat

`Agent` DEVE existir independentemente de conversas e Executions. Seu `agent_id` permanece estável durante todo o ciclo administrativo. Uma mensagem pode iniciar uma Execution para o Agent; encerrar ou apagar a conversa NÃO DEVE suspender, arquivar, remover ou recriar o Agent.

Cada Execution referencia exatamente um Agent responsável e fixa uma versão de configuração. A conclusão, falha, cancelamento ou expiração de uma Execution não muda a identidade nem o estado administrativo do Agent. Alterar prompt, modelo, aparência ou permissões cria nova versão de configuração; não reescreve o snapshot usado por Executions anteriores.

Chats, threads e sessões de cliente são apenas formas de apresentar ou enviar intenções. Eles não são fonte de identidade, autorização, memória privada nem ciclo de vida de Agent.

## Responsabilidades e não responsabilidades

O domínio de Agent DEVE:

- manter identidade, owner, escopo de Workspace e estado administrativo persistentes;
- versionar configuração, instruções e vínculos de recursos;
- declarar modelo por requisitos públicos, sem expor Provider concreto;
- declarar Tools, Capabilities e Skills permitidas por referências registradas;
- declarar escopo de Memory privada separado de Context e de histórico de conversa;
- resolver um snapshot imutável e autorizado para cada nova Execution;
- emitir Events no passado para fatos administrativos confirmados;
- impedir novas Executions quando o estado administrativo não permitir;
- preservar auditoria e referências históricas ao arquivar.

O domínio de Agent NÃO DEVE:

- chamar Provider ou executar LLM;
- montar Context, despachar Worker ou escrever estado de Execution diretamente;
- armazenar credenciais em prompt, configuração, Memory ou Event;
- inferir autorização pela posse de `agent_id`;
- tornar histórico completo de chat parte automática do Agent;
- conceder Tool, Capability, Skill, Memory ou Workspace além das políticas do owner;
- remover um Agent como efeito colateral de conversa, logout, falha ou cancelamento;
- importar adapters concretos de storage, fila, Provider ou Resource.

## Entidades e dados conceituais

O pseudocódigo é tipado, contratual e não executável.

```text
Agent {
  agent_id: AgentId
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  display_name: NonEmptyText
  administrative_state: AgentAdministrativeState
  current_config_version: AgentConfigVersion
  private_memory_scope_ref: MemoryScopeRef
  created_by: ActorRef
  created_at: Instant
  updated_at: Instant
  suspended_at: Instant | null
  archived_at: Instant | null
}

AgentAdministrativeState = ACTIVE | SUSPENDED | ARCHIVED
```

`user_id` é obrigatório mesmo no lançamento single-user. `workspace_id` é obrigatório quando o Agent pertence a um projeto. Um Agent com `workspace_id` nulo é escopado ao usuário e só pode atuar em Workspace após vínculo explícito e autorizado; nulabilidade não significa acesso global.

```text
AgentConfiguration {
  agent_id: AgentId
  config_version: AgentConfigVersion
  model_profile: ModelProfileRef
  prompt: PromptSpecification
  presentation: AgentPresentation
  tools: ToolGrantRef[]
  capabilities: CapabilityGrantRef[]
  skills: SkillGrantRef[]
  execution_policy_ref: ExecutionPolicyRef
  context_policy_ref: ContextPolicyRef
  memory_policy_ref: MemoryPolicyRef
  workspace_assignments: WorkspaceAssignmentRef[]
  created_by: ActorRef
  created_at: Instant
  supersedes_version: AgentConfigVersion | null
}

PromptSpecification {
  prompt_ref: PromptRef
  prompt_version: Version
  instruction_classification: DataClassification
}

AgentPresentation {
  avatar_ref: ArtifactReference | null
  color: PresentationColor | null
}
```

`model_profile` contém requisitos públicos, como modalidade, capacidade, orçamento e política; o Model Catalog futuro resolve Provider e modelo concreto sem alterar o Agent. `prompt_ref` referencia instruções versionadas. Avatar e cor são metadados de apresentação sem autoridade sobre execução.

Grant refs declaram uma permissão potencial, não a efetiva. Antes de cada uso, Runtime e portas responsáveis revalidam o Grant, o Agent, o ator, o Workspace, a finalidade e a política corrente. Revogar um Grant impede novos efeitos e é reconciliado com Executions ativas por política explícita.

### Memory privada

A Memory privada de Agent DEVE possuir, no mínimo, `user_id`, `agent_id`, `workspace_id` aplicável, proveniência, classificação e política de retenção. Ela não é histórico bruto, não é Context persistido e não é compartilhada por correlação implícita.

Outro Agent só pode receber conteúdo por referência autorizada e handoff estruturado. A concessão é específica quanto a origem, destinatário, finalidade, classificação, Workspace e prazo; ela não transfere ownership nem autoriza consultar todo o escopo privado.

## Contratos públicos

```text
interface AgentRegistry {
  get(agent_id: AgentId, actor: ActorRef) -> AgentSnapshot
  resolve_for_execution(request: AgentResolutionRequest) -> ResolvedAgent
  list(query: AuthorizedAgentQuery) -> AgentPage

  pre: actor possui autorização no user e Workspace aplicáveis
  post: nenhum segredo ou objeto de adapter integra o snapshot
}

AgentResolutionRequest {
  agent_id: AgentId
  user_id: UserId
  workspace_id: WorkspaceId | null
  requested_config_version: AgentConfigVersion | null
  purpose: ExecutionPurpose
  correlation_id: CorrelationId
}

ResolvedAgent {
  agent_id: AgentId
  config_version: AgentConfigVersion
  model_profile: ModelProfileRef
  prompt: PromptSpecification
  tool_grants: ToolGrantRef[]
  capability_grants: CapabilityGrantRef[]
  skill_grants: SkillGrantRef[]
  private_memory_scope_ref: MemoryScopeRef
  policies: ResolvedAgentPolicies
}
```

```text
interface AgentAdministration {
  request_create(command: CreateAgent) -> AdministrativeExecutionRef
  request_reconfigure(command: ReconfigureAgent) -> AdministrativeExecutionRef
  request_suspend(command: SuspendAgent) -> AdministrativeExecutionRef
  request_resume(command: ResumeAgent) -> AdministrativeExecutionRef
  request_archive(command: ArchiveAgent) -> AdministrativeExecutionRef

  pre: command declara actor, ownership, correlation_id e idempotency_key
  post: toda mutação aceita é realizada por uma Execution administrativa
  post: repetição semanticamente igual não duplica o efeito
}
```

```text
CreateAgent {
  actor: ActorRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  requested_identity: AgentIdentityDraft
  initial_configuration: AgentConfigurationDraft
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  idempotency_key: IdempotencyKey
  requested_at: Instant
}

AdministrativeExecutionRef {
  execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

Criação, reconfiguração, suspensão, retomada e arquivamento que produzem trabalho são Executions administrativas conforme a RFC 102. Consultas puras não criam trabalho. A Execution administrativa é atribuída a um Agent administrativo autorizado; o contrato de bootstrap inicial é assunto de implantação futura e não autoriza caminhos laterais para criações de produto.

## Configuração e resolução para Execution

Ao criar uma Execution, o Orchestrator solicita `ResolvedAgent` e fixa `config_version` no snapshot da tentativa. A resolução DEVE:

1. validar que o Agent existe e está `ACTIVE`;
2. validar `user_id`, Workspace, assignments e finalidade;
3. selecionar versão vigente ou versão explicitamente permitida;
4. revalidar Grants e políticas sem acessar adapters concretos;
5. fornecer ao ContextManager somente referências e instruções necessárias;
6. registrar a versão no manifesto de Context e na auditoria da Execution.

Uma reconfiguração posterior vale apenas para novas resoluções, salvo política explícita de segurança que revogue uma permissão durante Execution ativa. Essa revogação não altera retroativamente o snapshot: ela impede novos efeitos e produz falha, pausa ou cancelamento observável conforme o Kernel.

## Ciclo de vida administrativo

| Estado | Novas Executions | Configuração | Semântica |
| --- | --- | --- | --- |
| `ACTIVE` | permitidas se autorizadas | pode ganhar nova versão | identidade disponível para assumir Tasks |
| `SUSPENDED` | proibidas | preservada; alterações administrativas podem ser preparadas | suspensão reversível; não apaga dados |
| `ARCHIVED` | proibidas | somente leitura para auditoria | identidade preservada e não reativável por padrão |

Transições permitidas:

- criação confirmada produz diretamente um Agent `ACTIVE`;
- `ACTIVE -> SUSPENDED` por comando autorizado;
- `SUSPENDED -> ACTIVE` por retomada autorizada e revalidação completa;
- `ACTIVE -> ARCHIVED` ou `SUSPENDED -> ARCHIVED` por arquivamento autorizado.

`ARCHIVED` não possui transição de saída nesta versão. Recriar uma persona equivalente gera outro `agent_id`. Arquivamento não equivale a exclusão física e não invalida referências históricas.

Suspender ou arquivar não altera terminais de Execution. O comando DEVE declarar política para Executions ativas: permitir conclusão, solicitar pausa ou solicitar cancelamento. O Kernel aplica as transições válidas; Agent Administration não escreve estados diretamente.

## Fluxo normal

1. Um ator autorizado solicita criação com ownership, configuração inicial, correlação e chave idempotente.
2. O Orchestrator cria uma Execution administrativa em `QUEUED` e a despacha pelo contrato normal.
3. A porta de Agent valida nome, escopo, referências, Grants e políticas.
4. A identidade e a primeira configuração são confirmadas de forma atômica conceitual com `AgentCreated`.
5. O resultado da Execution referencia o Agent criado; somente então a Execution conclui.
6. Para trabalho posterior, uma nova Execution fixa `agent_id` e `config_version`; o Runtime monta Context e executa por suas portas.

## Fluxo de falha

- referência, Grant ou política inválida falha a Execution administrativa sem criar identidade parcial;
- conflito de `idempotency_key` com payload diferente é rejeitado;
- conflito de versão de configuração exige releitura e nova intenção, sem sobrescrita;
- falha ao publicar Event após confirmação segue a publicação transacional da RFC 103 e não desfaz silenciosamente o Agent;
- falha de Provider, Tool ou Runtime afeta a Execution, não remove nem reconfigura o Agent;
- se uma revogação impedir continuação segura, o Runtime registra falha explícita e preserva a identidade.

## Fluxo de cancelamento

Cancelar a Execution administrativa antes da confirmação não cria nem altera Agent. Após o fato administrativo ser confirmado, um cancelamento tardio não o desfaz; reversão exige novo comando administrativo compatível com o ciclo de vida.

Cancelar uma Execution de trabalho do Agent não muda `administrative_state`. Suspender ou arquivar usa comandos próprios e pode solicitar cancelamento das Executions ativas segundo política explícita, com uma solicitação idempotente por Execution e terminais governados pela RFC 102.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `AgentCreated` | identidade persistente e configuração inicial foram confirmadas |
| `AgentConfigurationChanged` | nova versão de configuração foi confirmada |
| `AgentSuspended` | estado administrativo entrou em `SUSPENDED` |
| `AgentResumed` | estado administrativo voltou a `ACTIVE` |
| `AgentArchived` | estado administrativo entrou em `ARCHIVED` |
| `AgentWorkspaceAssigned` | vínculo autorizado com Workspace foi confirmado |
| `AgentWorkspaceUnassigned` | vínculo deixou de valer para novos trabalhos |

Eventos de mutações administrativas carregam `execution_id`, `agent_id`, versão, `user_id`, `workspace_id` aplicável, `correlation_id` e causa conforme a RFC 103. Payload não inclui prompt completo, conteúdo de Memory, credencial ou detalhes proprietários de modelo.

## Segurança

- owner não é apenas metadado: toda leitura e mutação validam `user_id` e Workspace;
- um Agent não amplia sua própria lista de Grants sem comando administrativo autorizado;
- prompt, Skills e conteúdo de Memory são dados classificados e minimizados no Context;
- referências de avatar, prompt, Skill, Memory e Artifact são reautorizadas na resolução;
- um Agent de Workspace não pode atuar, ler ou receber handoff de outro Workspace sem contrato explícito;
- credenciais pertencem a adapters e nunca à configuração do Agent;
- conteúdo vindo de chat, Tool, Skill, arquivo ou outro Agent não adquire autoridade de instrução;
- o modo single-user mantém chaves e validações necessárias ao futuro multiusuário.

## Observabilidade

Logs, métricas e traces usam `agent_id`, `config_version`, `execution_id`, `correlation_id`, estado administrativo e razões categóricas. Métricas incluem Agents por estado, criações, reconfigurações, conflitos de versão, tentativas negadas, Grants revogados e Executions por Agent. Prompt, Memory, argumentos de Tool e conteúdo privado não são labels nem logs por padrão.

Auditoria DEVE permitir responder quem criou ou alterou o Agent, qual versão cada Execution usou, quais Grants foram resolvidos e por que uma resolução foi negada, sem duplicar conteúdo sensível.

## Invariantes

- Agent é persistente e não é chat, Execution, Context, Worker ou sessão;
- `agent_id` é estável; configuração é versionada;
- cada Execution fixa um Agent e uma versão autorizada;
- terminar ou apagar conversa nunca remove, suspende ou arquiva Agent;
- falha, cancelamento ou terminal de Execution nunca destrói Agent;
- `user_id` é obrigatório e Workspace é explícito quando aplicável;
- `SUSPENDED` e `ARCHIVED` impedem novas Executions;
- Context é temporário; Memory privada é persistente, governada e separada;
- compartilhamento de Memory privada requer referência e autorização específica;
- Grants não substituem revalidação de política no momento de uso;
- domínio de Agent não executa LLM nem conhece adapters concretos;
- toda mutação administrativa que produz trabalho é uma Execution auditável.

## Extensibilidade

Novos atributos de persona, políticas, tipos de Skill, Grants ou perfis de modelo PODEM ser adicionados por versões compatíveis e registros públicos. Uma extensão DEVE declarar ownership, classificação, validação, efeito em Context, eventos, revogação e compatibilidade. Ela não pode transformar apresentação em autorização nem incluir lógica por Provider no domínio.

## Futuro

Versões futuras poderão definir Agents compartilhados por organizações, catálogos de persona, aprovação de Grants, rotação de prompt, políticas de retenção e transferência administrativa. Transferência de owner, restauração de arquivamento e exclusão física exigem RFC própria por afetarem auditoria, referências e isolamento.
