# Orin Distribution — one command installs Orin on any machine

Design for publishing Orin as an installable product. The end state is a single
line, on all three operating systems, that leaves a working `orin` behind:

```powershell
irm <base>/install.ps1 | iex
```

```bash
curl -fsSL <base>/install.sh | sh
```

`<base>` is a variable with a GitHub Releases default, not a hardcoded domain.
Pointing `orin.dev` at it later is DNS plus a redirect, and changes no script.

Two things stand between today and that line. The runtime still needs Docker for
PostgreSQL and Redis, which is a prerequisite no consumer install can assume; and
there is no build, no release, and nothing that has ever run outside Windows.
This design removes the first and builds the second.

## What exists today

| Piece | State |
| --- | --- |
| `RuntimeProfile` (`installation/profile.py`) | Already answers `development` vs `installed`, resolves the web bundle from `_package_root()/"web"` when there is no checkout, resolves migrations from inside the package. A plain wheel is already a valid installation. |
| `OrinPaths` (`installation/paths.py`) | Config, data, logs, cache and run state already resolve to per-user OS locations outside the installation directory, on Windows and POSIX alike. |
| Multi-call launcher | Children are spawned as `orin internal-service <service>`, never as `python -m` against a source tree. |
| `scripts/install-orin.ps1` | Development-profile installer. Writes `orin.cmd` to `%LOCALAPPDATA%\Orin\bin` and puts it on the user PATH. Shaped deliberately like the public installer. |
| `conversation_dispatches` + `PostgresChatStore.claim` | The durable queue and its atomic claim. Already the real source of truth. |
| `workers/ports.py` | `WorkQueue.enqueue(item) -> QueueReceipt` — the protocol the Redis adapter implements. |
| CI | None. No `.github` directory, no tags, no release. |
| Non-Windows support | Untested. The launcher is written portably but has never run on macOS or Linux. |

## Decisions

| Decision | Choice | Why not the alternative |
| --- | --- | --- |
| What gets installed | A wheel into a `uv`-managed runtime | Freezing with PyInstaller means five artifacts, hidden-import hunting for alembic/SQLAlchemy, and macOS notarization. `uv` also supplies Python itself, so the user needs no Python either. |
| PostgreSQL | Embedded, managed by the launcher | Docker Desktop is a prerequisite a consumer install cannot assume. |
| Redis | Removed | There is no official Redis build for Windows, and the durable claim never depended on Redis anyway. |
| Install scripts host | GitHub Releases, behind `ORIN_INSTALL_BASE` | The domain is not chosen yet. A variable costs nothing now and absorbs the domain later. |
| Existing local data | Not migrated | Pre-1.0, no public users. Pointing `DATABASE_URL` at the old container preserves it for anyone who cares. |

---

## Part 1 — One datastore

### 1.1 Remove Redis and arq

The swap is small because the durable side already carries the guarantees.
`PostgresChatStore.claim` is a conditional `UPDATE ... WHERE state IN
('pending','enqueued')`; a second worker that races it gets `rowcount == 0` and
walks away. Redis only ever transported a `turn_id` and capped concurrency.

**`workers/adapters.py`** — `RedisArqWorkQueue` and `ArqQueueReceipt` are replaced
by `PostgresWorkQueue` and `DispatchReceipt`, implementing the same `WorkQueue`
protocol in `workers/ports.py`. That protocol is the seam that makes this a
substitution rather than a rewrite; it does not change.

**`workers/chat.py`** — `WorkerSettings`, `agentos_agent` and the arq `startup`
hook are removed. `ChatWorker` itself — the class that actually runs a turn — is
untouched. A new `run_worker_loop()` drives it:

- poll `conversation_dispatches` for rows in `('pending','enqueued')`, oldest
  first, at `POLL_SECONDS = 0.25`;
- for each candidate, call the existing `store.claim(turn_id)`; `None` means
  another worker won the race, which is ordinary and not logged as an error;
- run claimed turns on a bounded pool of 8 — the same ceiling as arq's
  `max_jobs` — and never hold a database transaction while a turn runs;
- `store.heartbeat("chat-worker")` every tick, so the launcher's existing
  readiness probe against `runtime_heartbeats` keeps working unchanged.

The turn deadline stays where it already is: `TURN_DEADLINE` in the runtime, plus
`recover_stale` in the publisher. arq's `job_timeout` was a third, redundant
bound and is dropped with it.

**`workers/publisher.py`** — keeps the `pending → enqueued` transition, the
watchdog, and `recover_stale`. It loses only `create_pool` and `enqueue_job`. The
state machine, its tests, and the three-process architecture survive intact.

**Removals elsewhere:**

- `pyproject.toml`: the `arq` and `redis` dependencies.
- `bootstrap/production.py`: `REDIS_URL` from `ProductionSettings` and the
  `redis` branch of `DependencyProbe`. `model_config` already sets
  `extra="ignore"`, so a stale `REDIS_URL` in an existing `orin.env` is inert
  rather than fatal.
- `launcher/probes.py`: `redis_probe`.
- `launcher/environment.py`: `DEFAULT_REDIS_URL`, `RuntimeEnvironment.redis_url`,
  and the `REDIS_URL` line written into a generated configuration.
- `/readyz`: the queue check. The queue is the database now; checking Postgres
  twice is not a second guarantee.
- `docker-compose.yml`: the `redis` service.

### 1.2 Embedded PostgreSQL

A new module, `agentos/services/postgres_server.py`, owns a per-user cluster.
The launcher's `Services` step keeps its contract — "make the datastore
reachable" — and changes implementation only.

**Binaries.** PostgreSQL 16 from `embedded-postgres-binaries` (~15 MB compressed
per platform; covers `windows-amd64`, `darwin-arm64v8`, `darwin-amd64`,
`linux-amd64`, `linux-arm64v8`). These are **re-published as assets of our own
GitHub Release with a `SHA256SUMS` file**. An install must not depend on a third
party's uptime, and a checksum we publish is a checksum we control.

**Layout.** Binaries land in `<state>/runtime/postgres/16/`, the cluster in
`<data>/postgres`. Both are outside the installation directory, so `orin update`
replaces the runtime without touching either.

**Lifecycle.**

| Phase | Action |
| --- | --- |
| First run | `initdb -U agentos --auth=trust --encoding=UTF8`, ~10s, reported on the console as its own line rather than a silent pause |
| Start | `pg_ctl start` with `listen_addresses=127.0.0.1` and a port from the existing `ports.select_port` logic (5433 preferred) |
| Adoption | the postmaster joins the same `ProcessGroup` / job object as every other child, so the no-orphans guarantee covers it |
| Stop | `pg_ctl stop -m fast` in the reverse-order shutdown, before the job object closes |
| Recovery | a `postmaster.pid` left by a crash is validated against a live process and cleared when stale, the same reasoning `InstanceLock` already applies to the launcher's own pid |

**Which cluster.** `DATABASE_URL` remains the single authority:

- **set** → external mode. Orin probes it and starts nothing. Docker, a system
  PostgreSQL, or a remote instance all keep working, with no conditional logic
  scattered through the launcher.
- **unset** → embedded mode. Orin starts the cluster and injects the resolved URL
  into every child's environment, exactly as it already injects the path layout.

A generated `orin.env` therefore no longer writes `DATABASE_URL`; it carries a
comment explaining that setting it opts into an external database.

**Removed from the launcher:** `profile.compose_file` and `_compose_up`. The
`docker-compose.yml` file stays in the repository, reduced to its `postgres`
service, for contributors who would rather run an external database — reached
through `DATABASE_URL` like any other external instance, never invoked by Orin.

**Pinned.** PostgreSQL 16, matching today's compose. A future major version needs
`pg_upgrade` or dump/restore; that is a separate project and is explicitly out of
scope here.

---

## Part 2 — Build, release, install

### 2.1 The artifact

One wheel, not one per platform. CI runs `npm ci && npm run build` and copies
`frontend/dist` into `src/agentos/web` before packaging; `RuntimeProfile.web_dist`
already looks there. Migrations are already inside the package.

`pyproject.toml` gains `[tool.setuptools.package-data]` entries for `web/**` and
the migration scripts, and takes its version from the release tag.

### 2.2 Workflows

**`.github/workflows/ci.yml`** — on push and pull request:

| Job | Steps |
| --- | --- |
| `python` | `ruff check`, `pytest tests/unit` |
| `frontend` | `npm ci`, `npm test`, `npm run lint`, `npm run build` |

**`.github/workflows/release.yml`** — on tag `v*`:

1. build the frontend, copy it into the package, build the wheel and sdist;
2. download the five PostgreSQL bundles, verify them, re-publish them with a
   `SHA256SUMS` file;
3. create the GitHub Release carrying the wheel, the bundles, the checksums, and
   copies of `install.ps1` and `install.sh`;
4. run the install smoke matrix (§3.2) against the artifacts just built.

### 2.3 The installers

`install.ps1` and `install.sh` perform the same six steps in the same order.

1. **Resolve the source.** `ORIN_INSTALL_BASE`, defaulting to
   `https://github.com/carlos-edu2367/orin/releases/latest/download`, under which
   every asset has a stable, version-independent name (`orin-<version>-py3-none-any.whl`
   resolved from a `manifest.json` published beside it, `postgres-<platform>.txz`,
   `SHA256SUMS`). `-Version` / `--version` swaps `latest/download` for
   `download/v<version>`.
2. **Ensure `uv`.** Use it if it is on PATH; otherwise fetch the official
   standalone installer.
3. **`uv python install 3.13`** — the user needs no Python of their own.
4. **Install the runtime.** `uv venv` plus `uv pip install <wheel>` into
   `<installation>/versions/<version>/`, then repoint a `current` junction
   (Windows) or symlink (POSIX). This is the layout `docs/LAUNCHER.md` already
   promises: an update installs beside the current version and flips the pointer,
   and rollback is flipping it back. User data was never inside either version.
5. **Fetch PostgreSQL** for the detected platform, verify SHA256 **before**
   unpacking, into `<state>/runtime/postgres/16/`.
6. **Write the shim and the PATH entry.** `%LOCALAPPDATA%\Orin\bin\orin.cmd` on
   Windows, `~/.local/bin/orin` on POSIX. **User PATH only, never machine PATH,
   never elevated.**

The concrete layout the two scripts and `orin update` all write to. Everything
mutable stays outside `versions/`, which is what makes replacing a version safe:

| | Windows | macOS / Linux |
| --- | --- | --- |
| Versions | `%LOCALAPPDATA%\Programs\Orin\versions\<version>` | `~/.local/share/orin/app/versions/<version>` |
| Pointer | `%LOCALAPPDATA%\Programs\Orin\current` (junction) | `~/.local/share/orin/app/current` (symlink) |
| Command | `%LOCALAPPDATA%\Orin\bin\orin.cmd` | `~/.local/bin/orin` |
| PostgreSQL binaries | `%LOCALAPPDATA%\Orin\runtime\postgres\16` | `~/.local/share/orin/runtime/postgres/16` |
| Config | `%APPDATA%\Orin\config` | `~/.config/orin` |
| Data, logs, cache, run | `%LOCALAPPDATA%\Orin\{data,logs,cache,run}` | `~/.local/share/orin/{data,logs,cache,run}` |

The last three rows are what `OrinPaths` already resolves, unchanged. The
installer adds only the first four, and `ORIN_HOME` keeps relocating the whole
layout as it does today.

Platform detection in `install.sh` maps `uname -s`/`uname -m` to `darwin-arm64`,
`darwin-x86_64`, `linux-x86_64`, `linux-aarch64`. Anything else exits with the
exact unsupported combination rather than installing something broken.

On POSIX the script appends the PATH entry to the detected shell's rc file inside
a delimited, greppable block, and `--no-modify-path` declines it — the convention
`rustup` and `uv` both use.

Both scripts are idempotent: re-running installs the newer version beside the old
one and repoints `current`. Both fail closed — a failed checksum, a missing
platform, or a failed download leaves no partial installation and no PATH entry.

### 2.4 `orin update` and `orin uninstall`

Installing from outside and updating from inside must not drift, so both become
CLI verbs backed by the same layout:

- **`orin update`** — resolve the latest version, install it beside the current
  one, apply migrations against the untouched data directory, flip `current`.
  Refuses while an instance is running; the existing `InstanceLock` already
  answers that question truthfully.
- **`orin uninstall`** — remove the shim, the PATH entry, and the installed
  versions. It does **not** touch data, configuration or logs, and prints the
  path of each so the user can remove them deliberately.

### 2.5 Portability audit

The launcher is written portably but has only ever run on Windows. Confirmed
defects and items to verify:

| Item | Status |
| --- | --- |
| `state.kill_process_tree` | **Bug.** On POSIX it sends `SIGKILL` to the pid alone. Children are created with `start_new_session=True`, so the fix is `os.killpg(os.getpgid(pid), SIGKILL)`; the Windows path already covers the tree via `taskkill /T`. |
| `webbrowser.open` | Verify behaviour with no display; a headless Linux session must print the URL rather than fail the launch. |
| Console output | Verify ANSI and the `✓` glyph on macOS Terminal, common Linux terminals, and legacy Windows consoles. |
| `InstanceLock` | `fcntl.flock` path is written but unexercised. |
| Path handling | Data directories containing spaces, exercised through `initdb` and `pg_ctl` arguments. |

---

## Part 3 — Verification

The risk here is not writing the scripts. It is believing they work on a Mac
nobody ran them on.

### 3.1 Unit tests

For logic that is now ours and used to be a dependency's:

- **Queue** — two concurrent workers race one dispatch and exactly one claim
  succeeds; an orphaned turn is recovered; the concurrency ceiling holds; a
  claim failure is not an error path.
- **Cluster** — `initdb` runs once and is idempotent on a second start; a stale
  `postmaster.pid` after a simulated crash is detected and cleared; a taken port
  moves the cluster; `stop -m fast` leaves no postmaster.
- **Installer helpers** — platform resolution, download URL construction, and
  the unsupported-platform failure.

### 3.2 Install smoke matrix

On `windows-latest`, `macos-latest` and `ubuntu-latest`, against the artifacts
built in the same run:

```
run the real install script
orin --no-browser
poll /readyz until ready
orin status            → expect exit 0
orin stop
orin status            → expect exit 3
```

Then, in the same job, `orin update` from the previous release to the new one,
re-running the same assertions. Without this, the update path is exercised for
the first time on a real user.

This job is what earns the right to claim the one-liner works. Nothing else does.

### 3.3 Existing suites

`tests/integration` moves from the compose datastore to an embedded cluster on a
dedicated port, which also removes the "stop the stack before running the suite"
caveat currently in the README.

## Risks

| Risk | Mitigation |
| --- | --- |
| `initdb` refuses to run as Administrator on Windows | Detect elevation before the attempt and fail with an explanatory message |
| PostgreSQL Windows binaries need the Visual C++ runtime | Detect the specific load failure and name the redistributable, rather than surfacing a cryptic error |
| Antivirus slows the first `initdb` past its deadline | Generous first-run deadline, distinct from the steady-state start deadline |
| PostgreSQL 16 pinned | Recorded as a known constraint; a major upgrade is a separate project |
| Domain not chosen | `ORIN_INSTALL_BASE` with a GitHub default; §4 documents the cutover |

## Documentation to update

- **`README.md`** — the install one-liner replaces the manual `.env.local`,
  `npm ci`, `npm run build`, `install-orin.ps1` sequence. Docker leaves the
  requirements list; Python and Node leave it too, since `uv` supplies Python and
  the web bundle ships built.
- **`docs/LAUNCHER.md`** — the "How this becomes `orin.exe`" section is replaced
  by what actually shipped; the Services table stops mentioning Redis and Docker
  Compose.
- **`docs/INSTALL.md`** (new) — the domain cutover: publish `install.ps1` and
  `install.sh` at the chosen domain, or redirect to the GitHub copies, and set
  `ORIN_INSTALL_BASE`'s default to it. One commit, no logic change.
- **`docs/ADR`** — one record for removing Redis, one for embedding PostgreSQL.

## Out of scope

Bundled Redis, a PostgreSQL major-version upgrade path, code signing and
notarization, package managers (Homebrew, winget, apt), auto-update on launch,
and migrating data out of the existing Docker cluster.
