"""W6 Tasks 14-16: the control protocol between `orin code` and an SDK host, on the live runtime."""
from __future__ import annotations

import uuid

from .live_runtime import LiveRuntime
from .sdk_host import Host

MODEL = ["--model", "ollama/fake-coder:latest"]
INITIALIZE = {"subtype": "initialize", "hooks": None}


def _start(host: Host, **initialize) -> None:
    assert host.response_to(host.control({**INITIALIZE, **initialize}))["subtype"] == "success"


def _result(host: Host, count: int = 1) -> dict:
    host.wait_for(lambda m: m.get("type") == "result" and len(host.results()) >= count)
    return host.results()[count - 1]


def test_can_use_tool_allows_a_write(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("cut-allow")
    host = Host(orin_runtime, workspace, "--permission-prompt-tool", "stdio", *MODEL,
                on_request=lambda request: {"behavior": "allow", "updatedInput": request["input"]} if request["subtype"] == "can_use_tool" else None)
    _start(host)
    host.prompt("[[write:allowed.txt]]")
    result = _result(host)
    assert host.close() == 0
    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "hi\n"
    (asked,) = [item["request"] for item in host.requests if item["request"]["subtype"] == "can_use_tool"]
    use = next(block for item in host.messages if item["type"] == "assistant" for block in item["message"]["content"] if block["type"] == "tool_use")
    assert asked["tool_name"] == "Write" and asked["input"]["file_path"] == "allowed.txt" and asked["tool_use_id"] == use["id"]
    assert result["subtype"] == "success" and result["permission_denials"] == []


def test_can_use_tool_denies_a_write(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("cut-deny")
    host = Host(orin_runtime, workspace, "--permission-prompt-tool", "stdio", *MODEL,
                on_request=lambda request: {"behavior": "deny", "message": "not in this folder"} if request["subtype"] == "can_use_tool" else None)
    _start(host)
    host.prompt("[[write:denied.txt]]")
    result = _result(host)
    host.close()
    assert not (workspace / "denied.txt").exists()
    assert result["permission_denials"][0]["tool_name"] == "Write"
    tool_result = next(block for item in host.messages if item["type"] == "user" for block in item["message"]["content"])
    assert tool_result["is_error"] is True and "not in this folder" in tool_result["content"]


def test_changed_input_is_refused(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("cut-changed")

    def change(request):
        if request["subtype"] != "can_use_tool":
            return None
        return {"behavior": "allow", "updatedInput": {**request["input"], "file_path": "elsewhere.txt"}}

    host = Host(orin_runtime, workspace, "--permission-prompt-tool", "stdio", *MODEL, on_request=change)
    _start(host)
    host.prompt("[[write:asked.txt]]")
    _result(host)
    host.close()
    assert not (workspace / "asked.txt").exists() and not (workspace / "elsewhere.txt").exists()


def test_without_a_callback_nothing_asks_the_host(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("no-callback")
    host = Host(orin_runtime, workspace, *MODEL)
    _start(host)
    host.prompt("[[write:x.txt]]")
    result = _result(host)
    host.close()
    assert host.requests == [] and not (workspace / "x.txt").exists() and result["permission_denials"]


def test_interrupt_stops_the_turn(orin_runtime: LiveRuntime) -> None:
    host = Host(orin_runtime, orin_runtime.workspace("interrupt"), "--include-partial-messages", *MODEL)
    _start(host)
    host.prompt("[[slow]]")
    host.wait_for(lambda m: m.get("type") == "stream_event" and m["event"]["type"] == "content_block_delta")
    assert host.response_to(host.control({"subtype": "interrupt"}))["subtype"] == "success"
    result = _result(host)
    host.close()
    assert result["is_error"] is True and result["subtype"] == "error_during_execution"
    deltas = [m for m in host.messages if m.get("type") == "stream_event" and m["event"]["type"] == "content_block_delta"]
    assert len(deltas) < 40


def test_set_permission_mode_applies_to_the_next_turn(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("set-mode")
    host = Host(orin_runtime, workspace, *MODEL)
    _start(host)
    assert host.response_to(host.control({"subtype": "set_permission_mode", "mode": "acceptEdits"}))["subtype"] == "success"
    assert host.response_to(host.control({"subtype": "set_permission_mode", "mode": "yolo"}))["subtype"] == "error"
    host.prompt("[[write:mode.txt]]")
    _result(host)
    host.close()
    assert (workspace / "mode.txt").exists()


def test_set_model_and_mcp_status(orin_runtime: LiveRuntime) -> None:
    host = Host(orin_runtime, orin_runtime.workspace("model"), *MODEL)
    _start(host)
    assert host.response_to(host.control({"subtype": "set_model", "model": "ollama/fake-coder:latest"}))["subtype"] == "success"
    assert host.response_to(host.control({"subtype": "set_model", "model": "nowhere/none"}))["subtype"] == "error"
    status = host.response_to(host.control({"subtype": "mcp_status"}))
    assert status["subtype"] == "success" and isinstance(status["response"]["mcpServers"], list)
    assert host.response_to(host.control({"subtype": "set_max_thinking_tokens", "max_thinking_tokens": 8000}))["subtype"] == "success"
    assert host.response_to(host.control({"subtype": "teleport"}))["subtype"] == "error"
    host.close()


def test_an_sdk_hook_can_refuse_a_tool(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("sdk-hook")
    seen: list[dict] = []

    def answer(request):
        if request["subtype"] != "hook_callback":
            return None
        seen.append(request)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "the host says no"}}

    host = Host(orin_runtime, workspace, "--permission-mode", "acceptEdits", *MODEL, on_request=answer)
    _start(host, hooks={"PreToolUse": [{"matcher": "Write", "hookCallbackIds": ["hook_0"], "timeout": 30}]})
    host.prompt("[[write:hooked.txt]]")
    _result(host)
    host.close()
    assert not (workspace / "hooked.txt").exists()
    (call,) = seen
    assert call["callback_id"] == "hook_0" and call["input"]["tool_name"] == "Write"
    assert call["input"]["hook_event_name"] == "PreToolUse" and call["tool_use_id"].startswith("toolu_")


def test_an_sdk_hook_that_allows_lets_the_tool_run(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("sdk-hook-ok")
    host = Host(orin_runtime, workspace, "--permission-mode", "acceptEdits", *MODEL,
                on_request=lambda request: {"continue": True} if request["subtype"] == "hook_callback" else None)
    _start(host, hooks={"PostToolUse": [{"matcher": None, "hookCallbackIds": ["hook_1"]}]})
    host.prompt("[[write:after.txt]]")
    _result(host)
    host.close()
    assert (workspace / "after.txt").exists()
    assert any(item["request"]["subtype"] == "hook_callback" for item in host.requests)


def test_rewind_files_restores_the_folder(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("rewind")
    host = Host(orin_runtime, workspace, "--permission-mode", "acceptEdits", "--replay-user-messages", *MODEL)
    _start(host)
    prompt_id = str(uuid.uuid4())
    host.prompt("[[write:undone.txt]]", uuid=prompt_id)
    _result(host)
    assert (workspace / "undone.txt").exists()
    assert host.response_to(host.control({"subtype": "rewind_files", "user_message_id": prompt_id}))["subtype"] == "success"
    assert host.response_to(host.control({"subtype": "rewind_files", "user_message_id": str(uuid.uuid4())}))["subtype"] == "error"
    host.close()
    assert not (workspace / "undone.txt").exists()
