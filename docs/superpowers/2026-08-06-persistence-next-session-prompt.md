# Prompt da próxima sessão — Persistência transacional do AgentOS

## Instrução de execução — sessão única e revisão independente obrigatória

Implemente este subsistema por completo em uma única sessão de trabalho, do
baseline à verificação final. Não pare após criar somente contratos, não deixe
um plano parcialmente executado para outro agente e não declare conclusão com
base apenas em testes focados.

É obrigatório disparar pelo menos um subagente de validação final antes da
resposta de conclusão. O subagente deve receber o contexto/diff da sessão,
fazer uma revisão independente e somente de leitura contra RFC 601 e ADRs
002/009/012, procurar lacunas de atomicidade, `UNKNOWN`, autorização,
vazamento de dependência tecnológica e regressões, e devolver achados
acionáveis com severidade e arquivos/linhas. O agente principal deve analisar
cada achado, corrigir os problemas aplicáveis, adicionar testes quando
necessário e executar novamente todos os gates. A resposta final deve
registrar que essa revisão ocorreu, seu resultado e qualquer risco residual.

Se a ferramenta de subagentes estiver indisponível, não simule a revisão nem
marque a sessão como concluída: registre o bloqueador objetivo e pare antes da
declaração de sucesso.

Você vai implementar o próximo subsistema do backend do AgentOS: a fronteira de Persistência da RFC 601, com adapter PostgreSQL baseado nas decisões das ADRs 002 e 012.

## Por que este é o próximo subsistema

Depois da conclusão do Memory/RFC 301, a próxima fronteira lógica é a
Persistência transacional da RFC 601: ela é a autoridade que permite compor
Execution, Events e Memory com commit, auditoria e outbox duráveis. Context,
Runtime, Events, Providers e os contratos de Memory já possuem implementação
de referência e testes; não reimplemente esses domínios nesta sessão.

O estado de partida esperado é:

- `ExecutionControl` como única fachada mutante do Runtime;
- `Runtime`, `Context`, `Events` e `Providers` com contratos públicos e testes de fronteira;
- envelope canônico de Events, Event Bus, archive, replay e Outbox Publisher em memória;
- `TransactionalPersistence` e `InMemoryTransactionalPersistence` como adapters de referência;
- RFC 201 preservada e integrada sem dependências concretas;
- suíte atual verde; confirme o número real com os comandos de baseline abaixo
  (a última verificação conhecida desta linha de trabalho teve `372 passed,
  1 skipped`).

A lacuna seguinte é completar e auditar a implementação durável, mantendo o
domínio independente de banco, ORM, migrations e conexões. PostgreSQL é a
autoridade transacional. Redis ainda não entra nesta sessão: coordenação
efêmera pertence à RFC 801/ADR 009 e será uma etapa posterior.

## Leitura obrigatória antes de editar

Leia integralmente:

- `C:\Users\reali\Documents\AgentOS\docs\architecture\000-overview.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\050-design-principles.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\060-glossary-and-conventions.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\101-runtime.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\102-execution-lifecycle.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\103-event-system.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\100-kernel\104-context-pipeline.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\200-agents\201-agent.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\501-provider-api.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\500-providers-models\502-model-catalog.md`
- `C:\Users\reali\Documents\AgentOS\docs\architecture\600-platform-data\601-persistence.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\002-postgresql-as-system-of-record.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\009-redis-for-ephemeral-coordination.md`
- `C:\Users\reali\Documents\AgentOS\docs\adr\012-sqlalchemy-alembic-persistence-adapters.md`

Inspecione também:

- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\ports.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\control.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\in_memory.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\execution\events.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\events\ports.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\events\compat.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\events\in_memory.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\agents\ports.py`
- `C:\Users\reali\Documents\AgentOS\src\agentos\agents\in_memory.py`
- `C:\Users\reali\Documents\AgentOS\tests\unit\execution\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\events\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\agents\`
- `C:\Users\reali\Documents\AgentOS\tests\unit\integration\`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\specs\2026-08-06-kernel-closeout-design.md`
- `C:\Users\reali\Documents\AgentOS\docs\superpowers\plans\2026-08-06-kernel-closeout.md`

Não comece editando código. Faça um brainstorming curto, proponha o desenho, registre uma especificação em `docs/superpowers/specs/2026-08-06-persistence-design.md`, registre um plano em `docs/superpowers/plans/2026-08-06-persistence.md` e só então implemente em TDD.

Antes de qualquer alteração, preserve alterações pré-existentes do working
tree. Registre exatamente:

```text
git status --short --branch
git log --oneline -12
python -m pytest -q
```

Depois produza a matriz `requisito RFC -> contrato/arquivo -> teste existente
-> lacuna -> correção -> evidência`. Não use `git reset --hard`, `git checkout`
destrutivo, limpeza ampla ou `git add .`.

## Objetivo

Implementar a persistência transacional sem vazar PostgreSQL para o domínio:

1. consolidar contratos públicos de contexto, opções transacionais, leituras autorizadas, scans, mudanças, outbox e recibos;
2. preservar compatibilidade entre `execution.ports.TransactionalPersistence` e a nova porta canônica, por adapter explícito ou migração mínima;
3. implementar adapter PostgreSQL usando SQLAlchemy 2 somente atrás da porta;
4. criar migrations Alembic versionadas, aplicadas por operação administrativa explícita, nunca pelo Runtime;
5. confirmar estado de domínio, auditoria mínima e outbox na mesma transação;
6. normalizar conflito, rejeição, rollback, timeout, deadlock e commit indeterminado;
7. implementar idempotência, fingerprint e concorrência otimista sem last-write-wins implícito;
8. oferecer leituras e scans bounded, consistentes e filtrados por ownership/classificação;
9. manter `InMemoryTransactionalPersistence` como adapter de teste compatível;
10. provar que Runtime, Agent, Events e demais domínios não importam SQLAlchemy, Alembic ou detalhes de schema.

## Escopo obrigatório

### Fronteira pública

Crie ou consolide, em `src/agentos/persistence/`, modelos e Protocols estáveis para:

```text
TransactionalPersistence.transact(request) -> TransactionResult
TransactionalPersistence.read(query) -> AuthorizedRecord | NotFound
TransactionalPersistence.scan(query) -> AuthorizedRecordPage
TransactionalPersistence.inspect_commit(query) -> TransactionReceipt
```

Os contratos devem expressar, no mínimo:

- `PersistenceOperationContext` com `user_id`, `workspace_id`, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`;
- `TransactionOptions` com consistência, isolamento, timeout e `read_only`;
- `TransactionRequest` com transaction ID, contexto, opções, versões esperadas, idempotency key, changes e outbox;
- `TransactionCommitted`, `TransactionRejected`, `TransactionConflicted` e `TransactionIndeterminate`;
- `AuthorizedRead`, `AuthorizedScan`, paginação opaca e ceiling de classificação;
- referências opacas de registro, versão e outbox, nunca ORM ou SQL.

Não duplique silenciosamente a porta já usada por Execution. Se houver uma porta canônica nova, forneça adapter de compatibilidade e mantenha a suíte existente verde.

### Atomicidade e idempotência

O adapter deve garantir que:

- `changes`, auditoria mínima e `OutboxEntry` sejam confirmados no mesmo commit;
- nenhuma publicação de Event ocorra dentro do adapter de persistência;
- uma rejeição ou rollback não deixe estado parcial visível;
- `COMMITTED` só seja retornado depois do commit durável;
- perda de conexão durante commit retorne `UNKNOWN`, nunca sucesso inventado;
- `inspect_commit` seja exigido antes de repetir um comando indeterminado;
- a chave de idempotência seja escopada por ownership, execution, operação, finalidade e fingerprint;
- mesma chave e mesmo fingerprint retornem o efeito confirmado;
- mesma chave com fingerprint divergente produza conflito explícito;
- o mesmo Event não possa ser gravado duas vezes por retry do comando.

### Ownership, classificação e consultas

Toda leitura e mutação deve:

- revalidar `user_id`, `workspace_id` quando aplicável, Agent, Execution, correlação e purpose;
- aplicar filtro de ownership no servidor, mesmo em modo single-user;
- aplicar `classification_ceiling` antes de materializar o resultado;
- não revelar se um registro existe quando o chamador não tem autorização;
- vincular cursor à consulta, contexto, filtros, classificação e versão do store;
- impor limite máximo de página e filtros bounded;
- não serializar segredos, SQL, credenciais, payload proprietário ou valores sensíveis em erro, `repr`, log ou Event.

### Adapter PostgreSQL e migrations

Implemente o adapter em um pacote tecnológico isolado, por exemplo `src/agentos/persistence/postgres/`:

- SQLAlchemy 2 para engine, sessão, transação e mapeamento interno;
- Alembic para migrations versionadas;
- URL, pool, timeout e credenciais recebidos por composição/configuração externa;
- nenhum objeto SQLAlchemy, ORM, sessão ou conexão atravessa `src/agentos/persistence` público;
- migrations explícitas para o mínimo necessário: registros duráveis, versões, ownership, auditoria, outbox e idempotência;
- índices e constraints devem proteger unicidade, versão, ownership e relação outbox-origem;
- aplicação de migration não pode ocorrer no import, no startup do Runtime ou em chamadas de domínio.

Use SQLite em memória apenas como harness de contrato quando isso não mascarar semântica específica do PostgreSQL. Testes que dependem de locking, isolamento ou erro de concorrência devem ser marcados como integração e só executar quando `AGENTOS_TEST_POSTGRES_DSN` estiver configurado; não crie banco, container ou serviço automaticamente.

### Recuperação, retenção e observabilidade

Cubra no domínio do adapter:

- status explícito de `NOT_COMMITTED`, `COMMITTED` e `UNKNOWN`;
- reconciliação/inspeção de commit sem replay cego;
- outbox atrasada sem perda e sem publicação antecipada;
- tombstone ou retenção somente quando houver contrato público necessário, sem apagar conteúdo fora do escopo;
- erros normalizados, sanitizados e classificáveis para retry;
- logs/métricas baseados em IDs, tipos, versões e códigos, nunca valores ou SQL.

Backup físico, restauração operacional, replicação, particionamento, multi-região e disaster recovery executável devem ser documentados como limitação, não simulados pelo adapter.

## Integração com Kernel e domínios

- `ExecutionControl` continua validando regras de estado; Persistência valida contexto, versão e commit, sem assumir regras de negócio do Runtime.
- `RuntimeService` continua sem importar `TransactionalPersistence`, SQLAlchemy, Alembic, Redis ou banco.
- `EventBus` e `OutboxPublisher` continuam atuando somente depois de `COMMITTED`.
- Agents, Providers e Context continuam dependentes de portas públicas e não conhecem o schema.
- O adapter deve conseguir persistir `Execution` e sua outbox atual sem exigir que Providers, Context ou Events sejam reescritos.
- Não transforme a persistência em Event Sourcing integral; estado versionado + outbox continuam sendo a decisão da ADR 002.

## Fora de escopo explícito

Não implemente nesta sessão:

- Redis, filas, pub/sub, sessões, locks, leases ou coordenação efêmera da RFC 801/ADR 009;
- Workers, Scheduler, DispatchCoordinator ou pools;
- Artifact Storage, Workspaces, Memory, Blackboard ou Configuration como domínios completos;
- API, FastAPI, SSE, autenticação ou frontend;
- execução de Provider, Tool, Browser ou filesystem;
- transação distribuída com Provider, Artifact Storage, filesystem ou Redis;
- schema ORM exposto ao domínio;
- migração automática no Runtime;
- exactly-once, commit+publish distribuído ou recuperação sem reconciliação.

## Testes obrigatórios

Use TDD: cada regressão deve começar com teste RED pelo motivo correto, seguir com implementação mínima GREEN e terminar com suíte do subsistema. Cubra pelo menos:

- contexto completo e rejeição de contexto incompleto;
- ownership entre usuário, Workspace, Agent e Execution;
- classificação acima do ceiling e ausência de vazamento por `NotFound`;
- idempotência da mesma operação e conflito por fingerprint divergente;
- versões esperadas, corrida concorrente e ausência de overwrite silencioso;
- commit de estado + auditoria + outbox na mesma transação;
- rollback sem estado parcial;
- `UNKNOWN` seguido de `inspect_commit` e retry seguro;
- publicação não antecipada da outbox e leitura somente de commits confirmados;
- cursor bounded vinculado ao contexto e aos filtros;
- normalização de deadlock, timeout, conexão perdida e erro de constraint;
- migrations ordenadas e não executadas implicitamente pelo Runtime;
- contrato do adapter em memória e do adapter SQLAlchemy;
- Runtime/Agent/Events sem dependência concreta de persistência;
- ausência de SQL, segredos, credenciais e conteúdo proprietário em erros, `repr`, logs e Events;
- suíte existente completa sem regressões.

## Processo obrigatório da sessão

1. Leia integralmente as RFCs, ADRs, planos e código listados.
2. Faça brainstorming curto e proponha o desenho antes de editar código.
3. Registre `docs/superpowers/specs/2026-08-06-persistence-design.md`.
4. Registre `docs/superpowers/plans/2026-08-06-persistence.md`.
5. Execute o plano em ciclos TDD, com commits pequenos e verificáveis.
6. Adicione apenas SQLAlchemy/Alembic como dependências tecnológicas justificadas pela ADR 012; não adicione Redis ou broker nesta sessão.
7. Execute:

```text
python -m pytest -q
python -m compileall -q src tests
```

8. Se houver testes PostgreSQL opcionais, execute-os somente com `AGENTOS_TEST_POSTGRES_DSN` e registre claramente se foram pulados.
9. Varra as fronteiras para garantir que SQLAlchemy/Alembic aparecem apenas no adapter e nas migrations; Runtime, Agent, Events, Context e Providers não podem importar esses pacotes.
10. Faça auditoria requisito por requisito contra RFCs 050, 060, 101–104, 201, 501, 502, 601 e ADRs 002, 009 e 012.
11. Dispare o subagente revisor obrigatório descrito no início desta instrução
    depois da implementação e antes da verificação final; aguarde sua resposta
    e trate os achados antes de continuar.
12. Só declare conclusão com evidência fresca, limitações de integração
    PostgreSQL claramente registradas e working tree/Git reportados
    honestamente. Rode novamente a suíte completa, `compileall`, os scans de
    fronteira e `git diff --check` depois de integrar qualquer correção do
    revisor.

## Critérios de conclusão

A sessão só está concluída quando:

- a porta de persistência é única, tipada, autorizada e independente de tecnologia;
- o adapter PostgreSQL confirma estado, auditoria e outbox atomicamente;
- conflitos, idempotência, rollback e commit indeterminado são explícitos;
- leituras, scans, paginação e classificação não vazam dados entre escopos;
- migrations são versionadas e operadas explicitamente;
- `InMemoryTransactionalPersistence` continua útil como adapter de teste;
- Runtime, Agent, Events, Context e Providers não importam detalhes de banco;
- nenhum Redis, broker, Worker, Scheduler, Artifact ou domínio fora do escopo foi criado;
- testes unitários passam e testes PostgreSQL opcionais têm resultado registrado;
- especificação, plano, código e limitações estão coerentes com RFC 601 e ADRs 002/009/012.
- um subagente independente revisou o diff final, seus achados foram tratados
  ou registrados como bloqueador objetivo, e a suíte/gates foram repetidos
  após essa revisão;
- a implementação, os testes, a documentação e a revisão foram concluídos na
  mesma sessão, sem deixar trabalho essencial para o próximo agente.
