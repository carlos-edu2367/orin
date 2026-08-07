# Persistência transacional do AgentOS — Especificação

**Data:** 2026-08-06
**Escopo:** RFC 601, ADRs 002, 009 e 012
**Status:** aprovado para implementação nesta sessão

## Objetivo

Completar a fronteira de persistência transacional do AgentOS sem permitir que Runtime, Execution, Events, Context, Agents ou Providers conheçam SQLAlchemy, Alembic, sessões, conexões ou schema físico. PostgreSQL será o adapter durável; o adapter em memória permanecerá como referência determinística e compatível.

## Decisões de desenho

### Porta pública

`agentos.persistence` será a única porta canônica. Seus quatro métodos públicos são `transact`, `read`, `scan` e `inspect_commit`. `PersistenceOperationContext` exige `user_id`, `workspace_id` opcional, `agent_id`, `execution_id`, `correlation_id`, `purpose` e `actor`. `TransactionRequest` carrega opções, versões esperadas, fingerprint, mudanças, auditoria e outbox; referências são opacas.

`ExecutionTransactionalPersistenceAdapter` fará a migração explícita da porta legada de `execution.ports.TransactionalPersistence`. Nenhum segundo contrato será duplicado ou exportado como se fosse canônico. A leitura de recibos históricos sem `correlation_id` só é permitida por uma opção de compatibilidade explícita, nunca por fallback genérico.

### Unidade transacional

Uma transação valida contexto, ownership, tipo, classificação, versões e relação entre mudança/auditoria/outbox antes de mutar. Estado, auditoria mínima, entrada de outbox, recibo e registro de idempotência são uma unidade de commit. Eventos não são publicados pelo adapter.

`COMMITTED` só aparece após o commit; rejeições não deixam efeitos parciais. Falha de conexão durante `commit()` produz `TransactionIndeterminate`/`UNKNOWN`, e `inspect_commit` é a única reconciliação antes de qualquer retry. Falhas de validação, constraint, timeout, deadlock e serialização são normalizadas em códigos públicos e sem detalhes do driver.

### Idempotência e concorrência

A chave de idempotência é indexada por todos os campos de escopo do contexto e pela própria chave. O fingerprint divergente retorna `IDEMPOTENCY_CONFLICT`; o mesmo fingerprint retorna o recibo e o resultado confirmado. Cada mudança exige a versão esperada coerente; concorrência retorna `TransactionConflicted`, sem last-write-wins. `event_id` é único e outbox aponta para a mudança e a versão resultante.

### Leituras e scans

`read` aplica ownership e ceiling de classificação antes de materializar o registro e retorna `NotFound` tanto para ausência quanto para falta de autorização. `scan` impõe tipo, filtros escalares bounded, ceiling e página máxima. Cursor é assinado/opaco e vinculado a contexto, filtros, classificação, consistência, limite e revisão do store; uma mudança invalida o cursor. `inspect_commit` retorna referências/recibo mesmo quando o snapshot excede o ceiling e não materializa esse conteúdo. O adapter PostgreSQL rejeita consistência eventual sem uma réplica explícita e configura leitura forte com `REPEATABLE READ`.

### Adapter PostgreSQL e migrations

`persistence.postgres` é o único pacote tecnológico. Ele usa SQLAlchemy 2 para engine/sessão/transação e Alembic para migrations versionadas. A URL e opções entram por composição. `upgrade()` é uma operação administrativa explícita; nenhum import, construção de adapter, startup do Runtime ou chamada de domínio executa migration.

O schema mínimo contém registros versionados, ownership, auditoria, outbox, idempotência e relógio de revisão, com constraints/índices para unicidade, versão, classificação e FK composta de ownership entre registro, auditoria e outbox. O bridge `PostgresConfirmedOutboxSource` lê somente entradas confirmadas, bounded e filtradas por contexto. SQLite é somente harness de contrato; locking/isolation PostgreSQL real permanece teste opcional condicionado a `AGENTOS_TEST_POSTGRES_DSN`.

### Segurança e observabilidade

Payloads públicos são congelados, bounded e rejeitam campos sensíveis. `repr`, exceções normalizadas, logs e eventos não incluem SQL, DSN, credenciais, segredos ou payload proprietário. O adapter expõe somente IDs, tipos, versões e códigos. Retenção física, backup, restore, replicação, multi-região e exatamente-uma-vez ficam documentados como limitações, não simulados.

## Fluxo de dados

```text
Domínio/Execution
    -> porta canônica + contexto completo
    -> validação/autorização/versão/fingerprint
    -> estado + auditoria + outbox + idempotência
    -> COMMITTED ou resultado explícito de falha
    -> OutboxPublisher/EventBus somente após COMMITTED
```

## Estratégia de testes

Cada correção começa com teste RED verificando a falha específica, recebe implementação mínima GREEN e termina com suíte focada. A matriz em `docs/superpowers/2026-08-06-persistence-requirement-matrix.md` liga requisitos a arquivos e evidências. O fechamento exige suíte completa, `compileall`, scan de dependências, `git diff --check` e revisão independente somente leitura contra RFC 601/ADRs.

## Limitações explícitas

- O adapter PostgreSQL é coberto em SQLite e por integração PostgreSQL somente quando o DSN é fornecido.
- Não serão criados Redis, broker, workers, scheduler, API, storage de artifacts, ou transação distribuída.
- `COMMITTED` + publicação externa não é exactly-once; outbox atrasada/repetida é reconciliável por `event_id`.
- Backup, restauração, retenção física, replicação, particionamento e disaster recovery executável permanecem operação futura.
