"""W6-R10: a stream-json prompt Orin cannot take gets an error result, and the session keeps reading."""
from __future__ import annotations

import io
import json
from pathlib import Path

from agentos.cli_code.args import CodeOptions
from agentos.cli_code.headless import MAX_PROMPT_CHARS, HeadlessRun
from agentos.cli_code.protocol import Translator


class _Client:
    def post(self, path, body=None, *, timeout=None):
        raise AssertionError("no request may be sent for a refused prompt")

    def get(self, path, **params):
        return {}


def _user(content) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}, "uuid": "u-1"})


def _session(tmp_path: Path, stdin_lines: list[str]) -> tuple[HeadlessRun, io.StringIO, list[str]]:
    options = CodeOptions(input_format="stream-json", output_format="stream-json")
    stdout = io.StringIO()
    run = HeadlessRun(options, client=_Client(), data_dir=tmp_path, cwd=tmp_path,
                      stdin=io.StringIO("".join(line + "\n" for line in stdin_lines)), stdout=stdout, stderr=io.StringIO())
    run.translator = Translator(session_id="s-1", model="fake", partial=False, hook_events=False)
    answered: list[str] = []

    def answer(prompt, *, prompt_uuid=None):
        answered.append(prompt)
        return {}

    run.answer = answer
    return run, stdout, answered


def _results(stdout: io.StringIO) -> list[dict]:
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return [message for message in messages if message.get("type") == "result"]


IMAGE = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]


def test_an_image_prompt_gets_an_error_result_and_the_session_answers_the_next(tmp_path: Path) -> None:
    run, stdout, answered = _session(tmp_path, [_user(IMAGE), _user("hello again")])
    assert run._run_stream_input() == 0
    results = _results(stdout)
    assert len(results) == 1
    assert results[0]["subtype"] == "error_during_execution" and results[0]["is_error"] is True
    assert any("'image'" in error for error in results[0]["errors"])
    assert answered == ["hello again"]


def test_an_overlong_prompt_is_refused_the_same_way(tmp_path: Path) -> None:
    run, stdout, answered = _session(tmp_path, [_user("x" * (MAX_PROMPT_CHARS + 1)), _user("short")])
    assert run._run_stream_input() == 0
    results = _results(stdout)
    assert len(results) == 1 and results[0]["is_error"] is True
    assert str(MAX_PROMPT_CHARS) in results[0]["errors"][0]
    assert answered == ["short"]


def test_a_refused_prompt_waits_in_line_so_it_never_lands_inside_a_turn(tmp_path: Path) -> None:
    run, stdout, _answered = _session(tmp_path, [])
    run._handle_input("stdin", json.loads(_user(IMAGE)))
    assert stdout.getvalue() == ""  # nothing is written while a turn could be running
    assert len(run._pending_prompts) == 1


def test_a_text_prompt_is_still_queued_as_text(tmp_path: Path) -> None:
    run, _stdout, _answered = _session(tmp_path, [])
    run._handle_input("stdin", json.loads(_user("fine")))
    assert run._pending_prompts[0][0] == "fine"
