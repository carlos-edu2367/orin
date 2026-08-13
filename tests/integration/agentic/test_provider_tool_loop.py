from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from agentos.agentic.action_loop import ActionLoop
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "turn-transport",
            "conversation_id": "conversation-1",
            "user_id": "user-1",
            "workspace_id": None,
            "agent_id": "agent-1",
            "execution_id": "execution-1",
            "provider": "openrouter",
            "model_id": "local-model",
            "user_message_id": "user-message-1",
            "assistant_message_id": "assistant-message-1",
        }
        self.states: list[str] = []
        self.content: list[str] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "call lookup"}]

    def lifecycle(self, turn, state, **payload):
        self.states.append(state)

    def delta(self, turn, text):
        self.content.append(text)

    def finish(self, turn, *, failed=False, code=None):
        self.states.append("failed" if failed else "completed")


class RuntimeTool:
    def invoke(self, request):
        return SimpleNamespace(result_ref="result:local", result={"private": "redact-me"})


class Registry:
    descriptor = SimpleNamespace(
        name="lookup",
        description="Local deterministic lookup",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        tool_ref=SimpleNamespace(tool_id="lookup", version=1),
    )

    def list(self, context):
        return (self.descriptor,)

    def resolve(self, name, context):
        return self.descriptor


def test_local_http_transport_runs_provider_tool_provider_loop_without_internet() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert request.url.host == "provider.test"
        assert "api-key" not in json.dumps(payload)
        assert payload["tools"][0].keys() == {"type", "function"}
        if calls == 1:
            body = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-local","function":{"name":"lookup","arguments":"{\\"q\\":\\"local\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
                "data: [DONE]\n\n"
            )
        else:
            body = 'data: {"choices":[{"delta":{"content":"local answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = HTTPProviderStreamTransport(
        provider="openrouter",
        base_url="https://provider.test/v1",
        api_key="api-key",
        model="local-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    store = Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=transport,
        actions=ActionLoop(Registry(), RuntimeTool()),
        limits=AgenticLimits(max_actions=2, max_iterations=3),
    )

    result = runtime.run("turn-transport")

    assert result.state == "completed"
    assert store.content == ["local answer"]
    assert store.states == ["running", "waiting_tool", "tool_started", "tool_finished", "running", "completed", "completed"]


def test_ollama_ndjson_stream_drives_a_full_tool_round_trip() -> None:
    """The native stream must reach the runtime as the same typed events an
    SSE provider produces, tool call included."""
    import json

    import httpx

    from agentos.agentic.provider_stream import HTTPProviderStreamTransport, StreamKind

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            body = "\n".join([
                json.dumps({"message": {"role": "assistant", "content": "checking"}, "done": False}),
                json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 40, "eval_count": 9}),
            ])
        else:
            body = "\n".join([
                json.dumps({"message": {"role": "assistant", "content": "done"}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 60, "eval_count": 3}),
            ])
        return httpx.Response(200, text=body)

    transport = HTTPProviderStreamTransport(
        provider="ollama", base_url="http://localhost:11434", api_key="", model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)), num_ctx=32_768,
    )
    request = {
        "messages": [{"role": "user", "content": "read a.txt"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {}}}}],
    }

    first = list(transport.stream(request))
    second = list(transport.stream({**request, "tool_choice": "none"}))

    assert [item.kind for item in first] == [StreamKind.TEXT, StreamKind.TOOL_CALL, StreamKind.USAGE, StreamKind.FINISH]
    assert first[1].tool_call_id == "tool-call:1"
    assert json.loads(first[1].arguments_delta) == {"path": "a.txt"}
    assert first[3].finish_reason.value == "TOOL_CALLS"
    assert first[2].usage.total_tokens == 49

    assert calls[0]["options"]["num_ctx"] == 32_768
    assert "tools" in calls[0]
    # The closing iteration withholds the declarations entirely.
    assert "tools" not in calls[1]
    assert [item.kind for item in second] == [StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert second[2].finish_reason.value == "STOP"


def test_ollama_native_follow_up_uses_native_tool_history_shapes() -> None:
    """A native Ollama follow-up must not receive OpenAI-only tool history."""
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, text="\n".join([
                json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop"}),
            ]))
        assistant_call = payload["messages"][1]["tool_calls"][0]
        assert assistant_call["function"]["arguments"] == {"path": "a.txt"}
        assert payload["messages"][2] == {
            "role": "tool",
            "tool_name": "read_file",
            "content": "file contents",
        }
        return httpx.Response(200, text=json.dumps({"message": {"content": "done"}, "done": True, "done_reason": "stop"}) + "\n")

    transport = HTTPProviderStreamTransport(
        provider="ollama", base_url="http://localhost:11434", api_key="", model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)), num_ctx=32_768,
    )
    first_request = {
        "messages": [{"role": "user", "content": "read a.txt"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {}}}}],
    }

    list(transport.stream(first_request))
    follow_up = {
        **first_request,
        "messages": [
            *first_request["messages"],
            {"role": "assistant", "content": None, "tool_calls": [{"id": "tool-call:1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]},
            {"role": "tool", "tool_call_id": "tool-call:1", "content": "file contents"},
        ],
        "tool_choice": "none",
    }

    list(transport.stream(follow_up))
