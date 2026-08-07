# RFC 405 Browser — Closeout

**Data:** 2026-08-07
**Status:** fechado
**Próximo gate:** RFC 406 — Capabilities

## Resultado

O pacote `src/agentos/browser` implementa os contratos públicos e as invariantes do RFC 405 com modelos imutáveis, `BrowserJobPort`, `BrowserWorker`, adapter determinístico, boundary Playwright opcional, política de rede, grants, lifecycle, Resource Manager, input/artifact sinks, secrets por referência, journal sanitizado e eventos mínimos.

## Arquivos do gate

- `src/agentos/browser/models.py`, `ports.py`, `security.py`, `service.py`, `worker.py`, `reference.py`, `playwright_adapter.py`, `integration.py`, `persistence.py`, `__init__.py`.
- `tests/unit/browser/` com 14 módulos e 34 testes cobertos pela suíte final.
- `docs/superpowers/specs/2026-08-07-rfc-405-browser-design.md`.
- `docs/superpowers/plans/2026-08-07-rfc-405-browser.md`.
- `docs/superpowers/2026-08-07-rfc-405-browser-requirement-matrix.md`.
- `docs/superpowers/2026-08-07-rfc-405-browser-closeout.md`.
- `docs/superpowers/2026-08-07-rfc-405-browser-next-session-prompt.md`.

## Decisões e alternativas rejeitadas

O domínio permanece sem Playwright, banco, ORM, Redis, Runtime ou API. O Resource Manager continua autoridade de `BROWSER` lease/isolation/fencing; Workspaces/Filesystem continuam autoridade de root/containment; Artifact Storage continua autoridade de staging/quota/referência; Persistence/Outbox continua autoridade durável. Playwright só é importado dinamicamente dentro de `playwright_adapter.py`, que mantém objetos nativos privados ao Worker.

Foram rejeitados Playwright na API/Runtime/worker genérico, banco no Worker, paths físicos para upload/download, payload integral em Event/snapshot e rede liberada ou validada apenas lexicalmente. O adapter de referência é determinístico para provar contratos sem instalar um engine; o adapter Playwright implementa a boundary operacional quando a capability está instalada.

## Matriz de cobertura

A matriz em `2026-08-07-rfc-405-browser-requirement-matrix.md` mapeia contexto, modelos, lifecycle, leases/fencing, worker boundary, network/SSRF/DNS, conteúdo não confiável, grants perigosos, cookies/secrets, DOM/screenshot/upload/download, cancelamento/UNKNOWN, cleanup, Resource Manager, Persistence/Events e regressão para arquivos e testes reais. Todos os itens estão `COVERED` após a execução final.

## Integrações comprovadas

- RFC 402: `BrowserService` solicita `ResourceType.BROWSER`, usa capabilities `BROWSER_SESSION`, preserva lease/fence, revalida estado e libera no close.
- RFC 403/603: input usa referência lógica e nunca aceita path físico; nenhum root físico atravessa a API Browser.
- RFC 602: DOM/screenshot/download usam `BrowserArtifactOutput` staging/commit/abort e refs sem bytes/path; quotas e limites são efetivos.
- RFC 601/103: `BrowserPersistenceJournal` usa `TransactionalPersistence` e `OutboxChange` com `EventEnvelope` sanitizado.
- RFC 101/102: todo contexto exige `execution_id`; Browser não cria Execution nem controla Runtime.
- RFC 401/ADR 004: jobs/grants são a única fronteira; Playwright não aparece no domínio nem no Worker genérico.

## Evidências de segurança

`NetworkPolicy` bloqueia schemes proibidos, IP privado/loopback/link-local/metadata, portas e hosts fora da allowlist; `validate_redirect` aplica limite/origin policy e revalidação DNS. Grants são mínimos, scoped e expirados. Cookies só aceitam `secret-ref`/`secret:` e `ReadCookies` produz metadados redigidos. `BrowserJob.arguments` é imutável. Upload resolve somente referência autorizada; artifact sinks limitam bytes e abortam staging parcial. Interações `evaluate`, JavaScript, clipboard, câmera e geolocation exigem capacidades granulares. Content web permanece dado.

## Revisão independente

A segunda passagem encontrou: falta de materialização pelo sink, upload validado tarde, close sem release, open session sem idempotência, argumentos mutáveis, cookie inline, lease expiry não revalidado e navegação sem policy. Cada finding recebeu teste RED, correção GREEN e foi reexecutado na suíte final. Scan adicional confirmou que `worker.py` importa apenas `models`, `ports`, `security` e bibliotecas padrão; não há import de banco, ORM, Redis, Runtime, API, fila ou state store.

## Comandos e resultados reais

```text
python -m pytest -q
600 passed, 5 skipped in 5.46s

python -m compileall -q src tests
exit 0

git diff --check
exit 0 (somente avisos de normalização LF/CRLF em arquivos preexistentes)

python -m pytest -q tests/integration/workspaces/test_postgres_optional.py
1 skipped (AGENTOS_TEST_POSTGRES_DSN não configurado)

python -m pytest -q tests/unit/browser/test_playwright_boundary.py
1 passed (capability Playwright ausente; teste de boundary não simula sucesso)

rg -n "^(from|import) " src/agentos/browser/worker.py
somente stdlib + agentos.browser.models/ports/security

rg -n "SQLAlchemy|sqlalchemy|Alembic|alembic|Redis|redis|sqlite|psycopg|asyncpg|Runtime|FastAPI|fastapi|requests|httpx|subprocess" \
  src/agentos/browser/worker.py src/agentos/browser/ports.py \
  src/agentos/browser/models.py src/agentos/browser/security.py
nenhuma ocorrência
```

## Commits

Nenhum commit foi criado nesta sessão. O worktree contém mudanças preexistentes do usuário; para evitar misturá-las, o gate permanece pronto para staging seletivo dos arquivos listados acima.

## Limitação legítima

A dependência Playwright e seus browsers não estão instalados neste ambiente. O teste opcional foi executado e a capability ficou `skipped`/indisponível por motivo real; os contratos, policy, isolamento, artifacts, worker e adapter determinístico foram executados sem simulação de sucesso do engine.

## Fechamento

Com as evidências acima, o Gate RFC 405 está 100% implementado, funcional, alinhado às RFCs/ADRs e sem pendências futuras de implementação. O próximo gate documentado é RFC 406 — Capabilities.
