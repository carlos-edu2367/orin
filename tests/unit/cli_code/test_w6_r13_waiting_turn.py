"""W6-R13: a turn that stops for the person is not a success: an error result, and exit 1 for `-p`."""
from __future__ import annotations

import io
from pathlib import Path

from agentos.cli_code.args import EXIT_ERROR, CodeOptions
from agentos.cli_code.headless import HeadlessRun

WAITING = "the turn is waiting for the person"


class _Client:
    def post(self, path, body=None, *, timeout=None):
        return {}

    def get(self, path, **params):
        return {}


def _run(tmp_path: Path, **options) -> HeadlessRun:
    run = HeadlessRun(CodeOptions(prompt="hi", **options), client=_Client(), data_dir=tmp_path, cwd=tmp_path,
                      stdin=io.StringIO(""), stdout=io.StringIO(), stderr=io.StringIO())
    run.conversation_id = "chat_1"
    from agentos.cli_code.protocol import Translator

    run.translator = Translator(session_id="s-1", model="fake", partial=False, hook_events=False)
    run._send = lambda prompt: "turn_1"  # the turn is queued; its end event is already in the inbox
    run._send_init = lambda: None
    run._start_events = lambda: None
    run.inbox.put(("event", {"turn_id": "turn_1", "event_type": "turn.waiting_user", "payload": {"state": "waiting_user"}}))
    return run


def test_a_waiting_turn_is_an_error_result_for_the_prompt(tmp_path: Path) -> None:
    result = _run(tmp_path).run_turn("hi")
    assert result["subtype"] == "error_during_execution"
    assert result["is_error"] is True
    assert len(result["errors"]) == 1 and result["errors"][0].startswith(WAITING + " (")


def test_a_waiting_turn_exits_one_for_print_mode(tmp_path: Path) -> None:
    run = _run(tmp_path, output_format="json")
    run._resolve_session = lambda: None
    assert run.run() == EXIT_ERROR
    assert '"is_error": true' in run.out._stdout.getvalue()


def test_a_waiting_turn_in_stream_json_is_a_result_the_session_reads_past(tmp_path: Path) -> None:
    run = _run(tmp_path, input_format="stream-json", output_format="stream-json")
    result = run.run_turn("hi")
    assert result["is_error"] is True
    assert "turn.waiting_user" not in run.out._stdout.getvalue()


def test_a_completed_turn_is_still_a_success(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.inbox = type(run.inbox)()
    run.inbox.put(("event", {"turn_id": "turn_1", "event_type": "turn.completed", "payload": {}}))
    result = run.run_turn("hi")
    assert result["subtype"] == "success" and result["is_error"] is False
