# Matriz de requisitos — RFC 403 Filesystem e RFC 402 Resource Manager

Esta é a matriz única dos dois gates. O estado é atualizado somente com evidência executada na sessão.

## RFC 403 — Filesystem

| Requisito | Implementação | Teste/evidência | Estado inicial |
|---|---|---|---|
| Contexto completo, WorkspacePath e entradas sem tecnologia | `src/agentos/filesystem/models.py` | `tests/unit/filesystem/test_contracts.py` | Planejado |
| Operações stat/list/read/create/write/move/copy/remove | `src/agentos/filesystem/ports.py`, `service.py` | `test_in_memory_operations.py` | Planejado |
| Parser contra absoluto/drive/UNC/URL/device/~ /env/ADS/./../separadores/Unicode/case | `security.py` | `test_security.py` | Planejado |
| Root opaca, identity e policy version via Workspace | `service.py`, `local.py` | `test_local_adapter.py`, integração | Planejado |
| Containment físico, links/reparse/mount/hard-link e root swap fail-closed | `local.py` | `test_local_adapter.py`, `test_security_races.py` | Planejado |
| Lease/handle/capability e contexto revalidados em cada operação | `service.py` + `resources.service.py` | integração E2E | Planejado |
| Quota bytes/entries/depth/file size e reserva antes do efeito | `service.py` + Workspace hooks | `test_quotas_and_concurrency.py` | Planejado |
| Overwrite, expected version, atomic write e idempotência | `in_memory.py`, `local.py` | operações/concurrency/local | Planejado |
| Timeout, cancelamento, efeito UNKNOWN e reconciliação | `models.py`, `service.py` | operações/concurrency/restart | Planejado |
| Move/copy somente na mesma root | `service.py` | segurança e E2E | Planejado |
| Stream limitado sem payload volumoso em eventos | `ports.py`, `service.py` | `test_events_and_leakage.py` | Planejado |
| Eventos mínimos pós-fato | `service.py` | `test_events_and_leakage.py` | Planejado |
| Adapter in-memory determinístico | `in_memory.py` | suíte Filesystem | Planejado |
| Adapter local operacional ou rejeição explícita de capability | `local.py`, `workspaces/local_root_adapter.py` | suíte local condicionada | Planejado |
| Persistência RFC 601/outbox RFC 103 sem handle/path | `filesystem/persistence.py` | `test_persistence_outbox.py` | Planejado |

## RFC 402 — Resource Manager

| Requisito | Implementação | Teste/evidência | Estado inicial |
|---|---|---|---|
| Contexto completo e validação de binding | `resources/models.py`, `security.py` | `test_contracts.py`, `test_manager.py` | Planejado |
| Catálogo FILESYSTEM/TERMINAL/BROWSER | `resources/service.py` | `test_manager.py` | Planejado |
| Descriptor, capabilities, isolation, limits, health e adapter ref | `resources/models.py` | `test_contracts.py` | Planejado |
| acquire/renew/authorize/release/revoke/inspect | `resources/service.py` | `test_leases.py`, `test_manager.py` | Planejado |
| Handle opaco, efêmero, não serializável e bound a lease/operação | `resources/models.py` | contracts/integration | Planejado |
| Budget, usage bounded e auditoria | `resources/models.py`, `service.py` | manager/persistence tests | Planejado |
| Isolation key derivada, sem escolha do chamador | `service.py` | `test_manager.py` | Planejado |
| Workspace ownership/root/lease/quota consumidos sem duplicação | `service.py` | E2E | Planejado |
| Lease expiration/revoke/release/fence e stale writer | `service.py` | leases/concurrency/E2E | Planejado |
| Race allocate/confirm/cancel/timeout/late result | `service.py` | `test_resource_concurrency.py` | Planejado |
| Terminal e Browser reference adapters com lifecycle/cancel/cleanup | `resources/adapters.py` | `test_adapters_cleanup.py` | Planejado |
| Cleanup sweep/reconcile, checkpoint, quarantine e UNKNOWN | `resources/service.py` | `test_adapters_cleanup.py`, restart | Planejado |
| Eventos Resource pós-fato | `resources/service.py` | manager/events tests | Planejado |
| Persistência RFC 601/outbox RFC 103 sem handle vivo | `resources/persistence.py` | persistence tests | Planejado |
| Integração Artifact Storage/quota por referências | hooks em `service.py`/persistence | persistence/E2E | Planejado |
| Auditoria sem segredo/conteúdo/tecnologia | `service.py` | leakage/boundary scans | Planejado |

## Gates finais

| Verificação | Evidência esperada | Estado inicial |
|---|---|---|
| `python -m pytest -q` | suíte completa sem falhas | Pendente |
| `python -m compileall -q src tests` | exit code 0 | Pendente |
| `git diff --check` | saída vazia | Pendente |
| scan de dependências proibidas | nenhuma ocorrência proibida nas portas/domínio | Pendente |
| PostgreSQL opcional | passou com DSN ou `skipped` sem DSN | Pendente |
| revisão independente | findings corrigidos e evidência fresca | Pendente |
