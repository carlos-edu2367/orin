# Prompt da próxima sessão — Event System do AgentOS

Você vai implementar o próximo subsistema do backend do AgentOS: o Sistema de Eventos da RFC 103, integrado à outbox conceitual da RFC 601.

## Por que este é o próximo subsistema

O estado atual já possui:

- `ExecutionControl` com ciclo de vida, ownership, idempotência, uso, auditoria e outbox em memória para testes;
- `RuntimeService` com loop síncrono, limites, cancelamento, pause/resume, Tool round-trip e recuperação;
- `agentos.context` com contratos canônicos da RFC 104, montagem determinística, sanitização, orçamento, manifestos e descarte efêmero;
- `agentos.providers` com Provider API, Model Catalog, resolver determinístico, snapshots, pricing, fallback explícito e adapters de compatibilidade;
- `TransactionalPersistence` como porta da RFC 601 e `InMemoryTransactionalPersistence` que confirma Execution, auditoria e `OutboxEntry` na mesma unidade em memória;
- 115 testes unitários passando;
- nenhum Event Bus, Outbox Publisher, consumidor, archive, replay ou infraestrutura de broker.

O Event System é a próxima lacuna de Kernel: Runtime e Execution já geram fatos confirmados na outbox, mas ainda não há caminho público para publicar, consumir, deduplicar, reordenar, consultar ou reproduzir esses fatos com ownership e classificação. A persistência concreta PostgreSQL/Redis continua fora de escopo nesta sessão.

## Leitura obrigatória antes de editar

Leia integralmente:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`

Inspecione também:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\events.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\ports.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\control.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\in_memory.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\runtime\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\context\`
- `C:\Users\reali\Documents\AgentOS\src\agentos\providers\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\runtime\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\context\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\providers\`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-runtime-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-context-pipeline-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-provider-model-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-provider-model.md`

Não comece editando código. Faça um brainstorming curto, proponha o desenho, registre uma especificação em `docs/superpowers/specs/2026-08-06-event-system-design.md`, registre um plano em `docs/superpowers/plans/2026-08-06-event-system.md` e só então implemente em TDD.

## Objetivo

Implementar o domínio backend da RFC 103 sem broker ou infraestrutura concreta:

1. contratos públicos de envelope, payload mínimo, publicação, consumo, archive e replay;
2. `EventBus` substituível com entrega ao-menos-uma-vez;
3. `OutboxPublisher` que consome somente entradas confirmadas pela porta de persistência;
4. subscriptions com ownership, classificação, tipos e versões aceitas;
5. deduplicação por `event_id`, mantendo `delivery_id` distinto por tentativa;
6. ordenação opcional por `execution_id` e `sequence`, sem inventar lacunas;
7. archive/query/replay autorizado, preservando identidade e envelope original;
8. falhas retryable/permanentes, quarentena observável e isolamento entre consumidores;
9. integração determinística com a outbox existente de `ExecutionControl`;
10. testes apenas com fakes e adapters em memória.

## Escopo obrigatório

### Envelope e payload

Defina um pacote canônico escolhido após inspecionar o envelope atual, preferencialmente `src/agentos/events/`, sem duplicar regras entre `agentos.execution.events` e o novo domínio. O envelope deve cobrir:

- `event_id`, `event_type`, `event_version`, `occurred_at`, `source`;
- `correlation_id`, `causation_id`, `sequence`;
- `user_id`, `workspace_id`, `execution_id`, `classification`;
- payload mínimo, tipado ou bounded, com referências opacas para conteúdo volumoso.

Valide UTC/offset, IDs não vazios, versão positiva, sequência positiva exatamente quando houver `execution_id`, ownership consistente e payload sem segredos. Não coloque prompts completos, respostas completas, credenciais, tokens, headers, cookies, bindings, argumentos privados ou exceções de tecnologia no envelope, payload, `repr` ou erro público.

Preserve compatibilidade com `ExecutionEventType`, `EventEnvelope` e `OutboxEntry` já usados por `ExecutionControl`, por adapter ou por migração mínima de tipos públicos. Não quebre a suíte existente.

### Portas públicas

Implemente Protocols para:

```text
OutboxPublisher.publish_pending(request: PublishOutboxBatch) -> OutboxPublishResult
EventBus.publish(batch: tuple[EventEnvelope, ...]) -> PublishReceipt
EventBus.subscribe(subscription: SubscriptionSpec, consumer: EventConsumer) -> SubscriptionRef
EventConsumer.handle(delivery: EventDelivery) -> DeliveryDisposition
EventArchive.query(query: AuthorizedEventQuery) -> EventPage
EventArchive.replay(request: ReplayRequest) -> ReplayJobRef
```

Inclua requests/results para cursor/posição de outbox, leases, subscription, delivery, archive, replay, cancelamento e quarentena. Toda operação sensível carrega `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose` quando a RFC exigir; consultas administrativas usam uma `Execution` administrativa auditável, nunca bypass de ownership.

`DeliveryDisposition` deve distinguir `ACKNOWLEDGED`, `RETRYABLE_FAILURE` e `PERMANENT_FAILURE`. O `EventBus` não executa lógica de domínio por conta própria; ele entrega envelopes a consumidores injetados.

### Semântica de publicação

Implemente um fake/in-memory `OutboxPublisher` que:

- leia somente entradas existentes na outbox da persistência em memória;
- publique o mesmo `event_id` em retries e nunca gere um novo Event para uma pendência;
- mantenha cursor/posição opacos e bounded;
- produza recibo com publicados, pendentes, falhas e duplicidades;
- não marque o estado de domínio como confirmado;
- tolere falha entre commit e publicação sem perda definitiva observável;
- não publique uma transação rejeitada, não confirmada ou indeterminada sem `inspect_commit` autorizado.

A integração deve provar que `ExecutionControl` continua escrevendo estado, auditoria e outbox pela `TransactionalPersistence`, enquanto o publisher atua somente depois do commit.

### Entrega, deduplicação e ordenação

O fake/in-memory `EventBus` deve oferecer entrega ao-menos-uma-vez:

- duplicatas podem ocorrer após retry, timeout ou replay;
- `event_id` é a chave de deduplicação por consumidor;
- `delivery_id` é diferente em cada tentativa;
- consumidor só é reconhecido depois de `ACKNOWLEDGED`;
- falha de um consumidor não bloqueia os demais;
- falha permanente vai para quarentena/auditoria sem ser descartada silenciosamente;
- retry preserva `event_id`, envelope e `causation_id`.

Quando `ordering_requirement = PER_EXECUTION`, entregue ou retenha eventos segundo a sequência da mesma Execution. Evento atrasado ou duplicado não regride o cursor. Lacuna não deve ser preenchida por suposição: aguarde, solicite replay autorizado ou exponha estado de reconciliação. Eventos sem `execution_id` não recebem sequência artificial.

### Ownership, classificação e subscriptions

`SubscriptionSpec` deve declarar nome do consumidor, tipos aceitos, versões aceitas, escopo de ownership, ordenação, clearance de classificação e política de replay. A subscription não concede acesso além do escopo declarado. Conhecer `event_id`, `delivery_id`, `execution_id` ou `subscription_ref` não concede acesso.

A entrega deve filtrar por ownership e classificação antes de chamar o consumidor. Falta de autorização não deve revelar se um Event existe. Versão incompatível deve ser rejeitada explicitamente e auditada.

### Archive e replay

Implemente archive em memória apenas para testes/domínio:

- query paginada por cursor opaco, tipo, versão, ownership, classificação e intervalo temporal bounded;
- replay autorizado por `event_id`/cursor e subscription alvo;
- replay preserva `event_id`, `event_type`, `event_version`, `occurred_at`, `sequence`, payload e causalidade;
- replay é distinguido por metadado operacional fora do envelope;
- replay não executa efeitos diretamente e exige deduplicação no consumidor;
- cancelamento de replay impede novas entregas sem apagar Events ou desfazer efeitos já confirmados;
- retenção e expiração produzem resultado explícito, nunca falso “não existe”.

## Integração com Runtime, Execution, Context e Provider

- `RuntimeService` não deve publicar Events diretamente e não deve conhecer bus, archive, broker ou outbox interna.
- `ExecutionControl` continua sendo a fachada que valida comandos e confirma mudanças + outbox atomicamente via `TransactionalPersistence`.
- Events de `ExecutionQueued`, `ExecutionStarted`, `ExecutionWaitingForTool`, `ExecutionWaitingForUser`, `ExecutionPaused`, `ExecutionResumed`, `ExecutionFinished`, `ExecutionFailed` e `ExecutionCancelled` devem continuar com envelope mínimo e sequência válida.
- Events de Provider/Model, Context, Tool ou Memory futuros devem poder entrar pelo mesmo contrato sem importar esses pacotes internos.
- Consumidores não podem acessar `TransactionalPersistence`, catálogo, Context ou Runtime interno para contornar portas.

## Segurança e fronteiras

- Não implementar PostgreSQL, SQLAlchemy, Alembic, Redis, pub/sub real, Kafka, RabbitMQ, FastAPI, SSE, workers ou outbox física.
- Não importar SDK de Provider, HTTP, filesystem, Memory, Artifact Storage, Tool Runtime ou Browser.
- Não expor payload proprietário, segredo ou conteúdo completo em logs, repr, erros, archive ou replay.
- Não confundir `delivery_id` com `event_id`.
- Não prometer exactly-once.
- Não publicar fato antes de commit confirmado.
- Não transformar replay em nova identidade de Event.
- Não deixar consumidor ampliar classificação, ownership ou finalidade.

## Testes obrigatórios

Use TDD: escreva cada teste antes do código de produção, execute-o falhando pelo motivo correto e implemente o mínimo necessário. Cubra pelo menos:

- validação de envelope, UTC, sequência, classificação, ownership e payload sanitizado;
- compatibilidade do envelope atual da Execution;
- publicação somente de outbox confirmada;
- cursor/lease de outbox, retry e mesmo `event_id`;
- publicação duplicada sem duplicar o Event histórico;
- subscription por tipo/versão/ownership/classificação;
- delivery id distinto, deduplicação por consumidor e ACK somente após efeito;
- isolamento entre consumidores;
- falha retryable, permanente e quarentena;
- ordenação por Execution, evento atrasado, duplicata e lacuna;
- archive paginado e query sem vazamento entre usuários/Workspaces;
- replay que preserva envelope, identidade, sequência e causalidade;
- cancelamento de replay/subscription;
- Runtime sem dependência concreta de Event Bus;
- ausência de segredos e payload proprietário em `repr`, erros, archive e deliveries;
- suíte existente de Execution, Runtime, Context e Providers sem regressões.

## Processo obrigatório da sessão

1. Leia integralmente as RFCs e o código/testes listados.
2. Faça brainstorming curto e proponha o desenho antes de editar código.
3. Registre `docs/superpowers/specs/2026-08-06-event-system-design.md`.
4. Registre `docs/superpowers/plans/2026-08-06-event-system.md`.
5. Execute o plano em ciclos TDD, mantendo contratos e arquivos focados.
6. Não adicione dependências concretas ou infraestrutura fora do escopo.
7. Execute:

```text
python -m pytest -q
python -m compileall -q src tests
```

8. Faça uma varredura de `src/agentos/events` ou do pacote escolhido para garantir ausência de FastAPI, HTTP, SDKs, banco, Redis, filesystem, broker e adapters tecnológicos.
9. Faça auditoria explícita requisito por requisito contra RFCs 050, 060, 101, 103, 601 e as integrações de 102.
10. Só declare conclusão com evidência fresca dos comandos e informe limitações do workspace, inclusive se não houver Git para commits.

## Critérios de conclusão

A sessão só está concluída quando:

- o Event System possui pacote canônico e contratos públicos estáveis;
- `EventEnvelope`, `EventBus`, `OutboxPublisher`, `EventConsumer` e `EventArchive` estão cobertos;
- publicação é pós-commit, ao-menos-uma-vez, deduplicável e observável;
- ownership, classificação, finalidade, correlação, causalidade e sequência são preservados;
- archive/replay são autorizados, bounded e não alteram identidade histórica;
- falhas, retry, quarentena, cancelamento e lacunas são explícitos;
- Runtime permanece independente do bus e da persistência concreta;
- nenhum broker, banco, SDK, segredo ou payload proprietário foi introduzido;
- testes novos e existentes passam integralmente;
- especificação, plano e implementação são coerentes com RFCs 050, 060, 101, 103, 601 e 102.
