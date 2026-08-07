# Matriz de requisitos — RFC 403 Filesystem e RFC 402 Resource Manager

Esta é a matriz única dos dois gates. O estado foi atualizado com a evidência executada em 2026-08-07.

## RFC 403 — Filesystem

| Requisito | Implementação | Teste/evidência | Estado verificado |
|---|---|---|---|
| Contexto completo, WorkspacePath e entradas sem tecnologia | `src/agentos/filesystem/models.py` | `tests/unit/filesystem/test_contracts.py` | Fechado |
| Operações stat/list/read/create/write/move/copy/remove | `src/agentos/filesystem/ports.py`, `service.py` | `test_in_memory_operations.py` | Fechado |
| Parser contra absoluto/drive/UNC/URL/device/~ /env/ADS/./../separadores/Unicode/case | `security.py` | `test_security.py` | Fechado |
| Root opaca, identity e policy version via Workspace | `service.py`, `local.py` | `test_local_adapter.py`, integração | Fechado |
| Containment físico, links/reparse/mount/hard-link e root swap fail-closed | `local.py` | `test_local_adapter.py`, `test_security_races.py` | Fechado |
| Lease/handle/capability e contexto revalidados em cada operação | `service.py` + `resources.service.py` | integração E2E | Fechado |
| Quota bytes/entries/depth/file size e reserva antes do efeito | `service.py` + Workspace hooks | `test_quotas_and_concurrency.py` | Fechado |
| Overwrite, expected version, atomic write e idempotência | `in_memory.py`, `local.py` | operações/concurrency/local | Fechado |
| Timeout, cancelamento, efeito UNKNOWN e reconciliação | `models.py`, `service.py` | operações/concurrency/restart | Fechado |
| Move/copy somente na mesma root | `service.py` | segurança e E2E | Fechado |
| Stream limitado sem payload volumoso em eventos | `ports.py`, `service.py` | `test_events_and_leakage.py` | Fechado |
| Eventos mínimos pós-fato | `service.py` | `test_events_and_leakage.py` | Fechado |
| Adapter in-memory determinístico | `in_memory.py` | suíte Filesystem | Fechado |
| Adapter local operacional ou rejeição explícita de capability | `local.py`, `workspaces/local_root_adapter.py` | suíte local condicionada | Fechado |
| Persistência RFC 601/outbox RFC 103 sem handle/path | `filesystem/persistence.py` | `test_persistence_outbox.py` | Fechado |

## RFC 402 — Resource Manager

| Requisito | Implementação | Teste/evidência | Estado verificado |
|---|---|---|---|
| Contexto completo e validação de binding | `resources/models.py`, `security.py` | `test_contracts.py`, `test_manager.py` | Fechado |
| Catálogo FILESYSTEM/TERMINAL/BROWSER | `resources/service.py` | `test_manager.py` | Fechado |
| Descriptor, capabilities, isolation, limits, health e adapter ref | `resources/models.py` | `test_contracts.py` | Fechado |
| acquire/renew/authorize/release/revoke/inspect | `resources/service.py` | `test_leases.py`, `test_manager.py` | Fechado |
| Handle opaco, efêmero, não serializável e bound a lease/operação | `resources/models.py` | contracts/integration | Fechado |
| Budget, usage bounded e auditoria | `resources/models.py`, `service.py` | manager/persistence tests | Fechado |
| Isolation key derivada, sem escolha do chamador | `service.py` | `test_manager.py` | Fechado |
| Workspace ownership/root/lease/quota consumidos sem duplicação | `service.py` | E2E | Fechado |
| Lease expiration/revoke/release/fence e stale writer | `service.py` | leases/concurrency/E2E | Fechado |
| Race allocate/confirm/cancel/timeout/late result | `service.py` | `test_resource_concurrency.py` | Fechado |
| Terminal e Browser reference adapters com lifecycle/cancel/cleanup | `resources/adapters.py` | `test_adapters_cleanup.py` | Fechado |
| Cleanup sweep/reconcile, checkpoint, quarantine e UNKNOWN | `resources/service.py` | `test_adapters_cleanup.py`, restart | Fechado |
| Eventos Resource pós-fato | `resources/service.py` | manager/events tests | Fechado |
| Persistência RFC 601/outbox RFC 103 sem handle vivo | `resources/persistence.py` | persistence tests | Fechado |
| Integração Artifact Storage/quota por referências | hooks em `service.py`/persistence | persistence/E2E | Fechado |
| Auditoria sem segredo/conteúdo/tecnologia | `service.py` | leakage/boundary scans | Fechado |

## Gates finais

| Verificação | Evidência executada | Estado verificado |
|---|---|---|
| `python -m pytest -q` | suíte completa sem falhas | Fechado |
| `python -m compileall -q src tests` | exit code 0 | Fechado |
| `git diff --check` | sem erros de whitespace | Fechado |
| scan de dependências proibidas | nenhuma ocorrência proibida nos pacotes | Fechado |
| PostgreSQL opcional | `skipped` porque `AGENTOS_TEST_POSTGRES_DSN` não está configurado | Fechado |
| revisão independente | auditoria read-only do diff/RFCs e findings corrigidos com testes | Fechado |
