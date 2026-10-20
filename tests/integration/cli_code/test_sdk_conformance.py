"""W6 acceptance (spec §2, §9.4): the official claude-agent-sdk drives `orin code`, unmodified, through `cli_path`.

Scenarios: a simple query, streaming (partial messages), can_use_tool (allow and deny), the SDK's own hooks,
interrupt, and resume. Offline: the live runtime of Task 5 on the fake Ollama.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, PermissionResultAllow, PermissionResultDeny,
    ResultMessage, SystemMessage, TextBlock, ToolUseBlock, query,
)
from claude_agent_sdk.types import StreamEvent

from .live_runtime import LiveRuntime

MODEL = "ollama/fake-coder:latest"


def _options(runtime: LiveRuntime, cwd: Path, **values: Any) -> ClaudeAgentOptions:
    executable = Path(sys.executable).parent / ("orin-code.exe" if os.name == "nt" else "orin-code")
    return ClaudeAgentOptions(cli_path=str(executable), cwd=str(cwd), env=dict(runtime.env), model=MODEL, **values)


async def _collect(prompt: Any, options: ClaudeAgentOptions) -> list[Any]:
    return [message async for message in query(prompt=prompt, options=options)]


async def _stream(text: str):
    yield {"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None, "session_id": ""}


def _result(messages: list[Any]) -> ResultMessage:
    results = [message for message in messages if isinstance(message, ResultMessage)]
    assert len(results) == 1, messages
    return results[0]


def test_a_simple_query(orin_runtime: LiveRuntime) -> None:
    messages = asyncio.run(_collect("say hello", _options(orin_runtime, orin_runtime.workspace("sdk-simple"))))
    texts = [block.text for message in messages if isinstance(message, AssistantMessage) for block in message.content if isinstance(block, TextBlock)]
    assert texts == ["Hello from fake."]
    result = _result(messages)
    assert result.subtype == "success" and result.result == "Hello from fake." and not result.is_error


def test_the_sdk_tools_preset_offers_the_model_its_tools(orin_runtime: LiveRuntime) -> None:
    """W6-R5: the preset sends `--tools default`; the model must still be offered Orin's tools, not none."""
    options = _options(orin_runtime, orin_runtime.workspace("sdk-preset"), tools={"type": "preset", "preset": "claude_code"})
    messages = asyncio.run(_collect("say hello", options))
    init = [message for message in messages if isinstance(message, SystemMessage) and message.subtype == "init"]
    assert init, messages
    offered = init[0].data["tools"]
    assert offered and "Read" in offered and "Bash" in offered
    assert _result(messages).subtype == "success"


def test_streaming_partial_messages(orin_runtime: LiveRuntime) -> None:
    options = _options(orin_runtime, orin_runtime.workspace("sdk-stream"), include_partial_messages=True)
    messages = asyncio.run(_collect("say hello", options))
    kinds = [message.event["type"] for message in messages if isinstance(message, StreamEvent)]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop" and kinds.count("content_block_delta") == 2
    assert _result(messages).subtype == "success"


def test_can_use_tool_allows_and_denies(orin_runtime: LiveRuntime) -> None:
    asked: list[tuple[str, dict]] = []

    async def allow(tool_name, tool_input, context):
        asked.append((tool_name, tool_input))
        return PermissionResultAllow()

    async def deny(tool_name, tool_input, context):
        return PermissionResultDeny(message="not here")

    allowed = orin_runtime.workspace("sdk-cut-allow")
    messages = asyncio.run(_collect(_stream("[[write:sdk.txt]]"), _options(orin_runtime, allowed, can_use_tool=allow)))
    assert (allowed / "sdk.txt").read_text(encoding="utf-8") == "hi\n"
    assert asked == [("Write", {"file_path": "sdk.txt", "content": "hi\n"})]
    assert _result(messages).subtype == "success"
    denied = orin_runtime.workspace("sdk-cut-deny")
    messages = asyncio.run(_collect(_stream("[[write:sdk.txt]]"), _options(orin_runtime, denied, can_use_tool=deny)))
    assert not (denied / "sdk.txt").exists()
    assert _result(messages).permission_denials


def test_the_sdks_hooks(orin_runtime: LiveRuntime) -> None:
    calls: list[dict] = []

    async def no_writes(input_data, tool_use_id, context):
        calls.append({"input": input_data, "tool_use_id": tool_use_id})
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "the SDK says no"}}

    workspace = orin_runtime.workspace("sdk-hooks")
    options = _options(orin_runtime, workspace, permission_mode="acceptEdits",
                       hooks={"PreToolUse": [HookMatcher(matcher="Write", hooks=[no_writes])]})
    messages = asyncio.run(_collect(_stream("[[write:hooked.txt]]"), options))
    assert not (workspace / "hooked.txt").exists()
    assert len(calls) == 1 and calls[0]["input"]["tool_name"] == "Write" and calls[0]["tool_use_id"].startswith("toolu_")
    uses = [block for message in messages if isinstance(message, AssistantMessage) for block in message.content if isinstance(block, ToolUseBlock)]
    assert uses and uses[0].id == calls[0]["tool_use_id"]


def test_interrupt(orin_runtime: LiveRuntime) -> None:
    async def scenario() -> tuple[ResultMessage, int]:
        options = _options(orin_runtime, orin_runtime.workspace("sdk-interrupt"), include_partial_messages=True)
        deltas = 0
        async with ClaudeSDKClient(options=options) as client:
            await client.query("[[slow]]")
            async for message in client.receive_response():
                if isinstance(message, StreamEvent) and message.event["type"] == "content_block_delta":
                    deltas += 1
                    if deltas == 2:
                        await client.interrupt()
                if isinstance(message, ResultMessage):
                    return message, deltas
        raise AssertionError("no result")

    result, deltas = asyncio.run(scenario())
    assert result.is_error and result.subtype == "error_during_execution" and deltas < 40


def test_resume(orin_runtime: LiveRuntime) -> None:
    workspace = orin_runtime.workspace("sdk-resume")
    first = _result(asyncio.run(_collect("say hello", _options(orin_runtime, workspace))))
    second = _result(asyncio.run(_collect("again", _options(orin_runtime, workspace, resume=first.session_id))))
    assert second.session_id == first.session_id and second.subtype == "success"
    with orin_runtime.api() as api:
        listed = api.get("/v1/conversations").json()
    assert sum(1 for item in listed.get("items", listed) if isinstance(item, dict) and item.get("title") == "say hello") >= 1


def test_an_sdk_mcp_server(orin_runtime: LiveRuntime) -> None:
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("add", "Adds two numbers", {"a": int, "b": int})
    async def add(arguments):
        return {"content": [{"type": "text", "text": str(arguments["a"] + arguments["b"])}]}

    calc = create_sdk_mcp_server(name="calc", version="1.0.0", tools=[add])
    options = _options(orin_runtime, orin_runtime.workspace("sdk-mcp"), mcp_servers={"calc": calc}, allowed_tools=["mcp__calc__add"])
    messages = asyncio.run(_collect(_stream('[[tool:mcp__calc__add:{"a": 2, "b": 3}]]'), options))
    assert _result(messages).result == "Result: 5"
