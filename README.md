# AgentOS

A local-first agent workspace. You describe what you need; an agent works on it
with real tools — files, a terminal, the web, memory — and can create subagents
for parts of the job. The interface stays a chat; everything the agents did is
visible in it, summarized, and expandable when you want the detail.

Everything runs on your machine. Nothing leaves it except the provider calls you
configure.

## Requirements

- Python 3.13+
- Node.js 20+
- Docker Desktop (PostgreSQL + Redis)
- An API key for at least one provider (OpenRouter, OpenAI, or Anthropic)

## Start it

```powershell
Copy-Item .env.local.example .env.local
Copy-Item frontend/.env.local.example frontend/.env.local
```

Put a real value in `AGENTOS_PROVIDER_ENCRYPTION_KEY` in `.env.local` — it
encrypts provider credentials at rest:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then bring everything up:

```powershell
docker compose up -d --wait
.\scripts\run-local.ps1
```

That script builds the web client, applies migrations, and starts the three
processes the system needs. Open <http://127.0.0.1:8000>.

```powershell
.\scripts\stop-local.ps1
```

`run-local.ps1 -SkipBuild` skips the client build when only Python changed.

### Why three processes

| Process | Responsibility |
| --- | --- |
| `uvicorn agentos.api.asgi:app` | HTTP + SSE. Accepts a turn, persists it, serves the built client. Never calls a provider. |
| `agentos.workers.publisher` | Moves durable pending turns onto the Redis/ARQ queue. |
| `arq agentos.workers.chat.WorkerSettings` | Claims a turn, runs the agent loop, calls the provider, executes tools. |

Keeping the worker out of the HTTP process is what lets a long turn run without
blocking the API, and what lets you restart the UI without killing a run in
flight. A turn nobody claims is failed by the worker watchdog rather than left
queued forever.

## Configure a provider

Open **Settings → Providers** (`Ctrl/Cmd + K` → Providers) and paste a key. It is
encrypted before it is stored and is never returned by any endpoint, log, or
event. Do not put provider keys in `.env.local`.

Refresh the catalog after saving; the composer's model picker only offers models
the server actually authorized.

### Configure web search

| Variable | Description |
| --- | --- |
| `AGENTOS_SEARCH_API_KEY` | Chave da API de busca (Brave Search por padrão). Sem ela a tool `web_search` não é registrada. |
| `AGENTOS_SEARCH_ENDPOINT` | Endpoint alternativo compatível com o formato do Brave Search. Opcional. |

## Using it

Type what you need and press Enter. While the agent works you will see:

- **grouped tool activity** — "3 operações em arquivos", "Terminal · 2 comandos".
  Click any row for the individual calls, their arguments' effects, status and
  timing.
- **agent creation** — a short animation when the main agent spawns a specialist.
- **agent-to-agent messages** — direction, preview, and the full text on click.
- **Stop** — replaces Send while a turn is running and actually cancels the run,
  not just the spinner.
- **Visão geral** — the execution as an orbital map: agents, links, live pulses,
  plus model, provider, duration, tool counts and errors.

`Ctrl/Cmd + K` opens the command palette: conversations, Settings, memory, providers, and new chat. Settings is the single global management area; project memory and workspace controls stay on the relevant project.

## What the agent can do

Tools are defined in `src/agentos/agentic/agent_tools.py`.

| Tool | Notes |
| --- | --- |
| `read_file`, `write_file`, `edit_file`, `list_files` | Confined to `data/workspaces/<conversation_id>`. `edit_file` replaces exactly one matching fragment, preventing ambiguous edits. Path traversal and symlinks out of the sandbox are rejected. |
| `run_command` | Runs in that same directory, 45s timeout. A denylist blocks host-destroying commands (`shutdown`, `mkfs`, `rm -rf /`, …). |
| `fetch_url` | Public http(s) only; private, loopback and link-local addresses are refused. Returns readable text. |
| `remember`, `recall` | Durable facts scoped to the user, recalled into later conversations. |
| `create_agent`, `ask_agent` | Create a specialist and hand it a self-contained task. A subagent cannot create further subagents, and there is a per-turn budget. |

`run_command` is a real shell on your machine. That is the point of a local
agent workspace, and it is also the reason not to expose this service to a
network.

## Architecture

```
browser ── HTTP/SSE ──► FastAPI gateway ──► PostgreSQL (conversations, turns,
   ▲                                        activity, memory, agents)
   │                                             ▲
   └──── activity stream ◄── publisher ─► Redis ─┴─► chat worker ─► provider
                                                          │
                                                          └─► tools
```

- **Conversation / turn** (`src/agentos/conversations/chat.py`) is the durable
  authority for what the user sees. A turn is created, queued, claimed, and
  reaches exactly one terminal state: `completed`, `failed`, or `cancelled`.
- **Turn session** (`src/agentos/agentic/session.py`) composes the system prompt,
  the toolset, memory, and the subagent lifecycle for one turn.
- **Turn runtime** (`src/agentos/agentic/runtime.py`) is the provider/tool loop:
  stream, collect tool calls, execute, feed results back, repeat within limits.
- **Execution records** are a *technical projection* of a turn. A failure to
  write one never changes the answer the user sees.

### Events

Everything observable is one append-only activity log per conversation, read by
the client through `GET /v1/conversations/{id}/events` (SSE) and replayed from
`GET /v1/conversations/{id}`.

Each event carries a type (`tool.started`, `tool.finished`, `agent.created`,
`agent.message_sent`, `agent.message_received`, `assistant.delta`,
`turn.completed`, …), the agent that produced it, its parent agent, a
human-readable summary, a bounded payload, a timestamp and a signed cursor.
Payloads are redacted and size-bounded at construction, so the log is safe to
render directly.

The snapshot deliberately omits `assistant.delta` events — the assistant
message already holds that text — while the live stream includes them so a reply
appears as it is written.

### Memory and context

Context per provider call is: the system prompt (identity, available tools,
workspace, remembered facts, existing subagents), the last 32 messages of the
conversation, and the tool results from the current turn. Subagents get their own
context and cannot see the user's conversation, so their work never contaminates
it. Memory is scoped to the user and retrieved by relevance into the prompt.

## Tests

```powershell
python -m pytest -q tests/unit
```

```powershell
.\scripts\stop-local.ps1     # the suite shares this database; see the note below
$env:AGENTOS_TEST_POSTGRES_DSN='postgresql+psycopg://agentos@127.0.0.1:5433/agentos'
$env:AGENTOS_POSTGRES_URL=$env:AGENTOS_TEST_POSTGRES_DSN
$env:AGENTOS_REDIS_URL='redis://127.0.0.1:6380/0'
python -m pytest -q tests/integration
```

The integration suite runs against the same local database as the app. Stop the
stack first: a running publisher will pick up the turns the tests create and move
them along, which makes assertions about queue state flaky. Point
`AGENTOS_TEST_POSTGRES_DSN` at a separate database if you want to run both at
once.

```powershell
Set-Location frontend
npm test          # component and reducer tests
npm run test:e2e  # Playwright against mocked backends
npm run lint
npm run build
```

Visual checks run against a live stack (start it first):

```powershell
Set-Location frontend
npx playwright test --config=playwright.visual.config.ts
```

Screenshots land in `frontend/tests/visual/output/`. Baseline images are
compared pixel-wise; after a deliberate visual change, re-record them with
`--update-snapshots`.

To exercise the real provider end to end:

```powershell
python scripts/smoke_chat.py "crie um arquivo teste.txt e leia de volta"
```

It prints the messages, every activity row, and the overview aggregation, and
exits non-zero if the turn did not complete.

## Notes on the local profile

`LOCALHOST_TRUST_ENABLED=true` means there is no login: the API authenticates
the loopback TCP peer and refuses anything else. Keep the bind on `127.0.0.1`,
do not put a reverse proxy or port forward in front of it, and do not enable this
profile outside `AGENTOS_ENV=local`/`development` — the service refuses to start
if you try.
