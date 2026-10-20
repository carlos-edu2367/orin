"""W6-R7: the session index survives concurrent `orin code` processes, and an unreadable index is never an empty one."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentos.cli_code import sessions
from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.sessions import SessionIndex

_WRITER = textwrap.dedent('''
    import sys, uuid
    from pathlib import Path
    from agentos.cli_code.sessions import SessionIndex
    data, tag = Path(sys.argv[1]), sys.argv[2]
    index = SessionIndex(data)
    for number in range(40):
        index.record(conversation_id=f"chat_{tag}_{number}", folder=data, session_id=str(uuid.uuid4()), name=f"{tag}-{number}")
''')


def test_four_processes_keep_every_session(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    env = dict(__import__("os").environ)
    processes = [subprocess.Popen([sys.executable, "-c", _WRITER, str(data), f"p{number}"], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for number in range(4)]
    for process in processes:
        _out, err = process.communicate(timeout=300)
        assert process.returncode == 0, err
    kept = SessionIndex(data).load()
    assert len(kept) == 160
    assert len({item.session_id for item in kept}) == 160


def test_a_missing_index_is_an_empty_index(tmp_path: Path) -> None:
    assert SessionIndex(tmp_path).load() == []


@pytest.mark.parametrize("content", ["{not json", "[]", '"text"'], ids=["unparsable", "a-list", "a-string"])
def test_an_unreadable_index_raises_and_is_not_overwritten(tmp_path: Path, content: str) -> None:
    (tmp_path / sessions.INDEX_FILE_NAME).write_text(content, encoding="utf-8")
    index = SessionIndex(tmp_path)
    with pytest.raises(CliRuntimeError):
        index.load()
    with pytest.raises(CliRuntimeError):
        index.record(conversation_id="chat_x", folder=tmp_path)
    assert (tmp_path / sessions.INDEX_FILE_NAME).read_text(encoding="utf-8") == content


def test_an_index_that_cannot_be_read_at_all_raises(tmp_path: Path) -> None:
    (tmp_path / sessions.INDEX_FILE_NAME).mkdir()  # reading a directory is an OSError
    with pytest.raises(CliRuntimeError):
        SessionIndex(tmp_path).load()


def test_a_transient_read_failure_is_retried_before_it_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = SessionIndex(tmp_path)
    index.record(conversation_id="chat_1", folder=tmp_path, session_id="a1b2c3d4-0000-4000-8000-000000000001")
    real = Path.read_text
    attempts: list[int] = []

    def flaky(self, *args, **kwargs):
        if self == index.path:
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError("another process is replacing the index")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert len(index.load()) == 1
    assert len(attempts) == 3


def test_a_record_that_cannot_take_the_lock_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def busy(path, wait_seconds=5.0):
        yield False

    monkeypatch.setattr(sessions, "file_lock", busy)
    with pytest.raises(CliRuntimeError):
        SessionIndex(tmp_path).record(conversation_id="chat_1", folder=tmp_path)
    assert not (tmp_path / sessions.INDEX_FILE_NAME).exists()


def test_record_writes_the_index_as_json(tmp_path: Path) -> None:
    SessionIndex(tmp_path).record(conversation_id="chat_1", folder=tmp_path, name="one")
    raw = json.loads((tmp_path / sessions.INDEX_FILE_NAME).read_text(encoding="utf-8"))
    assert raw["sessions"][0]["conversation_id"] == "chat_1"
