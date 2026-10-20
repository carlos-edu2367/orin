"""W6 Task 12: a CLI turn's transcript lives apart from the sanitized activity log."""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.agentic.cli_transcript import RETENTION_SECONDS, TranscriptStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService


def test_entries_are_written_and_read_back(tmp_path: Path) -> None:
    store = TranscriptStore("chat_1", root=tmp_path)
    entry = store.write({"tool_result": "x" * 5000})
    assert entry and entry.startswith("tr_") and store.read(entry) == {"tool_result": "x" * 5000}
    assert TranscriptStore("chat_2", root=tmp_path).read(entry) is None  # another conversation's folder
    for bad in ("../x", "tr_123", "", "tr_" + "g" * 32):
        assert store.read(bad) is None


def test_oversized_entries_are_not_written(tmp_path: Path) -> None:
    assert TranscriptStore("chat_1", root=tmp_path).write({"tool_result": "x" * 300_000}) is None


def test_old_entries_are_pruned_on_write(tmp_path: Path) -> None:
    now = [time.time()]
    store = TranscriptStore("chat_1", root=tmp_path, clock=lambda: now[0])
    old = store.write({"a": 1})
    path = store.folder / f"{old}.json"
    stamp = now[0] - RETENTION_SECONDS - 10
    os.utime(path, (stamp, stamp))
    store.write({"b": 2})
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_entries_are_owner_only(tmp_path: Path) -> None:
    store = TranscriptStore("chat_1", root=tmp_path)
    entry = store.write({"a": 1})
    assert stat.S_IMODE((store.folder / f"{entry}.json").stat().st_mode) == 0o600


class _Conversations:
    def get(self, conversation_id, user_id):
        if conversation_id != "chat_1":
            from agentos.api.gateway import ApplicationNotFoundError

            raise ApplicationNotFoundError(conversation_id)
        return {"conversation_id": "chat_1", "project_id": None}


def test_only_the_cli_token_reads_the_transcript(monkeypatch) -> None:
    entry = TranscriptStore("chat_1").write({"tool_input": {"prompt": "secret plan"}})
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Conversations()))
    client = TestClient(app, client=("127.0.0.1", 40000))
    ok = client.get(f"/v1/conversations/chat_1/transcript/{entry}", headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200 and ok.json() == {"tool_input": {"prompt": "secret plan"}}
    assert client.get(f"/v1/conversations/chat_1/transcript/{entry}").status_code == 403
    assert client.get(f"/v1/conversations/chat_9/transcript/{entry}", headers={"Authorization": "Bearer tok"}).status_code == 404
    assert client.get("/v1/conversations/chat_1/transcript/tr_" + "0" * 32, headers={"Authorization": "Bearer tok"}).status_code == 404
