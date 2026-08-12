# Orin Single Datastore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Orin starts and runs with no Docker and no Redis — PostgreSQL is embedded and managed by the launcher, and the chat queue is the database it already writes to.

**Architecture:** Redis only ever transported a `turn_id`; the guarantee that a turn runs once is `PostgresChatStore.claim`, a conditional `UPDATE` that already exists. The arq worker is replaced by a poll-claim loop against `conversation_dispatches`, and the Docker Compose call in the `Services` startup step is replaced by a per-user PostgreSQL cluster the launcher starts and stops like any other child. `DATABASE_URL` stays the single authority: set means "use my database", unset means "start the embedded one".

**Tech Stack:** Python 3.13, SQLAlchemy 2, FastAPI, pytest, PostgreSQL 16 (`embedded-postgres-binaries`), `uv` for the environment.

This plan is part one of three. Part two builds the release pipeline; part three builds the installers. Neither can start before this one lands, because the installer's whole purpose is removing the Docker prerequisite.

Spec: [`docs/superpowers/specs/2026-08-11-orin-distribution-design.md`](../specs/2026-08-11-orin-distribution-design.md)

## Global Constraints

- Python `>=3.13`. Match the existing code style: `from __future__ import annotations`, dataclasses with `frozen=True, slots=True`, module docstrings that explain *why*, not *what*.
- PostgreSQL is pinned to **major version 16**. Never resolve "latest".
- The embedded cluster listens on `127.0.0.1` only. Never a LAN address, never `*`.
- Secrets never appear on a command line. Child processes are configured through the inherited environment only.
- Every new subprocess call carries `# noqa: S603` with a comment naming why the executable is trusted, matching `launcher/services.py`.
- No new third-party dependencies. `arq` and `redis` are removed and nothing replaces them.
- Existing public behaviour that must not change: `orin`, `orin stop`, `orin status`, `orin restart`, `orin logs`, the three-process split, the console output shape, and the readiness semantics (`/healthz` → `/readyz` → heartbeats → frontend).
- Run `python -m pytest -q tests/unit` before every commit. It must stay green.

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `src/agentos/workers/loop.py` | The chat worker's poll-claim-run loop and its concurrency ceiling. Scheduling only; no SQL. |
| `src/agentos/services/__init__.py` | Package marker for the runtime services Orin manages itself. |
| `src/agentos/services/postgres_binaries.py` | Which PostgreSQL build this machine needs, where it lives, how it is verified and unpacked. No process management. |
| `src/agentos/services/postgres_checksums.json` | Pinned SHA256 per platform for PostgreSQL 16. Generated once, committed. |
| `src/agentos/services/postgres_server.py` | One cluster's lifecycle: initdb, start, stop, liveness, stale lock recovery. No downloading. |
| `scripts/mirror-postgres.py` | Mirrors the upstream builds into Orin's own assets and pins their checksums. Used here and by the release workflow. |
| `tests/unit/services/__init__.py` | Package marker. |
| `tests/unit/services/test_postgres_binaries.py` | Platform resolution, asset naming, checksum verification. |
| `tests/unit/services/test_postgres_server.py` | Cluster lifecycle against a fake `pg_ctl`. |
| `tests/unit/workers/test_loop.py` | Claim races, concurrency ceiling, heartbeat, shutdown. |

**Modified**

| File | Change |
| --- | --- |
| `src/agentos/launcher/state.py` | `kill_process_tree` kills the POSIX process group, not the bare pid. |
| `src/agentos/workers/adapters.py` | Deleted — unused arq scaffolding. |
| `src/agentos/workers/ports.py` | `WorkQueue` and `QueueReceipt` removed; `DispatchStore` stays. |
| `src/agentos/workers/__init__.py` | Exports updated. |
| `src/agentos/workers/chat.py` | arq entry points removed; `build_chat_worker()` added. |
| `src/agentos/workers/publisher.py` | arq pool removed. |
| `src/agentos/conversations/chat.py` | `claimable(limit)` added beside `pending()`. |
| `src/agentos/launcher/internal.py` | `run_worker` drives the new loop. |
| `src/agentos/launcher/probes.py` | `redis_probe` removed. |
| `src/agentos/launcher/environment.py` | Redis removed; `DATABASE_URL` becomes optional and resolvable. |
| `src/agentos/launcher/services.py` | Compose replaced by the embedded cluster. |
| `src/agentos/launcher/supervisor.py` | Owns the cluster's lifecycle inside the `Services` step. |
| `src/agentos/installation/profile.py` | `compose_file` removed. |
| `src/agentos/bootstrap/production.py` | `REDIS_URL` and the Redis dependency probe removed. |
| `pyproject.toml` | `arq` and `redis` dropped. |
| `docker-compose.yml` | Redis service dropped. |
| `.gitignore` | `data/postgres/` ignored. |
| `README.md`, `docs/LAUNCHER.md` | Requirements and the Services description. |

---

### Task 1: Close the portability gaps in the launcher

The launcher is written portably but has only ever run on Windows. Two things need attention before a Mac or a Linux box sees it.

`kill_process_tree` is the last-resort path when `orin stop` finds a supervisor that ignored the stop request. On Windows `taskkill /T` covers the tree. On POSIX it sends `SIGKILL` to the pid alone — but children are created with `start_new_session=True` (`processes.py:160`), so they are in their own session and survive. That is a confirmed defect.

The headless-browser fallback is the second: it already behaves correctly, and gets tests so it keeps doing so.

**Files:**
- Modify: `src/agentos/launcher/state.py:234-249`
- Test: `tests/unit/launcher/test_state.py`, `tests/unit/launcher/test_supervisor_plan.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `kill_process_tree(pid: int) -> bool` — unchanged signature and unchanged Windows behaviour.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/launcher/test_state.py`. The tests monkeypatch `os` so they assert the same behaviour on every platform, including the Windows machine this is written on.

```python
import signal

from agentos.launcher import state as state_module


def test_killing_a_tree_on_posix_targets_the_whole_process_group(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(state_module.os, "name", "posix")
    monkeypatch.setattr(state_module, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(state_module.os, "getpgid", lambda pid: 777, raising=False)
    monkeypatch.setattr(state_module.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False)

    assert state_module.kill_process_tree(4242) is True
    assert killed == [(777, signal.SIGKILL)]


def test_a_process_without_its_own_group_still_gets_killed(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(state_module.os, "name", "posix")
    monkeypatch.setattr(state_module, "process_is_alive", lambda pid: True)

    def no_group(pid: int) -> int:
        raise PermissionError("not permitted")

    monkeypatch.setattr(state_module.os, "getpgid", no_group, raising=False)
    monkeypatch.setattr(state_module.os, "kill", lambda pid, sig: killed.append((pid, sig)), raising=False)

    assert state_module.kill_process_tree(4242) is True
    assert killed == [(4242, signal.SIGKILL)]


def test_an_already_dead_process_is_reported_as_killed(monkeypatch) -> None:
    monkeypatch.setattr(state_module.os, "name", "posix")
    monkeypatch.setattr(state_module, "process_is_alive", lambda pid: False)

    assert state_module.kill_process_tree(4242) is True
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
python -m pytest tests/unit/launcher/test_state.py -q -k "tree or group or dead"
```

Expected: the first two fail. `test_killing_a_tree_on_posix_targets_the_whole_process_group` fails because `killpg` is never called; `test_a_process_without_its_own_group_still_gets_killed` may error on the missing `os.killpg` attribute.

- [ ] **Step 3: Fix the implementation**

Replace the POSIX branch of `kill_process_tree` in `src/agentos/launcher/state.py`:

```python
def kill_process_tree(pid: int) -> bool:
    """Last-resort termination of a recorded launcher that ignored the request."""
    if not process_is_alive(pid):
        return True
    if os.name == "nt":
        from subprocess import DEVNULL, run

        result = run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=DEVNULL, stderr=DEVNULL, check=False)  # noqa: S603, S607
        return result.returncode == 0
    import signal

    # Children are spawned with start_new_session=True, so the launcher leads its
    # own process group. Signalling the group is what makes this a tree kill;
    # signalling the pid alone would leave every child of it running.
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except ProcessLookupError:
        return not process_is_alive(pid)
    except OSError:
        # No group of its own, or not ours to signal. The pid is still worth a try.
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return False
        return True
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
python -m pytest tests/unit/launcher -q
```

Expected: all pass.

- [ ] **Step 5: Write the failing test for the headless browser fallback**

The second portability item: on a Linux box with no display, `webbrowser.open` can return `True` after launching nothing, or raise. Either way the user is left staring at a ready message with no window and no URL they can copy. Add to `tests/unit/launcher/test_supervisor_plan.py`:

```python
def test_a_machine_with_no_browser_is_told_the_url_instead(monkeypatch, capsys) -> None:
    from agentos.launcher import supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module.webbrowser, "open", lambda *args, **kwargs: False)
    printed: list[str] = []

    class Console:
        def detail(self, text: str) -> None:
            printed.append(text)

    supervisor_module.Supervisor._open_browser(
        type("S", (), {"base_url": "http://127.0.0.1:8000", "console": Console()})()
    )

    assert any("http://127.0.0.1:8000" in line for line in printed)


def test_a_browser_that_raises_does_not_take_the_launch_down(monkeypatch) -> None:
    from agentos.launcher import supervisor as supervisor_module

    def explode(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(supervisor_module.webbrowser, "open", explode)
    printed: list[str] = []

    class Console:
        def detail(self, text: str) -> None:
            printed.append(text)

    supervisor_module.Supervisor._open_browser(
        type("S", (), {"base_url": "http://127.0.0.1:8000", "console": Console()})()
    )

    assert printed
```

- [ ] **Step 6: Run them**

```bash
python -m pytest tests/unit/launcher/test_supervisor_plan.py -q -k browser
```

Expected: both pass — `_open_browser` (`supervisor.py:404-411`) already catches the exception and reports the URL. These tests exist so a later refactor cannot quietly remove the only thing standing between a headless Linux user and a dead end. If either fails, fix `_open_browser` so it does what they describe.

- [ ] **Step 7: Commit**

```bash
git add src/agentos/launcher/state.py tests/unit/launcher
git commit -m "fix(launcher): kill the process group, not just the pid, on POSIX"
```

---

### Task 2: Delete the unused arq queue adapter

`RedisArqWorkQueue`, `ArqQueueReceipt`, and the `WorkQueue`/`QueueReceipt` protocols are RFC 801 scaffolding. Nothing in the application constructs them; the only caller is their own unit test. Removing Redis starts by deleting what was never wired, so no one writes a PostgreSQL twin of an interface with no consumers.

**Files:**
- Delete: `src/agentos/workers/adapters.py`, `tests/unit/workers/test_adapters.py`
- Modify: `src/agentos/workers/ports.py`, `src/agentos/workers/__init__.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `agentos.workers` no longer exports `ArqQueueReceipt` or `RedisArqWorkQueue`. `DispatchStore` and `PostgresDispatchStore` are untouched.

- [ ] **Step 1: Confirm nothing else imports them**

```bash
grep -rn "RedisArqWorkQueue\|ArqQueueReceipt\|WorkQueue\|QueueReceipt" src/ tests/ --include=*.py
```

Expected: matches only in `src/agentos/workers/adapters.py`, `src/agentos/workers/ports.py`, `src/agentos/workers/__init__.py`, and `tests/unit/workers/test_adapters.py`. **If any other file appears, stop and re-plan** — something does use it and it needs a PostgreSQL implementation instead of deletion.

- [ ] **Step 2: Delete the files**

```bash
git rm src/agentos/workers/adapters.py tests/unit/workers/test_adapters.py
```

- [ ] **Step 3: Trim the protocols**

Replace the whole of `src/agentos/workers/ports.py` with:

```python
from __future__ import annotations

from typing import Protocol

from .models import DispatchAttempt, WorkItem


class DispatchStore(Protocol):
    def create(self, item: WorkItem) -> DispatchAttempt: ...
    def lease(self, dispatch_attempt_id: str, *, worker_id: str, lease_id: str, fence: int, expected_version: int) -> DispatchAttempt: ...
    def acknowledge(self, dispatch_attempt_id: str, *, lease_id: str, fence: int, expected_version: int) -> DispatchAttempt: ...
```

- [ ] **Step 4: Update the package exports**

Replace the whole of `src/agentos/workers/__init__.py` with:

```python
from .models import DispatchAttempt, DispatchAttemptState, DispatchState, WorkerOperationContext, WorkerPool, WorkItem, WorkKind, destination_pool_for
from .postgres import DispatchConflictError, PostgresDispatchStore

__all__ = ["DispatchAttempt", "DispatchAttemptState", "DispatchConflictError", "DispatchState", "PostgresDispatchStore", "WorkerOperationContext", "WorkerPool", "WorkItem", "WorkKind", "destination_pool_for"]
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest -q tests/unit
```

Expected: all pass, with one fewer test file.

- [ ] **Step 6: Commit**

```bash
git add -A src/agentos/workers tests/unit/workers
git commit -m "refactor(workers): drop the unused arq queue adapter"
```

---

### Task 3: A chat worker that polls PostgreSQL

The worker stops being an arq process and becomes a loop of its own. `ChatWorker.run` — the class that actually executes a turn — does not change at all; only what calls it does.

Two facts make this safe, and the implementer should understand both before writing code. First, `PostgresChatStore.claim` (`conversations/chat.py:308`) is an `UPDATE ... WHERE turn_id = :id AND state IN ('pending','enqueued')`; a second worker racing it gets `rowcount == 0`, `claim` returns `None`, and `ChatWorker.run` returns immediately. Losing that race is ordinary, not an error. Second, the launcher's worker-readiness probe watches `runtime_heartbeats`, so the loop must heartbeat every tick and not only when a turn arrives.

**Files:**
- Create: `src/agentos/workers/loop.py`
- Modify: `src/agentos/conversations/chat.py` (after `pending()` at line 300), `src/agentos/workers/chat.py:250-286`, `src/agentos/launcher/internal.py:53-63`
- Test: `tests/unit/workers/test_loop.py`

**Interfaces:**
- Consumes: `ChatWorker` from `agentos.workers.chat` (unchanged), `PostgresChatStore` from `agentos.conversations.chat`.
- Produces:
  - `PostgresChatStore.claimable(limit: int) -> tuple[str, ...]`
  - `agentos.workers.loop.run_worker_loop(worker, *, stop: threading.Event, poll_seconds: float = 0.25, max_concurrent: int = 8) -> None`
  - `agentos.workers.loop.POLL_SECONDS: float`, `MAX_CONCURRENT_TURNS: int`
  - `agentos.workers.chat.build_chat_worker() -> ChatWorker`

- [ ] **Step 1: Write the failing loop tests**

Create `tests/unit/workers/test_loop.py`:

```python
from __future__ import annotations

import threading

from agentos.workers.loop import run_worker_loop


class FakeStore:
    def __init__(self, *batches: tuple[str, ...]) -> None:
        self._batches = list(batches)
        self.heartbeats: list[str] = []

    def heartbeat(self, component: str) -> None:
        self.heartbeats.append(component)

    def claimable(self, limit: int) -> tuple[str, ...]:
        if not self._batches:
            return ()
        return self._batches.pop(0)[:limit]


class FakeWorker:
    def __init__(self, store: FakeStore, *, claimed: set[str] | None = None) -> None:
        self.store = store
        self.ran: list[str] = []
        self._claimed = claimed if claimed is not None else set()
        self._guard = threading.Lock()

    def run(self, turn_id: str) -> None:
        with self._guard:
            if turn_id in self._claimed:
                # What a real ChatWorker does when store.claim returns None.
                return
            self._claimed.add(turn_id)
            self.ran.append(turn_id)


def _run_until_idle(worker: FakeWorker, *, ticks: int = 4, **kwargs) -> None:
    stop = threading.Event()
    remaining = {"ticks": ticks}
    original = worker.store.claimable

    def counting(limit: int):
        remaining["ticks"] -= 1
        if remaining["ticks"] <= 0:
            stop.set()
        return original(limit)

    worker.store.claimable = counting  # type: ignore[method-assign]
    run_worker_loop(worker, stop=stop, poll_seconds=0.0, **kwargs)


def test_every_claimable_turn_is_run() -> None:
    store = FakeStore(("turn-1", "turn-2"), ("turn-3",))
    worker = FakeWorker(store)

    _run_until_idle(worker)

    assert sorted(worker.ran) == ["turn-1", "turn-2", "turn-3"]


def test_a_turn_another_worker_already_claimed_is_not_run_here() -> None:
    store = FakeStore(("turn-1",))
    worker = FakeWorker(store, claimed={"turn-1"})

    _run_until_idle(worker)

    assert worker.ran == []


def test_the_same_turn_is_never_submitted_twice_while_it_is_running() -> None:
    # The dispatch row stays visible until claim flips it, so consecutive polls
    # see the same turn. Submitting it twice would burn a slot in the pool.
    store = FakeStore(("turn-1",), ("turn-1",), ("turn-1",))
    worker = FakeWorker(store)

    _run_until_idle(worker)

    assert worker.ran == ["turn-1"]


def test_the_worker_heartbeats_on_every_tick_even_with_no_work() -> None:
    store = FakeStore()
    worker = FakeWorker(store)

    _run_until_idle(worker, ticks=3)

    assert store.heartbeats == ["chat-worker", "chat-worker", "chat-worker"]


def test_no_more_turns_run_at_once_than_the_ceiling_allows() -> None:
    store = FakeStore(tuple(f"turn-{index}" for index in range(10)))
    worker = FakeWorker(store)
    observed: list[int] = []
    running = threading.Semaphore(0)

    def blocking_run(turn_id: str) -> None:
        observed.append(len(worker.ran))
        worker.ran.append(turn_id)
        running.acquire()

    worker.run = blocking_run  # type: ignore[method-assign]
    stop = threading.Event()
    thread = threading.Thread(target=run_worker_loop, args=(worker,), kwargs={"stop": stop, "poll_seconds": 0.01, "max_concurrent": 2})
    thread.start()
    try:
        while len(worker.ran) < 2:
            pass
        assert len(worker.ran) == 2
    finally:
        stop.set()
        running.release()
        running.release()
        thread.join(timeout=5)
    assert not thread.is_alive()
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
python -m pytest tests/unit/workers/test_loop.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'agentos.workers.loop'`.

- [ ] **Step 3: Write the loop**

Create `src/agentos/workers/loop.py`:

```python
"""The chat worker's own loop.

Orin's queue is ``conversation_dispatches`` in PostgreSQL, and what stops a turn
from running twice is ``PostgresChatStore.claim`` — one conditional UPDATE. A
broker never provided that guarantee, so removing it costs nothing: what is left
is a poll, a claim, and a ceiling on how many turns run at once.

Losing a claim race is the ordinary outcome of two workers seeing the same row.
It is not logged as a failure.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

_LOGGER = logging.getLogger("agentos.workers.loop")

POLL_SECONDS = 0.25
# The ceiling arq enforced as max_jobs. A turn is mostly waiting on a provider,
# so this bounds memory and database connections rather than CPU.
MAX_CONCURRENT_TURNS = 8


def run_worker_loop(
    worker,
    *,
    stop: threading.Event,
    poll_seconds: float = POLL_SECONDS,
    max_concurrent: int = MAX_CONCURRENT_TURNS,
) -> None:
    """Claim and run turns until ``stop`` is set.

    ``worker`` is a ``ChatWorker``; only ``worker.store`` and ``worker.run`` are
    used, which is what lets this be tested without a database.
    """
    executor = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="orin-turn")
    in_flight: dict[str, Future] = {}
    try:
        while not stop.is_set():
            # Before the claim, not after: the launcher reads this row to decide
            # the worker is ready, and a worker with nothing to do is still up.
            worker.store.heartbeat("chat-worker")
            for turn_id in [turn_id for turn_id, future in in_flight.items() if future.done()]:
                in_flight.pop(turn_id)
            free = max_concurrent - len(in_flight)
            if free > 0:
                for turn_id in worker.store.claimable(free):
                    # A dispatch row stays visible until claim flips its state,
                    # so consecutive polls return a turn already being run here.
                    if turn_id in in_flight:
                        continue
                    in_flight[turn_id] = executor.submit(_run_one, worker, turn_id)
            stop.wait(poll_seconds)
    finally:
        # A turn in flight owns a claimed dispatch row. Dropping it would leave
        # the row for the recovery sweep and the user waiting; let it finish.
        executor.shutdown(wait=True)


def _run_one(worker, turn_id: str) -> None:
    try:
        worker.run(turn_id)
    except Exception:
        # ChatWorker.run already fails the turn for provider errors. Anything
        # reaching here would otherwise vanish into a Future nobody reads.
        _LOGGER.exception("chat turn %s ended in an unhandled error", turn_id)


__all__ = ["MAX_CONCURRENT_TURNS", "POLL_SECONDS", "run_worker_loop"]
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
python -m pytest tests/unit/workers/test_loop.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit the loop**

```bash
git add src/agentos/workers/loop.py tests/unit/workers/test_loop.py
git commit -m "feat(workers): add the postgres poll-claim loop"
```

- [ ] **Step 6: Write the failing test for `claimable`**

Add to `tests/unit/workers/test_postgres_store.py`, or create `tests/unit/conversations/test_claimable.py` if that file has no engine fixture. Use the in-memory pattern the surrounding tests already use; if `tests/unit/workers/test_postgres_store.py` builds a SQLite or fake engine, follow it exactly. Otherwise assert on the compiled statement, which needs no database:

```python
from agentos.conversations.chat import claimable_statement


def test_claimable_only_offers_queued_turns_oldest_first() -> None:
    compiled = str(claimable_statement(5))

    assert "conversation_dispatches" in compiled
    assert "state IN" in compiled.replace("state IN (__[POSTCOMPILE_state_1])", "state IN")
    assert "ORDER BY conversation_dispatches.queued_at" in compiled
    assert "LIMIT" in compiled
```

- [ ] **Step 7: Run it and verify it fails**

```bash
python -m pytest tests/unit/workers/test_postgres_store.py -q -k claimable
```

Expected: `ImportError: cannot import name 'claimable_statement'`.

- [ ] **Step 8: Add `claimable` to the store**

In `src/agentos/conversations/chat.py`, immediately after `pending()` (line 300-301), add the module-level statement builder and the method. Keeping the statement separate is what makes it assertable without a database.

```python
def claimable_statement(limit: int):
    """Turns waiting for a worker, oldest first.

    ``pending`` and ``enqueued`` are both offered: the publisher's transition
    between them is bookkeeping for the recovery sweep, not a gate the worker
    has to wait behind.
    """
    return (
        select(conversation_dispatches.c.turn_id)
        .where(conversation_dispatches.c.state.in_(("pending", "enqueued")))
        .order_by(conversation_dispatches.c.queued_at)
        .limit(limit)
    )
```

And as a method on `PostgresChatStore`, directly below `pending`:

```python
    def claimable(self, limit: int) -> tuple[str, ...]:
        with self._engine.connect() as c: return tuple(c.execute(claimable_statement(limit)).scalars())
```

- [ ] **Step 9: Run it and verify it passes**

```bash
python -m pytest tests/unit -q
```

Expected: all pass.

- [ ] **Step 10: Replace the arq entry points in the worker module**

In `src/agentos/workers/chat.py`, delete the `from arq.connections import RedisSettings` import (line 14) and replace everything from `async def agentos_agent` (line 250) to the end of the file with:

```python
def build_chat_worker() -> ChatWorker:
    """The worker the ``orin internal-service worker`` process runs.

    The API and the worker must sign activity cursors identically, otherwise a
    cursor issued by one is rejected by the other and every stream resyncs.
    """
    settings = ProductionSettings()
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    secret = settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None
    worker = ChatWorker(PostgresChatStore(engine, PostgresAgenticActivityStore(engine, secret or activity_cursor_fallback(engine))))
    # Report on startup, not only when a turn arrives. A worker that has claimed
    # nothing yet is still a worker that is up, and the launcher needs to be able
    # to tell "ready" from "never started" without waiting for a first message.
    worker.store.heartbeat("chat-worker")
    return worker


__all__ = ["ChatWorker", "PROVIDER_BASE_URLS", "build_chat_worker"]
```

Also delete the now-unused `import os` at line 10 if nothing else in the file uses it — check with `grep -n "os\." src/agentos/workers/chat.py` first and leave it if there are hits.

- [ ] **Step 11: Drive the loop from the service verb**

Replace `run_worker` in `src/agentos/launcher/internal.py:53-63`:

```python
def run_worker() -> int:
    """Claims turns from the durable queue and runs the agent loop."""
    import threading

    from agentos.workers.chat import build_chat_worker
    from agentos.workers.loop import run_worker_loop

    stop = threading.Event()
    try:
        run_worker_loop(build_chat_worker(), stop=stop)
    except KeyboardInterrupt:
        stop.set()
    return 0
```

- [ ] **Step 12: Run the full unit suite**

```bash
python -m pytest -q tests/unit
```

Expected: all pass. `tests/unit/workers/test_chat.py` exercises `ChatWorker` directly and must be untouched by this change; if it fails, the class was modified when it should not have been.

- [ ] **Step 13: Commit**

```bash
git add src/agentos/conversations/chat.py src/agentos/workers/chat.py src/agentos/launcher/internal.py tests/unit
git commit -m "feat(workers): run chat turns from the durable queue without arq"
```

---

### Task 4: A publisher with no broker

The publisher keeps every job that mattered — the `pending → enqueued` transition, the unclaimed-turn watchdog, and the stale-turn recovery. It loses only the Redis connection.

**Files:**
- Modify: `src/agentos/workers/publisher.py:11-80`

**Interfaces:**
- Consumes: `PostgresChatStore` (unchanged).
- Produces: `publish_once(store: PostgresChatStore) -> int` — now synchronous. `recover_once` and `main` keep their signatures.

- [ ] **Step 1: Remove the arq imports and pool**

In `src/agentos/workers/publisher.py`, delete `from arq.connections import RedisSettings, create_pool` (line 17) and rewrite `publish_once` and `main`:

```python
def publish_once(store: PostgresChatStore) -> int:
    """Move durable pending turns into the queued state.

    There is no broker to hand them to any more; the worker polls the same
    table. The transition still earns its keep by telling the recovery sweep
    the difference between "just created" and "waiting for a worker".
    """
    store.heartbeat("chat-publisher")
    return sum(1 for turn_id in store.pending() if store.mark_enqueued(turn_id))
```

```python
async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ProductionSettings()
    store = PostgresChatStore(create_engine(settings.DATABASE_URL, pool_pre_ping=True))
    loop = asyncio.get_running_loop()
    next_sweep = loop.time()
    while True:
        try:
            await asyncio.to_thread(publish_once, store)
            if loop.time() >= next_sweep:
                next_sweep = loop.time() + SWEEP_EVERY_SECONDS
                # The sweep touches several rows; keep it off the event loop.
                await asyncio.to_thread(recover_once, store)
        except Exception:
            # A transient database error must not end the loop; the next tick
            # retries, and the durable tables lost nothing.
            _LOGGER.exception("publisher tick failed")
        await asyncio.sleep(POLL_SECONDS)
```

Update the module docstring's first line from `Durable-to-ARQ publisher` to `Dispatch publisher and recovery loop for chat turns.` and drop the sentence `Run this process beside the ARQ worker.`

- [ ] **Step 2: Verify no arq references remain in the workers package**

```bash
grep -rn "arq\|redis" src/agentos/workers/
```

Expected: no output.

- [ ] **Step 3: Run the tests**

```bash
python -m pytest -q tests/unit
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/agentos/workers/publisher.py
git commit -m "refactor(workers): publish without a broker"
```

---

### Task 5: Remove Redis from configuration, probes and readiness

With no code left that connects to Redis, the configuration that describes it is a lie the user can act on. It goes, along with the dependencies.

**Files:**
- Modify: `src/agentos/bootstrap/production.py:67`, `:94-126`, `src/agentos/launcher/probes.py:105-117`, `:182-192`, `src/agentos/launcher/environment.py`, `pyproject.toml`, `docker-compose.yml`
- Test: `tests/unit/api/test_api_asgi.py`, `tests/unit/launcher/test_ports_and_environment.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProductionSettings` no longer has `REDIS_URL`. `DependencyProbe` is `DependencyProbe(postgres: Callable[[], bool])` with `from_settings` and `ready()` unchanged in name.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/api/test_api_asgi.py`:

```python
def test_settings_no_longer_require_a_redis_url() -> None:
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        AGENTOS_ENV="local",
    )

    assert not hasattr(settings, "REDIS_URL")


def test_a_stale_redis_url_in_an_old_config_file_is_ignored_not_fatal() -> None:
    settings = ProductionSettings(
        DATABASE_URL="postgresql+psycopg://user:password@localhost/agentos",
        REDIS_URL="redis://127.0.0.1:6380/0",
    )

    assert settings.DATABASE_URL.endswith("/agentos")
```

The second test matters: an installation that ran before this change has `REDIS_URL` in its `orin.env`, and `model_config` sets `extra="ignore"`, so it must be inert rather than a startup failure.

- [ ] **Step 2: Run it and verify it fails**

```bash
python -m pytest tests/unit/api/test_api_asgi.py -q -k redis
```

Expected: `test_settings_no_longer_require_a_redis_url` fails — the attribute still exists.

- [ ] **Step 3: Strip Redis from the settings and probe**

In `src/agentos/bootstrap/production.py`: delete the `REDIS_URL: str` field (line 67), and replace the `DependencyProbe` dataclass (lines 94-126) with:

```python
@dataclass(frozen=True, slots=True)
class DependencyProbe:
    postgres: Callable[[], bool]

    @classmethod
    def from_settings(cls, settings: ProductionSettings) -> "DependencyProbe":
        def postgres_ready() -> bool:
            try:
                from sqlalchemy import create_engine, text
                engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                engine.dispose()
                return True
            except Exception:
                return False

        return cls(postgres_ready)

    def ready(self) -> bool:
        # The queue is a table in this same database now, so there is nothing
        # else to reach. Checking it twice would not be a second guarantee.
        try:
            return bool(self.postgres())
        except Exception:
            return False
```

- [ ] **Step 4: Delete `redis_probe`**

In `src/agentos/launcher/probes.py`, delete the `redis_probe` function (lines 105-117) and its entry in `__all__`.

- [ ] **Step 5: Strip Redis from the launcher environment**

In `src/agentos/launcher/environment.py`:

- delete `DEFAULT_REDIS_URL` (line 19) and its `__all__` entry;
- delete the `redis_url` property (lines 104-105);
- delete the `f"REDIS_URL={DEFAULT_REDIS_URL}",` line from `write_default_configuration`;
- delete `values.setdefault("REDIS_URL", DEFAULT_REDIS_URL)` (line 134).

- [ ] **Step 6: Fix every test that constructs settings with a Redis URL**

```bash
grep -rn "REDIS_URL" tests/
```

Remove the `REDIS_URL=...` keyword from each `ProductionSettings(...)` call except the one added in Step 1, which asserts it is ignored. In `tests/integration/agentic/test_environment_smoke.py`, drop `_REDIS_URL` and remove it from the `skipif` condition.

- [ ] **Step 7: Drop the dependencies and the compose service**

In `pyproject.toml`, delete the `"arq>=0.26,<1",` and `"redis>=5.2,<7",` lines.

In `docker-compose.yml`, delete the entire `redis:` service block. Replace the file's leading comment with:

```yaml
services:
  # Orin manages its own PostgreSQL. This file is here for contributors who
  # would rather point DATABASE_URL at a container; the launcher never calls it.
```

- [ ] **Step 8: Reinstall the environment and run everything**

```bash
uv pip install -e . --reinstall-package agentos
```

```bash
python -m pytest -q tests/unit
```

Expected: all pass, with no import of `arq` or `redis` anywhere.

```bash
grep -rn "REDIS\|redis\|arq" src/ --include=*.py
```

Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A src pyproject.toml docker-compose.yml tests
git commit -m "refactor: remove Redis from configuration, probes and readiness"
```

---

### Task 6: Resolve and install the PostgreSQL binaries

This task only answers "which build does this machine need, and is it here" — downloading, verifying and unpacking. Starting a cluster is Task 7.

The archives are the `embedded-postgres-binaries` builds, re-published as assets of Orin's own release. Checksums are pinned in a JSON file inside the package, so a development machine verifies against exactly what the release workflow published.

**Files:**
- Create: `src/agentos/services/__init__.py`, `src/agentos/services/postgres_binaries.py`, `src/agentos/services/postgres_checksums.json`, `scripts/refresh-postgres-checksums.py`
- Test: `tests/unit/services/__init__.py`, `tests/unit/services/test_postgres_binaries.py`

**Interfaces:**
- Consumes: `OrinPaths` from `agentos.installation`.
- Produces:
  - `POSTGRES_MAJOR: str = "16"`
  - `UnsupportedPlatform(RuntimeError)`
  - `platform_tag(system: str | None = None, machine: str | None = None) -> str`
  - `asset_name(tag: str) -> str`
  - `install_root(paths: OrinPaths) -> Path`
  - `binary(root: Path, name: str) -> Path`
  - `is_installed(root: Path) -> bool`
  - `expected_sha256(tag: str) -> str`
  - `verify(archive: Path, expected: str) -> None`
  - `unpack(archive: Path, destination: Path) -> Path`
  - `ensure_installed(paths: OrinPaths, *, base_url: str, log) -> Path`
  - `DEFAULT_BASE_URL: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/__init__.py` (empty) and `tests/unit/services/test_postgres_binaries.py`:

```python
from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest

from agentos.installation.paths import OrinPaths
from agentos.services import postgres_binaries as binaries


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run")


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows-amd64"),
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-amd64"),
        ("Linux", "x86_64", "linux-amd64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Linux", "arm64", "linux-arm64"),
        ("Linux", "amd64", "linux-amd64"),
    ],
)
def test_every_supported_platform_resolves(system: str, machine: str, expected: str) -> None:
    assert binaries.platform_tag(system, machine) == expected


def test_an_unsupported_platform_names_itself_instead_of_guessing() -> None:
    with pytest.raises(binaries.UnsupportedPlatform) as error:
        binaries.platform_tag("Linux", "riscv64")

    assert "Linux" in str(error.value)
    assert "riscv64" in str(error.value)


def test_every_supported_platform_has_a_pinned_checksum() -> None:
    for tag in ("windows-amd64", "darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64"):
        assert len(binaries.expected_sha256(tag)) == 64


def test_binaries_live_beside_the_data_not_inside_the_installation(tmp_path: Path) -> None:
    # An update replaces the installation directory. Anything expensive to
    # re-download has to be somewhere else, which here means beside the data.
    root = binaries.install_root(_paths(tmp_path))

    assert root == tmp_path / "runtime" / "postgres" / "16"


def test_a_wrong_checksum_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "postgres-linux-amd64.txz"
    archive.write_bytes(b"not postgres")

    with pytest.raises(binaries.ChecksumMismatch):
        binaries.verify(archive, "0" * 64)


def test_a_matching_checksum_is_accepted(tmp_path: Path) -> None:
    archive = tmp_path / "postgres-linux-amd64.txz"
    archive.write_bytes(b"not postgres")
    digest = hashlib.sha256(b"not postgres").hexdigest()

    binaries.verify(archive, digest)


def test_unpacking_produces_a_usable_bin_directory(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "bin").mkdir(parents=True)
    (staging / "bin" / "initdb").write_text("#!/bin/sh\n", encoding="utf-8")
    (staging / "bin" / "pg_ctl").write_text("#!/bin/sh\n", encoding="utf-8")
    archive = tmp_path / "postgres.txz"
    with tarfile.open(archive, "w:xz") as handle:
        handle.add(staging / "bin", arcname="bin")

    destination = binaries.unpack(archive, tmp_path / "installed")

    assert binaries.is_installed(destination)


def test_an_incomplete_installation_is_not_reported_as_installed(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()

    assert binaries.is_installed(tmp_path) is False
```

Note on `test_binaries_live_outside_the_installation_so_an_update_keeps_them`: replace that loose assertion with the exact path once `install_root` is written in Step 3 — it must equal `tmp_path / "runtime" / "postgres" / "16"` given the `OrinPaths` above, because `install_root` derives from the state root, not from `cache`.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
python -m pytest tests/unit/services/test_postgres_binaries.py -q
```

Expected: `ModuleNotFoundError: No module named 'agentos.services'`.

- [ ] **Step 3: Write the module**

Create `src/agentos/services/__init__.py`:

```python
"""Runtime services Orin manages itself, rather than asking the user to install."""
```

Create `src/agentos/services/postgres_binaries.py`:

```python
"""Which PostgreSQL build this machine needs, and getting it here safely.

Orin ships no database and installs none system-wide. It keeps a private,
pinned PostgreSQL under the user's state directory — outside the installation,
so replacing a version never touches it — and verifies every archive against a
checksum committed to this package before a single byte is unpacked.

Nothing here starts a process. That is ``postgres_server``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from agentos.installation import OrinPaths

POSTGRES_MAJOR = "16"

DEFAULT_BASE_URL = "https://github.com/carlos-edu2367/orin/releases/latest/download"

_TAGS = {
    ("windows", "x86_64"): "windows-amd64",
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "x86_64"): "darwin-amd64",
    ("linux", "x86_64"): "linux-amd64",
    ("linux", "arm64"): "linux-arm64",
}

# One name per architecture, because uname does not agree with itself across
# operating systems: the same CPU is AMD64, amd64 or x86_64 depending on who
# is asked, and arm64 or aarch64 depending on the kernel.
_MACHINES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
}

_CHECKSUMS = Path(__file__).with_name("postgres_checksums.json")


class UnsupportedPlatform(RuntimeError):
    """No PostgreSQL build exists for this operating system and CPU."""


class ChecksumMismatch(RuntimeError):
    """A downloaded archive is not the archive that was published."""


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    import platform as platform_module

    resolved_system = (system or platform_module.system()).strip().lower()
    resolved_machine = _MACHINES.get((machine or platform_module.machine()).strip().lower(), "")
    tag = _TAGS.get((resolved_system, resolved_machine))
    if tag is None:
        raise UnsupportedPlatform(
            f"Orin has no PostgreSQL build for {system or platform_module.system()} "
            f"on {machine or platform_module.machine()}.\n"
            "Supported: Windows x86_64, macOS arm64 and x86_64, Linux x86_64 and arm64.\n"
            "Point DATABASE_URL at a PostgreSQL you run yourself to use Orin here."
        )
    return tag


def asset_name(tag: str) -> str:
    return f"postgres-{POSTGRES_MAJOR}-{tag}.txz"


def install_root(paths: OrinPaths) -> Path:
    """Where the binaries live: beside the data, never inside the installation."""
    return paths.data.parent / "runtime" / "postgres" / POSTGRES_MAJOR


def binary(root: Path, name: str) -> Path:
    import os

    return root / "bin" / (f"{name}.exe" if os.name == "nt" else name)


def is_installed(root: Path) -> bool:
    return binary(root, "initdb").is_file() and binary(root, "pg_ctl").is_file()


def expected_sha256(tag: str) -> str:
    try:
        digests = json.loads(_CHECKSUMS.read_text(encoding="utf-8"))
    except OSError as error:
        raise ChecksumMismatch(f"the pinned PostgreSQL checksums are missing: {error}") from error
    digest = digests.get(tag)
    if not digest:
        raise UnsupportedPlatform(f"no pinned checksum for {tag}")
    return str(digest)


def verify(archive: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        raise ChecksumMismatch(
            f"{archive.name} does not match its published checksum.\n"
            f"  expected {expected}\n  actual   {actual}\n"
            "The download was corrupted or tampered with; nothing was installed."
        )


def unpack(archive: Path, destination: Path) -> Path:
    """Extract into a staging directory, then move into place atomically.

    A half-extracted directory that looks installed is worse than no directory
    at all, because the next start would try to run a missing initdb.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".postgres-", dir=destination.parent))
    try:
        with tarfile.open(archive, "r:xz") as handle:
            handle.extractall(staging, filter="data")
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def ensure_installed(paths: OrinPaths, *, base_url: str = DEFAULT_BASE_URL, log) -> Path:
    """The binaries, downloading them once if this machine has never had them."""
    root = install_root(paths)
    if is_installed(root):
        return root
    import httpx

    tag = platform_tag()
    url = f"{base_url.rstrip('/')}/{asset_name(tag)}"
    log.info("downloading PostgreSQL %s for %s from %s", POSTGRES_MAJOR, tag, url)
    root.parent.mkdir(parents=True, exist_ok=True)
    archive = root.parent / asset_name(tag)
    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for block in response.iter_bytes(1024 * 256):
                    handle.write(block)
        verify(archive, expected_sha256(tag))
        unpack(archive, root)
    finally:
        archive.unlink(missing_ok=True)
    log.info("PostgreSQL installed at %s", root)
    return root


__all__ = [
    "ChecksumMismatch",
    "DEFAULT_BASE_URL",
    "POSTGRES_MAJOR",
    "UnsupportedPlatform",
    "asset_name",
    "binary",
    "ensure_installed",
    "expected_sha256",
    "install_root",
    "is_installed",
    "platform_tag",
    "unpack",
    "verify",
]
```

- [ ] **Step 4: Write the mirroring script and generate the pinned checksums**

The upstream builds are `.jar` files (which are zip archives) each containing one `.txz`. Orin publishes the **inner `.txz`**, so the checksum pinned in the package must be of that inner file — the same bytes the launcher downloads and the same bytes the release publishes. One script produces both, which is what keeps them from ever disagreeing.

Create `scripts/mirror-postgres.py`:

```python
"""Mirror the pinned PostgreSQL builds and pin their checksums.

Upstream ships one jar per platform with a single .txz inside. Orin republishes
that .txz as a release asset, and the launcher verifies it against the checksums
this script writes into the package. Both sides therefore describe the same
bytes by construction, rather than by someone remembering to update two places.

    python scripts/mirror-postgres.py --out dist/postgres

Run it when POSTGRES_MAJOR changes, and in the release workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import httpx

VERSION = "16.4.0"
UPSTREAM = "https://repo1.maven.org/maven2/io/zonky/test/postgres"
ARTIFACTS = {
    "windows-amd64": "embedded-postgres-binaries-windows-amd64",
    "darwin-arm64": "embedded-postgres-binaries-darwin-arm64v8",
    "darwin-amd64": "embedded-postgres-binaries-darwin-amd64",
    "linux-amd64": "embedded-postgres-binaries-linux-amd64",
    "linux-arm64": "embedded-postgres-binaries-linux-arm64v8",
}
ROOT = Path(__file__).resolve().parent.parent
CHECKSUMS = ROOT / "src" / "agentos" / "services" / "postgres_checksums.json"
MAJOR = VERSION.split(".")[0]


def asset_name(tag: str) -> str:
    """Must match agentos.services.postgres_binaries.asset_name."""
    return f"postgres-{MAJOR}-{tag}.txz"


def mirror_one(tag: str, artifact: str, out: Path) -> str:
    url = f"{UPSTREAM}/{artifact}/{VERSION}/{artifact}-{VERSION}.jar"
    print(f"fetching {url}")
    response = httpx.get(url, timeout=600.0, follow_redirects=True)
    response.raise_for_status()
    jar = out / f"{artifact}.jar"
    jar.write_bytes(response.content)
    try:
        with zipfile.ZipFile(jar) as archive:
            members = [name for name in archive.namelist() if name.endswith(".txz")]
            if len(members) != 1:
                raise SystemExit(f"{artifact}: expected exactly one .txz inside the jar, found {members}")
            payload = archive.read(members[0])
    finally:
        jar.unlink(missing_ok=True)
    target = out / asset_name(tag)
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"  {target.name}  {digest}")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "postgres")
    parser.add_argument("--no-pin", action="store_true", help="mirror without rewriting the pinned checksums")
    arguments = parser.parse_args()

    arguments.out.mkdir(parents=True, exist_ok=True)
    digests = {tag: mirror_one(tag, artifact, arguments.out) for tag, artifact in sorted(ARTIFACTS.items())}

    (arguments.out / "SHA256SUMS").write_text(
        "".join(f"{digest}  {asset_name(tag)}\n" for tag, digest in sorted(digests.items())),
        encoding="utf-8",
    )
    if not arguments.no_pin:
        CHECKSUMS.write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"pinned {CHECKSUMS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it once to produce the committed checksum file. It downloads roughly 100 MB and takes a few minutes:

```bash
python scripts/mirror-postgres.py --out dist/postgres
```

Expected: five `postgres-16-*.txz` files plus `SHA256SUMS` in `dist/postgres`, and `src/agentos/services/postgres_checksums.json` containing five 64-character digests. `dist/` is already in `.gitignore`, so only the JSON file is committed.

The release workflow in part two runs this same script with `--no-pin` and uploads the result, so the published assets and the pinned checksums are the same bytes by construction.

- [ ] **Step 5: Run the tests and verify they pass**

```bash
python -m pytest tests/unit/services/test_postgres_binaries.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/services scripts/mirror-postgres.py tests/unit/services
git commit -m "feat(services): resolve, verify and install the pinned PostgreSQL build"
```

---

### Task 7: The cluster's lifecycle

One PostgreSQL cluster, started and stopped by Orin like any other child. Everything here shells out to `initdb`, `pg_ctl` and `createdb` from the directory Task 6 installed.

**Files:**
- Create: `src/agentos/services/postgres_server.py`
- Test: `tests/unit/services/test_postgres_server.py`

**Interfaces:**
- Consumes: `binary`, `is_installed` from `postgres_binaries`.
- Produces:
  - `ClusterError(RuntimeError)`
  - `ClusterSettings(binaries: Path, data_dir: Path, port: int, user: str = "orin", database: str = "orin")` — frozen dataclass
  - `EmbeddedPostgres(settings: ClusterSettings, *, log)` with `dsn: str`, `initialized() -> bool`, `running() -> bool`, `initialize() -> None`, `start() -> None`, `stop() -> None`, `ensure_database() -> None`, `clear_stale_lock() -> bool`, `pid: int | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/test_postgres_server.py`. The tests replace the real binaries with a recording script, so they run on any machine with no PostgreSQL present.

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.services.postgres_server import ClusterError, ClusterSettings, EmbeddedPostgres


class Log:
    def info(self, *args, **kwargs) -> None: ...
    def debug(self, *args, **kwargs) -> None: ...
    def warning(self, *args, **kwargs) -> None: ...


def _cluster(tmp_path: Path, **overrides) -> tuple[EmbeddedPostgres, list[list[str]]]:
    calls: list[list[str]] = []
    settings = ClusterSettings(
        binaries=tmp_path / "pg",
        data_dir=tmp_path / "data" / "postgres",
        port=overrides.get("port", 5433),
    )
    cluster = EmbeddedPostgres(settings, log=Log())
    cluster._run = lambda command, **kwargs: (calls.append(list(command)), 0, "")[1:]  # type: ignore[assignment]
    return cluster, calls


def test_the_dsn_points_at_loopback_and_the_chosen_port(tmp_path: Path) -> None:
    cluster, _ = _cluster(tmp_path, port=5544)

    assert cluster.dsn == "postgresql+psycopg://orin@127.0.0.1:5544/orin"


def test_an_uninitialized_directory_is_detected(tmp_path: Path) -> None:
    cluster, _ = _cluster(tmp_path)

    assert cluster.initialized() is False


def test_initialize_runs_initdb_with_loopback_only_trust(tmp_path: Path) -> None:
    cluster, calls = _cluster(tmp_path)

    cluster.initialize()

    command = calls[0]
    assert command[0].endswith("initdb") or command[0].endswith("initdb.exe")
    assert "-U" in command and "orin" in command
    assert "--auth-host=trust" in command
    assert "--auth-local=trust" in command
    assert "--encoding=UTF8" in command


def test_initializing_twice_is_not_an_error_and_does_not_rerun_initdb(tmp_path: Path) -> None:
    cluster, calls = _cluster(tmp_path)
    cluster.settings.data_dir.mkdir(parents=True)
    (cluster.settings.data_dir / "PG_VERSION").write_text("16\n", encoding="utf-8")

    cluster.initialize()

    assert calls == []


def test_start_binds_loopback_only(tmp_path: Path) -> None:
    cluster, calls = _cluster(tmp_path, port=5544)
    cluster.settings.data_dir.mkdir(parents=True)
    (cluster.settings.data_dir / "PG_VERSION").write_text("16\n", encoding="utf-8")

    cluster.start()

    options = " ".join(" ".join(call) for call in calls)
    assert "listen_addresses=127.0.0.1" in options
    assert "-p 5544" in options or "5544" in options
    assert "0.0.0.0" not in options and "*" not in options


def test_stop_uses_fast_shutdown(tmp_path: Path) -> None:
    cluster, calls = _cluster(tmp_path)

    cluster.stop()

    assert any("-m" in call and "fast" in call for call in calls)


def test_a_stale_lock_from_a_crash_is_cleared(tmp_path: Path) -> None:
    cluster, _ = _cluster(tmp_path)
    cluster.settings.data_dir.mkdir(parents=True)
    lock = cluster.settings.data_dir / "postmaster.pid"
    # A pid that cannot be running: the file records a dead process.
    lock.write_text("999999999\n/somewhere\n", encoding="utf-8")

    assert cluster.clear_stale_lock() is True
    assert not lock.exists()


def test_a_lock_held_by_a_live_process_is_left_alone(tmp_path: Path) -> None:
    cluster, _ = _cluster(tmp_path)
    cluster.settings.data_dir.mkdir(parents=True)
    lock = cluster.settings.data_dir / "postmaster.pid"
    lock.write_text(f"{os.getpid()}\n/somewhere\n", encoding="utf-8")

    assert cluster.clear_stale_lock() is False
    assert lock.exists()


def test_a_failing_initdb_explains_itself(tmp_path: Path) -> None:
    cluster, _ = _cluster(tmp_path)
    cluster._run = lambda command, **kwargs: (1, "initdb: error: directory is not empty")  # type: ignore[assignment]

    with pytest.raises(ClusterError) as error:
        cluster.initialize()

    assert "directory is not empty" in str(error.value)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
python -m pytest tests/unit/services/test_postgres_server.py -q
```

Expected: `ModuleNotFoundError: No module named 'agentos.services.postgres_server'`.

- [ ] **Step 3: Write the module**

Create `src/agentos/services/postgres_server.py`:

```python
"""One PostgreSQL cluster, owned by Orin.

This is the module that replaced ``docker compose up``. It runs the pinned
binaries against a data directory under the user's state root, listening on
loopback and nothing else, and it is stopped the same way every other child is:
in reverse order, before the job object closes.

A crash leaves ``postmaster.pid`` behind. Like the launcher's own instance lock,
the file is not believed on its own — the pid inside it is checked against a
live process before anything is cleared.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .postgres_binaries import binary

STARTUP_TIMEOUT_SECONDS = 60
# initdb writes a whole cluster and fsyncs it. On a cold machine behind an
# antivirus scanner this is the slowest thing Orin ever does on first run.
INITDB_TIMEOUT_SECONDS = 300


class ClusterError(RuntimeError):
    """The cluster could not be prepared, started or stopped."""


@dataclass(frozen=True, slots=True)
class ClusterSettings:
    binaries: Path
    data_dir: Path
    port: int
    user: str = "orin"
    database: str = "orin"


class EmbeddedPostgres:
    def __init__(self, settings: ClusterSettings, *, log) -> None:
        self.settings = settings
        self._log = log

    # -- description ----------------------------------------------------

    @property
    def dsn(self) -> str:
        return f"postgresql+psycopg://{self.settings.user}@127.0.0.1:{self.settings.port}/{self.settings.database}"

    @property
    def pid(self) -> int | None:
        try:
            first = (self.settings.data_dir / "postmaster.pid").read_text(encoding="utf-8").splitlines()[0]
            return int(first.strip())
        except (OSError, IndexError, ValueError):
            return None

    def initialized(self) -> bool:
        return (self.settings.data_dir / "PG_VERSION").is_file()

    def running(self) -> bool:
        code, _ = self._run([str(binary(self.settings.binaries, "pg_ctl")), "status", "-D", str(self.settings.data_dir)])
        return code == 0

    # -- lifecycle ------------------------------------------------------

    def initialize(self) -> None:
        """Create the cluster, once. A second call is a no-op, not a failure."""
        if self.initialized():
            return
        self._refuse_if_elevated()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        code, output = self._run(
            [
                str(binary(self.settings.binaries, "initdb")),
                "-D", str(self.settings.data_dir),
                "-U", self.settings.user,
                "--encoding=UTF8",
                # Loopback-only, and the launcher is the only thing that connects.
                # A password here would be a secret in a file for no gain.
                "--auth-host=trust",
                "--auth-local=trust",
            ],
            timeout=INITDB_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise ClusterError(f"Preparing the Orin database failed.\n  {output.strip()}")

    def start(self) -> None:
        if self.running():
            return
        self.clear_stale_lock()
        code, output = self._run(
            [
                str(binary(self.settings.binaries, "pg_ctl")),
                "start",
                "-D", str(self.settings.data_dir),
                "-w", "-t", str(STARTUP_TIMEOUT_SECONDS),
                "-o", f"-p {self.settings.port} -c listen_addresses=127.0.0.1",
            ],
            timeout=STARTUP_TIMEOUT_SECONDS + 30,
        )
        if code != 0:
            raise ClusterError(f"The Orin database did not start.\n  {output.strip()}")

    def ensure_database(self) -> None:
        """Create the application database if this is a fresh cluster."""
        code, output = self._run(
            [
                str(binary(self.settings.binaries, "createdb")),
                "-h", "127.0.0.1", "-p", str(self.settings.port),
                "-U", self.settings.user, self.settings.database,
            ]
        )
        if code == 0 or "already exists" in output:
            return
        raise ClusterError(f"Creating the Orin database failed.\n  {output.strip()}")

    def stop(self) -> None:
        code, output = self._run(
            [
                str(binary(self.settings.binaries, "pg_ctl")),
                "stop", "-D", str(self.settings.data_dir), "-m", "fast", "-w", "-t", "30",
            ],
            timeout=60,
        )
        if code != 0:
            # Reported, never raised: a database that will not stop cleanly must
            # not turn an otherwise successful shutdown into a failure. The job
            # object is the backstop.
            self._log.warning("the embedded database did not stop cleanly: %s", output.strip())

    def clear_stale_lock(self) -> bool:
        """Remove a ``postmaster.pid`` whose process is gone. ``True`` if cleared."""
        lock = self.settings.data_dir / "postmaster.pid"
        pid = self.pid
        if pid is None or not lock.exists():
            return False
        from agentos.launcher.state import process_is_alive

        if process_is_alive(pid):
            return False
        self._log.warning("clearing a stale postmaster.pid left by pid %s", pid)
        try:
            lock.unlink()
        except OSError:
            return False
        return True

    # -- plumbing -------------------------------------------------------

    def _refuse_if_elevated(self) -> None:
        """initdb refuses to run as Administrator, with an error nobody can act on."""
        if os.name != "nt":
            return
        try:
            import ctypes

            if ctypes.windll.shell32.IsUserAnAdmin():
                raise ClusterError(
                    "Orin cannot prepare its database from an Administrator terminal.\n"
                    "PostgreSQL refuses to create a cluster as an elevated user.\n"
                    "Open a normal terminal and run 'orin' again."
                )
        except AttributeError:  # pragma: no cover - non-Windows ctypes shim
            return

    def _run(self, command: list[str], *, timeout: int = 60) -> tuple[int, str]:
        try:
            result = subprocess.run(  # noqa: S603 - executables come from the verified, pinned installation
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                # PGPASSWORD and friends in the caller's shell must not reach a
                # cluster that authenticates by trust on loopback.
                env={**os.environ, "PGPASSWORD": "", "PGUSER": "", "PGDATABASE": ""},
            )
        except FileNotFoundError as error:
            raise ClusterError(f"{command[0]} is missing from this installation ({error}).") from error
        except subprocess.TimeoutExpired:
            return 1, f"{Path(command[0]).name} did not finish within {timeout}s"
        return result.returncode, (result.stdout or "") + (result.stderr or "")


__all__ = ["ClusterError", "ClusterSettings", "EmbeddedPostgres", "INITDB_TIMEOUT_SECONDS", "STARTUP_TIMEOUT_SECONDS"]
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
python -m pytest tests/unit/services -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/services/postgres_server.py tests/unit/services/test_postgres_server.py
git commit -m "feat(services): manage the embedded PostgreSQL cluster"
```

---

### Task 8: Make the `Services` step start the cluster

The step keeps its contract — "make the datastore reachable, then migrate" — and changes what it does to get there. `DATABASE_URL` becomes the switch between the two modes.

**Files:**
- Modify: `src/agentos/launcher/environment.py`, `src/agentos/launcher/services.py`, `src/agentos/launcher/supervisor.py:143-151`, `:345-356`, `src/agentos/installation/profile.py:73-84`, `.gitignore`
- Test: `tests/unit/launcher/test_ports_and_environment.py`, `tests/unit/launcher/test_supervisor_plan.py`

**Interfaces:**
- Consumes: `EmbeddedPostgres`, `ClusterSettings` (Task 7); `ensure_installed`, `install_root` (Task 6).
- Produces:
  - `RuntimeEnvironment.database_url: str | None` (was `str`)
  - `RuntimeEnvironment.external_database: bool`
  - `RuntimeEnvironment.with_database_url(dsn: str) -> RuntimeEnvironment`
  - `agentos.launcher.services.ensure_datastores(environment, profile, paths, *, log, cluster=None) -> DatastoreStatus`
  - `DatastoreStatus(postgres: ProbeResult, dsn: str, cluster: EmbeddedPostgres | None)`

- [ ] **Step 1: Write the failing environment tests**

Add to `tests/unit/launcher/test_ports_and_environment.py`:

```python
def test_a_generated_configuration_does_not_pin_a_database(tmp_path: Path) -> None:
    path = write_default_configuration(tmp_path / "orin.env")
    content = path.read_text(encoding="utf-8")

    assert "REDIS_URL" not in content
    assert "DATABASE_URL=" not in content
    assert "AGENTOS_PROVIDER_ENCRYPTION_KEY=" in content


def test_an_unset_database_url_means_the_embedded_cluster(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    environment = load_environment(_paths(tmp_path), _profile(tmp_path))

    assert environment.external_database is False
    assert environment.database_url is None


def test_a_configured_database_url_is_honoured_as_external(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://me@db.example:5432/mine")
    environment = load_environment(_paths(tmp_path), _profile(tmp_path))

    assert environment.external_database is True
    assert environment.database_url == "postgresql+psycopg://me@db.example:5432/mine"


def test_the_resolved_cluster_url_reaches_every_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    environment = load_environment(_paths(tmp_path), _profile(tmp_path))

    resolved = environment.with_database_url("postgresql+psycopg://orin@127.0.0.1:5433/orin")

    assert resolved.for_port(8000)["DATABASE_URL"] == "postgresql+psycopg://orin@127.0.0.1:5433/orin"
    assert resolved.database_url == "postgresql+psycopg://orin@127.0.0.1:5433/orin"
```

- [ ] **Step 2: Run them and verify they fail**

```bash
python -m pytest tests/unit/launcher/test_ports_and_environment.py -q
```

Expected: failures on the missing `external_database`, `with_database_url`, and on `DATABASE_URL` still being defaulted.

- [ ] **Step 3: Rework the environment**

In `src/agentos/launcher/environment.py`:

Delete `DEFAULT_DATABASE_URL` usage as a default (keep the constant only if another module imports it — check with `grep -rn "DEFAULT_DATABASE_URL" src/ tests/`; if the only hits are this file, delete the constant too). Then:

```python
@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    """Everything a child process needs to know, already resolved."""

    values: dict[str, str]
    sources: tuple[Path, ...]
    created_configuration: Path | None
    external_database: bool = False

    @property
    def database_url(self) -> str | None:
        """The database, when one is known.

        ``None`` until the launcher starts the embedded cluster and learns which
        port it took. Nothing downstream may assume a value before the Services
        step has run.
        """
        return self.values.get("DATABASE_URL") or None

    def with_database_url(self, dsn: str) -> "RuntimeEnvironment":
        return RuntimeEnvironment({**self.values, "DATABASE_URL": dsn}, self.sources, self.created_configuration, self.external_database)

    def for_port(self, port: int) -> dict[str, str]:
        return {**self.values, "ORIN_BACKEND_PORT": str(port), "ORIN_BACKEND_HOST": "127.0.0.1"}

    def describe(self) -> str:
        return "\n".join(f"{name}={redact(name, value)}" for name, value in sorted(self.values.items()) if name.startswith(("DATABASE", "AGENTOS", "ORIN", "WEB", "LOCALHOST")))
```

In `load_environment`, replace the `setdefault` block with:

```python
    external = bool(values.get("DATABASE_URL", "").strip())
    values.setdefault("AGENTOS_ENV", "local")
    values.setdefault("LOCALHOST_TRUST_ENABLED", "true")
```

and return `RuntimeEnvironment(values, files, created, external)`.

In `write_default_configuration`, delete the `DATABASE_URL` and `REDIS_URL` lines and add the explanation:

```python
                "# Orin runs its own PostgreSQL. Set DATABASE_URL to point at a",
                "# database you manage yourself instead:",
                "#   DATABASE_URL=postgresql+psycopg://user@host:5432/orin",
```

- [ ] **Step 4: Rewrite the services module**

Replace the whole of `src/agentos/launcher/services.py`:

```python
"""The database Orin depends on, and the schema it expects to find in it.

Orin manages PostgreSQL itself. This module is where that happens: install the
pinned binaries if this machine has never had them, prepare the cluster on a
first run, start it on loopback, and migrate.

Setting ``DATABASE_URL`` opts out entirely. Then Orin starts nothing and only
checks that the database named there answers, which is what keeps Docker, a
system PostgreSQL and a remote instance all working with no special cases.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.services.postgres_binaries import ChecksumMismatch, UnsupportedPlatform, ensure_installed
from agentos.services.postgres_server import ClusterError, ClusterSettings, EmbeddedPostgres

from .environment import RuntimeEnvironment
from .ports import select_port
from .probes import ProbeResult, postgres_probe, wait_until

DEFAULT_CLUSTER_PORT = 5433


class ServicesUnavailable(RuntimeError):
    """The database could not be reached, explained in terms a user can act on."""


@dataclass(frozen=True, slots=True)
class DatastoreStatus:
    postgres: ProbeResult
    dsn: str
    cluster: EmbeddedPostgres | None

    @property
    def ready(self) -> bool:
        return bool(self.postgres)


def ensure_datastores(
    environment: RuntimeEnvironment,
    profile: RuntimeProfile,
    paths: OrinPaths,
    *,
    log,
) -> DatastoreStatus:
    """Make PostgreSQL reachable, or explain precisely why it is not."""
    if environment.external_database:
        dsn = environment.database_url or ""
        log.info("using the configured database")
        status = wait_until(lambda: postgres_probe(dsn), timeout=15.0, interval=0.5)
        if not status:
            raise ServicesUnavailable(
                "Orin could not reach the database in DATABASE_URL.\n"
                f"  {status.detail}\n"
                "Start it, or unset DATABASE_URL to let Orin run its own."
            )
        return DatastoreStatus(status, dsn, None)

    try:
        binaries = ensure_installed(paths, log=log)
    except (UnsupportedPlatform, ChecksumMismatch) as error:
        raise ServicesUnavailable(str(error)) from error
    except Exception as error:
        raise ServicesUnavailable(
            "Orin could not download PostgreSQL.\n"
            f"  {error}\n"
            "Check your connection and try again, or set DATABASE_URL to a database you already run."
        ) from error

    cluster = EmbeddedPostgres(
        ClusterSettings(
            binaries=binaries,
            data_dir=paths.data / "postgres",
            port=select_port(DEFAULT_CLUSTER_PORT).port,
        ),
        log=log,
    )
    try:
        first_run = not cluster.initialized()
        if first_run:
            log.info("preparing the database for the first time")
        cluster.initialize()
        cluster.start()
        cluster.ensure_database()
    except ClusterError as error:
        raise ServicesUnavailable(str(error)) from error

    status = wait_until(lambda: postgres_probe(cluster.dsn), timeout=30.0, interval=0.5)
    if not status:
        raise ServicesUnavailable(f"The Orin database started but did not answer.\n  {status.detail}")
    return DatastoreStatus(status, cluster.dsn, cluster)


def apply_migrations(dsn: str, profile: RuntimeProfile, *, log) -> None:
    """Bring the schema to head.

    Configured in code rather than from ``alembic.ini`` so that the scripts are
    found inside the package, wherever the process happens to be running.
    """
    from alembic import command
    from alembic.config import Config

    migrations = profile.migrations
    if not (migrations / "env.py").is_file():
        raise ServicesUnavailable(
            f"Database migrations are missing from this installation (expected {migrations})."
        )
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", dsn)
    log.info("applying migrations from %s", migrations)
    command.upgrade(config, "head")


__all__ = [
    "DEFAULT_CLUSTER_PORT",
    "DatastoreStatus",
    "ServicesUnavailable",
    "apply_migrations",
    "ensure_datastores",
]
```

- [ ] **Step 5: Wire it into the supervisor**

In `src/agentos/launcher/supervisor.py`, add a field beside the other `init=False` fields (after line 78):

```python
    cluster: object | None = field(default=None, init=False)
```

Replace `_step_services` (lines 143-151):

```python
    def _step_services(self) -> None:
        assert self.environment is not None
        try:
            status = ensure_datastores(self.environment, self.profile, self.paths, log=self.log)
            # Every child is told which database to use; none of them resolves it.
            self.environment = self.environment.with_database_url(status.dsn)
            self.cluster = status.cluster
            apply_migrations(status.dsn, self.profile, log=self.log)
        except Exception as error:
            self._stop_cluster()
            self.console.failed("Services")
            raise StartupFailed(str(error)) from error
        self.console.step("Services")
```

Add the stop helper beside `_stop_children`:

```python
    def _stop_cluster(self) -> None:
        """Stop the database Orin started. One it did not start is left alone."""
        cluster, self.cluster = self.cluster, None
        if cluster is None:
            return
        try:
            cluster.stop()
        except Exception:
            self.log.exception("stopping the embedded database failed")
```

Call it in `shutdown`, after `_stop_children()` and before `self.group.terminate_all()`:

```python
        self._stop_cluster()
```

And in `_rollback`, after `self._stop_children()`:

```python
        self._stop_cluster()
```

Note `_rollback`'s early return when there are no children — restructure it so the cluster is stopped in both branches:

```python
    def _rollback(self) -> None:
        """Undo a partial startup, newest first."""
        if self.children:
            self.console.stopping()
            self._stop_children()
        self._stop_cluster()
        self.group.close()
```

Finally, update the import at line 30:

```python
from .services import apply_migrations, ensure_datastores
```

(unchanged text, but confirm `ServicesUnavailable` is not imported anywhere that no longer exists).

- [ ] **Step 6: Remove the compose seam from the profile**

Delete the `compose_file` property from `src/agentos/installation/profile.py` (lines 73-84). Verify nothing else uses it:

```bash
grep -rn "compose_file" src/ tests/
```

Expected: no output. If `tests/unit/launcher/test_paths_and_profile.py` asserts on it, delete that test — the seam it described no longer exists.

- [ ] **Step 7: Ignore the cluster directory**

Add to `.gitignore`, under the "Local runtime state" comment. In a development checkout `paths.data` is `<repo>/data`, so both the cluster and the downloaded binaries land inside the repository and neither belongs in git:

```
data/postgres/
runtime/
```

- [ ] **Step 8: Run the unit suite**

```bash
python -m pytest -q tests/unit
```

Expected: all pass. Fix any test in `tests/unit/launcher/test_supervisor_plan.py` that calls `ensure_datastores` with the old signature.

- [ ] **Step 9: Start Orin for real**

This is the first end-to-end proof. Stop any running stack and remove the old configuration's database pin first:

```bash
docker compose down
```

Then confirm `.env.local` has no `DATABASE_URL` or `REDIS_URL` line (comment them out rather than deleting, so the previous cluster stays reachable if it is needed), and run:

```bash
orin --no-browser -v
```

Expected console output, with a first-run pause of up to a minute at Services:

```text
  ORIN

  ✓ Services
  ✓ Backend
  ✓ Workers
  ✓ Frontend

  Orin is ready
  http://127.0.0.1:8000
```

Then, in a second terminal:

```bash
orin status
```

Expected: `Orin is running.` and exit code 0.

Send a message through the interface and confirm the turn completes. That exercises the new worker loop against the new cluster, which is the whole point of this plan.

```bash
orin stop
```

Expected: `Orin stopped.` Then confirm no PostgreSQL process survived:

```bash
orin status
```

Expected: `Orin is not running.`, exit code 3.

- [ ] **Step 10: Commit**

```bash
git add -A src .gitignore tests
git commit -m "feat(launcher): run an embedded PostgreSQL instead of Docker Compose"
```

---

### Task 9: Tell the truth in the documentation

The README still asks for Docker, Node and a hand-generated encryption key. Every line of that is now wrong.

**Files:**
- Modify: `README.md:32-37`, `:39-59`, `:99-108`, `:218-235`, `docs/LAUNCHER.md:44-51`, `:238-242`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code depends on.

- [ ] **Step 1: Update the requirements**

Replace the `## Requirements` list in `README.md`:

```markdown
## Requirements

- An API key for at least one provider (OpenRouter, OpenAI, or Anthropic)

Orin brings its own PostgreSQL and runs it on loopback. Set `DATABASE_URL` if you
would rather point it at a database you manage yourself.
```

For the "Start it" section, keep the development flow (this plan does not build the installer yet) but drop the Redis and encryption-key steps, which the launcher now handles on first run:

```markdown
## Start it

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.\scripts\install-orin.ps1
```

Open a new terminal — any terminal, any directory — and run:

```powershell
orin
```

The first run prepares a PostgreSQL cluster under your user directory and writes
a configuration file with a freshly generated key for encrypting provider
credentials. It takes about a minute; every run after that is seconds.
```

- [ ] **Step 2: Fix the three-processes table**

In `README.md`, replace the publisher and worker rows:

| Process | Responsibility |
| --- | --- |
| `agentos.workers.publisher` | Moves durable pending turns into the queued state and sweeps lost ones. |
| `agentos.workers.loop` | Claims a queued turn, runs the agent loop, calls the provider, executes tools. |

And replace the sentence about Redis in the paragraph below it with: "The queue is `conversation_dispatches` in PostgreSQL, and a turn is claimed with a single conditional `UPDATE`, so two workers can never run the same turn."

- [ ] **Step 3: Fix the test instructions**

In `README.md`, the integration test block sets `AGENTOS_REDIS_URL`. Replace it with:

```powershell
orin stop
$env:AGENTOS_TEST_POSTGRES_DSN='postgresql+psycopg://orin@127.0.0.1:5433/orin'
$env:AGENTOS_POSTGRES_URL=$env:AGENTOS_TEST_POSTGRES_DSN
python -m pytest -q tests/integration
```

- [ ] **Step 4: Fix the launcher document**

In `docs/LAUNCHER.md`, the startup table's `Services` row becomes:

| Services | PostgreSQL, prepared on first run, plus schema migrations | `SELECT 1` succeeds |

and delete the sentence about Redis answering `PING`. In the final section, replace the closing paragraph about Docker Compose with:

```markdown
The datastore is no longer an external dependency. Orin installs a pinned
PostgreSQL under your user directory, starts it on loopback, and stops it with
everything else. `DATABASE_URL` opts out and points Orin at a database you run.
```

- [ ] **Step 5: Verify no stale instruction survives**

```bash
grep -rni "redis\|docker" README.md docs/LAUNCHER.md
```

Expected: only the deliberate mentions — `DATABASE_URL` alternatives, and the note that `docker-compose.yml` remains a contributor convenience.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/LAUNCHER.md
git commit -m "docs: Orin no longer needs Docker or Redis"
```

---

## Definition of done

- [ ] `python -m pytest -q tests/unit` passes.
- [ ] `grep -rn "arq\|redis" src/ --include=*.py` returns nothing.
- [ ] `orin` starts on a machine with Docker Desktop stopped, completes a chat turn, and `orin stop` leaves no PostgreSQL process behind.
- [ ] `DATABASE_URL` pointing at an external PostgreSQL still works, and Orin starts no cluster in that mode.
- [ ] README and `docs/LAUNCHER.md` describe what the software now does.
