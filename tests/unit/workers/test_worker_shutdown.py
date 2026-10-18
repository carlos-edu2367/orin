from __future__ import annotations

import json
import os
import signal
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from agentos.hooks import session_end
from agentos.launcher import processes
from agentos.launcher.processes import ProcessGroup, SupervisedProcess
from agentos.settings.paths import orin_user_dir
from agentos.workers import publisher

SRC = Path(publisher.__file__).resolve().parents[2]

# A worker process shaped like the real one: publisher.main() with an in-memory store, a registered session whose
# SessionEnd hook writes a marker, and a ready file written once the shutdown handlers are installed.
_CHILD = """
import sys
from pathlib import Path

from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.hooks import session_end
from agentos.persistence.postgres.schema import metadata
from agentos.workers import publisher
from agentos.workers.chat import ChatWorker

READY, WORKSPACE = Path(sys.argv[1]), sys.argv[2]


def build_worker():
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    session_end.register_session("conv-stop", workspace_root=WORKSPACE, user_id="u", trusted=False)
    READY.write_text("ready", encoding="utf-8")
    return ChatWorker(PostgresChatStore(engine))


publisher.create_chat_worker = build_worker
publisher.main()
"""


def test_a_supervised_stop_runs_session_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_end.clear_registry()
    marker = tmp_path / "session-end.txt"
    hook = tmp_path / "hook.py"
    hook.write_text(textwrap.dedent(f"""
        import json, sys
        data = json.loads(sys.stdin.read())
        with open(r"{marker}", "w", encoding="utf-8") as handle:
            handle.write(str(data.get("reason")))
    """), encoding="utf-8")
    settings = orin_user_dir() / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    command = f'"{sys.executable}" "{hook}"'
    settings.write_text(
        json.dumps({"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}]}}),
        encoding="utf-8",
    )
    child = tmp_path / "worker_like.py"
    child.write_text(_CHILD, encoding="utf-8")
    ready = tmp_path / "ready.txt"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(item for item in (str(SRC), environment.get("PYTHONPATH", "")) if item)
    # The production case: the supervisor shares a console with its children, so a Ctrl+Break can reach them.
    monkeypatch.setattr(processes, "_attached_to_console", lambda: True)

    group = ProcessGroup()
    proc = SupervisedProcess("worker", (sys.executable, str(child), str(ready), str(workspace)), tmp_path / "worker.log")
    try:
        proc.start(environment=environment, cwd=tmp_path, group=group)
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert proc.is_running(), f"worker exited before it was ready:\n{proc.tail()}"
            assert time.monotonic() < deadline, f"worker never became ready:\n{proc.tail()}"
            time.sleep(0.1)

        proc.stop(grace=8.0)

        assert proc.process is not None and proc.process.returncode == 0, proc.tail()
        assert marker.read_text(encoding="utf-8") == "other", "SessionEnd did not run on a supervised stop"
    finally:
        group.close()


def test_session_end_runs_before_the_turns_in_flight_are_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class Runner:
        def __init__(self, worker: object) -> None:
            calls.append("runner")

        def shutdown(self, *, wait: bool = True) -> None:
            calls.append(("shutdown", wait))

    def stop_after_one_tick(runner: object, next_sweep: float) -> tuple[bool, float]:
        raise SystemExit(0)

    def end_sessions(reason: str = "other", *, timeout_seconds: float = 5.0) -> int:
        calls.append(("end_sessions", reason, timeout_seconds))
        return 0

    monkeypatch.setattr(publisher, "install_shutdown_handlers", lambda: None)
    monkeypatch.setattr(publisher, "create_chat_worker", lambda: object())
    monkeypatch.setattr(publisher, "ConcurrentTurnRunner", Runner)
    monkeypatch.setattr(publisher, "poll_once", stop_after_one_tick)
    monkeypatch.setattr(session_end, "end_sessions", end_sessions)

    with pytest.raises(SystemExit):
        publisher.main()

    assert calls == ["runner", ("end_sessions", "other", 5.0), ("shutdown", True)]


def test_a_repeated_stop_signal_does_not_cut_the_shutdown_short() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    try:
        publisher.install_shutdown_handlers()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
        assert handler(signal.SIGTERM, None) is None
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_handler_installation_is_a_no_op_off_the_main_thread() -> None:
    before = signal.getsignal(signal.SIGTERM)
    errors: list[BaseException] = []

    def install() -> None:
        try:
            publisher.install_shutdown_handlers()
        except BaseException as exc:  # noqa: BLE001 - the thread must not fail, whatever it raises
            errors.append(exc)

    thread = threading.Thread(target=install)
    thread.start()
    thread.join()
    assert errors == []
    assert signal.getsignal(signal.SIGTERM) is before
