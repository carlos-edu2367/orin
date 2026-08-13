# The `orin` command

Orin is a local product, not a set of processes you assemble by hand. There is
one command:

```powershell
orin
```

It works from any directory, starts everything Orin needs, waits until the
interface really answers, and opens it in your browser.

```text
  ORIN

  ✓ Services
  ✓ Backend
  ✓ Workers
  ✓ OmniRouter
  ✓ Frontend

  Orin is ready
  http://127.0.0.1:49200
```

`Ctrl+C` stops it again, and takes every process it started with it.

## Installing the command

```powershell
.\scripts\install-orin.ps1
```

That registers `orin` as a shim in `%LOCALAPPDATA%\Orin\bin` and puts that
directory on your user PATH. Open a new terminal afterwards — Windows only gives
a PATH change to terminals started after it.

The shim points at the runtime in this checkout, so a `git pull` is reflected in
`orin` immediately. `.\scripts\install-orin.ps1 -Uninstall` removes the command
and leaves your data, configuration and logs alone.

## What starts, in what order

| Step | What it is | Ready when |
| --- | --- | --- |
| Services | PostgreSQL and Redis, plus schema migrations | `SELECT 1` succeeds and Redis answers `PING` |
| Backend | HTTP, SSE, and the web interface itself | `/healthz` answers, then `/readyz` confirms the database and queue |
| Workers | the dispatch publisher and the chat worker | both have written fresh rows to `runtime_heartbeats` |
| OmniRouter | the optional local gateway — **only if you enabled it** | `GET <gateway>/v1/models` returns 2xx |
| Frontend | the built SPA the backend serves | `GET /` returns the application document |

No step waits a fixed number of seconds and hopes. Each one waits for evidence,
with a deadline, and gives up early if the process it is waiting for has already
exited. The browser opens after the last step, because the last step is the one
that proves the interface is being served.

If a step fails, the steps that already succeeded are shut down in reverse and
`orin` exits non-zero with the reason and the log file to look in. A half-running
Orin is never left behind.

## OmniRouter

OmniRouter is optional, and whether it starts with Orin is decided by one
setting that already exists:

**Settings → Providers → OmniRoute → "Start OmniRoute when Orin launches"**

That preference is the only source of truth. The launcher reads it and never
writes it.

- **Off** — OmniRouter is not part of startup. There is no step, no probe and no
  warning; choosing not to run it is an ordinary choice, not a problem to report.
- **On** — the step appears and is awaited briefly. The gateway is a Node
  application that can take a long time to serve its first request on a cold
  start, so Orin gives it a few seconds and then continues without it, reporting
  `· OmniRouter starting` and printing `✓ OmniRouter` later, when it answers.
  Orin is fully usable throughout: nothing else depends on the gateway.

The gateway process is owned by the backend, which is what the Start, Stop and
Restart buttons in the interface act on. Orin stops the gateway it started when
it shuts down, and never touches an instance that was already running — if you
started OmniRoute yourself, it is still running after `orin stop`, and the
shutdown output does not claim otherwise.

## Running instances

Only one Orin runs per installation. The launcher holds an exclusive lock on a
file for its whole lifetime; the operating system releases that lock when the
process dies, however it dies, so a crash never leaves a stale claim behind. A
pid file is not trusted on its own — pids are recycled.

Running `orin` while Orin is already running does not start a second copy:

```text
  Orin is already running.

  Opening http://127.0.0.1:49200
```

## Ports

Orin binds `127.0.0.1` only, never a LAN address. Port 49200 by default.

- The port is free → it is used.
- The port belongs to your own running Orin → that instance is opened.
- The port belongs to other software → Orin moves to the next free port and says
  so.
- You asked for a specific port with `--port` and it is taken → Orin refuses
  rather than quietly using a different address than the one you named.

## Commands

```powershell
orin                    # start, wait for ready, open the browser
orin --port 9000        # start on a specific port
orin --no-browser       # start without opening a browser
orin --desktop          # start and host the API-served app in Electron
orin -v                 # show startup detail on the console
orin start              # the same as `orin`
orin stop
orin restart
orin status
orin logs [--service backend|publisher|worker|launcher] [-n 50] [--follow]
orin --version
orin --help
```

`orin status` exits `0` when Orin is running and `3` when it is not, so it can be
used in a script.

## Orin Desktop

`orin --desktop` uses the same `Supervisor` and the same local services as the
regular command. It starts Electron before datastore work begins, then writes an
atomic snapshot to `data/run/desktop-startup.json` (or the configured run
directory). The splash polls that file and displays actual launcher stages:
Docker, PostgreSQL, Redis, migrations, API, `/healthz`, `/readyz`, publisher,
worker, and the frontend probe.

Electron never starts Docker, services, or Python workers itself. Once the
launcher confirms readiness it provides the chosen loopback URL, and Electron
loads `http://127.0.0.1:<port>` in the same `BrowserWindow`. This preserves the
existing cookies, SSE, WebSocket, upload, download, and routing behavior.

The shell is a single Electron instance. A second `orin --desktop` asks its
existing window to focus, while the launcher's existing lock continues to stop a
second backend set. Closing the window writes the regular cooperative stop
request, including when startup is still waiting for a health check.

In a development checkout, install its dependencies once:

```powershell
Set-Location desktop
npm ci
```

Then use `orin --desktop --desktop-devtools` while changing shell files. Electron
logs are written beside the other launcher logs as `desktop.log`. Build the
initial Windows shell with `npm run build` from `desktop`; it is only the
Electron host until the frozen launcher is packaged next to it.

## Logs

The console stays minimal. Full detail is always written to files, whether or
not you passed `--verbose`:

| File | Contents |
| --- | --- |
| `launcher.log` | startup decisions, spawned commands, probe results, shutdown |
| `backend.log` | the HTTP process |
| `publisher.log` | the dispatch publisher |
| `worker.log` | the chat worker |
| `desktop.log` | Electron main-process output |

Values that look like keys, tokens or passwords are redacted before anything is
written, and secrets are passed to child processes through the environment —
never on a command line, where any other user on the machine could read them out
of the process table.

## Where Orin keeps things

Mutable state never lives inside the installation directory. That separation is
what will let a future `orin update` replace the runtime without touching
anything you care about.

| | Development (this checkout) | Installed |
| --- | --- | --- |
| Installation | the repository | `%LOCALAPPDATA%\Programs\Orin\current` |
| Config | `.env.local` in the repository | `%APPDATA%\Orin\config` |
| Data, workspaces | `data\` | `%LOCALAPPDATA%\Orin\data` |
| Logs | `.logs\` | `%LOCALAPPDATA%\Orin\logs` |
| Cache | `.cache\` | `%LOCALAPPDATA%\Orin\cache` |
| Run state | `data\run\` | `%LOCALAPPDATA%\Orin\run` |

Set `ORIN_HOME` to relocate the whole layout, or `ORIN_DATA_DIR`,
`ORIN_LOGS_DIR`, `ORIN_CONFIG_DIR`, `ORIN_CACHE_DIR`, `ORIN_RUN_DIR` to move one
root. Child processes are told the layout explicitly, so a worker never has to
infer which installation it belongs to from its working directory.

On first run with no configuration at all, Orin writes one — including a freshly
generated encryption key for provider credentials. Provider API keys are never
put in that file; they are entered in the interface and encrypted at rest.

## Development and runtime are not the same thing

```text
DEVELOPMENT                    RUNTIME
source                         orin
  ↓                              ↓
vite dev server, hot reload    built assets served by the backend
uvicorn --reload                supervised child processes
```

The frontend dev server is a development tool. It is not part of running Orin:
the backend serves a static build, so an installation needs no bundler, no npm
and no Node.

## Shutting down

`Ctrl+C`, `SIGINT`, `SIGTERM` and `orin stop` all take the same path: children
are asked to stop, in reverse order, and given time to finish.

```text
  Stopping Orin...

  ✓ Workers stopped
  ✓ Backend stopped
  ✓ OmniRouter stopped

  Orin stopped.
```

On Windows there is a second, independent guarantee. Every child is placed in a
job object created with `KILL_ON_JOB_CLOSE`, and the kernel closes that handle
when the launcher exits — by any route, including being killed outright.
Grandchildren are inside the job too, which is what stops an npm shim or a
gateway from surviving the process that started it.

`orin stop` from another terminal writes a request file the supervisor polls,
and falls back to terminating the recorded process tree if the supervisor does
not act on it.

## How this becomes `orin.exe`

Nothing above is tied to a repository, a virtual environment, or a working
directory, so packaging is a build problem rather than a redesign.

**The launcher is already a multi-call binary.** It starts services by
re-executing *itself* with a hidden verb — `orin internal-service backend` — not
by invoking `python -m` against a source tree. Freezing changes only which
executable that is.

**The installation directory is already read-only at runtime.** Configuration,
data, logs and run state resolve to per-user locations outside it.

The remaining work, none of which requires touching the launcher:

1. **Build** — CI produces `frontend/dist` and freezes the launcher and backend
   into one `orin.exe` (PyInstaller onedir), shipped as a versioned archive with
   the web bundle as a static asset directory inside the installation.
2. **`install.ps1`** — downloads the archive, unpacks it to
   `%LOCALAPPDATA%\Programs\Orin\<version>`, repoints a `current` junction, and
   writes the same `orin.cmd` shim to the same bin directory that
   `scripts\install-orin.ps1` writes today. The published one-liner
   (`irm https://orin.dev/install.ps1 | iex`) is that script.
3. **`orin update`** — downloads the next version *beside* the current one, runs
   migrations against the untouched data directory, then flips the junction.
   Rollback is flipping it back: user data was never inside either version, so
   neither direction can lose it.

One external dependency remains: PostgreSQL and Redis. Today the launcher brings
them up through Docker Compose when a compose file ships with the installation.
The `Services` step is the single seam where a bundled datastore replaces that,
and no step above it changes when it does.
