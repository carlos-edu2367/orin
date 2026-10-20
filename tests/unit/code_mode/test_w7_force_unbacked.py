"""W7 Batch F (W4 force_unbacked): rewinding a file too large for an undo copy, only when the person names it."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.code_mode import file_history as file_history_module
from agentos.code_mode.file_history import TOO_LARGE_NOW, FileHistory


@pytest.fixture()
def small_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    limit = 1024
    monkeypatch.setattr(file_history_module, "MAX_BLOB_BYTES", limit)
    return limit


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _two_grown_files(tmp_path: Path, limit: int) -> tuple[FileHistory, Path, Path, Path, bytes]:
    work = tmp_path / "work"
    history = FileHistory(tmp_path / "history", "chat_unbacked")
    created, edited = work / "data.csv", work / "report.txt"
    history.snapshot_before_write("m1", created, turn_id="t1")
    _write(created, b"header\n")
    _write(edited, b"v0\n")
    history.snapshot_before_write("m1", edited, turn_id="t1")
    _write(edited, b"v1 by the turn\n")
    big = b"row\n" * (limit // 2)
    _write(created, big)
    _write(edited, big)
    return history, work, created, edited, big


def test_naming_only_one_of_two_blocked_files_still_changes_nothing(tmp_path: Path, small_limit: int) -> None:
    history, work, created, edited, big = _two_grown_files(tmp_path, small_limit)
    result = history.rewind("m1", roots=[work], force_unbacked=[str(created)])
    assert result["blocked"] is True
    assert [item["path"] for item in result["skipped"]] == [str(edited)]
    assert created.read_bytes() == big and edited.read_bytes() == big


def test_naming_every_blocked_file_rewinds_them_without_an_undo_copy(tmp_path: Path, small_limit: int) -> None:
    history, work, created, edited, _ = _two_grown_files(tmp_path, small_limit)
    result = history.rewind("m1", roots=[work], force_unbacked=[str(created), str(edited)])
    assert result["blocked"] is False
    assert result["deleted"] == [str(created)] and result["restored"] == [str(edited)]
    assert result["unbacked"] == sorted([str(created), str(edited)])
    assert not created.exists() and edited.read_bytes() == b"v0\n"
    undo = next(item for item in history.messages() if item["message_id"] == result["undo_id"])
    assert not undo["files"]  # nothing could be backed up, and nothing was pretended to be


def test_naming_a_file_never_waives_a_missing_backup(tmp_path: Path, small_limit: int) -> None:
    history, work, _, edited, big = _two_grown_files(tmp_path, small_limit)
    for blob in (tmp_path / "history").rglob("*"):
        if blob.is_file() and "blobs" in blob.parts:
            blob.unlink()
    result = history.rewind("m1", roots=[work], force_unbacked=[str(path) for path in (work / "data.csv", edited)])
    assert result["blocked"] is True
    assert {item["reason"] for item in result["skipped"]} == {"its backup is missing"}
    assert edited.read_bytes() == big


def test_the_waived_reason_is_the_too_large_one() -> None:
    assert TOO_LARGE_NOW == "its current content is too large to keep an undo copy"
