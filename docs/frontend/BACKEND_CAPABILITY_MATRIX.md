# Backend Capability Matrix

| UX desejada | Backend suporta? | Como observar/controlar | Fonte | Limitação |
| --- | --- | --- | --- | --- |
| Criar uma tarefa | Parcial | `POST /v1/executions` | `api/gateway.py` | `task_ref` é referência, não texto de prompt; o recibo é assíncrono. |
| Ver lifecycle da execution | Parcial | GET de projection + eventos Execution se stream for composto | `execution/models.py`, `api/events.py` | Produção não injeta query/stream reais. |
| Pausar, retomar, cancelar | Sim no contrato | Control `PAUSE`, `RESUME`, `CANCEL`, versão esperada | `api/gateway.py` | UI precisa tratar 202, conflito e corrida de estado. |
| Pedir input ao usuário | Parcial | `WAITING_USER`; POST input com versão | `runtime/service.py`, gateway | O contrato recebe `input_ref`, não conteúdo. |
| Mostrar resposta textual | Não | Apenas `result_ref` | `execution/models.py` | Falta resolução/DTO seguro para resultado. |
| Indicador “trabalhando” | Sim, se eventos projetados | `QUEUED/STARTING/RUNNING` | execution | Não significa que há texto ou token streaming. |
| Agrupar tool calls | Parcial | `ToolStarted/Progressed/Finished` por `invocation_id` | tool runtime | Ponte outbox→`ClientEventStream` existe e está provada (`PostgresToolActivitySink`, ver IMPLEMENTATION_PLAN.md Fase B), mas nenhuma rota HTTP compõe um `ToolRuntimeService` com esse sink hoje; args/output continuam não públicos. |
| Mostrar progresso de tool | Parcial | `ToolProgressed(progress_kind, sequence)` | tool runtime | Sem percentagem, label garantido ou payload stream. |
| Estado timeout/cancelamento de tool | Sim no domínio | `ToolFinished` outcome/error | tool runtime | Precisa ponte de projeção para UI. |
| Mostrar comunicação A → B | Parcial forte | `AgentMessageCreated`, sender/recipient no registro; delivery execution | multi-agent | Ponte até o `ClientEventStream` existe (`PostgresMultiAgentEventRecorder`, ver IMPLEMENTATION_PLAN.md Fase B), mas o fact persistido nunca grava `sender_agent_id` (só `recipient_agent_id`, via `agent_id` do envelope) e payload público não traz texto; nenhuma rota HTTP compõe um coordinator hoje. |
| Mostrar delegação / child | Parcial forte | `DelegationCreated`, parent/child IDs, handoff | multi-agent | Ponte até o `ClientEventStream` existe (mesmo mecanismo acima); falta endpoint de graph/listagem dedicado e uma rota HTTP real que componha um `MultiAgentCoordinatorService`. |
| Mostrar retorno B → A | Parcial forte | `DelegationResultReturned` e terminal | multi-agent | Sem conteúdo do resultado; só refs. |
| Mostrar espera do pai | Sim no domínio | `AgentWaitRegistered/Satisfied` + PAUSED | multi-agent | A UI não deve inferir “mensagem sendo escrita”. |
| Mostrar provider/modelo | Parcial | GET provider e configurações | gateway/provider | Estado público exato além de enabled/model depende do adapter. |
| Configurar/revogar provider | Sim, composto em produção | PUT/GET/DELETE provider | gateway (`PostgresProviderConfigurationAdapter`) | Chave jamais é relida/exibida; armazenada em texto plano (sem criptografia de campo, ver IMPLEMENTATION_PLAN.md Fase D); CSRF/PAT e idempotência via semântica HTTP do PUT. |
| Mostrar memória/artifacts/workspace | Parcial | GET resource genérico | gateway | Schema, autorização de conteúdo e realtime não comprovados. |
| Mostrar browser/terminal/filesystem | Não para produto atual | Domínios e events internos | serviços específicos | Não há rota/stream público especializado. |
| Página de scheduler/workers | Não | Somente domínio | scheduler/workers | Sem endpoints nem projeções públicas. |
| Stream de texto de modelo | Não no frontend | Provider stream é interno | provider models/runtime | Runtime usa geração não streaming; SSE não carrega deltas. |
| Reconectar sem duplicar | Sim | cursor opaco + `event_id` | api events | Reconsultar snapshots quando cursor falhar; ordenação é por execution. |

## Requisitos de frontend ainda não observáveis

| Prioridade | Experiência | Dado ausente | Menor alteração recomendada |
| --- | --- | --- | --- |
| P0 | Produto utilizável | Composição de serviços de produção | Injetar adapters duráveis e autorizados de command/query/client stream no bootstrap. |
| P0 | Conversa real | Input textual e resultado textual autorizados | DTO de submit/result ou referências resolvíveis por endpoint com redaction. |
| P0 | Realtime confiável | Ponte outbox/archive → ClientEventStream | Projetor autorizado que publique somente eventos permitidos e retenção/cursor operacional. |
| P1 | Activity de Tools | Rota HTTP real que componha `ToolRuntimeService` com `PostgresToolActivitySink` | A ponte outbox→stream já existe e está provada (IMPLEMENTATION_PLAN.md Fase B); falta um composition root que a torne alcançável por um usuário real. |
| P1 | Grafo multiagent | Consulta autorizada de collaboration/delegation + composição real de `MultiAgentCoordinatorService` | `GET` projection por execution que traga nós/arestas e versões; a ponte de eventos até o stream já existe (Fase B), mas nenhuma rota HTTP compõe o coordinator que os produziria. |
| P1 | Inspector | DTOs versionados de usage, custo, logs e referências | Projeções explícitas, não retorno genérico de dataclasses. |
| P2 | Artifacts navegáveis | Lista/metadata/download grant | Resource DTO + endpoint de download autorizado e de curta duração. |
| P2 | Details de memory | Snapshot/redaction por autorização | Projection de memória e política de conteúdo explícita. |
