# Orin (AgentOS)

A local-first agent workspace. You describe what you need; Orin works on it with
real tools — files, a terminal, the web, memory — and can create subagents for
parts of the job. The interface stays a chat; everything the agents did is
visible in it, summarized, and expandable when you want the detail.

Everything runs on your machine. Nothing leaves it except the provider calls you
configure.

## License

This project is licensed under the [MIT License](LICENSE).

## Status

This repository is intentionally being published while the project is still in
active development. It is a working foundation, not a production release or a
security-complete product yet.

The current version includes the agent/tool loop, durable conversations and
turns, context continuity, memory, subagent delegation, provider catalog and
credential encryption, web access, browser policy controls, and the execution
overview in the frontend.

Known limitations before using it beyond a local development machine:

- SSRF hardening is not complete for every shared-address edge case, including
  the CGNAT range (`100.64.0.0/10`). Keep the service bound to `127.0.0.1` and
  do not expose it through a reverse proxy or port forward.
- The latest local validation still has two known persistence-test failures:
  one migration downgrade expectation and one schema metadata expectation.
- Playwright-based visual checks are optional and are skipped when the browser
  dependency is unavailable.

## Browser for agents

Orin can open public HTTPS pages in an isolated Chromium process for each
agent turn. The browser can observe a page, click non-submit controls, fill
non-password fields, select options, toggle controls, use safe navigation
keys and capture the current screen. Each observation creates a private PNG
in the conversation workspace; the chat renders it as a browser activity
card and the file endpoint applies the usual conversation authorization.

The release package includes Chromium. For source development,
`scripts/run-local.ps1` provisions it automatically; to provision it separately:

```powershell
.\scripts\install-browser.ps1
```

Form submission, password entry, arbitrary JavaScript, cookies, clipboard,
camera and geolocation are intentionally unavailable. They require an
explicit future approval/profile flow rather than a model-controlled tool
argument.

## Requirements

The Windows release includes the runtime, SQLite and Chromium. It does not need
Python, Node.js, Docker, PostgreSQL or Redis. You only need a provider account
when you choose to configure one in Settings. Python and Node remain source
development requirements only.

## Install on Windows

After releases are published, install the complete release from
PowerShell:

```powershell
irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex
```

The installer verifies the release SHA-256 before activation, creates the local
configuration and asks whether to create an **Orin Desktop** shortcut. The
shortcut starts a native Windows PowerShell launcher hidden in the background, then opens only the
desktop app. It adds `orin` to the user PATH. The source-controlled [install.ps1](install.ps1) is
the same release installer and may be downloaded, reviewed and executed locally
instead of using a one-liner.

`orin update` will use this same verified release flow. The desktop app shows a
visible update banner with the current and latest versions, plus an **Atualizar**
button that invokes the packaged `orin update` command. A taskbar flag is also
shown when a newer verified release is found; the app never replaces itself in
the background without that explicit action.

To completely remove an installed copy, including its local data and
configuration, close the desktop window and run:

```powershell
orin --uninstall
```

This command refuses to run from a source checkout.

## Start from source

```powershell
Copy-Item .env.local.example .env.local
Copy-Item frontend/.env.local.example frontend/.env.local
```

Put a real value in `AGENTOS_PROVIDER_ENCRYPTION_KEY` in `.env.local` — it
encrypts provider credentials at rest:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Build the web client once, then register the command:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.\scripts\install-orin.ps1
```

Open a new terminal — any terminal, any directory — and run:

```powershell
orin
```

Orin opens its local SQLite database, applies migrations, starts the backend and
worker, starts OmniRouter only if you already installed/enabled it with npm, waits until the interface actually
answers, and opens it in your browser. `Ctrl+C` stops everything it started.

```text
  ORIN

  ✓ Services
  ✓ Backend
  ✓ Workers
  ✓ Frontend

  Orin is ready
  http://127.0.0.1:49200
```

`orin status`, `orin logs`, `orin stop` and `orin restart` are there when you
need them. See [docs/LAUNCHER.md](docs/LAUNCHER.md) for readiness checks,
single-instance detection, process lifecycle, paths, and how this becomes an
installable `orin.exe`.

### Orin Desktop

The browser version remains the default. To host that exact same local web app
in an Electron window instead, install the small desktop shell once and run:

```powershell
Set-Location desktop
npm ci
Set-Location ..
orin --desktop
```

The Electron window appears immediately with a live startup screen. The Python
launcher remains the single owner of SQLite, migrations, the API, scheduler and
worker; Electron only observes its local startup snapshot
and loads the API-served application after `/healthz`, `/readyz`, and the
frontend probe pass. The main web UI is never loaded from `file://`.

If the local database or another startup step fails, the window keeps the
concise error and offers to retry, open the existing Orin logs, or close.
Closing the window asks the same cooperative launcher shutdown path used by
`orin stop`.

For Electron development, use the same command with DevTools enabled:

```powershell
orin --desktop --desktop-devtools
```

The shell can also be packaged as a starting point for a Windows installer:

```powershell
Set-Location desktop
npm run build
```

`scripts/build-windows.ps1` builds the complete release layout: frozen launcher,
Electron host, frontend, SQLite migrations and Chromium. Release publication is
handled separately from the build.

Rebuild the web client after changing the frontend:

```powershell
npm --prefix frontend run build
```

`scripts\run-local.ps1` and `scripts\stop-local.ps1` still exist for driving the
individual processes during development.

### Why three local processes

| Process | Responsibility |
| --- | --- |
| `uvicorn agentos.api.asgi:app` | HTTP + SSE. Accepts a turn, persists it, serves the built client. Never calls a provider. |
| `agentos.workers.publisher` | Polls and claims the durable SQLite turn queue, runs the provider/tool loop. |
| `agentos.workers.scheduler` | Materializes due scheduled chats as normal durable turns. |

Keeping the worker out of the HTTP process is what lets a long turn run without
blocking the API, and what lets you restart the UI without killing a run in
flight. A turn nobody claims is failed by the worker watchdog rather than left
queued forever.

## Configure a provider

Open **Settings → Providers → the provider card** (`Ctrl/Cmd + K` → Providers) and paste a key. It is
encrypted before it is stored and is never returned by any endpoint, log, or
event. Do not put provider keys in `.env.local`.

Refresh the catalog after saving; the composer's model picker only offers models
the server actually authorized.

## Repository and secrets

Only source, tests, public examples, and project documentation belong in this
repository. Local credentials and runtime output are intentionally excluded:

- `.env.local` is ignored; never commit real provider keys or database secrets.
- `.env.example` and `.env.local.example` are safe templates for configuration.
- `.superpowers/`, caches, `data/`, build output, and local test artifacts are
  local-only files and are not part of the product snapshot.

Provider credentials entered in the UI are encrypted at rest with
`AGENTOS_PROVIDER_ENCRYPTION_KEY`. Treat that key like a secret and generate a
new one for each local environment.

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

**Attaching a file** — use the composer's attach button, drag a file onto it, or
paste one from the clipboard. The file is written to `uploads/` inside the
conversation's workspace, same as anything else the agent creates — including
when that workspace is a local folder you picked, not the managed
`data/workspaces/<conversation_id>` directory. Reading it back is `view_file`
(see the tool table above): a document's text is extracted locally, but
reading an image or a scanned page visually sends the file's bytes to the
provider of whichever model does the reading — the turn's own model when it
can see, otherwise the configured visual-reading model — which is why the
automatic choice prefers a local Ollama over sending the file off-machine.

`Ctrl/Cmd + K` opens the command palette: conversations, Settings, memory, providers, and new chat. Settings is the single global management area; project memory and workspace controls stay on the relevant project.

## Conectores MCP

Orin conecta a servidores MCP (Model Context Protocol), locais (stdio, ex.:
`npx`/`uvx`) ou remotos (HTTPS). O próprio agente pode buscar, explicar e
propor uma conexão durante a conversa — mas nunca ativa nada sozinho: a
proposta fica `pending_approval` até você digitar a credencial (se houver) em
um card no chat ou em **Settings → MCP**. Veja [docs/MCP.md](docs/MCP.md) para
o que é suportado, os limites de segurança e como adicionar um servidor ao
catálogo curado.

## Plugins

Plugins declarativos podem adicionar skills, subagentes e propostas de MCP sem
executar código no processo do Orin. O pacote é inspecionado e só é ativado
após aprovação no chat ou em Settings → Plugins. Veja [docs/PLUGINS.md](docs/PLUGINS.md).

## What the agent can do

Tools are defined in `src/agentos/agentic/agent_tools.py`.

| Tool | Notes |
| --- | --- |
| `read_file`, `write_file`, `edit_file`, `list_files` | Confined to `data/workspaces/<conversation_id>`. `edit_file` replaces exactly one matching fragment, preventing ambiguous edits. Path traversal and symlinks out of the sandbox are rejected. |
| `view_file` | Lê um documento (PDF, Word, Excel, PowerPoint, texto) ou uma imagem do workspace. Texto nativo é extraído sem custo de modelo; imagem e página escaneada vão para o modelo do turno quando ele enxerga, ou para o modelo de leitura visual configurado. |
| `run_command` | Runs in that same directory, 45s timeout. A denylist blocks host-destroying commands (`shutdown`, `mkfs`, `rm -rf /`, …). |
| `fetch_url` | Public http(s) only; private, loopback and link-local addresses are refused. Returns readable text. |
| `remember`, `recall` | Durable facts scoped to the user, recalled into later conversations. |
| `create_agent`, `ask_agent` | Create a specialist and hand it a self-contained task. A subagent cannot create further subagents, and there is a per-turn budget. |
| `list_mcp_catalog`, `list_mcp_servers`, `configure_mcp`, `test_mcp_server` | Propose and inspect MCP connectors. `configure_mcp` never accepts a credential value — it creates a pending connection and shows the user an approval card; see [docs/MCP.md](docs/MCP.md). Tools an approved server publishes appear alongside these as `mcp__<server>__<tool>`. |
| `search_plugin`, `inspect_plugin`, `install_plugin`, `list_plugins`, `uninstall_plugin` | Busca, inspeção, proposta e lifecycle de plugins. `install_plugin` nunca ativa sem aprovação explícita; segredos não são argumentos dessas tools. |

`run_command` is a real shell on your machine. That is the point of a local
agent workspace, and it is also the reason not to expose this service to a
network.

## Architecture

```
browser ── HTTP/SSE ──► FastAPI gateway ──► SQLite (conversations, turns,
   ▲                                        activity, memory, agents, queue)
   │                                             ▲
   └──── activity stream ◄──── worker ──────────┴─► provider
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
python -m pytest -q tests/integration
```

The integration suite uses an isolated SQLite database. Stop an active Orin
instance before tests that exercise durable turn recovery.

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
