# Plano de entrega — Kernel único de execução e recuperação durável

**Origem:** análise `docs/reports/2026-08-19-analise-estrutura-agentica-e-visao-orin.md`
**Escopo:** tornar `Execution` a autoridade operacional do chat e permitir retomada segura de execuções longas.
**Tipo:** planejamento arquitetural; este documento não altera o comportamento de produção.

## Status de implementação

Em 2026-08-27, a entrega foi concluída para o caminho padrão de chat: novo turno e `Execution` canônica são gravados juntos; o `ChatWorker` executa `RuntimeService` como autoridade de lifecycle; `AgenticTurnRuntime` é somente um adaptador de stream/tool; e a migration `0040_execution_recovery_journal` acrescenta checkpoints e journal de efeitos. A recuperação reenfileira somente execuções sem fronteira externa; efeitos em voo ou desconhecidos entram em pausa de reconciliação, nunca em retry automático. A evidência técnica está em `docs/agent_memory/2026-08-27-execution-kernel-and-recovery-journal.md`.

## Resultado de produto

Uma mensagem no chat continua com a mesma experiência atual — streaming, ferramentas, perguntas estruturadas, cancelamento, projeto e agendamento — mas passa a iniciar e acompanhar uma única `Execution` canônica. O worker não administra um segundo ciclo de vida para a mesma tarefa. Após reinício, perda de lease ou queda do processo, o sistema retoma somente a partir de um checkpoint seguro; nunca executa novamente um efeito externo cujo resultado ainda seja desconhecido.

```text
HTTP / Scheduler
      |
      v
ConversationCommandService
  └── transação única: turn + Execution + dispatch + outbox
      |
      v
Execution dispatcher / worker com lease e fencing
      |
      v
Execution Kernel (RuntimeService evoluído)
  ├── contexto conversacional por referências
  ├── provider streaming por referências
  ├── gateway de efeitos (tools / provider)
  ├── checkpoints e effect journal transacionais
  └── eventos canônicos
      |
      +--> projeção Chat/atividade/SSE
      +--> resultado, auditoria e recuperação
```

## Estado encontrado

O plano parte dos seguintes fatos observados no checkout atual:

- `PostgresChatStore.create()` persiste mensagens, `conversation_turns` e `conversation_dispatches`; `ChatApplication` só depois chama `_project_execution()`, que declara explicitamente ser best-effort.
- `ChatWorker` reivindica o turno no `PostgresChatStore`, constrói `TurnSession` e executa `AgenticTurnRuntime`. Seus estados chamam `ExecutionApplicationAdapter.transition()` como projeção tolerante a erro. O próprio comentário do worker define a conversa como a autoridade visível.
- `ExecutionControlService` já oferece ownership, versões otimistas, idempotência, cancelamento/pausa/input, transições, commit de mudanças e outbox na mesma transação. `RuntimeService` já controla orçamento, contexto, provider, ação e a carga de um `CheckpointSnapshot`.
- `CapabilityService` tem um modelo mais completo para passos, `EffectState`, checkpoints e reconciliação, mas seu estado de referência é `InMemoryCapabilityState` e a composição de produção mantém a superfície de capabilities indisponível por padrão.
- O worker local ainda recupera turnos por `conversation_dispatches` e `recover_stale()`. Existem também contratos genéricos de dispatch/lease/fencing, mas eles não são o caminho do chat.

Portanto, há dois ciclos de vida parcialmente independentes. Este plano elimina a duplicidade sem remover, antes da migração, os contratos já usados por chat, UI, scheduler, Skills, browser, MCP, plugins e subagentes.

## Objetivos e limites

### Objetivos

1. `Execution` torna-se a fonte de verdade para estado, limites, cancelamento, pausa, entrada, resultado, checkpoint e causalidade de um turno.
2. A criação de chat passa a ser atômica: não existe turno visível/na fila sem `Execution`, nem `Execution` órfã sem o vínculo conversacional esperado.
3. Cada chamada que pode produzir efeito recebe uma identidade durável, estado de efeito e regra explícita de retry/reconciliação.
4. O worker usa um mecanismo único de lease, fencing, tentativa e recuperação para chat e futuras execuções longas.
5. Chat e SSE permanecem compatíveis; `conversation_*` torna-se projeção/read model e não um controlador concorrente.
6. Autorização, ownership, workspace, provider/modelo e referências de artefato continuam decididos por serviços confiáveis, nunca pelo prompt, UI ou resultado de ferramenta.

### Fora de escopo desta entrega

- Redesenhar a interface de chat, publicar Capabilities para a API ou criar um editor visual de workflows.
- Converter todos os subagentes e capacidades em uma única entrega. Eles são consumidores posteriores do Kernel já migrado.
- Garantir idempotência em sistemas externos que não a suportam. Nesses casos o resultado será `UNKNOWN` e exigirá reconciliação.
- Migrar ou exibir segredos, prompts completos, DOM, cookies ou argumentos sensíveis em checkpoints, eventos ou logs.

## Decisões arquiteturais propostas

### 1. Autoridade única e projeções unidirecionais

O estado canônico é `Execution`. A conversa continua sendo dona do transcript e da apresentação, mas não decide se uma execução está em andamento, concluída ou recuperável. A relação é de um turno para uma execução (`conversation_turns.execution_id`, já única), e as mudanças ocorrem em uma direção:

```text
Execution commit + outbox
        -> projector de conversa/atividade
        -> SSE e read models
```

Não haverá atualização de `Execution` a partir de `conversation_turns.state`, exceto em uma rotina controlada de reparo durante a migração. Qualquer divergência será observável e reconciliada, nunca silenciosamente engolida como é hoje.

### 2. Kernel canônico, não um terceiro loop

`RuntimeService` é a base do Kernel porque já usa `ExecutionControl`, contexto, provider, action port, orçamento e checkpoint. A implementação deve estender suas portas para representar streaming e efeitos conversacionais, em vez de criar outro loop paralelo chamado "runtime do chat".

Durante a transição, `AgenticTurnRuntime` fica atrás de um adaptador de compatibilidade, apenas para preservar formato de provider/tool e streaming. O adaptador não poderá persistir lifecycle, executar efeitos diretamente ou fazer transições próprias; ele deve delegar essas decisões ao Kernel. A remoção de `AgenticTurnRuntime` como orquestrador só ocorre depois de paridade comprovada.

### 3. Efeito é uma entidade durável

Provider e ferramenta passam pelo mesmo protocolo de efeito. Antes da chamada, o Kernel persiste a intenção (`PREPARED`) com `effect_id` e chave de idempotência. Após a resposta, persiste o recibo e o resultado (`APPLIED` ou `NOT_APPLIED`) na mesma transação do checkpoint/transição seguinte. Uma queda entre os dois produz `UNKNOWN`, não retry automático.

Estados propostos:

| Estado | Significado | Ação após recuperação |
| --- | --- | --- |
| `NOT_STARTED` | Não houve tentativa externa. | Pode iniciar uma vez. |
| `PREPARED` | Intenção durável, sem confirmação de envio. | Consultar o gateway/recibo; se não enviado, iniciar; se incerto, `UNKNOWN`. |
| `IN_FLIGHT` | Chamada foi entregue ao gateway, sem recibo terminal. | Não repetir; reconciliar. |
| `APPLIED` | Resultado e recibo confirmados. | Reaplicar contexto/retomar do checkpoint seguinte; não reinvocar. |
| `NOT_APPLIED` | Falha confirmou ausência de efeito. | Retry apenas se política e orçamento permitirem. |
| `UNKNOWN` | Não é possível provar o resultado. | Pausar em `WAITING_RECONCILIATION`/`PAUSED`; reconciliar ou pedir decisão humana. |
| `COMPENSATED` | Efeito reversível foi compensado e confirmado. | Seguir a política da tarefa. |

`UNKNOWN` é deliberadamente conservador. Para ferramentas locais que já possuem operação idempotente verificável, o reconciliador pode concluir `APPLIED` ou `NOT_APPLIED`; para envio de formulário, compra, e-mail ou escrita remota sem consulta segura, nenhuma nova tentativa é permitida sem decisão explícita.

### 4. Checkpoint representa uma decisão segura

Um checkpoint não guarda um dump de processo nem payloads sensíveis. Ele contém referências e o mínimo necessário para recomeçar deterministicamente:

- `execution_id`, versão da execução, sequência/iteração e versão do programa/adapter;
- `context_manifest_ref`, seleção de modelo autorizada e refs do transcript/resultado já confirmados;
- próximo passo lógico, `effect_id` pendente e estado de reconciliação;
- uso acumulado, limites restantes e causalidade;
- referências de aprovação/grant ainda válidos, nunca o segredo/grant em si.

O checkpoint é salvo na mesma transação em que a execução muda para espera, pausa, conclusão ou estado posterior a um efeito. Um checkpoint com efeito pendente jamais é classificado como seguro para executar novamente o efeito.

### 5. Segurança e tenancy são invariantes de todas as portas

Todo registro de execução, efeito, checkpoint, dispatch, resultado e projeção contém ou deriva `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, propósito e ator. Leituras e reconciliações repetem a autorização no backend. `effect_id`, idempotency key ou referência encontrada no contexto do modelo não concede acesso a outro usuário/workspace.

Provider keys continuam resolvidas no limite do provider e não são persistidas no journal. Checkpoints, outbox e eventos públicos carregam referências e metadados redigidos/bounded; o material completo somente fica no storage já autorizado para transcript, artefato ou configuração.

## Desenho de dados e contratos

Os nomes abaixo são propostos e devem ser validados contra a nomenclatura da migration imediatamente anterior antes da implementação.

### Persistência nova ou ampliada

1. **`execution_checkpoints`**: `checkpoint_id`, escopo completo, `execution_state_version`, `sequence`, `runtime_version`, `context_manifest_ref`, `next_decision`, `pending_effect_id`, `usage_snapshot`, `created_at`; unicidade por execução/sequência e índice para último checkpoint seguro.
2. **`execution_effects`**: `effect_id`, escopo completo, `kind` (`provider`/`tool`/futuro), `invocation_ref`, `request_ref`, `result_ref`, `idempotency_key`, `effect_state`, `retryability`, `attempt`, `deadline`, `prepared_at`, `started_at`, `resolved_at`, `reconciliation_ref`, `error_code` sanitizado e versão otimista.
3. **`execution_dispatch_links`** ou extensão do dispatch canônico: ligação idempotente entre execution, tipo de trabalho, payload por referência e tentativa/lease. Não manter uma segunda fila de conversa após a mudança.
4. **Projeção conversacional**: acrescentar somente campos de diagnóstico não sensíveis que forem necessários, como `execution_projection_version` e último evento canônico aplicado. Não duplicar estado como autoridade.

`tool_invocations` e registros de atividade existentes devem ser avaliados como possíveis projeções/ledger. Não devem ser reutilizados como source of truth sem verificar se já suportam todos os estados, chave de idempotência, ownership, versão e transação do efeito.

### Portas a introduzir ou completar

- `ExecutionCheckpointStore`: `save`, `load`, `latest_safe`, sempre com contexto autorizado; substitui o checkpoint apenas de teste no caminho do chat.
- `ExecutionEffectStore`: prepara, inicia, conclui, falha, marca desconhecido e lista pendentes por execução/lease.
- `EffectGateway`: encapsula provider e ferramenta, recebe apenas uma requisição autorizada/referenciada e expõe `invoke`, `inspect_receipt` e `reconcile`.
- `ConversationExecutionAdapter`: adapta transcript, `TurnSession`, Skills, memória, retrieval e atividade à porta de contexto/stream do Kernel; não chama `ExecutionApplicationAdapter` como RPC interno.
- `ConversationProjectionWorker`: consome eventos canônicos idempotentemente e atualiza mensagem, estado visível, activity ledger e SSE compatível.
- `ExecutionRecoveryService`: varre leases expiradas e efeitos/checkpoints não terminais, decide elegibilidade e enfileira continuação ou reconciliação.

`ExecutionApplicationAdapter` permanece como borda HTTP compatível. Internamente, chat e worker devem receber uma composição tipada de `ExecutionControlService`/portas, não dicionários de aplicação e não queries seguidas de transições best-effort.

## Fluxos-alvo

### Criação de chat

```text
API autenticada / Scheduler
  -> valida projeto, workspace, provider/modelo e anexos
  -> escreve em uma única transação:
       conversation/messages/turn
       Execution(QUEUED)
       task/input snapshot por referência
       dispatch canônico
       outbox ExecutionQueued
  -> retorna ChatReceipt + execution_id
```

Se a transação falhar, nenhum assistant placeholder, turno ou dispatch fica visível. Repetição da mesma idempotency key retorna exatamente a mesma relação turn/execution.

### Efeito de ferramenta ou provider

```text
RUNNING
  -> commit: effect PREPARED + checkpoint + WAITING_TOOL
  -> gateway recebe effect_id, lease/fence e idempotency key
  -> commit: effect IN_FLIGHT
  -> chamada externa
  -> commit: efeito terminal + result ref + checkpoint + RUNNING/WAITING_*
```

O streaming de texto é uma projeção incremental de um provider effect em andamento. Deltas podem ser persistidos em lote e publicados à UI, mas não confirmam conclusão do provider. O resultado final tem `result_ref` durável; uma reconexão reconstrói a mensagem a partir do transcript/projeção, sem repetir a chamada ao modelo.

### Recuperação

```text
lease vencida ou worker reiniciado
  -> carrega Execution + último checkpoint + efeitos não terminais
  -> nenhum efeito pendente: adquire nova lease e continua
  -> APPLIED sem projeção: reprojeta/avança sem reinvocar
  -> NOT_STARTED/PREPARED comprovadamente não enviados: continua
  -> IN_FLIGHT/UNKNOWN: agenda reconciliador e pausa a execução
  -> terminal: somente repara projeções ausentes
```

Uma execução só pode ser continuada por worker com lease e fencing token atuais. Escritas de worker antigo falham por versão/fence e não podem sobrescrever um resultado mais novo.

## Fases de implementação

### Fase 0 — Caracterização, telemetria e guardrails

**Mudanças:** nenhuma alteração de autoridade. Instrumentar a correlação atual entre turn, execution e dispatch; adicionar métricas de falha de projeção, divergência de estados, turn sem execução, execução sem turn e duração por fase. Documentar uma matriz de equivalência de estados (`queued`, `starting`, `running`, `waiting_user`, `completed`, `failed`, `cancelled`).

**Arquivos a examinar/alterar:** `src/agentos/conversations/chat.py`, `src/agentos/workers/chat.py`, `src/agentos/persistence/postgres/agentic_activity.py`, testes de conversas/workers/execution e runbook.

**Aceite:** dashboard/log sanitizado permite medir a divergência sem alterar resposta do usuário; testes comprovam que a instrumentação não expõe prompt, chave, cookie, argumentos sensíveis ou caminhos fora do workspace.

### Fase 1 — Criação atômica e vínculo canônico

**Mudanças:** substituir a sequência `PostgresChatStore.create()` seguida de `_project_execution()` por um serviço transacional de comando de conversa. Ele cria conversation/turn/messages, `Execution`, input/task reference, dispatch e outbox em uma transação. Injetar a dependência tipada no bootstrap e no scheduler.

**Compatibilidade:** manter as rotas e o `ChatReceipt` atuais. A execução continua tendo o ID determinístico derivado do `turn_id`, preservando links e telas existentes. Criar reparador idempotente apenas para turns legados que já existem sem execution.

**Aceite:** rollback transacional não deixa registros parciais; concorrência e idempotência retornam o mesmo turno/execution; scheduled chat usa o mesmo serviço; os testes de criação de conversa, agendamento, API e ownership passam.

### Fase 2 — Dispatch, lease e estados canônicos

**Mudanças:** introduzir um adaptador de dispatch para que o ChatWorker receba `execution_id` e lease, não `turn_id` como unidade de controle. Mover aquisição, heartbeat, fencing, retry agendável e detecção de lease vencida para o dispatch canônico. A conversa recebe atualizações por projeção de eventos da `Execution`.

**Compatibilidade:** o poller local pode continuar como processo único, mas usa a porta de dispatch/lease. `conversation_dispatches` torna-se read model temporário; não será mais a fonte da recuperação.

**Aceite:** dois workers concorrentes não executam a mesma execução; worker antigo não consegue finalizar após nova lease; cancelamento/pausa/input chegam à execução ativa; queda antes/depois da aquisição segue política previsível.

### Fase 3 — Journal de efeitos e checkpoints persistentes

**Mudanças:** criar migration, adapters PostgreSQL e contratos para checkpoints/effects. Encaminhar provider e ferramentas através de `EffectGateway`; persistir `PREPARED` antes da chamada e um recibo terminal antes de avançar. Conectar `RuntimeService.CheckpointPort` a storage real. Persistir uso, manifest e resultado por referência com cada decisão segura.

**Ordem importante:** primeiro ferramentas estritamente locais/idempotentes e provider com fixture determinística; depois terminal/browser/MCP/plugins e demais efeitos com reconciliação específica. Uma ferramenta sem reconciliador deve declarar `UNKNOWN` em crash entre envio e recibo.

**Aceite:** testes de falha injetada cobrem todas as fronteiras: antes da preparação, após preparo, após início, após efeito e antes do commit, após commit e antes da projeção. Nenhum caso reinvoca automaticamente um efeito `UNKNOWN`.

### Fase 4 — Adaptadores conversacionais e paridade do loop

**Mudanças:** implementar `ConversationExecutionAdapter` para converter os dados de `TurnSession` nas portas de contexto, provider streaming e action. Fazer o Kernel comandar transições, orçamento, retries, cancelamento e espera. No início, manter `AgenticTurnRuntime` somente como adaptador de formato de stream/tool enquanto a lógica de lifecycle passa a ser do Kernel; em seguida mover o parsing/continuação para as portas do Kernel e remover o orquestrador legado.

**Preservar:** compactação/context indicator, anexos e visão, Skills, memória, retrieval, browser isolado, MCP, plugins/hooks, subagentes, limite de ações/iterações, fallback de chaves e mensagens/deltas atuais.

**Aceite:** suite de paridade reproduz cenários de provider final, tool único/múltiplo/paralelo, tool malformado, retry de provider, cancelamento, deadline, `ask_user`, anexos, Skills, subagentes e streaming. O chat não chama `ExecutionApplicationAdapter.transition()` nem usa `_project()` best-effort.

### Fase 5 — Recuperação e reconciliação operacional

**Mudanças:** implementar `ExecutionRecoveryService` e reconciliadores por classe de efeito. Criar política declarativa de retry (`SAFE`, `POLICY_DEPENDENT`, `NEVER`), máximos por efeito/execution e prazo de reconciliação. Expor somente operações internas/autorizadas para reexecutar, aceitar resultado reconciliado ou solicitar decisão humana.

**Estados de usuário:** uma execução em reconciliação mostra que está aguardando confirmação, sem alegar conclusão. `ask_user` continua `WAITING_USER`; decisão sobre efeito desconhecido é uma aprovação distinta, vinculada ao `effect_id` e à evidência disponível.

**Aceite:** kill/restart real do worker nos pontos críticos prova: resultado confirmado não repete; efeito desconhecido não é repetido; output parcial é preservado quando válido; worker e UI retomam por evento/replay; ownership cruzado em checkpoint/effect/reconciliação é recusado.

### Fase 6 — Troca de autoridade e remoção controlada

**Mudanças:** ativar o Kernel para novos turns por feature flag de servidor, inicialmente em instalação/teste interna. Fazer shadow comparison somente com metadados e fixtures sem provider externo. Quando a paridade for estável, habilitar para todos os novos turns; manter leitura dos turns legados no caminho antigo até encerrar ou migrar de modo seguro. Remover projeções reversas, recovery de `conversation_dispatches` como controlador e dependências diretas do `ChatWorker` em `AgenticTurnRuntime`.

**Rollback:** a flag só volta o tráfego novo para o caminho legado; uma `Execution` já iniciada não muda de Kernel no meio. Em incidente, pausar execuções novas, preservar checkpoints/effects e reconciliar antes de qualquer retry.

**Aceite final:** não há código de produção que permita ao chat criar, controlar ou finalizar uma execução fora do Kernel; toda transição crítica possui commit/outbox; recovery E2E PostgreSQL/local worker passa; contratos públicos de chat e execuções seguem compatíveis.

## Matriz de testes e evidência

| Camada | Cenários obrigatórios |
| --- | --- |
| Domínio | transições válidas, ownership, idempotência, fence, limites, estado terminal, checkpoints seguros e tabela de effect state. |
| Persistência | migration upgrade/downgrade, escopo multi-tenant, CAS, commit atômico de execution/effect/checkpoint/outbox, índices e referências não sensíveis. |
| Kernel | provider final/tool/input/cancelamento/pausa, orçamento antes do efeito, retries e restauração do manifest. |
| Recuperação | crash em cada fronteira do efeito, lease expirada, worker duplicado, resultado tardio, replay idempotente e reparo de projeção. |
| Integração | PostgreSQL + poller local, scheduler → chat → execution, provider fixture, ferramenta local, activity/SSE e reabertura do chat. |
| Segurança | usuário/workspace/agent trocados, cursor adulterado, effect/checkpoint ref alheio, redaction de chaves e blocos de conteúdo não confiável. |
| Frontend | estados canônicos, reconexão, Stop, waiting user, waiting reconciliation, timeline deduplicada e rolagem/composer inalterados. |
| Operação | migração em base existente, monitoramento de filas/leases, restart do processo, backup/restore e rollback da feature flag. |

O gate de produção requer evidência registrada no runbook: commit, migration aplicada, configuração de PostgreSQL/local worker, cenário de queda executado, eventos sanitizados, estado final no banco e resultado de todos os checks. Fixture unitária não é prova de recuperação operacional.

## Critérios de saída mensuráveis

1. Taxa de `turn` sem `Execution` e `Execution` sem vínculo conversacional é zero para novos turns.
2. Não há divergência terminal entre projeção de chat e execução canônica; divergências não terminais são métricas/alertas e têm reconciliador.
3. Todo efeito possui `effect_id`, regra de retry e estado terminal ou de reconciliação antes de a execução prosseguir.
4. Reiniciar o worker não duplica efeitos confirmados e não reinvoca efeitos desconhecidos.
5. Cancelamento, pausa, input e limites têm uma única transição auditável por execution.
6. Chat manual e chat agendado percorrem o mesmo Kernel e preservam workspace/projeto/contexto.
7. O teste E2E com a infraestrutura local do projeto passa antes de remover o caminho legado.

## Riscos e mitigação

- **Migração grande do loop de provider/tool:** reduzir com adapters, casos de paridade e rollout por novos turns; nunca trocar execução ativa de caminho.
- **Streaming dificulta uma transação longa:** tratar deltas como projeção em lote e usar o resultado/checkpoint como confirmação canônica.
- **Efeitos sem consulta externa:** modelar como `UNKNOWN`, exigir aprovação/reconciliação, não inventar idempotência.
- **Duplicação de tabelas durante a transição:** definir claramente authority por fase e data de remoção; não sincronizar dois controladores bidirecionalmente.
- **Exposição de dados no journal:** apenas refs e campos bounded/redigidos; revisar DTO/SSE/log antes de publicar novos eventos.
- **Compatibilidade de instalações locais existentes:** migration aditiva, backfill idempotente, feature flag e leitura legada até o fim da janela de transição.

## Sequência recomendada e dependências

Executar Fases 0 e 1 primeiro. Não iniciar recovery real antes de ter criação atômica, porque um reconciliador não pode corrigir a ausência estrutural de `Execution`. Fase 2 prepara segurança de concorrência; Fase 3 cria a semântica de retomada; Fase 4 troca o loop; Fases 5 e 6 só são liberadas após a paridade e E2E da infraestrutura local.

Capabilities, orquestração e subagentes devem consumir as mesmas portas de checkpoint/effect/dispatch depois da Fase 4. Eles não devem criar outro modelo de recovery. Esse é o ponto de convergência que reduz o risco de corrigir autorização, retry, observabilidade ou recuperação em apenas um dos caminhos.
