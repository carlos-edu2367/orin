# Multi-agent Design — RFC 203

**Status:** aprovado para implementação no escopo de contratos públicos e adapters in-memory de referência.

## Objetivo e limites

Implementar a fronteira normativa de colaboração entre Agents persistentes: Collaboration, participantes, mensagens, delegações, handoffs, espera, resultados, falha, retry, cancelamento e Events mínimos. O subsistema coordena intenções e solicita mudanças através das portas existentes; ele não executa Agent/LLM, não monta Context final, não grava Execution diretamente e não conhece transporte ou infraestrutura.

O lançamento desta sessão é deliberadamente in-memory e bounded. Persistência durável, outbox física, broker, mailbox, worker, scheduler, lease, API e recuperação distribuída permanecem adapters futuros. Essa limitação será exposta nos contratos e na documentação, nunca mascarada como garantia de produção.

## Fontes normativas e decisão canônica

- RFC 050/060: portas públicas, imutabilidade, referências opacas, UTC, ownership, correlação e Events como fatos.
- RFC 101/102: Runtime/Execution e transições canônicas; `ExecutionControl` é a única fachada de lifecycle.
- RFC 103: envelope canônico `agentos.events.EventEnvelope`, outbox e deduplicação/publicação posterior ao commit.
- RFC 104: Context temporário, mínimo, versionado e montado pelo destinatário.
- RFC 201: resolução de Agent ativo, administração via Execution e configuração versionada.
- RFC 202: coordenação sem duplicar plano, retry, cancelamento ou reconciliação do Orchestrator.
- RFC 203: colaboração, mensagens, delegação, espera e políticas.
- RFC 303: fonte canônica de `StructuredHandoff`, `HandoffRef`, Grants e referências compartilhadas.
- RFC 601 e ADRs 002/009/012/013 disponíveis: persistência atrás de portas e tecnologias concretas fora do domínio. O arquivo `docs/adr/013-orchestrator-plan-and-dispatch.md` citado no prompt não existe; a implementação usa o ADR 013 presente (`asyncio-concurrency-runtime`) apenas como contexto de concorrência, sem criar runtime físico.

`StructuredHandoff` e `HandoffRef` serão definidos uma única vez no contrato público de compartilhamento do pacote `agentos.context` e importados por alias em `agentos.multi_agent`. O pacote multi-agent não terá uma variante paralela.

## Arquitetura escolhida

```text
Commands imutáveis
        |
        v
MultiAgentCoordinatorService
  |       |          |          |       |
Agent  Execution  Context   Events  Orchestrator
ports  Control    Sharing   outbox  adapter
        |
        v
In-memory collaboration/message/delegation/wait records
```

`models.py` contém apenas valores bounded e imutáveis. `ports.py` declara a porta `MultiAgentCoordinator` e as dependências mínimas (`AgentRegistry`, `AgentAdministration`, `ExecutionControl`, `ContextSharingService`, `EventRecorder`, `OrchestratorAdapter`). `security.py` concentra ownership, classificação, purpose, fingerprint, limites e sanitização. `service.py` coordena fluxos sem conhecer adapters. `in_memory.py` implementa registros duráveis apenas como referência de processo e fornece faults de commit/indeterminação para testes. `compat.py` traduz as portas existentes sem importar implementação concreta.

## Contratos públicos

Todos os requests/receipts são `@dataclass(frozen=True, slots=True)`, usam tuplas para coleções, instantes timezone-aware e referências opacas. O coordenador expõe:

```text
request_agent_creation(CreateParticipantAgent) -> AdministrativeExecutionRef
send(SendAgentMessage) -> MessageExecutionRef
delegate(DelegateTask) -> DelegationReceipt
wait_for(WaitForDelegations) -> WaitReceipt
return_result(ReturnDelegationResult) -> ReturnReceipt
request_cancel(CancelDelegation) -> CancellationReceipt
```

Os modelos incluem `Collaboration`, `CollaborationParticipant`, `AgentMessage`, `Delegation`, `DelegationResult`, `WaitRegistration`, políticas de falha/cancelamento, `ContentReference`, `ResultReference`, `FailureReference`, `CheckpointRef`, `purpose`, `classification`, `user_id`, `workspace_id`, owner, correlation, causation, deadline e idempotency. Textos inline são pequenos e sanitizados; conteúdo durável é sempre uma referência autorizada.

`Collaboration` mantém participantes e versão. Remoção bloqueia trabalho novo sem apagar Agent, Execution, Event ou referência histórica. Resolução exige Agent existente e `ACTIVE`, owner compatível, user/workspace compatíveis, purpose permitido, classificação abaixo do teto e Grants válidos. Cross-workspace falha fechado.

## Fluxos e invariantes

### Criação, mensagem e entrega

Criação de Agent chama somente `AgentAdministration.request_create` e devolve a referência administrativa. `send` valida a Collaboration e participantes, calcula fingerprint e cria `AgentMessage` mais uma Execution de entrega atribuída ao destinatário. Mensagens `INFORM`, `REQUEST`, `RESPONSE` e `CONTROL_NOTICE` são dados distintos; `CONTROL_NOTICE` nunca transiciona ou cancela Execution. Deduplicação usa `(ownership, idempotency_key)` e `message_id`; a mesma chave com fingerprint diferente falha. Deadline vencido registra `AgentMessageExpired`; entrega inválida registra `AgentMessageRejected`.

### Delegação, handoff e resultado

`delegate` revalida pai, origem, destino, Collaboration e `HandoffRef` no instante de uso. Resolve o handoff pela porta RFC 303, verifica versão/integridade/expiração e limita Grants delegados. Solicita uma nova Execution filha em `QUEUED` com Agent/configuração/limites próprios, preservando correlation e causalidade. A mãe não transfere prompt, Context, Memory, Tool, Skill, Capability, Worker, segredo ou Grant. Cada retry tem nova `execution_id`, nova tentativa e causa apontando a anterior.

`return_result` aceita somente terminal `COMPLETED`, `FAILED` ou `CANCELLED` confirmado e guarda refs sanitizadas. Somente `COMPLETED` pode preencher `result_ref`; falhas/cancelamentos permanecem distintos e não são promovidos a sucesso.

### Espera e continuação

`wait_for` aceita conjunto bounded de delegações, regra `ALL`, `ANY` ou `MINIMUM_COUNT`, checkpoint ref, versão esperada, deadline e política para terminais não bem-sucedidos. Primeiro solicita pausa via `ExecutionControl`; somente após confirmação registra a espera. Eventos de terminal reavaliam a regra, deduplicando por `child_execution_id` e preservando causalidade por refs. Quando satisfeita, solicita `PAUSED -> QUEUED` via `ExecutionControl`; não existe `WAITING_CHILD` público. Falha de checkpoint não retoma o pai e resultado tardio após deadline fica auditável, mas não entra na espera sem nova autorização.

### Falha, retry e cancelamento

Falha aplica `PROPAGATE`, `CONTINUE_WITH_FAILURE_REF` ou `REQUEST_RETRY`; retry é permitido apenas dentro de limites/deadline e nunca reabre terminal. Cancelamento congela novas continuações antes de enumerar `PARENT`, `CHILD` ou `SUBTREE`, autoriza cada alvo individualmente e chama `ExecutionControl.request_cancel`. `CASCADE`, `DETACH_IF_AUTHORIZED` e `CANCEL_CHILD_ONLY` são explícitos. Terminais confirmados permanecem imutáveis; falha parcial de cascata é retornada como resultado parcial.

## Events e atomicidade

O serviço só registra fatos confirmados no envelope canônico de `agentos.events`. Os tipos normativos cobertos são CollaborationCreated/participant changes, AgentMessageCreated/Delivered/Rejected/Expired, DelegationCreated/StructuredHandoffCreated/Completed/Failed/Cancelled, AgentWaitRegistered/Satisfied/Cancelled e DelegationResultReturned. Payloads contêm somente IDs, ownership, correlation, causation, versão/sequência, estado, reason code e refs. O `EventBus` não é chamado diretamente; o adapter de gravação entrega o fato à outbox depois do commit. Eventos duplicados por `event_id`, `message_id`, `delegation_id` ou relação são idempotentes.

## Erros e limites

Falhas públicas são categóricas e sanitizadas: `UNAUTHORIZED`, `CROSS_WORKSPACE`, `AGENT_INACTIVE`, `PARTICIPANT_REMOVED`, `CLASSIFICATION_EXCEEDED`, `PURPOSE_REJECTED`, `GRANT_REVOKED`, `HANDOFF_EXPIRED`, `HANDOFF_INTEGRITY_FAILED`, `DEADLINE_EXPIRED`, `IDEMPOTENCY_CONFLICT`, `INVALID_STATE`, `CHECKPOINT_FAILED`, `DELIVERY_FAILED`, `COMMIT_UNKNOWN` e `CANCEL_PARTIAL`. Nenhuma mensagem de erro contém prompt, histórico, segredo, credencial, localização física ou exceção de adapter.

Bounds incluem tamanho de IDs/textos, número de participantes, refs, Grants, delegações por espera, resultados por handback e profundidade de cancelamento. A classificação de um ref não pode superar o teto do comando; `expires_at` do handoff não supera Grant nem deadline. Redelegação é proibida salvo Grant explícito, e o primeiro contrato da sessão não a habilita implicitamente.

## Testes e evidência

Os testes serão escritos antes de cada implementação e cobrirão contratos/bounds, ownership, Agent ativo, Collaboration, mensagens, entrega, idempotência, handoff canônico, delegação, espera, retry, falha, cancelamento, Events, outbox, ausência de dependências concretas e integração com portas públicas. A validação final executará `pytest`, `compileall`, scan obrigatório, scan transversal, `git diff --check` e `git status --short --branch`, além de uma auditoria requisito a requisito contra as RFCs listadas.

## Limitações explícitas

Os adapters in-memory não provam durabilidade, concorrência entre processos, entrega de transporte, lease, retenção, recuperação distribuída ou atomicidade de banco. A porta fica preparada para adapters futuros, mas nenhum broker, worker, scheduler, Redis, PostgreSQL, API, LLM, Tool, Memory ou Artifact Storage será criado nesta sessão.
