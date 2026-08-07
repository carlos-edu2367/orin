# Closeout — RFC 403 Filesystem e RFC 402 Resource Manager

**Data:** 2026-08-07  
**Estado:** fechado no escopo normativo do repositório  
**Dependência consumida:** RFC 603 — Workspaces

## Resultado

Os dois gates foram implementados na mesma sessão e permanecem integrados: o Filesystem só opera com contexto, root opaca, capability/lease autorizado e revalidação; o Resource Manager é a autoridade de catálogo, lease, autorização, fencing, cleanup e reconciliação. O adapter de referência é determinístico em memória e o adapter local operacional mantém a resolução física privada na boundary do adapter.

Não há TODO, `pass`, stub ou bypass tecnológico nos domínios `agentos.filesystem` e `agentos.resources`. As limitações legítimas são as previstas nas RFCs: Terminal e Browser entregues como reference adapters de lifecycle (sem shell/Playwright concretos), PostgreSQL como teste condicional da porta RFC 601, e APIs/serviços remotos fora do domínio.

## Cobertura requisito por requisito

A matriz [2026-08-07-filesystem-resource-manager-requirement-matrix.md](2026-08-07-filesystem-resource-manager-requirement-matrix.md) marca todos os requisitos RFC 403 e RFC 402 como `Fechado` e aponta a implementação e os testes correspondentes. A cobertura inclui:

- Filesystem: contratos, parser seguro, containment/canonicalização, root identity e swap fail-closed, leases/capabilities, quotas reservadas antes do efeito, atomicidade/idempotência, concorrência, cancelamento/UNKNOWN, operações dentro da mesma root, streams limitados, eventos sanitizados, adapter in-memory, adapter local e journal RFC 601/RFC 103.
- Resource Manager: catálogo FILESYSTEM/TERMINAL/BROWSER, descriptors e capabilities, leases, renew/revoke/release/inspect, handles opacos, budget/usage/auditoria, isolation derivada, integração com Workspace, fencing e stale writer, corridas, adapters de referência, cleanup/reconcile/quarantine/UNKNOWN, eventos e journal sem handle vivo.

## Integrações comprovadas

- **RFC 601:** `FilesystemPersistenceJournal` e `ResourcePersistenceJournal` compõem `TransactionalPersistence` usando `RecordChange`, `AuditChange` e `OutboxChange`.
- **RFC 602:** quota e persistência aceitam referências/contadores limitados; nenhum payload de Artifact ou handle vivo é persistido.
- **RFC 603:** `WorkspaceBackedRootResolver` usa o `WorkspaceManagerService` existente para root, ownership, estado, lease, fencing e quota; não replica lifecycle de Workspace.
- **RFC 103:** eventos pós-fato são `EventEnvelope` com sequência, correlação e payload mínimo sanitizado.

## Evidência executada

Na verificação fresca desta sessão:

```text
python -m pytest -q
541 passed, 5 skipped

python -m compileall -q src tests
exit code 0

git diff --check
exit code 0; sem erros de whitespace

scan de dependências proibidas em src/agentos/filesystem e src/agentos/resources
nenhuma ocorrência

pytest -q tests/integration/persistence/test_filesystem_resource_postgres_optional.py -rs
1 skipped — AGENTOS_TEST_POSTGRES_DSN não configurado
```

Os skips restantes da suíte completa são condicionais a capacidades do ambiente de teste; não representam sucesso simulado. O teste PostgreSQL foi executado separadamente e registrado como `skipped` por ausência de DSN.

## Revisão e decisões

Foi feita revisão read-only independente do diff contra RFC 402, RFC 403, RFC 603 e ADRs relacionados, concentrada em containment, lease/handle, cleanup, persistência e bypass entre Resource/Filesystem. Os findings de idempotência, limpeza de expiry e containment físico foram corrigidos e cobertos por testes de regressão; a suíte final foi executada novamente após as correções. A decisão registrada na spec mantém portas Filesystem e Resource separadas e compõe ambas explicitamente por Workspace + handles opacos; alternativas de monólito e de referência apenas sem adapter local foram rejeitadas.

## Commits

- `7e852a1 docs: specify filesystem and resource manager gates`
- `782f8df feat: implement reference filesystem resource`
- `61d07f2 feat: add safe local filesystem adapter`
- `e163252 feat: integrate resource manager with filesystem and workspaces`
- `0c53451 fix: harden resource expiry and filesystem cleanup`
- `2d3e2a9 fix: enforce resource idempotency and filesystem containment`

## Próximo gate

O próximo gate normativo indicado pela arquitetura é o **RFC 404 — Terminal**, seguido do **RFC 405 — Browser**, usando o Resource Manager fechado como autoridade de leases, isolamento, cleanup e lifecycle. Este closeout não deixa implementação obrigatória de RFC 403 ou RFC 402 para uma sessão futura.
