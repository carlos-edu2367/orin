"""W4 review fixes for file history: a rewind is two-phase, so a blocked rewind changes nothing (R10), and a write
over a file too large to back up says so (R10). Recording is done after validation and survives concurrent
reads (R20)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode import file_history as file_history_module
from agentos.code_mode.file_history import FileHistory


@pytest.fixture()
def small_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    """A backup limit small enough to test with; the module reads it at call time."""
    limit = 1024
    monkeypatch.setattr(file_history_module, "MAX_BLOB_BYTES", limit)
    return limit


def _setup(tmp_path: Path) -> tuple[FileHistory, Path]:
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    return FileHistory(tmp_path / "history", "chat_r10"), work


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _message_ids(history: FileHistory) -> list[str]:
    return [item["message_id"] for item in history.messages()]


# -- R10: a rewind is two-phase -------------------------------------------------

def test_a_created_file_that_grew_past_the_limit_blocks_the_rewind(tmp_path: Path, small_limit: int) -> None:
    history, work = _setup(tmp_path)
    created, edited = work / "data.csv", work / "report.txt"
    history.snapshot_before_write("m1", created, turn_id="t1")
    _write(created, b"header\n")  # the turn's write
    history.snapshot_before_write("m1", edited, turn_id="t1")
    _write(edited, b"v0\n")
    _write(edited, b"v1 by the turn\n")
    big = b"precious row\n" * (small_limit // 10 + 10)
    _write(created, big)  # later, the person's own data, past the limit
    _write(edited, big)

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is True and result["restored"] == [] and result["deleted"] == []
    assert {item["path"] for item in result["skipped"]} == {str(created), str(edited)}
    assert {item["reason"] for item in result["skipped"]} == {"its current content is too large to keep an undo copy"}
    assert created.read_bytes() == big and edited.read_bytes() == big  # nothing was destroyed
    assert _message_ids(history) == ["m1"]  # no undo snapshot was saved for the blocked rewind


def test_an_undo_copy_that_cannot_be_written_blocks_the_rewind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0 before the turn\n")
    history.snapshot_before_write("m1", a, turn_id="t1")
    _write(a, b"v1 the turn wrote this\n")
    _write(a, b"v2 the person's later work, not saved anywhere else\n")
    real_write = file_history_module._atomic_write

    def failing_for_blobs(path: Path, data: bytes) -> None:
        if path.parent.name == "blobs":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(file_history_module, "_atomic_write", failing_for_blobs)

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is True and result["restored"] == []
    assert result["skipped"][0]["path"] == str(a)
    assert "could not be copied for undo" in result["skipped"][0]["reason"]
    assert a.read_bytes() == b"v2 the person's later work, not saved anywhere else\n"
    assert _message_ids(history) == ["m1"]


def test_a_missing_backup_blocks_every_file_not_only_the_broken_one(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    api, client, new = work / "api.py", work / "client.py", work / "helpers.py"
    _write(api, b"def get(): return 1\n")
    _write(client, b"from api import get\n")
    for path in (api, client, new):
        history.snapshot_before_write("m1", path, turn_id="t1")
    _write(api, b"def fetch(): return 1\n")
    _write(client, b"from api import fetch\nfrom helpers import h\n")
    _write(new, b"def h(): pass\n")
    import json

    manifest = json.loads((history.base / "messages" / "m1.json").read_text(encoding="utf-8"))
    client_entry = next(entry for entry in manifest["files"].values() if entry["path"].endswith("client.py"))
    (history.base / "blobs" / client_entry["sha256"]).unlink()  # one backup lost

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is True and result["restored"] == [] and result["deleted"] == []
    assert result["skipped"] == [{"path": str(client), "reason": "its backup is missing"}]
    assert api.read_bytes() == b"def fetch(): return 1\n"  # the other files are as the turn left them
    assert client.read_bytes() == b"from api import fetch\nfrom helpers import h\n"
    assert new.read_bytes() == b"def h(): pass\n"
    assert _message_ids(history) == ["m1"]


def test_a_successful_rewind_is_not_blocked(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0\n")
    history.snapshot_before_write("m1", a, turn_id="t1")
    _write(a, b"v1\n")

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is False and result["restored"] == [str(a)] and result["undo_id"]
    assert a.read_bytes() == b"v0\n"


# -- R10: a Write over a file that cannot be backed up says so -------------------------

def test_a_write_over_a_file_too_large_to_back_up_says_it_cannot_be_rewound(tmp_path: Path, small_limit: int) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    dataset = workspace.root / "dataset.csv"
    _write(dataset, b"id,value\n" + b"1,2\n" * (small_limit // 4 + 10))
    history = FileHistory(tmp_path / "history", "c1")
    tools = AgentToolset(workspace, file_history=history, user_message_id="msg_1", turn_id="t1")

    assert tools.invoke("Read", {"file_path": "dataset.csv"}).status == "succeeded"
    outcome = tools.invoke("Write", {"file_path": "dataset.csv", "content": "id,value\n"})

    assert outcome.status == "succeeded", outcome.content
    assert "this file cannot be rewound" in str(outcome.content)


# -- R12: a working directory or a folder under it replaced by a junction ---------------

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
REPLACED = "its directory was replaced after the snapshot"
ROOT_REPLACED = "its working directory was replaced after the snapshot"


@windows_only
def test_an_added_folder_replaced_by_a_junction_is_not_written_through(tmp_path: Path) -> None:
    import _winapi

    history = FileHistory(tmp_path / "history", "chat_r12")
    extra = tmp_path / "extra"
    kept, created = extra / "deep" / "f.txt", extra / "sub" / "important.docx"
    _write(kept, b"before\n")
    history.snapshot_before_write("m1", kept, turn_id="t1")
    history.snapshot_before_write("m1", created, turn_id="t1")
    _write(created, b"turn draft")
    _write(kept, b"after\n")
    shutil.rmtree(extra)
    victim = tmp_path / "victim"
    _write(victim / "sub" / "important.docx", b"VICTIM DATA")
    _winapi.CreateJunction(str(victim), str(extra))

    result = history.rewind("m1", roots=[extra])

    assert result["blocked"] is True and result["restored"] == [] and result["deleted"] == []
    assert {item["reason"] for item in result["skipped"]} == {ROOT_REPLACED}
    assert (victim / "sub" / "important.docx").read_bytes() == b"VICTIM DATA"
    assert not (victim / "deep").exists()


@windows_only
def test_a_workspace_root_replaced_by_a_junction_is_not_written_through(tmp_path: Path) -> None:
    import _winapi

    history = FileHistory(tmp_path / "history", "chat_r12b")
    work = tmp_path / "work"
    edited = work / "src" / "a.py"
    _write(edited, b"v0\n")
    history.snapshot_before_write("m1", edited, turn_id="t1")
    _write(edited, b"v1\n")
    shutil.rmtree(work)
    victim = tmp_path / "victim"
    _write(victim / "src" / "a.py", b"VICTIM\n")
    _winapi.CreateJunction(str(victim), str(work))

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is True and result["restored"] == []
    assert result["skipped"][0]["reason"] == ROOT_REPLACED
    assert (victim / "src" / "a.py").read_bytes() == b"VICTIM\n"


@windows_only
def test_a_folder_under_the_root_replaced_by_a_junction_inside_the_root_is_refused(tmp_path: Path) -> None:
    import _winapi

    history = FileHistory(tmp_path / "history", "chat_r12c")
    work = tmp_path / "work"
    edited = work / "a" / "b" / "f.txt"
    _write(edited, b"v0\n")
    history.snapshot_before_write("m1", edited, turn_id="t1")
    _write(edited, b"v1\n")
    shutil.rmtree(work / "a")
    other = work / "other" / "b"
    _write(other / "f.txt", b"OTHER\n")
    _winapi.CreateJunction(str(work / "other"), str(work / "a"))  # a link that stays inside the root

    result = history.rewind("m1", roots=[work])

    assert result["blocked"] is True and result["restored"] == []
    assert result["skipped"][0]["reason"] == REPLACED
    assert (other / "f.txt").read_bytes() == b"OTHER\n"


@windows_only
def test_a_working_directory_reached_through_a_junction_above_it_is_refused(tmp_path: Path) -> None:
    import _winapi

    history = FileHistory(tmp_path / "history", "chat_r12d")
    real = tmp_path / "elsewhere" / "work"
    edited = real / "a.py"
    _write(edited, b"v0\n")
    link = tmp_path / "link"  # the recorded path goes through this link
    _winapi.CreateJunction(str(tmp_path / "elsewhere"), str(link))
    recorded = link / "work" / "a.py"
    history.snapshot_before_write("m1", recorded, turn_id="t1")
    _write(recorded, b"v1\n")

    result = history.rewind("m1", roots=[link / "work"])

    assert result["blocked"] is True and result["restored"] == []
    assert result["skipped"][0]["reason"] == ROOT_REPLACED
    assert edited.read_bytes() == b"v1\n"


# -- R20: a recorded change is one that happened, and a manifest is never lost ----------

def _entries(history: FileHistory) -> list[str]:
    return [path for item in history.messages() for path in item["files"]]


def test_a_write_that_fails_leaves_no_history_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = ConversationWorkspace(tmp_path, "c20")
    target = workspace.root / "notes.md"
    _write(target, b"the person's notes\n")
    history = FileHistory(tmp_path / "history", "c20")
    tools = AgentToolset(workspace, file_history=history, user_message_id="msg_1", turn_id="t1")
    assert tools.invoke("Read", {"file_path": "notes.md"}).status == "succeeded"

    def refuse(path: str, content: str):
        raise OSError("the write was refused")

    monkeypatch.setattr(workspace, "write_text", refuse)
    outcome = tools.invoke("Write", {"file_path": "notes.md", "content": "turn draft\n"})

    assert outcome.status != "succeeded"
    assert _entries(history) == []  # the history does not say the file changed


def test_a_failed_notebook_edit_leaves_no_history_entry(tmp_path: Path) -> None:
    import json

    workspace = ConversationWorkspace(tmp_path, "c20b")
    notebook = workspace.root / "analysis.ipynb"
    notebook_json = {"cells": [{"cell_type": "code", "id": "c1", "source": ["x = 1\n"], "metadata": {}, "outputs": [], "execution_count": None}],
                     "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    _write(notebook, json.dumps(notebook_json).encode("utf-8"))
    history = FileHistory(tmp_path / "history", "c20b")
    tools = AgentToolset(workspace, file_history=history, user_message_id="msg_2", turn_id="t2")
    assert tools.invoke("Read", {"file_path": "analysis.ipynb"}).status == "succeeded"

    outcome = tools.invoke("NotebookEdit", {"notebook_path": "analysis.ipynb", "cell_id": "missing", "new_source": "y = 2"})

    assert outcome.status != "succeeded"
    assert _entries(history) == []


def test_an_entry_the_message_already_had_survives_a_later_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = ConversationWorkspace(tmp_path, "c20c")
    target = workspace.root / "a.txt"
    _write(target, b"before the message\n")
    history = FileHistory(tmp_path / "history", "c20c")
    tools = AgentToolset(workspace, file_history=history, user_message_id="msg_3", turn_id="t3")
    assert tools.invoke("Read", {"file_path": "a.txt"}).status == "succeeded"
    assert tools.invoke("Write", {"file_path": "a.txt", "content": "first write\n"}).status == "succeeded"
    monkeypatch.setattr(workspace, "write_text", lambda path, content: (_ for _ in ()).throw(OSError("refused")))

    tools.invoke("Write", {"file_path": "a.txt", "content": "second write\n"})

    assert _entries(history) == [str(target)]
    result = history.rewind("msg_3", roots=[workspace.root])
    assert target.read_bytes() == b"before the message\n" and result["restored"] == [str(target)]


def test_a_sharing_violation_while_saving_a_snapshot_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = FileHistory(tmp_path / "history", "c20d")
    work = tmp_path / "work"
    a = work / "a.txt"
    _write(a, b"v0\n")
    real_write = file_history_module._atomic_write
    calls = {"failed": 0}

    def busy_then_free(path: Path, data: bytes) -> None:
        if path.parent.name == "messages" and calls["failed"] < 2:
            calls["failed"] += 1
            raise PermissionError("another thread holds the manifest")
        real_write(path, data)

    monkeypatch.setattr(file_history_module, "_atomic_write", busy_then_free)

    entry = history.snapshot_before_write("m4", a)

    assert entry is not None and calls["failed"] == 2
    assert [item["message_id"] for item in history.messages()] == ["m4"]
    assert _entries(history) == [str(a)]


def test_an_unreadable_manifest_is_never_overwritten(tmp_path: Path) -> None:
    history = FileHistory(tmp_path / "history", "c20e")
    work = tmp_path / "work"
    earlier, a = work / "earlier.txt", work / "a.txt"
    _write(earlier, b"e0\n")
    history.snapshot_before_write("m2", earlier)
    _write(a, b"v0\n")
    damaged = history.base / "messages" / "m1.json"
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b"{not a manifest")

    entry = history.snapshot_before_write("m1", a)

    assert entry is not None
    assert damaged.read_bytes() == b"{not a manifest"  # the damaged file is kept for recovery
    assert {item["message_id"] for item in history.messages()} == {"m1", "m2"}
    assert str(a) in _entries(history)


def test_a_write_is_refused_when_its_history_cannot_be_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_history_module, "_RETRY_SECONDS", 0.05)
    workspace = ConversationWorkspace(tmp_path, "c20f")
    target = workspace.root / "b.txt"
    _write(target, b"b0\n")
    history = FileHistory(tmp_path / "history", "c20f")
    tools = AgentToolset(workspace, file_history=history, user_message_id="msg_5", turn_id="t5")
    assert tools.invoke("Read", {"file_path": "b.txt"}).status == "succeeded"
    real_write = file_history_module._atomic_write

    def manifest_is_held(path: Path, data: bytes) -> None:
        if path.parent.name == "messages":
            raise PermissionError("another handle holds the manifest")
        real_write(path, data)

    monkeypatch.setattr(file_history_module, "_atomic_write", manifest_is_held)
    outcome = tools.invoke("Edit", {"file_path": "b.txt", "old_string": "b0", "new_string": "b1"})

    assert outcome.status != "succeeded" and "could not be copied to its file history" in str(outcome.content)
    assert target.read_bytes() == b"b0\n"  # nothing changed without its copy
