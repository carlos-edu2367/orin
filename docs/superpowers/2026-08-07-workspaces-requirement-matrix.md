# Matriz de requisitos — RFC 603 Workspaces

| Requisito normativo | Contrato/implementação | Evidência automatizada | Estado |
|---|---|---|---|
| Contextos completos e bootstrap de criação | `models.py`, `security.py` | `test_contracts.py`, `test_security.py`, `test_manager_lifecycle.py` | Implementado |
| Ownership antes de provisionamento e ID sem reuso | `registry.py`, `service.py` | `test_registry.py`, `test_manager_lifecycle.py`, `test_cleanup_and_reconcile.py` | Implementado |
| Lifecycle exato, versões e transições | `models.py`, `service.py` | `test_manager_lifecycle.py`, `test_events.py` | Implementado |
| Root opaca e adapter substituível | `ports.py`, `root_adapter.py` | `test_root_adapter.py`, `test_contracts.py` | Implementado |
| Canonicalização/containment fail-closed | `root_adapter.py`, `security.py` | `test_root_adapter.py`, `test_security.py`, `test_leases_and_fencing.py` | Implementado no adapter de referência |
| Revalidação contra troca de root | `service.py` | `test_leases_and_fencing.py::test_root_swap_between_resolution_and_final_revalidation_denies_lease` | Implementado |
| Leases limitados e handles efêmeros | `service.py`, `models.py` | `test_leases_and_fencing.py`, `test_contracts.py` | Implementado |
| Locks administrativos e fencing monotônico | `service.py`, `models.py` | `test_leases_and_fencing.py` | Implementado |
| Quotas e reserva antes do efeito | `service.py`, `models.py` | `test_quotas.py` | Implementado |
| Usage CURRENT/STALE/IN_PROGRESS/DIVERGENT | `models.py`, `service.py` | `test_quotas.py`, `test_cleanup_and_reconcile.py` | Implementado |
| Delete recuperável e bounded | `service.py`, `root_adapter.py` | `test_cleanup_and_reconcile.py` | Implementado |
| Reconcile ROOT/USAGE/LEASES/CLEANUP/ALL | `service.py` | `test_cleanup_and_reconcile.py` | Implementado |
| Persistência pela porta RFC 601 | `persistence.py` | `test_persistence_boundary.py` | Implementado |
| Eventos mínimos pós-fato e outbox | `service.py`, `persistence.py` | `test_events.py`, `test_persistence_boundary.py` | Implementado |
| Sem vazamento de root/path/handle/tecnologia | todo `agentos.workspaces` | `test_contracts.py`, `test_events.py`, `test_boundary_scan.py`, scan final | Implementado |
| PostgreSQL opcional | composição RFC 601 | `tests/integration/workspaces/test_postgres_optional.py` | `skipped` sem `AGENTOS_TEST_POSTGRES_DSN` |
| Filesystem de produção, volumes remotos, Redis, HTTP e exatamente-uma-vez | limites RFC/ADR | scan e closeout | Não pertencem a este gate; sem enfraquecer a semântica |

## Evidência de baseline e execução

- Baseline registrado antes da implementação: `427 passed, 2 skipped`.
- A suíte específica de Workspaces alcançou `57 passed, 1 skipped` na verificação final.
- A suíte completa final alcançou `484 passed, 3 skipped`.
- `python -m compileall -q src tests` passou; o scan de dependências proibidas não encontrou ocorrências; `git diff --check` passou.
