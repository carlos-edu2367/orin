"""W7 Batch F (W6-R16): `hook_response` names the hook and carries its outcome, from the records Orin keeps."""
from __future__ import annotations

from agentos.cli_code.protocol import Translator


def _response(event: dict) -> dict:
    translator = Translator(session_id="s1", model="m", hook_events=True)
    [message] = translator.feed(event)
    assert message["subtype"] == "hook_response"
    return message


def test_a_settings_hook_is_named_by_its_label_and_its_reason_is_the_output() -> None:
    message = _response({"event_type": "plugin.hook_executed", "summary": "Hook PreToolUse (project): blocked",
                         "payload": {"hook_event": "PreToolUse", "label": "npm run lint", "reason": "lint failed", "exit_code": 2}})
    assert (message["hook_name"], message["hook_event"], message["output"], message["exit_code"]) == ("npm run lint", "PreToolUse", "lint failed", 2)


def test_a_plugin_hook_is_named_by_its_id_and_its_summary_is_the_output() -> None:
    message = _response({"event_type": "plugin.hook_executed", "summary": "formatted 3 files",
                         "payload": {"hook_event": "PostToolUse", "hook_id": "kit:format", "status": "ok"}})
    assert (message["hook_name"], message["output"]) == ("kit:format", "formatted 3 files")


def test_a_settings_hook_without_a_reason_does_not_echo_its_summary() -> None:
    message = _response({"event_type": "plugin.hook_executed", "summary": "Hook Stop (user): ok",
                         "payload": {"hook_event": "Stop", "label": "notify-send done", "status": "ok", "exit_code": 0}})
    assert message["output"] == ""
