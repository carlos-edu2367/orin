# Closeout — backend finalization

Data: 2026-08-07

## Implementado

| Área | Evidência |
| --- | --- |
| RFC 401 | `src/agentos/tool_runtime/`, registro imutável/versionado, execução atômica, stream/cancelamento, leases, outbox e bridge de Capability |
| RFCs 501–502 | `src/agentos/providers/http.py` contém adapters HTTP reais de OpenAI, Anthropic e OpenRouter; o catálogo continua selecionando o Provider/modelo |
| RFC 601 | migration `0003_workers_scheduler.py`, stores PostgreSQL, outbox/CAS/idempotência e testes reais de PostgreSQL |
| RFC 604 | `src/agentos/configuration/`, `src/agentos/bootstrap/production.py` e `.env.example` com validação fail-closed de Provider |
| RFCs 701–702 | `src/agentos/api/`, autenticação, CSRF, PAT, rate limit, DTOs, SSE cursorizado e revogação |
| RFCs 801–802 | `src/agentos/workers/`, `src/agentos/scheduler/`, Redis/ARQ opaco, fencing e cycle persistente de ocorrência |
| RFC 803 | `src/agentos/observability/` para logs redigidos, métricas bounded e reconstrução baseada em Events |
| Operação | `docker-compose.yml`, `alembic.ini`, `.env.example`, `README.md` e guia de frontend |

## Evidência executada

- `python -m pytest -q` com PostgreSQL/Redis AgentOS reais: **667 passed, 1 skipped**.
- `python -m pytest -q tests/integration` com `AGENTOS_TEST_POSTGRES_DSN=postgresql+psycopg://agentos@localhost:5433/agentos` e Redis em `localhost:6380`: **16 passed**.
- `python -m compileall -q src tests`: sucesso.
- `python -m alembic upgrade head`: sucesso. O comando curto `alembic` não estava no `PATH` desta sessão; o módulo Python equivalente executou a mesma migration.
- `git diff --check`: sucesso na rodada de gates.

## Limitações reais

- Um teste de symlink de filesystem continua skipado porque a conta Windows desta sessão não possui o privilégio de criar symlinks. Isso não é tratado como sucesso de cobertura desse caso.
- Não foi feita chamada externa autenticada aos Providers: nenhuma API key foi fornecida. Os adapters foram exercitados com `httpx.MockTransport` somente em teste de contrato; a composição usa HTTP real e falha fechada sem chave/modelo habilitado.
- Docker Compose usou portas host `5433` e `6380` porque `5432` e `6379` estavam ocupadas por outro projeto local. Os serviços AgentOS ficaram saudáveis nessas portas.

## Decisões registradas

Provider HTTP permanece confinado a `agentos.providers.http`; Kernel/Runtime não importam HTTPX. Redis transporta somente referências operacionais. PostgreSQL continua a autoridade para estado, outbox e replays. O ASGI de produção não instala adapters in-memory: sem composição autorizada, a superfície falha fechada.
