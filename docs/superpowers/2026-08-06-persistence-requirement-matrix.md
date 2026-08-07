# Matriz de requisitos — Persistência RFC 601

| Requisito RFC/ADR | Contrato/arquivo | Teste existente | Lacuna a auditar | Correção/evidência |
|---|---|---|---|---|
| Contexto completo e autorização | `persistence.models.PersistenceOperationContext`, `security.py` | `test_contracts.py`, `test_in_memory_authorization.py` | Revalidar todos os campos também em retry/compatibilidade | TDD da Task 2; suíte focada e completa |
| Estado + auditoria + outbox atômicos | `persistence.in_memory`, `postgres.adapter`, schema | `test_in_memory_transactions.py`, `test_postgres_adapter.py` | Provar ausência de parcial em falha de validação/commit | TDD da Task 2/4; inspeção de tabelas |
| Idempotência e fingerprint | `TransactionRequest`, constraints de idempotência | testes de transação e adapter | Escopo completo e replay autorizado em corrida | TDD da Task 2/4; resultado `TransactionCommitted(already_applied=True)` |
| Concorrência otimista | `ExpectedVersion`, `TransactionConflicted` | testes in-memory/PostgreSQL opcional | Nenhum overwrite silencioso | TDD da Task 2/4; conflito explícito |
| `UNKNOWN` e inspeção | `TransactionIndeterminate`, `InspectCommit` | testes de indeterminate/compatibilidade | Repetição segura somente após inspeção | TDD da Task 2/4; receipt `COMMITTED/NOT_COMMITTED/UNKNOWN` |
| Ownership/classificação/NotFound | `AuthorizedRead`, `AuthorizedScan`, `security.py` | `test_in_memory_authorization.py`, adapter | Evitar vazamento por existência/ceiling | TDD da Task 2/4; `NotFound()` sanitizado |
| Scans bounded e cursor | `PageRequest`, `_cursor_state`, `_encode_cursor` | autorização + adapter scan tests | Cursor deve invalidar em revisão/filtro/contexto | TDD da Task 2/4; assinatura/escopo |
| Outbox sem publicação antecipada | `OutboxChange`, `InMemoryOutboxPublisher`, schema | `test_outbox_publisher.py`, persistence tests | Persistência não chama EventBus | boundary scan + revisão independente |
| Erros sanitizados | `PersistenceErrorCode`, `normalize_database_error` | `test_security_regressions.py`, adapter | Nenhum SQL/DSN/segredo em repr/erro | TDD da Task 2/4; códigos públicos |
| SQLAlchemy/Alembic isolados | `persistence/postgres`, migrations | `test_boundary_scan.py` | Nenhuma tecnologia em domínios/porta pública | scan final e teste de fronteira |
| Migrations explícitas | `postgres.migrate.upgrade` | `test_migrations.py` | Sem criação automática no Runtime/import | teste de engine sem tabelas antes de `upgrade` |
| Compatibilidade Execution | `execution_compat.py` | `test_execution_persistence_compat.py` | Não duplicar silenciosamente a porta | adapter explícito + suíte Execution |
| ADR 009 | exclusão de Redis desta sessão | boundary tests | Não introduzir coordenação efêmera | scan final e limitação documentada |
| Limitações operacionais | spec/plano | revisão final | Não simular backup/DR/exactly-once | documentação e resposta final |

## Evidência inicial

- Baseline antes de alterações: `372 passed, 1 skipped`.
- Suíte de persistência antes da implementação desta sessão: `44 passed, 1 skipped`.
- A matriz será atualizada com os resultados finais somente após repetir os gates depois da revisão independente.

