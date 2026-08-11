# AgentOS agentic E2E runbook

This runbook is for the F harness and its operational gates. It distinguishes
local deterministic contract evidence from real infrastructure/provider/browser
evidence. Never report a fixture run as a production provider run.

## 1. Local prerequisites

- Python 3.13+ with the project environment available.
- Docker Desktop for the optional PostgreSQL/Redis gate.
- No provider credential is required for the deterministic fixture.
- The fixture binds to an ephemeral `127.0.0.1` port and shuts down at test end.

Run the deterministic slice:

```powershell
python -m pytest -q `
  tests/integration/agentic/test_environment_smoke.py `
  tests/integration/agentic/test_security_boundaries.py `
  tests/integration/agentic/test_recovery.py
```

Expected without local infrastructure: the deterministic tests pass and the
PostgreSQL/Redis readiness test is skipped with an explicit reason.

The whole directory may include work from another roadmap slice. Runbook
evidence for F must use the three files above; do not hide or modify unrelated
collection failures.

## 2. Deterministic provider cases

`tests/fixtures/agentic/provider_server.py` provides only local test cases:

| Case | Expected contract |
| --- | --- |
| `success` | OpenAI-compatible JSON normalizes to a successful outcome. |
| `stream` | SSE deltas are ordered and end in an explicit terminal event. |
| `rate_limited` | HTTP 429 becomes a public rate-limited failure. |
| `retry_then_success` | First HTTP 503 is safely retryable; the next call succeeds. |
| `invalid_response` | Malformed JSON becomes an invalid-response failure. |

The server records the request at the test boundary so the test can prove that
the credential was sent to the provider edge. Public outcome/snapshot assertions
must continue to exclude that credential.

## 3. Optional PostgreSQL/Redis smoke gate

Start only the project-owned local dependencies. Their host ports remain
loopback-bound by design:

```powershell
docker compose up -d --wait postgres redis
python -m alembic upgrade head
$env:AGENTOS_TEST_POSTGRES_DSN = 'postgresql+psycopg://agentos@127.0.0.1:5433/agentos'
$env:AGENTOS_REDIS_URL = 'redis://127.0.0.1:6380/0'
python -m pytest -q tests/integration/agentic/test_environment_smoke.py
```

If either variable is absent, the dependency test is skipped. If both are
present but the services cannot be reached, the test fails: that is an
operational failure, not a reason to convert the test into a skip.

Do not point this gate at an unrelated application database or Redis instance.
Do not put a provider key in `.env.local`, test output, logs, or this runbook.

## 4. Security and redaction checks

The deterministic security tests cover rate limiting, cursor tampering,
cross-principal stream access, and provider-secret exclusion from public state.
For a manual review, inspect only sanitized output and use a clearly fake marker
such as `sk-agentic-redaction-secret`:

```powershell
python -m pytest -q tests/integration/agentic/test_security_boundaries.py
rg -n -i "api[_-]?key|authorization|bearer|password|secret|credential" `
  docs/agentic tests/fixtures/agentic tests/integration/agentic
```

The scan is a review aid, not a claim that every matching word is a leak. The
fixture intentionally contains header names and fake markers; the assertions
that matter are the absence of the marker from public outcome/snapshot state.

## 5. Recovery, budget, cursor, and replay expectations

Run:

```powershell
python -m pytest -q tests/integration/agentic/test_recovery.py
```

The harness expects a transient provider failure to be classified as safely
retryable, a budget to prevent the provider effect before invocation, an
unacquired turn to become retryable, a cursor to advance without duplicate
delivery, and replay to preserve event identity while retaining history.

These are contract-level expectations. They become production evidence only
after the PostgreSQL/Redis publisher-worker path and the real provider boundary
have been exercised and recorded.

## 6. Evidence record

For every promoted capability record:

1. exact command and commit/worktree state;
2. whether PostgreSQL, Redis, a real provider, or a browser was involved;
3. sanitized logs/DTOs/events and the redaction scan result;
4. retry, budget, cursor, replay, and recovery observations;
5. failures and external blockers, including absent services or credentials.

Until those fields exist, keep the capability `BLOCKED` or `HARNESS_ONLY` in
`TOOL_CAPABILITY_MATRIX.md`.
