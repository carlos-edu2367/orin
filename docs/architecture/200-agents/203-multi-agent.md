# RFC 203 — Multi-agent

**Estado:** Normativa  
**Idioma:** PT-BR  
**Relações:** [RFC 000 — Visão geral](../000-overview.md), [RFC 050 — Princípios de design](../050-design-principles.md), [RFC 060 — Glossário e convenções](../060-glossary-and-conventions.md), [RFC 101 — Runtime](../100-kernel/101-runtime.md), [RFC 102 — Ciclo de vida da Execution](../100-kernel/102-execution-lifecycle.md), [RFC 103 — Sistema de eventos](../100-kernel/103-event-system.md), [RFC 104 — Pipeline de contexto](../100-kernel/104-context-pipeline.md), [RFC 201 — Agent](201-agent.md), [RFC 202 — Orchestrator](202-orchestrator.md), [RFC 303 — Compartilhamento de contexto](../300-context-memory/303-context-sharing.md)

## Objetivo

Definir colaboração entre Agents persistentes por criação administrativa, mensagens, delegações, espera, resultados, cancelamento e handoffs estruturados. Toda delegação e todo trabalho assíncrono recebe uma Execution própria; Agents compartilham somente referências e contexto mínimo autorizado, nunca histórico indiscriminado.

## Fora de escopo

- algoritmo de consenso, eleição de líder ou sociedade autônoma de Agents;
- chat visual, presença, typing indicator ou protocolo de transporte;
- implementação de fila, mailbox, pub/sub, banco, ORM ou Worker;
- conteúdo e retenção detalhados de Memory compartilhada ou Blackboard futuros;
- algoritmo de ranking, roteamento semântico ou seleção automática de Agent;
- execução direta de LLM pelo coordenador multi-agent;
- transferência implícita de owner, credenciais, Grants ou Workspace;
- novo estado de Execution para espera multi-agent.

## Princípios

1. Agent participante é identidade persistente, não sessão ou chat.
2. Criar Agent é uma Execution administrativa; delegar trabalho cria uma Execution filha.
3. Mensagem que solicita processamento é uma Execution de comunicação atribuída ao destinatário.
4. Toda comunicação preserva `correlation_id`, ownership, autorização, deadline, idempotência e auditoria.
5. Relação pai-filho expressa causalidade; não transfere privilégios.
6. Context, Memory, resultados e Artifacts são compartilhados por referências autorizadas e handoff mínimo.
7. Espera longa libera Worker e usa checkpoint, Event e continuação; não mantém loop oculto.
8. Falha e cancelamento propagam resultados explícitos conforme política, sem serem convertidos em sucesso.

## Responsabilidades e não responsabilidades

O subsistema multi-agent DEVE:

- resolver participantes persistentes e ativos pela RFC 201;
- criar novos participantes somente por Agent Administration e Execution;
- validar remetente, destinatário, owner, Workspace, finalidade e Grants;
- criar mensagens e delegações com identidades, correlação e chaves idempotentes;
- criar uma Execution distinta para cada tentativa delegada;
- registrar handoff com objetivo, limites, referências e resultado esperado;
- oferecer espera cancelável, com deadline e liberação de Worker;
- retornar resultados por referência com terminal e proveniência;
- aplicar propagação explícita de falha e cancelamento;
- emitir Events no passado e tolerar entrega duplicada.

O subsistema multi-agent NÃO DEVE:

- copiar Context ou histórico completo da origem;
- conceder acesso à Memory privada do remetente por padrão;
- reutilizar a Execution do remetente como Execution do destinatário;
- chamar Provider ou executar Agent;
- escrever estados de Execution diretamente;
- tratar Event como comando ou autorização;
- inferir confiança por nome, persona, correlação ou relação causal;
- manter segredo, credencial ou handle vivo em mensagem ou handoff;
- ocultar falha de filho, entrega ou autorização.

## Topologia e ownership

Uma colaboração pode conter um coordenador e participantes, mas a topologia não altera ownership:

```text
Collaboration {
  collaboration_id: CollaborationId
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  participant_agent_ids: AgentId[]
  coordinator_agent_id: AgentId | null
  policy_ref: CollaborationPolicyRef
  correlation_id: CorrelationId
  created_at: Instant
  version: Version
}
```

Participar de `Collaboration` permite somente as operações declaradas pela política. Um Agent de usuário diferente ou Workspace incompatível exige autorização explícita e contrato futuro de compartilhamento; o modo single-user inicial não elimina essa validação.

Criar uma Collaboration ou alterar participantes, quando produzir trabalho, ocorre por Execution administrativa. Remover participante impede novas mensagens e delegações, mas não remove o Agent persistente nem apaga Executions e Events anteriores.

## Entidades e dados conceituais

O pseudocódigo é tipado, contratual e não executável.

```text
AgentMessage {
  message_id: AgentMessageId
  collaboration_id: CollaborationId
  sender_agent_id: AgentId
  recipient_agent_id: AgentId
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  authorization_ref: CommunicationGrantRef
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  delivery_execution_id: ExecutionId
  kind: AgentMessageKind
  inline_summary: BoundedText | null
  content_refs: ContentReference[]
  handoff_ref: HandoffRef | null
  deadline_at: Instant | null
  idempotency_key: IdempotencyKey
  classification: DataClassification
  created_at: Instant
}

AgentMessageKind = INFORM | REQUEST | RESPONSE | CONTROL_NOTICE
```

`inline_summary` é pequeno, sanitizado e suficiente apenas para roteamento ou compreensão imediata. Conteúdo volumoso, privado ou durável permanece em referência autorizada. `CONTROL_NOTICE` informa um fato; não substitui comando de cancelamento, autorização ou transição.

```text
Delegation {
  delegation_id: DelegationId
  collaboration_id: CollaborationId
  parent_execution_id: ExecutionId
  child_execution_id: ExecutionId
  delegator_agent_id: AgentId
  delegate_agent_id: AgentId
  handoff_ref: HandoffRef
  user_id: UserId
  workspace_id: WorkspaceId | null
  owner: ActorRef
  authorization_ref: DelegationGrantRef
  correlation_id: CorrelationId
  causation_id: EventId | CommandId
  deadline_at: Instant | null
  failure_policy: DelegationFailurePolicy
  cancellation_policy: DelegationCancellationPolicy
  idempotency_key: IdempotencyKey
  created_at: Instant
}

DelegationFailurePolicy = PROPAGATE | CONTINUE_WITH_FAILURE_REF | REQUEST_RETRY
DelegationCancellationPolicy = CASCADE | DETACH_IF_AUTHORIZED | CANCEL_CHILD_ONLY
```

O filho recebe novo `execution_id`, seu próprio `agent_id`, limites e snapshot de Agent. Ele preserva `correlation_id`, referencia a causa e nunca herda autoridade, contexto ou Worker da Execution pai.

### Handoff estruturado

```text
alias StructuredHandoff = RFC303.StructuredHandoff
alias HandoffRef = RFC303.HandoffRef
```

A RFC 303 é a fonte canônica dos campos de `StructuredHandoff`, `HandoffRef`, referências compartilhadas, budget, Grants e resolução. Esta RFC usa somente o alias e nunca materializa uma variante `Handoff`. O valor canônico DEVE declarar origem e destino de Agent e Execution, objetivo, critérios, constraints, contrato de saída, referências, budget, purpose, classificação, correlação, integridade e `expires_at` não nulo.

Somente `HandoffRef` atravessa mensagens, delegações e retornos. A referência carrega versão, expiração e integridade e DEVE ser resolvida e reautorizada pela RFC 303 antes do uso. `expires_at` não pode exceder a validade do Grant e a `deadline_at` da delegação não pode ampliar essa validade. Handoff expirado falha fechado; não é convertido em histórico inline. O handoff NÃO DEVE conter histórico bruto, Context completo, prompt secreto, credencial ou Grant mais amplo que a tarefa.

```text
DelegationResult {
  delegation_id: DelegationId
  child_execution_id: ExecutionId
  terminal_state: COMPLETED | FAILED | CANCELLED
  result_ref: ResultReference | null
  failure_ref: FailureReference | null
  handback_ref: HandoffRef | null
  usage_summary: UsageSummary
  finished_at: Instant
}
```

Somente `COMPLETED` possui resultado de sucesso. Falha e cancelamento usam referências sanitizadas e permanecem distinguíveis.

## Contratos públicos

```text
interface MultiAgentCoordinator {
  request_agent_creation(command: CreateParticipantAgent) -> AdministrativeExecutionRef
  send(command: SendAgentMessage) -> MessageExecutionRef
  delegate(command: DelegateTask) -> DelegationReceipt
  wait_for(command: WaitForDelegations) -> WaitReceipt
  return_result(command: ReturnDelegationResult) -> ReturnReceipt
  request_cancel(command: CancelDelegation) -> CancellationReceipt

  pre: actor e Agents estão autorizados no ownership declarado
  post: trabalho assíncrono aceito possui Execution própria
  post: repetição semanticamente igual não duplica mensagem, filho ou espera
}
```

```text
SendAgentMessage {
  actor: ActorRef
  collaboration_id: CollaborationId
  sender_agent_id: AgentId
  recipient_agent_id: AgentId
  kind: AgentMessageKind
  inline_summary: BoundedText | null
  content_refs: ContentReference[]
  handoff_ref: HandoffRef | null
  deadline_at: Instant | null
  correlation_id: CorrelationId
  causation_id: EventId | CommandId | null
  idempotency_key: IdempotencyKey
}

MessageExecutionRef {
  message_id: AgentMessageId
  delivery_execution_id: ExecutionId
}
```

Enviar mensagem cria uma Execution de entrega/processamento atribuída ao destinatário. Uma mensagem puramente informativa pode concluir após entrega autorizada; uma solicitação só produz trabalho do destinatário dentro dessa Execution ou de uma delegação explicitamente criada. Resposta assíncrona é nova mensagem/Execution correlacionada ou resultado referenciado, não mutação oculta da mensagem original.

```text
DelegateTask {
  actor: ActorRef
  collaboration_id: CollaborationId
  parent_execution_id: ExecutionId
  delegator_agent_id: AgentId
  delegate_agent_id: AgentId
  handoff_ref: HandoffRef
  child_limits: ExecutionLimits
  deadline_at: Instant | null
  failure_policy: DelegationFailurePolicy
  cancellation_policy: DelegationCancellationPolicy
  correlation_id: CorrelationId
  causation_id: EventId | CommandId
  idempotency_key: IdempotencyKey
}

DelegationReceipt {
  delegation_id: DelegationId
  child_execution_id: ExecutionId
  correlation_id: CorrelationId
}
```

```text
WaitForDelegations {
  actor: ActorRef
  waiting_execution_id: ExecutionId
  delegation_ids: DelegationId[]
  completion_rule: ALL | ANY | MINIMUM_COUNT
  minimum_count: PositiveInteger | null
  deadline_at: Instant | null
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
}

WaitReceipt {
  wait_id: WaitId
  waiting_execution_id: ExecutionId
  checkpoint_ref: CheckpointRef
}
```

Uma espera aceita exige checkpoint seguro. Por política explícita, a Execution aguardante transiciona para `PAUSED`, libera Worker e registra os filhos aguardados. Projeções internas podem nomear essa condição `WAITING_CHILD`, mas seu mapeamento canônico é sempre `ExecutionState = PAUSED`. Eventos de terminal disparam reavaliação; quando a regra é satisfeita, o Orchestrator solicita `PAUSED -> QUEUED` conforme a RFC 102. O ContextManager remonta Context com somente resultados autorizados. Não se introduz estado adicional para Execution em espera, e a retomada não ocorre diretamente em `RUNNING`.

Alternativamente, uma coordenação destacada pode concluir a Execution corrente e criar nova Execution de continuação após os resultados. A escolha é explícita no contrato da operação; em ambos os casos a espera não mantém Worker bloqueado nem caminho de trabalho fora de Execution.

## Criação de Agents participantes

`request_agent_creation` delega à RFC 201 e retorna a Execution administrativa. O Agent criado permanece após a Collaboration terminar e tem owner, Workspace, configuração e ciclo de vida próprios. Encerrar Collaboration ou remover participante não arquiva nem exclui Agent.

Se a criação falhar ou for cancelada, nenhum participante parcial pode receber mensagens ou delegações. Repetição com a mesma chave retorna a mesma tentativa ou resultado; nova tentativa após terminal recebe outra Execution.

## Mensagens e entrega

O fluxo de mensagem é:

1. validar ator, participantes, finalidade, Grants, classificação e deadline;
2. registrar `AgentMessage` e criar a `delivery_execution_id` de forma atômica conceitual;
3. despachar somente IDs e referências mínimas;
4. revalidar ownership e resolver referências no destinatário;
5. registrar entrega, rejeição ou expiração como fato;
6. concluir a Execution de mensagem com resultado explícito.

Entrega é ao-menos-uma-vez no nível de Event; destinatário deduplica por `message_id` e efeito por `idempotency_key`. Mensagem expirada não é processada com autoridade antiga. Atraso e duplicata não autorizam reordenação causal inventada.

## Delegação e contexto

O fluxo de delegação é:

1. a Execution pai produz intenção estruturada de delegar;
2. o coordenador valida ambos os Agents e o handoff;
3. cria Delegation e Execution filha em `QUEUED` quando elegível;
4. o filho fixa sua própria configuração, políticas e limites;
5. ContextManager monta Context a partir do handoff e referências reautorizadas;
6. o filho executa e termina segundo a RFC 102;
7. o resultado terminal é encapsulado em `DelegationResult`;
8. espera ou continuação recebe somente refs necessárias.

A mãe não injeta seu prompt completo nem sua Memory privada. O filho não recebe automaticamente Tools, Capabilities, Skills ou Grants da mãe; usa apenas sua configuração e concessões delegadas estritamente limitadas.

## Espera e resultados

Espera é coordenação observável, cancelável e com deadline. O registro DEVE indicar conjunto aguardado, regra de conclusão, versão esperada, checkpoint e política para terminais não bem-sucedidos.

Ao satisfazer a espera:

- validar que cada terminal pertence à delegação e ao ownership esperado;
- deduplicar resultados por `child_execution_id`;
- preservar ordem causal por referências, sem depender apenas de `occurred_at`;
- montar um handback mínimo com resultados e falhas;
- solicitar retomada idempotente ou criar continuação;
- nunca alterar terminal do filho.

Um resultado tardio depois do deadline permanece auditável, mas só entra em nova continuação se houver autorização e política explícita.

## Propagação de falha

Falha do filho produz `DelegationFailed` com `failure_ref` sanitizada e aplica uma das políticas:

- `PROPAGATE`: a espera é satisfeita com falha; ao retomar, o Runtime pode terminar o pai em `FAILED` segundo seu contrato;
- `CONTINUE_WITH_FAILURE_REF`: o pai recebe a referência e decide degradar explicitamente;
- `REQUEST_RETRY`: o Orchestrator cria nova Execution filha, se limites, idempotência e deadline permitirem.

Nenhuma política fabrica `COMPLETED`. Retry cria novo `execution_id`, mantém `correlation_id` e aponta a tentativa anterior como causa. Se o pai já for terminal, falha tardia não o reabre; tratamento adicional requer nova Execution.

## Cancelamento

```text
CancelDelegation {
  actor: ActorRef
  delegation_id: DelegationId
  target: PARENT | CHILD | SUBTREE
  correlation_id: CorrelationId
  idempotency_key: IdempotencyKey
  requested_at: Instant
}
```

Cancelar delegação solicita cancelamento por `ExecutionControl`; não escreve estado. Para `SUBTREE`, cada descendente é enumerado e autorizado individualmente. A política define:

- `CASCADE`: solicita cancelamento de descendentes não terminais;
- `DETACH_IF_AUTHORIZED`: filhos continuam sob owner explícito e deixam de satisfazer a espera original;
- `CANCEL_CHILD_ONLY`: cancela o filho alvo e entrega o terminal ao pai conforme política de falha.

Cancelar pai não cancela filho implicitamente sem política. Cancelar espera remove sua continuação e pode retomar o pai com resultado de cancelamento ou cancelar o pai por comando separado. Terminais confirmados não são desfeitos, e resultado tardio de filho cancelado não vira sucesso.

## Fluxo normal

1. Collaboration autorizada resolve participantes persistentes.
2. Agent coordenador cria handoff mínimo e solicita delegação.
3. Nova Execution filha é criada e despachada.
4. Pai continua, registra espera com checkpoint ou conclui etapa destacada.
5. Filho monta Context próprio, executa e publica terminal.
6. Coordenador valida terminal e registra resultado por referência.
7. Regra de espera satisfeita retoma o pai por `QUEUED` ou cria continuação.
8. Síntese final permanece outra etapa observável da Execution correspondente.

## Fluxo de falha

- participante inválido ou suspenso impede criação de mensagem/delegação;
- handoff com referência não autorizada é rejeitado sem fallback de outro escopo;
- conflito de idempotência não duplica filho;
- falha de entrega termina a Execution de mensagem explicitamente;
- falha do filho produz `DelegationResult` não bem-sucedido;
- falha de espera ou checkpoint não retoma o pai com Context parcial;
- referência expirada é excluída ou falha conforme necessidade, nunca substituída por histórico bruto;
- publicação duplicada é deduplicada por `event_id`, `message_id` e IDs de relação.

## Fluxo de cancelamento

1. validar comando e escopo;
2. congelar novas continuações daquela espera/delegação;
3. aplicar política de subtree por comandos idempotentes;
4. observar terminais confirmados;
5. reconciliar resultados em trânsito;
6. registrar resultado parcial se algum alvo não puder ser cancelado;
7. liberar Worker e material temporário sem apagar auditoria, Agent ou Artifact.

## Eventos

| Event | Fato confirmado |
| --- | --- |
| `CollaborationCreated` | Collaboration autorizada foi registrada |
| `CollaborationParticipantAdded` | participante passou a integrar a Collaboration |
| `CollaborationParticipantRemoved` | participante deixou de receber novos trabalhos pela Collaboration |
| `AgentMessageCreated` | mensagem e Execution de entrega foram confirmadas |
| `AgentMessageDelivered` | destinatário autorizado confirmou entrega |
| `AgentMessageRejected` | entrega terminou recusada ou inválida |
| `AgentMessageExpired` | deadline venceu antes do processamento permitido |
| `DelegationCreated` | relação e Execution filha foram confirmadas |
| `StructuredHandoffCreated` | handoff canônico da RFC 303 foi confirmado; a RFC 203 apenas consome o fato |
| `DelegationCompleted` | filho terminou `COMPLETED` e resultado foi vinculado |
| `DelegationFailed` | filho terminou `FAILED` e falha foi vinculada |
| `DelegationCancelled` | filho terminou `CANCELLED` e cancelamento foi vinculado |
| `AgentWaitRegistered` | espera e checkpoint seguro foram confirmados |
| `AgentWaitSatisfied` | regra de espera foi atendida por terminais validados |
| `AgentWaitCancelled` | espera deixou de produzir continuação |
| `DelegationResultReturned` | resultado foi disponibilizado ao destinatário autorizado |

Events usam passado, envelope da RFC 103, `execution_id` aplicável, `correlation_id`, causalidade, sequência, owner e Workspace. Payload contém IDs, estados, razões categóricas e refs; não contém histórico completo, prompts, segredos ou resultados volumosos.

## Segurança

- remetente e destinatário são revalidados em envio, entrega, resolução e retorno;
- Collaboration e parentesco não transferem privilégios;
- referências têm escopo, finalidade, classificação, destinatário e expiração;
- Grants delegados são mínimos, revogáveis e não podem ser redelegados sem permissão;
- handoffs e mensagens tratam conteúdo de outro Agent como dado não confiável;
- prompt injection em resultado, Memory ou Artifact não altera hierarquia de instruções;
- segredo e credencial nunca entram em mensagem, handoff, Event ou Context;
- cross-workspace é negado por padrão;
- o lançamento single-user preserva `user_id` para futura separação entre usuários.

## Observabilidade

Logs, métricas e traces permitem reconstruir mensagem, delegação, espera, retorno, propagação e cancelamento por IDs. Métricas incluem volume de mensagens, latência de entrega, fan-out, profundidade de delegação, duração de espera, deduplicações, expirações, retries, falhas por política, cascatas e tamanho/relação de handoffs.

Conteúdo privado não é label. Auditoria registra ator, finalidade, destinatário, referências concedidas, política, versão e resultado terminal.

## Invariantes

- Agents participantes são persistentes e independentes de Collaboration e chat;
- criar Agent, enviar trabalho e delegar trabalho usa Execution;
- cada delegação possui exatamente uma Execution filha por tentativa;
- filho tem identidade, Agent, limites e configuração próprios;
- `correlation_id`, ownership, autorização, deadline e idempotência atravessam toda comunicação;
- parentesco e Collaboration não concedem autorização;
- Context e Memory não são copiados indiscriminadamente;
- handoff usa referências mínimas, versionadas e reautorizadas;
- espera longa libera Worker e não introduz novo estado de Execution;
- retomada de `PAUSED` passa por `QUEUED`;
- somente `COMPLETED` retorna sucesso;
- falha e cancelamento permanecem explícitos e auditáveis;
- cancelamento não apaga Agent, Execution, Event ou resultado confirmado;
- nenhum coordenador multi-agent chama LLM ou conhece adapter concreto.

## Extensibilidade

Novos roteadores, papéis, políticas de fan-out, regras de espera e formatos de handoff PODEM entrar por contratos versionados. Extensões DEVEM declarar ownership, compatibilidade, limites, classificação, idempotência, cancelamento, falha e Events. Elas não podem criar canal lateral de execução, acesso implícito a Memory ou estado oculto.

## Futuro

Blackboard, grupos dinâmicos, contratos de equipe, votação, mercados de Tasks, delegation budgets e handoffs multimodais poderão especializar colaboração. Compartilhamento entre usuários ou organizações exigirá grants, auditoria e revogação próprios. Qualquer evolução manterá Agent persistente, uma Execution por tentativa e compartilhamento mínimo por referência.
