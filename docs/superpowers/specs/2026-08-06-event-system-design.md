# Event System do AgentOS — Especificação de Design

**Data:** 2026-08-06  
**Base normativa:** RFC 050, RFC 060, RFC 101, RFC 102, RFC 103 e RFC 601  
**Estado:** Aprovada em brainstorming; pronta para plano e implementação

## Objetivo

Implementar o domínio completo do Sistema de Eventos da RFC 103, integrado à
outbox conceitual da RFC 601, sem introduzir broker, banco, transporte, worker
ou adapter tecnológico concreto. A implementação desta sessão será composta
por contratos públicos estáveis e fakes/adapters determinísticos em memória,
com fronteiras que permitam substituir cada adapter no futuro.

O sistema deverá publicar somente fatos confirmados, entregar ao-menos-uma-vez,
preservar identidade e causalidade, filtrar por ownership/classificação antes
do consumo, tornar falhas e lacunas observáveis e permitir archive/query/replay
autorizados sem criar nova identidade histórica.

## Decisões de design

### Pacote canônico e compatibilidade

O pacote canônico será `src/agentos/events/`. O envelope existente em
`agentos.execution.events` continuará válido para a suíte e para os contratos
de Execution. Um adapter em `agentos.events.compat` fará a conversão entre os
dois modelos sem duplicar regras de segurança ou entrega. A compatibilidade
preservará `ExecutionEventType`, `EventEnvelope` e `OutboxEntry` existentes;
quando necessário, a migração será mínima e manterá os imports públicos atuais.

### Implementação em memória

As implementações desta sessão serão síncronas, determinísticas e bounded:

- `InMemoryEventBus`: publicação, subscriptions, filtragem, tentativas,
  deduplicação, ordenação por Execution, quarentena e cancelamento;
- `InMemoryOutboxPublisher`: leitura da outbox já confirmada pela porta de
  persistência, cursor opaco, lease, retry e receipts;
- `InMemoryEventArchive`: armazenamento de envelopes imutáveis, query paginada,
  jobs de replay e cancelamento.

Síncrono não significa entrega exactly-once: a API simula perda de confirmação,
retry e replay e sempre conserva a semântica mínima de entrega duplicável.

### Limites de responsabilidade

`ExecutionControl` continua responsável por validar comandos e registrar estado,
auditoria e `OutboxEntry` na mesma transação. `RuntimeService` não conhecerá o
bus, archive, broker ou persistência. O publisher não confirma domínio. O bus
não executa lógica de domínio: apenas entrega envelopes a consumidores
injetados.

## Modelo canônico

### Envelope

`EventEnvelope` será imutável, com campos:

- `event_id`, `event_type`, `event_version`, `occurred_at`, `source`;
- `correlation_id`, `causation_id`, `sequence`;
- `user_id`, `workspace_id`, `execution_id`, `classification`;
- `payload`, como mapa bounded e imutável de dados mínimos/referências opacas.

As invariantes são:

- IDs, tipo, source e correlation são não vazios;
- tipo segue a convenção de fato passado e versão é positiva;
- `occurred_at` tem timezone/offset explícito;
- `sequence` é positiva exatamente quando há `execution_id`;
- ownership do envelope é consistente e `workspace_id` é preservado;
- payload contém somente valores bounded, sanitizados e serializáveis;
- segredos, credenciais, tokens, cookies, headers, prompts, respostas completas,
  argumentos privados, exceções concretas e conteúdo proprietário são rejeitados;
- `repr`, erros e estruturas de archive não incluem payload proibido.

`DataClassification` será ordenável por clearance (`INTERNAL`, `CONFIDENTIAL`,
`RESTRICTED`), sem transformar classificação em autorização. `EventType` será
representável como string para aceitar eventos futuros sem importar pacotes de
Provider, Context, Tool ou Memory.

### Payload mínimo

Será fornecido um payload bounded que aceita scalars, referências opacas e
estruturas aninhadas limitadas. O validador rejeitará chaves e valores com nomes
ou padrões de segredo e referências de conteúdo completo. A representação
pública de falhas usará somente códigos, resumos sanitizados e referências
opacas.

### Execution events

Os nove fatos já usados por Execution permanecem suportados:
`ExecutionQueued`, `ExecutionStarted`, `ExecutionWaitingForTool`,
`ExecutionWaitingForUser`, `ExecutionPaused`, `ExecutionResumed`,
`ExecutionFinished`, `ExecutionFailed` e `ExecutionCancelled`.

O adapter garantirá que esses eventos carreguem `execution_id`, sequência válida,
ownership, correlation e payload mínimo, sem modificar a semântica existente.

## Portas públicas

As portas serão Protocols síncronos com requests/results imutáveis:

```text
OutboxPublisher.publish_pending(request: PublishOutboxBatch)
    -> OutboxPublishResult
EventBus.publish(batch: tuple[EventEnvelope, ...]) -> PublishReceipt
EventBus.subscribe(subscription: SubscriptionSpec, consumer: EventConsumer)
    -> SubscriptionRef
EventConsumer.handle(delivery: EventDelivery) -> DeliveryDisposition
EventArchive.query(query: AuthorizedEventQuery) -> EventPage
EventArchive.replay(request: ReplayRequest) -> ReplayJobRef
```

Todos os pedidos sensíveis carregam, quando aplicável, `user_id`,
`workspace_id`, `agent_id`, `execution_id`, `correlation_id` e `purpose`.
Consultas administrativas exigem um contexto de Execution administrativa
auditável; não haverá bypass por ID.

O domínio também exporá tipos para:

- posição/cursor opaco de outbox e archive;
- leases com proprietário, validade bounded e renovação explícita;
- subscription e cancelamento;
- delivery, tentativa, receipt e estado operacional;
- retry, falha permanente, quarentena e auditoria;
- jobs de replay e resultado/cancelamento.

## Publicação e outbox

`InMemoryOutboxPublisher` receberá uma implementação da porta de persistência
existente e inspecionará somente entradas cuja transação esteja confirmada.

Regras:

1. `COMMITTED` pode ser publicado;
2. `NOT_COMMITTED` não é publicado;
3. `UNKNOWN` exige `inspect_commit` autorizado antes de qualquer publicação;
4. retry reutiliza exatamente o mesmo `event_id` e envelope;
5. cursor e lease são opacos, bounded e não expõem conteúdo;
6. receipt informa publicados, pendentes, falhas, duplicidades e posição;
7. publisher nunca altera Execution, auditoria, commit ou estado de domínio;
8. falha pós-commit permanece pendente/observável e pode ser reconciliada;
9. uma transação rejeitada ou não confirmada nunca produz fato publicado.

O publisher poderá consultar uma outbox em memória por uma porta estreita ou
adapter de teste, sem importar detalhes privados de `InMemoryTransactionalPersistence`.

## EventBus, entrega e consumers

`InMemoryEventBus` manterá histórico operacional bounded e uma subscription
isolada por consumidor. A entrega seguirá:

1. validar envelope e compatibilidade de tipo/versão;
2. filtrar ownership e classificação antes de chamar o consumer;
3. aplicar ordenação opcional por Execution;
4. gerar novo `delivery_id` para cada tentativa;
5. chamar `consumer.handle` com o mesmo envelope e `event_id`;
6. reconhecer somente após `ACKNOWLEDGED`;
7. em falha retryable, conservar pendência para nova tentativa;
8. em falha permanente, registrar quarentena/auditoria e isolar a subscription;
9. não bloquear os demais consumidores por falha de um consumer.

Deduplicação é por `(subscription_ref, event_id)`, nunca por `delivery_id`.
Depois de ACK, uma entrega repetida não reaplica o efeito do consumer. Retries e
replays anteriores ao ACK podem criar múltiplas tentativas, todas com o mesmo
envelope e `causation_id`.

### Subscriptions

`SubscriptionSpec` declara nome, tipos aceitos, ranges de versão, ownership,
ordering (`NONE` ou `PER_EXECUTION`), clearance e `ReplayPolicy`. A subscription
não amplia nenhum desses limites. Tipo/versão incompatível será rejeitado
explicitamente e auditado; autorização insuficiente não revelará se o evento
existe.

### Ordenação

Para `PER_EXECUTION`, o bus conserva o maior sequence aplicado por Execution.
Evento atrasado ou duplicado não regride o cursor. Sequence maior com lacuna
fica retida e gera estado explícito de reconciliação; o bus não inventa eventos.
O modelo oferecerá uma ação de replay autorizado/reconciliação, mas não executará
essa ação automaticamente. Eventos sem `execution_id` nunca receberão sequência
artificial.

## Archive, query e replay

`InMemoryEventArchive` persistirá envelopes canônicos imutáveis somente para
domínio/testes. Query terá cursor opaco, limite positivo bounded e filtros por:

- `event_type` e versões;
- ownership e contexto autorizado;
- classificação compatível;
- intervalo temporal bounded.

Uma consulta autorizada devolverá uma página, próximo cursor e indicação explícita
de retenção/expiração quando o alvo estiver fora da retenção; não confundirá
expiração com “não existe”. Nenhum resultado cruzará user/workspace.

Replay exigirá ator, finalidade, ownership, subscription alvo e política de
replay. Poderá selecionar `event_id` ou cursor. O replay preservará
`event_id`, tipo, versão, ocorrido, sequence, payload e causalidade. Apenas
metadados operacionais fora do envelope indicarão que a entrega é replay.

Replay não executa efeitos diretamente: ele agenda entregas pelo bus, sujeitas
à deduplicação do consumer. Cancelamento impede novas entregas, não apaga Events
nem desfaz efeitos já ACKed. Retenção/expiração e subscription cancelada terão
resultados explícitos.

## Segurança, erro e observabilidade

Erros públicos serão classes/domain values sanitizados, sem mensagens de
tecnologia ou dados de entrada sensíveis. Falhas de entrega distinguem
`ACKNOWLEDGED`, `RETRYABLE_FAILURE` e `PERMANENT_FAILURE`. Quarentena carregará
referências operacionais, código e contexto mínimo, nunca payload privado.

As operações registrarão dados suficientes para consultar tentativas, duplicatas,
falhas, lacunas, backlog, replay e cancelamento usando IDs, tipo, versão e
sequence. Logs/repr não incluirão payload completo ou segredo.

## Integração e fronteiras

- `ExecutionControl` continua o único produtor de mudanças de Execution;
- `TransactionalPersistence` continua a unidade atômica de estado, auditoria e
  outbox;
- `RuntimeService` permanece livre de imports de `agentos.events` concretos,
  broker, persistência e infraestrutura;
- providers, Context, Tool e Memory poderão criar envelopes via contrato comum,
  sem importar tipos internos uns dos outros;
- consumidores só veem a entrega recebida e portas públicas autorizadas;
- não serão adicionados FastAPI, HTTP, SDK de Provider, SQLAlchemy, Alembic,
  Redis, filesystem, storage de artefato, broker ou dependência de produção.

## Estratégia de testes

A implementação será conduzida em ciclos TDD, com teste falhando observado antes
de cada comportamento novo. A suíte cobrirá:

- validação do envelope, UTC, sequence, ownership, classificação e payload;
- compatibilidade com Execution;
- publicação pós-commit, `UNKNOWN` com `inspect_commit`, cursor/lease e retry;
- mesmo `event_id` em retry e ausência de duplicação histórica;
- subscriptions por tipo/versão/ownership/classificação;
- delivery IDs distintos, deduplicação, ACK pós-efeito e isolamento;
- falhas retryable/permanentes, quarentena e cancelamento;
- ordenação, atraso, duplicata e lacuna por Execution;
- archive paginado, filtros e ausência de vazamento;
- replay preservando identidade/envelope/causalidade e cancelamento;
- ausência de dependência concreta do Runtime e ausência de segredos em repr,
  erros, archive e delivery;
- regressão completa das suítes Execution, Runtime, Context e Providers.

Verificação final obrigatória:

```text
python -m pytest -q
python -m compileall -q src tests
rg -n "FastAPI|fastapi|HTTP|openai|anthropic|google|SQLAlchemy|sqlalchemy|Redis|redis|filesystem|ArtifactStorage|requests|httpx|kafka|rabbit" src/agentos/events
```

Também será feita auditoria requisito por requisito contra RFCs 050, 060, 101,
102, 103 e 601. O sucesso será reportado somente com saída fresca desses
comandos. Como o workspace não é um repositório Git, a especificação e o plano
serão salvos, mas não haverá commit local.

## Fora de escopo desta sessão

Não serão implementados broker, PostgreSQL/Redis, schemas físicos, endpoints,
SSE, workers assíncronos, transporte de cliente, Event Sourcing, exactly-once,
integrações concretas ou payloads detalhados de RFCs futuras.

