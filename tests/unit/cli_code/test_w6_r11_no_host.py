"""W6-R11: a host request in a run with no SDK host is answered at once, and the run refuses what needs a host."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from agentos.cli_code.args import CliUsageError, CodeOptions
from agentos.cli_code.headless import HeadlessRun
from agentos.cli_code.turn_options import build_turn_options

SDK_SERVER = '{"mcpServers": {"calc": {"type": "sdk", "name": "calc"}}}'
CALLBACK_HOOKS = '{"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "callback", "callback_id": "h1"}]}]}}'


class _Client:
    def __init__(self, entry: dict) -> None:
        self.entry = entry
        self.posts: list[tuple[str, dict]] = []

    def get(self, path, **params):
        return {"host_request": self.entry} if "transcript" in path else {}

    def post(self, path, body=None, *, timeout=None):
        self.posts.append((path, body or {}))
        return {}


def _run(tmp_path: Path, options: CodeOptions, entry: dict | None = None) -> tuple[HeadlessRun, io.StringIO, _Client]:
    client = _Client(entry or {})
    stdout = io.StringIO()
    run = HeadlessRun(options, client=client, data_dir=tmp_path, cwd=tmp_path, stdin=io.StringIO(""),
                      stdout=stdout, stderr=io.StringIO())
    run.conversation_id = "chat_1"
    run._session_id = "s-1"
    return run, stdout, client


def test_an_mcp_request_without_a_host_is_answered_at_once(tmp_path: Path) -> None:
    entry = {"kind": "mcp_message", "request": {"server_name": "calc", "message": {"method": "tools/list"}}}
    run, stdout, client = _run(tmp_path, CodeOptions(prompt="hi"), entry)
    run.bridge._host_request({"request_id": "req-1", "transcript_id": "tr_1"})
    assert stdout.getvalue() == ""  # no control_request is written into the output
    assert len(client.posts) == 1
    path, body = client.posts[0]
    assert path.endswith("/host-requests/req-1")
    assert body == {"response": {"_error": "no SDK host on this run"}}


def test_a_hook_request_without_a_host_is_answered_at_once(tmp_path: Path) -> None:
    entry = {"kind": "hook_callback", "request": {"callback_id": "h1", "input": {"hook_event_name": "PreToolUse"}}}
    run, stdout, client = _run(tmp_path, CodeOptions(prompt="hi"), entry)
    run.bridge._host_request({"request_id": "req-2", "transcript_id": "tr_2"})
    assert stdout.getvalue() == ""
    assert client.posts[0][1] == {"response": {"_error": "no SDK host on this run"}}


def test_a_host_request_in_a_stream_json_run_still_waits_for_the_host(tmp_path: Path) -> None:
    entry = {"kind": "mcp_message", "request": {"server_name": "calc", "message": {}}}
    options = CodeOptions(prompt="hi", input_format="stream-json", output_format="stream-json")
    run, stdout, client = _run(tmp_path, options, entry)
    run.bridge._host_request({"request_id": "req-3", "transcript_id": "tr_3"})
    assert '"control_request"' in stdout.getvalue()
    assert client.posts == []


def test_an_sdk_mcp_server_is_refused_without_a_host(tmp_path: Path) -> None:
    with pytest.raises(CliUsageError, match="stream-json"):
        build_turn_options(CodeOptions(prompt="hi", mcp_configs=[SDK_SERVER]), tmp_path, host_answers_prompts=False)


def test_an_sdk_mcp_server_is_accepted_with_a_host(tmp_path: Path) -> None:
    options = CodeOptions(prompt="hi", mcp_configs=[SDK_SERVER], input_format="stream-json", output_format="stream-json")
    built = build_turn_options(options, tmp_path, host_answers_prompts=True)
    assert built.mcp_servers["calc"]["type"] == "sdk"


def test_a_callback_hook_from_settings_is_refused_without_a_host(tmp_path: Path) -> None:
    with pytest.raises(CliUsageError, match="stream-json"):
        build_turn_options(CodeOptions(prompt="hi", settings=CALLBACK_HOOKS), tmp_path, host_answers_prompts=False)


def test_a_command_hook_from_settings_needs_no_host(tmp_path: Path) -> None:
    command = '{"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "echo hi"}]}]}}'
    build_turn_options(CodeOptions(prompt="hi", settings=command), tmp_path, host_answers_prompts=False)


def test_a_callback_hook_is_accepted_with_a_host(tmp_path: Path) -> None:
    options = CodeOptions(prompt="hi", settings=CALLBACK_HOOKS, input_format="stream-json", output_format="stream-json")
    build_turn_options(options, tmp_path, host_answers_prompts=True)
