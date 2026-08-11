# RFC 603 — Workspaces closeout

**Data:** 2026-08-07  
**Status:** gate RFC 603 concluído; revisão independente e gates finais aprovados.

## Entregue

- Porta pública `agentos.workspaces` com contextos completos, estados normativos, quotas, usage, leases, locks, fencing, receipts, erros e referências opacas.
- `InMemoryWorkspaceRegistry` com ownership antes de root, idempotência, optimistic versioning e tombstones.
- `InMemoryWorkspaceRootAdapter` com root isolada de referência, identity, handles não serializáveis, rejeição de links/raízes inseguras, troca de identity, cleanup bounded e tombstone.
- `WorkspaceManagerService` com create/activate/inspect, lifecycle explícito, leases/renew/release, locks/fencing, quota reservation/accounting, delete recuperável e reconcile por escopo.
- `TransactionalWorkspaceRegistry` integrado somente pela porta `TransactionalPersistence`, com metadata bounded, auditoria mínima e outbox para fatos confirmados.
- Eventos normativos com payload mínimo e sem root física, path, handle, conteúdo ou segredo.

## Decisões

- Separação registry/root/service mantém PostgreSQL, filesystem e detalhes de plataforma fora do domínio.
- Root resolve e revalida identity/state/version antes do lease; corrida ou incerteza falha fechado.
- Reservas e contabilização vivem na autoridade de Workspace; Artifacts continuam sob a quota/lifecycle da RFC 602.
- Delete usa checkpoints e limite de entradas, mantendo `DELETING`/`RECOVERY_REQUIRED` quando a ausência da root esperada não pode ser provada.
- Idempotência de criação e workflow de delete (fence/checkpoint) são restauráveis pelo registry transacional após restart; reservas concorrentes de entries entram no cálculo de quota; renew revalida estado, reconciliação e duração máxima.

## Evidência final

- `python -m pytest -q`: `484 passed, 3 skipped`.
- `python -m pytest -q tests/unit/workspaces tests/integration/workspaces`: `57 passed, 1 skipped`.
- `python -m compileall -q src tests`: passou.
- Scan RFC 603 (`FastAPI`, clientes HTTP, SQLAlchemy/Alembic, Redis, brokers, workers e campos físicos): nenhuma ocorrência em `src/agentos/workspaces`.
- `git diff --check`: passou.
- PostgreSQL opcional: `skipped`, pois `AGENTOS_TEST_POSTGRES_DSN` não está definido.

## Revisão independente

A revisão encontrou e a implementação corrigiu cinco pontos acionáveis: lookup durável de idempotência após restart, fence/checkpoint duráveis no delete, contagem de reservas concorrentes de entries, revalidação de elegibilidade no renew e emissão do fato `WorkspaceQuotaExceeded`. A suíte completa foi executada novamente após essas correções.

## Limitações tecnológicas legítimas

O gate não implementa serviço de filesystem de produção, volume/container específico, storage remoto, Redis, broker, API HTTP, UI, colaboração, billing, VCS, sync, backup, snapshots, clone, migração de storage class, criptografia por Workspace ou transação distribuída. O adapter de referência demonstra a semântica normativa; adapters físicos posteriores devem consumir estas portas sem expor localização.

## Próximo gate

O próximo gate indicado pela documentação é a fronteira especializada de Filesystem/Resource Manager e os adapters operacionais, consumindo `WorkspaceRootResolver`/leases sem reimplementar ownership ou lifecycle.
