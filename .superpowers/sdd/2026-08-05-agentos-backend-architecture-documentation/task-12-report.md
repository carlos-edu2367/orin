# Relatório — Task 12: ADRs de recursos, tenancy e extensibilidade

**Status:** Concluída e verificada em 2026-08-06.

## Arquivos criados

- `docs/adr/004-playwright-browser-workers.md`
- `docs/adr/005-local-workspaces.md`
- `docs/adr/006-single-user-multi-tenant-ready.md`
- `docs/adr/007-server-side-sessions.md`
- `docs/adr/008-artifact-storage-abstraction.md`
- `docs/adr/010-provider-ports-and-model-catalog.md`

## Resumo

Os seis ADRs documentam Playwright em Browser Workers com contextos e sessões isolados; roots locais por Workspace na primeira versão, incluindo o bootstrap autorizado sem `workspace_id` prévio; experiência single-user com invariantes de tenancy desde o início; sessões server-side em Redis, cookie opaco `HttpOnly` e CSRF; `ArtifactStorage` como porta de bytes substituível; e portas uniformes de Provider combinadas a um catálogo de modelos versionado.

Cada decisão registra fronteiras, benefícios, custos operacionais, falhas aceitas, limitações e alternativas. Os documentos preservam que o Runtime não conhece Playwright nem SDK de Provider, Browser Workers não acessam banco, Redis não é fonte de verdade, toda autorização considera usuário e Workspace, e Artifacts e seleção de modelo permanecem substituíveis e auditáveis.

## Verificação

- Os seis documentos estão em `docs/adr/` e estão em Markdown.
- Cada ADR contém: Status, Contexto, Decisão, Consequências, Alternativas consideradas e Relações com RFCs.
- Cada ADR declara limites, custos, falhas previsíveis e o que a decisão não resolve.
- As relações usam somente RFCs existentes, além da relação interna entre ADR 007 e ADR 009.
- Nenhum arquivo introduz código, endpoint, schema executável ou configuração de produção.
