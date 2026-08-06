# ADR 012 — SQLAlchemy e Alembic nos adapters de persistência

**Status:** Aceita  
**Data:** 2026-08-06

## Contexto

PostgreSQL é o system of record, mas o domínio não pode conhecer sessões, mapeamentos ORM, SQL ou migrations. A implementação precisa de uma camada consistente de acesso e de evolução versionada do schema físico sem transformar modelos ORM em contratos públicos.

## Decisão

Adotar **SQLAlchemy 2** no adapter PostgreSQL e **Alembic** para migrations do schema físico. Ambos permanecem atrás de `TransactionalPersistence` da RFC 601. Uma sessão/transação SQLAlchemy implementa uma chamada `transact`; estado e entradas de outbox são confirmados no mesmo commit. Modelos ORM, conexões, queries e objetos Alembic não atravessam a porta. Migrations são revisadas, ordenadas e aplicadas por operação administrativa controlada; não são executadas implicitamente pelo Runtime.

## Consequências

- A implementação recebe mapeamento e transações explícitas compatíveis com PostgreSQL.
- O projeto assume disciplina de migrations forward e procedimentos de rollback/restore testados.
- Alteração de schema não muda contratos de RFC sem revisão própria.
- Falha de migration, sessão ou commit é normalizada pela porta; commit incerto exige inspeção.

## Alternativas consideradas

- **SQL manual em todo o adapter:** possível, mas rejeitado como padrão inicial pelo custo de consistência e manutenção.
- **ORM como modelo de domínio:** rejeitado por vazar persistência e lifecycle de sessão.
- **Criar schema automaticamente em runtime:** rejeitado por remover controle operacional e auditoria.

## Relações com RFCs

- [RFC 601 — Persistência](../architecture/600-platform-data/601-persistence.md) define a única fronteira transacional e a outbox.
- [ADR 002 — PostgreSQL como system of record](002-postgresql-as-system-of-record.md) escolhe a autoridade durável.
- [RFC 103 — Sistema de eventos](../architecture/100-kernel/103-event-system.md) define entrega posterior ao commit.

