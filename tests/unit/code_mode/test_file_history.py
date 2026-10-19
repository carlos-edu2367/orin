"""W4: per-message file history and a rewind that refuses to write through anything unexpected."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agentos.code_mode.file_history import FileHistory, MAX_BLOB_BYTES


def _setup(tmp_path: Path) -> tuple[FileHistory, Path]:
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    return FileHistory(tmp_path / "history", "chat_1"), work


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_rewind_restores_bytes_and_removes_created_files(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a, new = work / "src" / "a.py", work / "src" / "new.py"
    _write(a, b"original\r\n")
    history.snapshot_before_write("m1", a, turn_id="t1")
    _write(a, b"changed\n")
    history.snapshot_before_write("m1", new, turn_id="t1")
    _write(new, b"created\n")
    result = history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"original\r\n"
    assert not new.exists()
    assert result["restored"] == [str(a)] and result["deleted"] == [str(new)] and result["skipped"] == []


def test_the_first_snapshot_of_a_message_wins(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0")
    history.snapshot_before_write("m1", a)
    _write(a, b"v1")
    history.snapshot_before_write("m1", a)
    _write(a, b"v2")
    history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"v0"


def test_rewinding_to_an_earlier_message_also_undoes_later_ones(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a, b = work / "src" / "a.py", work / "src" / "b.py"
    _write(a, b"a0")
    _write(b, b"b0")
    history.snapshot_before_write("m1", a)
    _write(a, b"a1")
    history.snapshot_before_write("m2", a)
    _write(a, b"a2")
    history.snapshot_before_write("m2", b)
    _write(b, b"b2")
    history.rewind("m2", roots=[work])
    assert a.read_bytes() == b"a1" and b.read_bytes() == b"b0"
    _write(a, b"a3")
    history.snapshot_before_write("m3", a)
    history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"a0" and b.read_bytes() == b"b0"


def test_the_undo_snapshot_puts_the_rewound_state_back(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"before")
    history.snapshot_before_write("m1", a)
    _write(a, b"after")
    result = history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"before"
    history.rewind(result["undo_id"], roots=[work])
    assert a.read_bytes() == b"after"
    kinds = {item["message_id"]: item["kind"] for item in history.messages()}
    assert kinds["m1"] == "message" and kinds[result["undo_id"]] == "undo"


def test_a_corrupted_backup_is_not_written(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"good")
    entry = history.snapshot_before_write("m1", a)
    _write(a, b"edited")
    (history.base / "blobs" / entry["sha256"]).write_bytes(b"tampered")
    result = history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"edited"
    assert result["skipped"][0]["reason"] == "its backup does not match its checksum"


def test_a_path_outside_the_working_directories_is_not_touched(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    outside = tmp_path / "elsewhere.txt"
    _write(outside, b"keep")
    history.snapshot_before_write("m1", outside)
    _write(outside, b"edited")
    result = history.rewind("m1", roots=[work])
    assert outside.read_bytes() == b"edited" and result["skipped"][0]["reason"] == "it is outside the working directories"


def test_a_replaced_directory_is_refused(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0")
    history.snapshot_before_write("m1", a)
    shutil.rmtree(work / "src")
    _write(a, b"planted")  # same path, a different directory now
    result = history.rewind("m1", roots=[work])
    assert a.read_bytes() == b"planted"
    assert result["skipped"][0]["reason"] == "its directory was replaced after the snapshot"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_directory_swapped_for_a_junction_is_refused(tmp_path: Path) -> None:
    import _winapi

    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0")
    history.snapshot_before_write("m1", a)
    target = tmp_path / "victim"
    target.mkdir()
    shutil.rmtree(work / "src")
    _winapi.CreateJunction(str(target), str(work / "src"))
    result = history.rewind("m1", roots=[work])
    assert not (target / "a.py").exists()
    # refused either way: the junction leads outside, and it is not the recorded directory
    assert result["restored"] == [] and len(result["skipped"]) == 1


def test_a_file_replaced_by_a_link_is_refused(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    a = work / "src" / "a.py"
    _write(a, b"v0")
    history.snapshot_before_write("m1", a)
    secret = tmp_path / "secret.txt"
    _write(secret, b"secret")
    a.unlink()
    try:
        os.symlink(secret, a)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symlinks")
    result = history.rewind("m1", roots=[work])
    assert secret.read_bytes() == b"secret"
    assert result["skipped"][0]["reason"] == "it is now a link"


def test_large_and_odd_entries_are_recorded_but_never_restored(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    big = work / "big.bin"
    _write(big, b"x" * (MAX_BLOB_BYTES + 1))
    assert history.snapshot_before_write("m1", big)["too_large"] is True
    assert history.snapshot_before_write("m1", work / "src")["unsupported"] is True
    _write(big, b"small")
    result = history.rewind("m1", roots=[work])
    assert big.read_bytes() == b"small"
    assert {item["reason"] for item in result["skipped"]} == {
        "it was too large to keep a copy", "it was not a regular file when it was recorded",
    }


def test_an_unknown_message_and_odd_ids(tmp_path: Path) -> None:
    history, work = _setup(tmp_path)
    with pytest.raises(LookupError):
        history.rewind("nope", roots=[work])
    a = work / "src" / "a.py"
    _write(a, b"v0")
    assert history.snapshot_before_write("../../escape", a) is not None
    assert all(path.is_relative_to(history.base) for path in history.base.rglob("*"))
    assert FileHistory(tmp_path / "h", "a/b").base != FileHistory(tmp_path / "h", "a_b").base


def test_a_failed_snapshot_never_raises(tmp_path: Path) -> None:
    history = FileHistory(tmp_path / "file-as-root", "chat_1")
    (tmp_path / "file-as-root").write_text("not a directory", encoding="utf-8")
    assert history.snapshot_before_write("m1", tmp_path / "x.txt") is None
