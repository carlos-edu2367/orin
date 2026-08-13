# AgentOS agentic capability matrix

This matrix is an evidence ledger for the F harness. It deliberately separates
deterministic local evidence from real provider, infrastructure, and browser
evidence. A deterministic fixture is never treated as proof that a production
provider or deployment works.

## State vocabulary

| State | Meaning |
| --- | --- |
| `HARNESS_ONLY` | A repeatable local contract test exists. This is not production evidence. |
| `BLOCKED` | The capability cannot be claimed until the named external evidence is captured. |
| `READY` | Reserved for a capability with fresh, reproducible evidence at the stated boundary. |

## Matrix

| Capability / boundary | Current state | Evidence available | Required to unblock or promote |
| --- | --- | --- | --- |
| Deterministic provider JSON, SSE, 429, 503/retry, and invalid-response cases | `HARNESS_ONLY` | `tests/integration/agentic/test_environment_smoke.py` against `tests/fixtures/agentic/provider_server.py` | Keep fixture tests green; do not infer provider availability. |
| Provider secret stays at the HTTP edge and public outcome is redacted | `HARNESS_ONLY` | `tests/integration/agentic/test_security_boundaries.py` | Real provider request plus captured public logs/DTO/event scan with no credential leakage. |
| API rate-limit envelope | `HARNESS_ONLY` | In-memory security adapter test in `test_security_boundaries.py` | Real deployed security/rate-limit store evidence under the target profile. |
| Runtime budget blocks provider effects before invocation | `HARNESS_ONLY` | `test_recovery.py` runtime boundary test | Real worker execution with persisted usage and budget records. |
| Cursor monotonicity, tamper rejection, and ownership | `HARNESS_ONLY` | API boundary test in `test_security_boundaries.py` | PostgreSQL event-stream round trip with two principals and revocation. |
| Replay preserves event identity and history | `HARNESS_ONLY` | In-memory archive test in `test_recovery.py` | Durable archive/outbox replay run with authorization and audit evidence. |
| Watchdog/recovery behavior | `HARNESS_ONLY` | In-memory chat runtime test in `test_recovery.py` | Publisher + Redis + worker recovery run against PostgreSQL/Redis. |
| `view_file` reads a promoted attachment: native image to a model that sees, transcription for a text-only model | `HARNESS_ONLY` | `tests/integration/agentic/test_attachment_turn.py` — real `AgentToolset`/`ConversationWorkspace`/promotion path against a deterministic HTTP provider double | Real turn against a real vision-capable provider and a real configured visual-reading model, with attachments persisted through PostgreSQL. |
| PostgreSQL + Redis readiness | `BLOCKED` when `AGENTOS_TEST_POSTGRES_DSN`/`AGENTOS_REDIS_URL` are absent | The optional smoke test skips without those variables | Start the project-owned services, migrate the test database, set both variables, and rerun. |
| HTTP → publisher → Redis/ARQ → worker → provider E2E | `BLOCKED` | No real run is claimed by this harness | Capture a run with the project services, a controlled provider endpoint, logs, and persisted state. |
| Real OpenAI, Anthropic, or OpenRouter integration | `BLOCKED` | No real provider credential/endpoint evidence is included | Run with an explicitly authorized credential and sanitize the evidence before recording it. |
| Browser/UI agentic flow | `BLOCKED` | No browser evidence is included in this slice | Capture a browser E2E run with network, auth, recovery, and redaction evidence. |

The current environment did not provide the optional PostgreSQL/Redis variables
when this matrix was authored. The skip is an honest gate, not a passing
production claim.

