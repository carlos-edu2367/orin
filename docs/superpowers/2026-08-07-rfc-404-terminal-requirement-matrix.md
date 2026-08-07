# RFC 404 Terminal — Requirement Matrix

**Data da evidência:** 2026-08-07  
**Escopo verificado:** `src/agentos/terminal`, testes unitários e integração do gate.

| Requisito RFC 404 | Implementação real | Evidência executada | Estado |
|---|---|---|---|
| Contexto completo e binding | `models.py:TerminalOperationContext`; `service.py:_same_context` | `test_contracts.py`, `test_service_security.py` | concluído |
| Modelos imutáveis e estados | `models.py` | `test_contracts.py` | concluído |
| Sessão persistente com TTL/lease | `service.py`, `persistence.py` | `test_service_lifecycle.py`, `test_persistence_and_recovery.py` | concluído |
| Create idempotente | `TerminalService.create` | `test_service_lifecycle.py` | concluído |
| Foreground único e comando idempotente | `TerminalService.execute`, `ReferenceTerminalAdapter.execute` | `test_service_lifecycle.py`, `test_stream_input_and_output.py` | concluído |
| Input sequenciado e limitado | `TerminalService.write_input`, `ReferenceTerminalAdapter.write_input` | `test_stream_input_and_output.py` | concluído |
| Buffer limitado e truncation | `TerminalBuffer`, adapter `stream` | `test_stream_input_and_output.py`, `test_artifact_overflow.py` | concluído |
| Stream com chunks/bytes/backpressure | `TerminalAdapter.stream`, `StreamResult` | `test_reference_adapter.py`, `test_stream_input_and_output.py` | concluído |
| Cwd como `WorkspacePath` | `models.py`, `service.py`, `local.py` | `test_contracts.py`, `test_local_adapter.py`, E2E | concluído |
| Workspace ownership/root state | `TerminalService._validate_workspace` | `test_terminal_end_to_end.py` | concluído |
| Resource Manager capability/lease/fencing | `TerminalService._authorize` | `test_service_lifecycle.py`, E2E | concluído |
| Ambiente allowlisted e secrets por referência | `LocalTerminalAdapter._environment`; modelos repr redigidos | `test_local_adapter.py`, `test_service_security.py` | concluído |
| Processo e árvore pertencentes à sessão | supervisor de referência e process group local | `test_reference_adapter.py`, `test_local_adapter.py` | concluído |
| Cancelamento escalonado e late result | supervisor/adapter `cancel` | `test_reference_adapter.py`, `test_stream_input_and_output.py` | concluído |
| Timeout efetivo e sem reexecução | `TerminalService.reconcile` + adapter cancel | `test_service_lifecycle.py::test_reconcile_enforces_command_timeout_without_reexecution` | concluído |
| Close com cleanup/release e UNKNOWN | `TerminalService.close` | teste de cleanup unknown | concluído |
| Reconcile/restart | `TerminalService.restore/reconcile`, journal | `test_persistence_and_recovery.py` | concluído |
| Events mínimos e sequenciados | `service.py:_event`, journal outbox | lifecycle/persistence tests | concluído |
| Persistência sem command/output/secret/path físico | `TerminalPersistenceJournal._data` | `test_persistence_and_recovery.py` | concluído |
| Outbox na fronteira transacional | `TerminalPersistenceJournal.record` | `test_persistence_and_recovery.py` | concluído |
| Overflow por Artifact reference ou truncation | writer de output por referência opaca | `test_artifact_overflow.py` | concluído |
| Adapter de referência sem processo real | `reference.py` | `test_reference_adapter.py` | concluído |
| Adapter local isolado | `local.py`, `shell=False` | `test_local_adapter.py`, `test_boundary_scan.py` | concluído |
| Ausência de bypass tecnológico no domínio | package boundary scan | `test_boundary_scan.py` e scan final | concluído |

## Dependências normativas

| Dependência | Integração comprovada |
|---|---|
| RFC 402 Resource Manager | lease, capability, inspect, release e cleanup pela porta existente; lifecycle/E2E |
| RFC 403 Filesystem | `WorkspacePath` é o único cwd público; resolver privado no adapter; contract/local/E2E |
| RFC 603 Workspaces | estado ACTIVE/root descriptor/ownership revalidados via `WorkspaceManagerService`; E2E |
| RFC 601 Persistence | journal usa `TransactionalPersistence.transact/read/inspect_commit`; persistence tests |
| RFC 602 Artifact Storage | overflow usa writer de referência opaca, sem path/bytes em snapshot/event; artifact tests |
| RFC 103 Events | `EventEnvelope`, sequência por Execution e payload mínimo; lifecycle/persistence tests |
| RFC 101/102 Runtime/Execution | contexto exige execution/correlation e Terminal não cria Execution; contract/E2E |
| RFC 401 Tool Runtime | Terminal expõe somente `TerminalPort` e não invoca Tools/Memory/Context/Registry; scan |

Os resultados da regressão completa e dos comandos obrigatórios estão no closeout.
