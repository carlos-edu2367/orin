"""W7 (W6-R9, W6-R15): CLI transcripts expire in every conversation's folder, and the CLI's data folders are owner-only."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from agentos.agentic import cli_transcript
from agentos.agentic.cli_transcript import RETENTION_SECONDS, TranscriptStore, sweep_expired
from agentos.installation.api_token import make_private_dir


def test_an_old_entry_of_another_conversation_is_removed(tmp_path: Path) -> None:
    old = TranscriptStore("finished", root=tmp_path)
    entry = old.write({"tool_result": "a secret file"})
    stale = old.folder / f"{entry}.json"
    past = time.time() - RETENTION_SECONDS - 3600
    os.utime(stale, (past, past))
    kept = TranscriptStore("finished-recently", root=tmp_path)
    recent = kept.write({"tool_result": "x"})
    cli_transcript._LAST_SWEEP.clear()
    TranscriptStore("active", root=tmp_path).write({"tool_result": "y"})
    assert not stale.exists() and not old.folder.exists()  # the emptied folder goes too
    assert (kept.folder / f"{recent}.json").exists()


def test_the_sweep_runs_at_most_once_an_hour(tmp_path: Path) -> None:
    folder = tmp_path / "conv"
    folder.mkdir()
    stale = folder / "tr_x.json"
    stale.write_text("{}", encoding="utf-8")
    past = time.time() - RETENTION_SECONDS - 10
    os.utime(stale, (past, past))
    cli_transcript._LAST_SWEEP.clear()
    sweep_expired(tmp_path, clock=lambda: 1_000_000.0 + time.time())  # a first sweep from a clock far ahead
    assert not stale.exists()
    stale.parent.mkdir(exist_ok=True)
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (past, past))
    sweep_expired(tmp_path, clock=lambda: 1_000_000.0 + time.time() + 60)  # a minute later: skipped
    assert stale.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_a_private_folder_and_its_new_files_are_owner_only_on_windows(tmp_path: Path) -> None:
    folder = make_private_dir(tmp_path / "cli-transcripts")
    (folder / "entry.json").write_text("{}", encoding="utf-8")
    for target in (folder, folder / "entry.json"):
        listing = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True).stdout
        entries = [line.strip() for line in listing.splitlines()[:-1] if ":(" in line]
        assert len(entries) == 1 and os.environ["USERNAME"].lower() in entries[0].lower(), listing


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_a_private_folder_is_mode_700_on_posix(tmp_path: Path) -> None:
    folder = make_private_dir(tmp_path / "cli-transcripts")
    assert (folder.stat().st_mode & 0o777) == 0o700
