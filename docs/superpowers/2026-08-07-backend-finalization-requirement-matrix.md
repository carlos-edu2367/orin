# Matriz de requisitos — finalização do backend

Data: 2026-08-07

Esta matriz traduz os RFCs normativos de lançamento em entregáveis verificáveis. RFCs 901 e 902 permanecem somente contrato futuro; RFC 903 não é adotado no lançamento.

| RFC | Requisito de lançamento | Entregável/evidência |
| --- | --- | --- |
| 401 | Registro versionado, invocação, stream, cancelamento, leases, outbox e integração das Capabilities | `agentos.tool_runtime`, adapters de Tools, testes unitários e integração ponta a ponta |
| 501–502 | OpenRouter, Anthropic e OpenAI por HTTP real; catálogo continua autoridade de seleção | adapters em `agentos.providers.http`, testes com transporte HTTP injetado e configuração sem segredo em modelo público |
| 601 | PostgreSQL como autoridade, migrations explícitas, outbox, CAS/idempotência e reconciliação | migration Alembic, stores PostgreSQL e teste opt-in com `AGENTOS_TEST_POSTGRES_DSN` |
| 604 | Configuração tipada, validação de startup, referências/redação de segredo e rotação/revogação local | `agentos.configuration`, `.env.example`, testes de validação e documentação |
| 701–702 | FastAPI sem regra de domínio, DTOs, sessão/PAT/CSRF, SSE cursorizado e autorizado, rate limits | `agentos.api`, testes ASGI e OpenAPI |
| 801–802 | Redis efêmero, dispatch idempotente, leases/fencing, ARQ, scheduler durável e recuperação | `agentos.workers`, `agentos.scheduler`, comandos operacionais e testes de adapter |
| 803 | Logs redigidos, métricas bounded, tracing, auditoria e reconstrução autorizada | `agentos.observability`, testes de redaction e consultas de auditoria |
| Operação | PostgreSQL/Redis locais, migrations explícitas, health/readiness e scripts Unix/PowerShell | `docker-compose.yml`, scripts e README |
| Frontend | Contrato integrado sem segredos ou suposições ocultas | `docs/backend/frontend-integration.md` |

## Decisões de escopo

- PostgreSQL preserva estado e outbox; Redis contém apenas referências operacionais com TTL.
- A API adapta HTTP/SSE para serviços públicos; não acessa ORM, outbox ou Redis diretamente.
- Providers são HTTPX assíncronos isolados no adapter e falham fechados quando habilitados sem chave.
- A composição local cria somente adapters reais; adapters in-memory continuam limitados a testes de portas.
- Resultados grandes transitam por referências de Artifact, nunca por Events ou stream público integral.

## Gates finais

1. `python -m pytest -q` e `python -m pytest -q -rs`.
2. `python -m compileall -q src tests` e `git diff --check`.
3. `alembic upgrade head` e `python -m pytest -q tests/integration` com os serviços disponíveis.
4. Scans de pendências, segredos e fronteiras definidos no prompt de finalização.
5. Revisão read-only do diff completo e closeout por RFC.
