"""W6 Task 13: a turn's questions for the SDK host travel through files the API answers."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.cli_transcript import TranscriptStore
from agentos.agentic.host_requests import HostRequests
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService


def _channel(tmp_path: Path, emitted: list[dict], **kwargs) -> HostRequests:
    return HostRequests("chat_1", emit=emitted.append, root=tmp_path / "requests",
                        transcript=TranscriptStore("chat_1", root=tmp_path / "transcripts"), poll_seconds=0.01, **kwargs)


def test_a_question_is_announced_and_its_answer_returned(tmp_path: Path) -> None:
    emitted: list[dict] = []
    channel = _channel(tmp_path, emitted)

    def host() -> None:
        while not emitted:
            time.sleep(0.01)
        event = emitted[0]
        body = TranscriptStore("chat_1", root=tmp_path / "transcripts").read(event["transcript_id"])
        assert body == {"host_request": {"kind": "hook_callback", "request": {"callback_id": "hook_0"}}}
        assert HostRequests("chat_1", root=tmp_path / "requests").answer(event["request_id"], {"continue": True})

    threading.Thread(target=host, daemon=True).start()
    assert channel.ask("hook_callback", {"callback_id": "hook_0"}, timeout=5) == {"continue": True}
    assert emitted[0]["kind"] == "hook_callback" and emitted[0]["request_id"].startswith("host_")
    assert list(channel.folder.iterdir()) == []  # nothing left behind


def test_an_unanswered_question_times_out_and_a_cancelled_turn_stops_waiting(tmp_path: Path) -> None:
    emitted: list[dict] = []
    started = time.monotonic()
    assert _channel(tmp_path, emitted).ask("hook_callback", {}, timeout=0.2) is None
    assert time.monotonic() - started < 3
    assert _channel(tmp_path, [], cancelled=lambda: True).ask("hook_callback", {}, timeout=30) is None


def test_answers_are_refused_for_unknown_or_finished_questions(tmp_path: Path) -> None:
    channel = HostRequests("chat_1", root=tmp_path)
    assert channel.answer("host_" + "0" * 32, {"a": 1}) is False
    assert channel.answer("../escape", {"a": 1}) is False
    assert channel.answer("host_" + "0" * 32, "not a mapping") is False


def test_without_a_cli_turn_nothing_is_asked(tmp_path: Path) -> None:
    assert HostRequests("chat_1", root=tmp_path).ask("hook_callback", {}, timeout=1) is None


class _Conversations:
    def get(self, conversation_id, user_id):
        return {"conversation_id": conversation_id, "project_id": None}


def test_only_the_cli_token_answers(tmp_path: Path) -> None:
    from agentos.agentic import host_requests

    emitted: list[dict] = []
    channel = HostRequests("chat_1", emit=emitted.append, transcript=TranscriptStore("chat_1", root=tmp_path), poll_seconds=0.01)
    result: list[object] = []
    worker = threading.Thread(target=lambda: result.append(channel.ask("mcp_message", {"server_name": "s"}, timeout=10)), daemon=True)
    worker.start()
    while not emitted:
        time.sleep(0.01)
    request_id = emitted[0]["request_id"]
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Conversations()))
    client = TestClient(app, client=("127.0.0.1", 40000))
    body = {"response": {"mcp_response": {"jsonrpc": "2.0", "id": 1, "result": {}}}}
    assert client.post(f"/v1/conversations/chat_1/host-requests/{request_id}", json=body, headers={"Idempotency-Key": "a"}).status_code == 403
    ok = client.post(f"/v1/conversations/chat_1/host-requests/{request_id}", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "b"})
    assert ok.status_code == 200
    worker.join(5)
    assert result == [body["response"]]
    again = client.post(f"/v1/conversations/chat_1/host-requests/{request_id}", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "c"})
    assert again.status_code == 404
    assert host_requests.host_requests_root().is_dir()
