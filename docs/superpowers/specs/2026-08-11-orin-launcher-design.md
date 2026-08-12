# Orin Launcher — `orin` global command

Design for a single global entrypoint that starts the whole Orin runtime from any
directory, waits for real readiness, opens the browser, and shuts everything down
cleanly. The architecture is chosen so the same entrypoint can later be frozen
into `orin.exe` and installed with `irm https://orin.dev/install.ps1 | iex`.

## What exists today

| Piece | How it starts today | Notes |
| --- | --- | --- |
| Backend | `uvicorn agentos.api.asgi:app --env-file .env.local --host 127.0.0.1 --port 8000` | Also serves the built SPA when `LOCALHOST_TRUST_ENABLED`. Has `/healthz` and `/readyz`. |
| Publisher | `python -m agentos.workers.publisher` | Moves durable pending turns onto Redis. Heartbeats `chat-publisher` every poll. |
| Chat worker | `arq agentos.workers.chat.WorkerSettings` | Runs the agent loop. |
| Postgres + Redis | `docker compose up -d --wait` | Ports 5433 / 6380 on loopback. |
| Frontend | `npm run build` → `frontend/dist`, served by the backend | **No dev server is needed at runtime.** |
| OmniRouter | `OmniRouteProcessManager`, auto-started by the API's FastAPI startup hook | Preference persisted per user in `data/omniroute-runtime.json`. |

Everything above is driven by `scripts/run-local.ps1`, which is coupled to the
repo: relative `data/` paths, `.venv`, `frontend/dist`, `alembic.ini`, `.env.local`.

The single source of truth for OmniRouter startup already exists:
`OmniRouteRuntimeSettingsStore` (`data/omniroute-runtime.json`), surfaced in the
UI as **Start OmniRoute when Orin launches** and written only by
`PUT /v1/providers/omniroute/runtime`.

## Architecture

```
orin (console script / future orin.exe)
        │
   Launcher CLI ── single-instance lock ──► already running? open browser, exit
        │
   RuntimeProfile  (development | installed)   ← where the runtime lives
   OrinPaths       (config/data/logs/cache/run) ← where mutable state lives
        │
   Supervisor
     1. Services   docker compose / probe Postgres + Redis, run migrations
     2. Backend    child: `orin internal-service backend`   → /healthz, /readyz
     3. Workers    children: publisher + chat worker        → runtime heartbeats
     4. OmniRouter only if the existing preference is ON    → gateway health
     5. Frontend   built SPA served by the backend          → GET / returns app
        │
   instance state written ──► browser opens ──► supervise until Ctrl+C
```

### Multi-call binary

Children are never spawned as `python -m ...` from a repo path. The launcher
spawns **itself** with a hidden verb:

```
orin internal-service backend | publisher | worker
```

In development that resolves to `[sys.executable, "-m", "agentos.launcher", ...]`;
once frozen it becomes `[sys.executable, "internal-service", ...]` with no change
to the supervisor. This is the single decision that makes freezing to `orin.exe`
mechanical rather than a rewrite.

### Paths

Mutable state never lives in the installation directory — that is what makes
`orin update` and reinstall-without-data-loss possible later.

| Kind | Development (repo checkout detected) | Installed |
| --- | --- | --- |
| Installation | repo root | `%LOCALAPPDATA%\Programs\Orin\current` |
| Config | repo root (`.env.local`) | `%APPDATA%\Orin\config` |
| Data | `<repo>/data` | `%LOCALAPPDATA%\Orin\data` |
| Logs | `<repo>/.logs` | `%LOCALAPPDATA%\Orin\logs` |
| Cache | `<repo>/.cache` | `%LOCALAPPDATA%\Orin\cache` |
| Run state | `<repo>/data/run` | `%LOCALAPPDATA%\Orin\run` |

`ORIN_HOME` overrides the whole layout; `ORIN_DATA_DIR`, `ORIN_LOGS_DIR`,
`ORIN_CONFIG_DIR`, `ORIN_CACHE_DIR`, `ORIN_RUN_DIR` override individual roots.
Development defaults deliberately match today's `data/` and `.logs/` so existing
state, `run-local.ps1` and the test suites keep working unchanged.

Core modules that hardcode `data/...` relative to the cwd
(`agentic/session.py`, `agentic/settings.py`, `api/gateway.py`,
`bootstrap/production.py`, `workers/chat.py`, `omniroute/process_manager.py`)
resolve their defaults through `agentos.installation.paths` instead. That is what
makes `cd C:\ && orin` behave identically to running from the repo.

### Readiness — no arbitrary sleeps

| Step | Real check |
| --- | --- |
| Services | TCP connect + `SELECT 1` on Postgres, `PING` on Redis |
| Backend | `GET /healthz` then `GET /readyz` (which itself verifies Postgres + Redis) |
| Workers | fresh rows in `runtime_heartbeats` for `chat-publisher` and `chat-worker` |
| OmniRouter | `GET <base_url>/models` returns 2xx |
| Frontend | `GET /` returns 200 `text/html` containing the SPA root element |

The chat worker only heartbeats when it claims a turn, so `arq`'s `on_startup`
also writes the heartbeat. That makes worker readiness observable and makes the
existing watchdog slightly more accurate.

### OmniRouter

The launcher **reads** `OmniRouteRuntimeSettingsStore` and never writes it. The
process itself stays owned by the backend, because the Start/Stop/Restart
endpoints must control the same handle.

- preference OFF → the step does not exist in the plan, no probe, no log line,
  no warning. Absence is the normal case, not an error.
- preference ON → the step is displayed and awaited. Failure is reported as a
  warning and startup continues: the module's own contract is that a failed
  optional gateway never makes Orin unavailable.

Two supporting fixes: the backend gains a shutdown hook that stops the process it
owns (today it is orphaned when uvicorn exits), and every child is placed in a
Windows job object so nothing survives the launcher under any exit path.

### Single instance

`<run>/instance.lock` is held with an exclusive OS lock (`msvcrt.locking` on
Windows, `fcntl.flock` elsewhere) for the entire lifetime of the supervisor. The
lock is released by the kernel when the process dies, so a crash never leaves a
stale lock. `<run>/instance.json` carries pid, port, url, version and start time.

A second `orin` fails to take the lock, reads the state file, verifies the
recorded port actually answers `/healthz`, prints `Orin is already running` and
opens that URL. Pid liveness alone is never trusted.

### Ports

`--port` given explicitly must be free or already ours, otherwise a plain error.
Without it: prefer 8000, attach if it is our own instance, otherwise scan forward
for a free loopback port. Everything binds `127.0.0.1` only.

### Lifecycle

Startup is ordered and fail-fast: if a step fails, the steps already started are
shut down in reverse before the launcher exits non-zero. Shutdown (Ctrl+C,
SIGINT, SIGTERM, `orin stop`) is graceful — CTRL_BREAK to each child's own
process group, then terminate, with the job object as the backstop that
guarantees no orphans on Windows.

`orin stop` from another terminal writes `<run>/stop.request`; the supervisor
polls it and shuts down. If the supervisor does not exit in time, `stop` falls
back to killing the recorded process tree.

### CLI

```
orin                    start the runtime and open the browser (default)
orin start              same, with --port / --no-browser / --verbose
orin stop | restart | status
orin logs [--follow] [--service backend|publisher|worker|launcher] [-n]
orin --version | --help
orin internal-service <name>   hidden; used by the supervisor for children
```

Console output is minimal by default (`✓ Backend`, …). Full detail always goes to
`<logs>/launcher.log` and one file per service, regardless of `--verbose`.

### Security

Every service binds `127.0.0.1`. Secrets are passed to children through the
inherited environment, never through argv, and the launcher redacts anything
key-shaped before it writes a log line. No existing authorization is relaxed:
`LOCALHOST_TRUST_ENABLED` still requires `AGENTOS_ENV` to be local/development.

## Path to `orin.exe`

Nothing in the design blocks the installer flow, because the launcher already
treats the installation directory as read-only and resolves everything through
`RuntimeProfile`:

1. CI builds `frontend/dist`, freezes the launcher and the backend into a single
   `orin.exe` (PyInstaller onedir; the multi-call verb is the entrypoint), and
   ships them as a versioned release archive.
2. `install.ps1` downloads the archive, unpacks it to
   `%LOCALAPPDATA%\Programs\Orin\<version>`, repoints the `current` junction and
   adds `%LOCALAPPDATA%\Programs\Orin\bin` to the user PATH.
3. `orin` starts the installed profile: no Python, Node, npm or `.venv` needed,
   because the interpreter is inside the executable and the SPA is a static
   asset directory inside the installation.
4. `orin update` downloads the next version beside the current one, runs
   migrations against the untouched data directory and flips the junction —
   rollback is flipping it back, since user data was never inside either.

The one remaining external dependency is Postgres + Redis. Today the launcher
brings them up through Docker Compose when a compose file is part of the profile.
The installed profile will instead ship a bundled datastore; the supervisor's
"Services" step is the seam where that swap happens, and nothing above it changes.
