# RFC 403/402 Filesystem e Resource Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar RFC 403 e RFC 402 na mesma execução, com Filesystem seguro, Resource Manager completo, adapters de referência/local, integração com Workspaces, eventos e persistência.

**Architecture:** `agentos.filesystem` mantém contratos e adapters de path/bytes; `agentos.resources` mantém catálogo, leases, authorization handles, adapters e cleanup. A composição entre ambos usa `WorkspaceManager` para root/lease/quota e um validador de handle emitido pelo Resource Manager. Estado durável opcional atravessa somente `TransactionalPersistence` e outbox RFC 103.

**Tech Stack:** Python 3.13+, `dataclasses(frozen=True, slots=True)`, `Protocol`, `RLock`, `pathlib`/`os` apenas no adapter local, `datetime`, `hashlib`, `pytest`, módulos existentes de `agentos.workspaces`, `agentos.persistence`, `agentos.artifact_storage` e `agentos.events`. Nenhuma dependência nova.

## Global Constraints

- A porta pública nunca expõe path físico, handle nativo, PID, processo, cookie, sessão, segredo ou conteúdo volumoso.
- Filesystem só aceita `WorkspacePath` relativo, normalizado, não ambíguo e bounded; containment é revalidado imediatamente antes do efeito.
- Workspace é a autoridade de ownership, root, lifecycle, fencing e quota; Filesystem/Resource Manager não duplicam esse lifecycle.
- Nenhum Resource é usado sem lease válido e autorização por operação; handles são efêmeros, opacos e não serializáveis.
- Leases, reservas, uso, cleanup e eventos são idempotentes, correlacionáveis e fail-closed.
- Eventos e outbox só registram fatos confirmados; `UNKNOWN` exige reconciliação e nunca vira sucesso.
- Adapter local rejeita quando a plataforma não oferece prova de no-follow/identity/containment suficiente.
- Terminal/Browser são adapters de referência completos no lifecycle, sem shell/browser real fora das RFCs especializadas.

---

### Task 1: Contratos públicos e segurança Filesystem

**Files:**
- Create: `src/agentos/filesystem/__init__.py`
- Create: `src/agentos/filesystem/models.py`
- Create: `src/agentos/filesystem/ports.py`
- Create: `src/agentos/filesystem/security.py`
- Test: `tests/unit/filesystem/test_contracts.py`
- Test: `tests/unit/filesystem/test_security.py`

**Interfaces:**
- Produces `FilesystemOperationContext`, `WorkspacePath`, entries, limits, requests, typed outcomes, opaque root/handle refs and `FilesystemPort`/resolver protocols.
- Consumes only public `DataClassification`, Workspace context/root models and Resource handle models.

- [ ] Escrever testes RED para bounded contexts, parser de todos os caminhos proibidos, `repr` sem tecnologia e handle não serializável.
- [ ] Rodar `python -m pytest -q tests/unit/filesystem/test_contracts.py tests/unit/filesystem/test_security.py`; confirmar falha por módulos ausentes.
- [ ] Implementar dataclasses congeladas, enums de erro/outcome, parser Unicode/case policy e Protocols públicos.
- [ ] Rodar os testes focados GREEN e refatorar sem ampliar comportamento.
- [ ] Commitar somente os arquivos da Task 1.

### Task 2: Adapter Filesystem in-memory e serviço de operações

**Files:**
- Create: `src/agentos/filesystem/in_memory.py`
- Create: `src/agentos/filesystem/service.py`
- Test: `tests/unit/filesystem/test_in_memory_operations.py`
- Test: `tests/unit/filesystem/test_quotas_and_concurrency.py`
- Test: `tests/unit/filesystem/test_events_and_leakage.py`

**Interfaces:**
- Consumes os contratos da Task 1, `WorkspaceManager`/hooks de quota e `ResourceHandleValidator`.
- Produces `InMemoryFilesystemAdapter`, `FilesystemService`, `InMemoryFilesystemEventSink` e operações stat/list/read/create/write/move/copy/remove.

- [ ] Escrever RED para fluxo feliz, ownership/lease/capability, version conflict, overwrite, atomicidade, quota, idempotência, timeout/cancelamento, symlink lógico, concorrência e eventos mínimos.
- [ ] Rodar os testes focados e verificar falha esperada.
- [ ] Implementar store lógico copy-on-write sob `RLock`, reservations before effect, bounded sinks e reconciliação de efeitos indeterminados.
- [ ] Rodar os testes focados GREEN, depois `tests/unit/workspaces` e `tests/unit/artifact_storage`.
- [ ] Commitar a fatia Filesystem de referência.

### Task 3: Adapter local e containment operacional

**Files:**
- Create: `src/agentos/filesystem/local.py`
- Create: `src/agentos/workspaces/local_root_adapter.py`
- Test: `tests/unit/filesystem/test_local_adapter.py`
- Test: `tests/unit/filesystem/test_local_boundary_scan.py`

**Interfaces:**
- Consumes root identity/ownership do Workspace e os contratos da Task 1.
- Produces adapter local que só usa roots provisionadas internamente, com no-follow/identity checks, staging atômico no mesmo diretório e capability rejection fail-closed.

- [ ] Escrever RED para provisionamento privado, caminho fora da root, traversal, symlink/junction/reparse, root swap, hard-link ambiguity, create/write/move/copy/remove e atomic write.
- [ ] Rodar `pytest tests/unit/filesystem/test_local_adapter.py -q` e confirmar RED.
- [ ] Implementar boundary física privada, validação componente a componente e revalidação pré-efeito; usar `os.O_NOFOLLOW`/atributos da plataforma quando disponíveis e rejeitar capability ausente.
- [ ] Rodar testes locais em Windows e, quando suportado, casos condicionais marcados por capability explícita.
- [ ] Commitar adapter local e scan de fronteira.

### Task 4: Catálogo, contratos e leases do Resource Manager

**Files:**
- Create: `src/agentos/resources/__init__.py`
- Create: `src/agentos/resources/models.py`
- Create: `src/agentos/resources/ports.py`
- Create: `src/agentos/resources/security.py`
- Create: `tests/unit/resources/test_contracts.py`
- Create: `tests/unit/resources/test_leases.py`

**Interfaces:**
- Produces `ResourceType`, descriptors, requests/results, `ResourceLease`, `AuthorizedResourceHandle`, usage/audit models, `ResourceManager`/`ResourceAdapter`/`CleanupSupervisor` protocols.
- Consumes `WorkspaceOperationContext`, `TransactionalPersistence`/`EventEnvelope` public types only.

- [ ] Escrever RED para catálogo typed, capabilities/isolation/limits/health, context validation, derived isolation, bounded budget, lease states e handle serialization.
- [ ] Rodar testes RED.
- [ ] Implementar contratos opacos e policy helpers sem imports concretos.
- [ ] Rodar testes GREEN e boundary scan do pacote.
- [ ] Commitar contratos Resource.

### Task 5: Resource Manager in-memory, adapters e cleanup

**Files:**
- Create: `src/agentos/resources/in_memory.py`
- Create: `src/agentos/resources/adapters.py`
- Create: `src/agentos/resources/service.py`
- Test: `tests/unit/resources/test_manager.py`
- Test: `tests/unit/resources/test_adapters_cleanup.py`
- Test: `tests/unit/resources/test_resource_concurrency.py`

**Interfaces:**
- Consumes Workspace manager, adapters e contratos da Task 4.
- Produces acquire/renew/authorize/release/revoke/inspect, `CleanupSupervisor.sweep/reconcile`, reference Filesystem/Terminal/Browser adapters and health/quarantine state.

- [ ] Escrever RED para acquire/reject/unavailable, ownership/capability/budget/health, renew bounds, release/revoke/expiry, stale fence, allocation race, cleanup failure/quarantine e terminal/browser lifecycle.
- [ ] Rodar os testes focados RED.
- [ ] Implementar state machine sob lock, adapter compensation before grant confirmation, authorization per operation, usage bounded e cleanup checkpoints.
- [ ] Rodar `pytest tests/unit/resources -q` GREEN e `python -m compileall -q src/agentos/resources`.
- [ ] Commitar Resource Manager e adapters.

### Task 6: Integração real Resource ↔ Workspace ↔ Filesystem

**Files:**
- Modify: `src/agentos/filesystem/service.py`
- Modify: `src/agentos/resources/service.py`
- Modify: `src/agentos/filesystem/ports.py`
- Test: `tests/integration/filesystem_resource/test_end_to_end.py`
- Test: `tests/integration/filesystem_resource/test_security_races.py`
- Test: `tests/integration/filesystem_resource/test_restart_reconcile.py`

**Interfaces:**
- Consumes `WorkspaceManagerService`, `FilesystemService` e `ResourceManagerService`.
- Produces fluxo obrigatório: acquire FILESYSTEM → authorize → Filesystem operation; release/revoke bloqueia operações e aciona cleanup.

- [ ] Escrever RED para handle em outro lease/workspace/agent/execution/purpose/adapter, root identity change, Workspace suspend, quota/budget/file limits, release/revoke immediate block e restart idempotente.
- [ ] Rodar RED com a integração isolada.
- [ ] Implementar binding bidirecional sem bypass: Resource chama o adapter correto, Filesystem exige handle validado pelo Resource Manager e Workspace quota é reservada/contabilizada.
- [ ] Rodar testes de integração GREEN e a suíte completa até esse ponto.
- [ ] Commitar integração.

### Task 7: Persistência RFC 601, outbox RFC 103, Artifact quota e auditoria

**Files:**
- Create: `src/agentos/filesystem/persistence.py`
- Create: `src/agentos/resources/persistence.py`
- Create: `tests/unit/filesystem/test_persistence_outbox.py`
- Create: `tests/unit/resources/test_persistence_outbox.py`
- Test: `tests/integration/persistence/test_filesystem_resource_postgres_optional.py`

**Interfaces:**
- Consumes `TransactionalPersistence`, `ArtifactManager`/quota hooks and `EventEnvelope` only through public ports.
- Produces bounded journal adapters for lease/usage/operation checkpoints with commit/outbox correlation and optional Postgres test.

- [ ] Escrever RED para round-trip metadata, optimistic conflict, idempotency, outbox after commit, indeterminate transaction inspection, no live handle/path and Artifact quota aggregation.
- [ ] Rodar RED.
- [ ] Implementar record serializers bounded e `TransactionRequest` com `RecordChange`, `AuditChange` e `OutboxChange`; nunca persistir bytes ou tecnologia.
- [ ] Rodar unit tests e teste Postgres condicionado por DSN.
- [ ] Commitar persistência/outbox.

### Task 8: Matriz, revisão independente e closeout final

**Files:**
- Create: `docs/superpowers/2026-08-07-filesystem-resource-manager-requirement-matrix.md`
- Create: `docs/superpowers/2026-08-07-filesystem-resource-manager-closeout.md`
- Modify: `docs/superpowers/2026-08-07-next-two-gates-filesystem-resource-manager-prompt.md`

- [ ] Atualizar a matriz com cada requisito RFC 403/402, arquivos e testes reais.
- [ ] Fazer revisão independente do diff focada em containment, leases, handles, cleanup, persistence e bypass Resource/Filesystem; corrigir findings com RED/GREEN.
- [ ] Rodar `python -m pytest -q`, `python -m compileall -q src tests`, `git diff --check`, `git status --short --branch` e scans finais dos dois pacotes.
- [ ] Rodar teste PostgreSQL opcional; sem `AGENTOS_TEST_POSTGRES_DSN`, registrar `skipped` sem simulação.
- [ ] Atualizar closeout e prompt somente com evidência fresca e declarar o próximo gate documentado.

## Self-review

Tasks 1–3 cobrem todos os contratos, contenção, quotas, atomicidade e adapters de RFC 403. Tasks 4–5 cobrem catálogo, leasing, autorização, adapters e cleanup de RFC 402. Task 6 prova integração e ausência de bypass; Task 7 cobre RFC 601/103/602; Task 8 fecha a matriz, revisão e evidências. Não há etapa que deixe requisito normativo como backlog.
