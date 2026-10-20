"""W6 Task 10: `orin code -p` and stream-json against a real local runtime (fake Ollama)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .live_runtime import LiveRuntime

MODEL = ["--model", "ollama/fake-coder:latest"]


def orin_code(runtime: LiveRuntime, cwd: Path, *args: str, stdin: str | None = None, env: dict | None = None,
              timeout: float = 180) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).parent / ("orin-code.exe" if os.name == "nt" else "orin-code")
    return subprocess.run([str(executable), *args], cwd=str(cwd), env=env or runtime.env, input=stdin if stdin is not None else "",
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def _lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_print_mode_answers_with_text(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("text"), "-p", "say hello", *MODEL)
    assert (done.returncode, done.stdout, done.stderr) == (0, "Hello from fake.\n", "")


def test_json_output_is_one_result(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("json"), "-p", "say hello", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    (result,) = _lines(done.stdout)
    assert result["type"] == "result" and result["subtype"] == "success" and result["result"] == "Hello from fake."
    assert uuid.UUID(result["session_id"]) and result["usage"]["output_tokens"] > 0


def test_stream_json_shows_init_assistant_and_result(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("stream"), "-p", "say hello", "--output-format", "stream-json", "--verbose", *MODEL)
    assert done.returncode == 0, done.stderr
    messages = _lines(done.stdout)
    assert [item["type"] for item in messages] == ["system", "assistant", "result"]
    init, assistant, result = messages
    assert init["subtype"] == "init" and "Read" in init["tools"] and init["permissionMode"] == "default"
    assert assistant["message"]["content"] == [{"type": "text", "text": "Hello from fake."}]
    assert init["session_id"] == assistant["session_id"] == result["session_id"]


def test_an_allowed_write_runs_and_is_shown_as_tool_use_and_result(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("write")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:made.txt]]", "--permission-mode", "acceptEdits",
                     "--output-format", "stream-json", "--verbose", *MODEL)
    assert done.returncode == 0, done.stderr
    assert (workspace / "made.txt").read_text(encoding="utf-8") == "hi\n"
    messages = _lines(done.stdout)
    use = next(block for item in messages if item["type"] == "assistant" for block in item["message"]["content"] if block["type"] == "tool_use")
    assert use["name"] == "Write" and use["input"]["file_path"] == "made.txt"
    result_block = next(block for item in messages if item["type"] == "user" for block in item["message"]["content"])
    assert result_block["tool_use_id"] == use["id"] and result_block["is_error"] is False
    assert messages[-1]["result"] == "Done." and messages[-1]["num_turns"] == 2


def test_without_a_host_a_write_that_would_ask_is_refused(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("refused")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:nope.txt]]", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    (result,) = _lines(done.stdout)
    assert not (workspace / "nope.txt").exists()
    assert result["permission_denials"] and result["permission_denials"][0]["tool_name"] == "Write"


def test_a_disallowed_tool_is_refused_even_in_accept_edits(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("disallowed")
    done = orin_code(orin_runtime, workspace, "-p", "please [[write:no.txt]]", "--permission-mode", "acceptEdits",
                     "--disallowedTools", "Write", "--output-format", "json", *MODEL)
    assert done.returncode == 0, done.stderr
    assert not (workspace / "no.txt").exists()


def test_max_turns_ends_the_run_with_its_result_subtype(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("max-turns"), "-p", "please [[write:t.txt]]", "--permission-mode",
                     "acceptEdits", "--max-turns", "1", "--output-format", "json", *MODEL)
    (result,) = _lines(done.stdout)
    assert done.returncode == 1 and result["subtype"] == "error_max_turns" and result["is_error"] is True


def test_continue_resumes_the_folders_latest_session(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("continue")
    first = _lines(orin_code(orin_runtime, workspace, "-p", "say hello", "--output-format", "json", *MODEL).stdout)[0]
    again = orin_code(orin_runtime, workspace, "-c", "-p", "say hello again", "--output-format", "json")
    assert again.returncode == 0, again.stderr
    second = _lines(again.stdout)[0]
    assert second["session_id"] == first["session_id"]  # the model came from the resumed conversation
    by_id = _lines(orin_code(orin_runtime, workspace, "-r", first["session_id"], "-p", "third", "--output-format", "json").stdout)[0]
    assert by_id["session_id"] == first["session_id"]


def test_an_sdk_style_stream_json_session(orin_runtime: LiveRuntime) -> None:
    stdin = "\n".join(json.dumps(item) for item in (
        {"type": "control_request", "request_id": "req_1", "request": {"subtype": "initialize", "hooks": None}},
        {"type": "user", "session_id": "", "message": {"role": "user", "content": "say hello"}, "parent_tool_use_id": None},
        {"type": "control_request", "request_id": "req_2", "request": {"subtype": "mcp_status"}},
    )) + "\n"
    done = orin_code(orin_runtime, orin_runtime.workspace("sdk-style"), "--output-format", "stream-json", "--verbose",
                     "--input-format", "stream-json", "--system-prompt", "", *MODEL, stdin=stdin)
    assert done.returncode == 0, done.stderr
    messages = _lines(done.stdout)
    responses = {item["response"]["request_id"]: item["response"] for item in messages if item["type"] == "control_response"}
    assert responses["req_1"]["subtype"] == "success"
    assert responses["req_2"]["subtype"] == "error"  # Batch C answers mcp_status
    kinds = [item["type"] for item in messages if item["type"] != "control_response"]
    assert kinds == ["system", "assistant", "result"]


def test_a_shared_orin_is_refused(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("server"), "-p", "x", *MODEL, env={**orin_runtime.env, "ORIN_MODE": "server"})
    assert done.returncode == 1 and "local Orin only" in done.stderr


def test_flags_this_build_does_not_handle_are_refused_before_anything_runs(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("refuse"), "-p", "x", "--fork-session", *MODEL)
    assert done.returncode == 1 and "--fork-session" in done.stderr


def test_a_prompt_longer_than_orin_takes_is_an_error(orin_runtime: LiveRuntime) -> None:
    done = orin_code(orin_runtime, orin_runtime.workspace("long"), "-p", "x" * 16001, *MODEL)
    assert done.returncode == 1 and "16000" in done.stderr
