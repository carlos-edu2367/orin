"""W4 review fixes: a rewind and the turns of one folder never overlap (R11)."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from agentos.code_mode.workspace_lock import WorkspaceBusy, enter_turn, exclusive_folder, exit_turn, folder_key


def test_a_rewind_is_refused_while_a_turn_of_the_folder_runs(tmp_path: Path) -> None:
    key = enter_turn(tmp_path)
    try:
        with pytest.raises(WorkspaceBusy):
            with exclusive_folder(tmp_path):
                pass
    finally:
        exit_turn(key)
    with exclusive_folder(tmp_path):
        pass  # free again once the turn ends


def test_two_turns_of_one_folder_hold_it_until_both_end(tmp_path: Path) -> None:
    first = enter_turn(tmp_path)
    second = enter_turn(tmp_path)
    exit_turn(first)
    with pytest.raises(WorkspaceBusy):
        with exclusive_folder(tmp_path):
            pass
    exit_turn(second)
    with exclusive_folder(tmp_path):
        pass


def test_a_turn_cannot_start_while_a_rewind_holds_the_folder(tmp_path: Path) -> None:
    started = threading.Event()
    key_holder: dict[str, str] = {}

    def start_turn() -> None:
        key_holder["key"] = enter_turn(tmp_path)
        started.set()

    with exclusive_folder(tmp_path):
        thread = threading.Thread(target=start_turn, daemon=True)
        thread.start()
        assert not started.wait(0.3)  # the turn waits for the rewind
        with pytest.raises(WorkspaceBusy):
            with exclusive_folder(tmp_path):  # a second rewind is refused too
                pass
    assert started.wait(5)
    thread.join(5)
    exit_turn(key_holder["key"])


def test_the_folder_key_is_the_same_for_every_spelling_of_the_root(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    assert folder_key(folder) == folder_key(folder / ".") == folder_key(os.path.join(str(folder), "..", folder.name))


def test_a_turn_waits_for_a_rewind_of_its_folder_and_holds_it_while_it_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import insert

    from agentos.agentic.runtime import AgenticRunResult
    from agentos.agentic.workspace import ConversationWorkspace
    from agentos.conversations.chat import PostgresChatStore
    from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
    from agentos.persistence.sqlite import create_local_engine
    from agentos.workers.chat import ChatWorker

    now = datetime.now(UTC)
    engine = create_local_engine(f"sqlite+pysqlite:///{(tmp_path / 'orin.db').as_posix()}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="w1")
    folder = ConversationWorkspace(tmp_path / "workspaces", receipt.conversation_id).root
    seen: dict[str, str] = {}

    class _Runtime:
        def __init__(self, turn: dict[str, object]) -> None:
            self._turn = turn

        def run(self, turn_id: str) -> AgenticRunResult:
            try:
                with exclusive_folder(folder):
                    seen["rewind_during_turn"] = "allowed"
            except WorkspaceBusy:
                seen["rewind_during_turn"] = "refused"
            store.delta(self._turn, "done")
            store.finish(self._turn)
            return AgenticRunResult("completed", 1, 0)

    worker = ChatWorker(store, runtime_factory=lambda turn: _Runtime(turn), workspace_root=str(tmp_path / "workspaces"))
    monkeypatch.setattr(worker, "_project", lambda *args, **kwargs: None)
    with exclusive_folder(folder):  # a rewind holds the folder
        thread = threading.Thread(target=worker.run, args=(receipt.turn_id,), daemon=True)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive() and seen == {}  # the turn waits
    thread.join(20)
    assert not thread.is_alive()
    assert seen == {"rewind_during_turn": "refused"}  # while the turn runs, a rewind is refused


# -- R11 across processes: the desktop app runs the API and the chat worker as separate processes ---------------
# A turn leaves a marker file <hash>.turn.<pid>.<uuid> in the run directory; a rewind leaves <hash>.rewind.
# A marker whose pid is not running is stale and is ignored (and removed when seen).

import hashlib
import os
import subprocess
import sys
from uuid import uuid4

from agentos.installation.paths import orin_paths

STALE_PID = 999999


def _locks() -> Path:
    folder = orin_paths().run / "workspace-locks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _digest(root: Path) -> str:
    return hashlib.sha256(folder_key(root).encode("utf-8")).hexdigest()[:24]


def _gone(pid: int) -> bool:
    """Whether ``pid`` is not running. Checked without signalling on Windows, where os.kill is not a probe."""
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False).stdout
        return str(pid) not in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_a_live_turn_of_another_process_refuses_the_rewind(tmp_path: Path) -> None:
    turn = _locks() / f"{_digest(tmp_path)}.turn.{os.getpid()}.{uuid4().hex}"  # our pid: alive
    turn.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(WorkspaceBusy):
        with exclusive_folder(tmp_path):
            pass
    assert not (_locks() / f"{_digest(tmp_path)}.rewind").exists()  # the refused rewind leaves no marker
    assert turn.exists()  # another process's turn marker is left alone


def test_a_stale_turn_marker_does_not_refuse_the_rewind(tmp_path: Path) -> None:
    assert _gone(STALE_PID)
    turn = _locks() / f"{_digest(tmp_path)}.turn.{STALE_PID}.{uuid4().hex}"
    turn.write_text(str(STALE_PID), encoding="utf-8")
    with exclusive_folder(tmp_path):
        pass
    assert not turn.exists()  # the stale marker is removed when seen


def test_a_turn_waits_for_a_rewind_marker_of_another_process(tmp_path: Path) -> None:
    rewind = _locks() / f"{_digest(tmp_path)}.rewind"
    rewind.write_text(str(os.getpid()), encoding="utf-8")  # our pid: alive
    result: dict[str, object] = {}

    def start() -> None:
        result["token"] = enter_turn(tmp_path)

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(0.5)
    assert thread.is_alive() and "token" not in result  # the turn waits for the rewind
    rewind.unlink()
    thread.join(2)
    assert not thread.is_alive() and result.get("token") is not None
    exit_turn(result["token"])  # type: ignore[arg-type]


def test_a_stale_rewind_marker_is_cleaned_and_does_not_block(tmp_path: Path) -> None:
    assert _gone(STALE_PID)
    rewind = _locks() / f"{_digest(tmp_path)}.rewind"
    rewind.write_text(str(STALE_PID), encoding="utf-8")
    with exclusive_folder(tmp_path):
        pass
    assert not rewind.exists()


_CHILD_TURN = (
    "import sys\n"
    "from pathlib import Path\n"
    "from agentos.code_mode.workspace_lock import enter_turn, exit_turn\n"
    "token = enter_turn(Path(sys.argv[1]))\n"
    "print('READY', flush=True)\n"
    "sys.stdin.read()\n"
    "exit_turn(token)\n"
)


def test_a_turn_in_a_live_child_process_refuses_the_rewind_until_it_ends(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_TURN, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "READY"
        with pytest.raises(WorkspaceBusy):
            with exclusive_folder(tmp_path):
                pass
    finally:
        assert child.stdin is not None
        child.stdin.close()  # the child exits its turn and ends
        child.wait(timeout=30)
    with exclusive_folder(tmp_path):
        pass
