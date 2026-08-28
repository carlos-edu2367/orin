from __future__ import annotations

from agentos.agentic import transcript


def _call_step(call_id: str, name: str = "read_file", arguments: str = '{"path": "a.txt"}') -> dict[str, object]:
    return {
        "kind": transcript.STEP_ASSISTANT_TOOL_CALL,
        "payload": transcript.assistant_tool_call_payload("", [{"id": call_id, "name": name, "arguments": arguments}]),
    }


def _result_step(call_id: str, content: str = "conteudo", name: str = "read_file") -> dict[str, object]:
    return {
        "kind": transcript.STEP_TOOL_RESULT,
        "payload": transcript.tool_result_payload(call_id=call_id, name=name, status="succeeded", content=content),
    }


# -- storage shape ----------------------------------------------------------


def test_a_result_within_the_limit_is_stored_whole() -> None:
    payload = transcript.tool_result_payload(call_id="c1", name="read_file", status="succeeded", content="curto")
    assert payload["content"] == "curto"
    assert payload["truncated"] is False
    assert payload["content_bytes"] == 5


def test_an_oversized_result_records_its_real_size() -> None:
    """A truncated result must not look complete to the next turn."""
    payload = transcript.tool_result_payload(
        call_id="c1", name="run_command", status="succeeded", content="x" * (transcript.MAX_STEP_CHARS + 500),
    )
    assert payload["truncated"] is True
    assert payload["content_bytes"] == transcript.MAX_STEP_CHARS + 500
    assert len(str(payload["content"])) == transcript.MAX_STEP_CHARS


def test_a_truncated_result_says_so_when_replayed() -> None:
    payload = transcript.tool_result_payload(
        call_id="c1", name="run_command", status="succeeded", content="x" * (transcript.MAX_STEP_CHARS + 500),
    )
    message = transcript.tool_result_message("openrouter", payload)
    assert "truncado" in str(message["content"])
    assert "run_command" in str(message["content"])


# -- provider projection ----------------------------------------------------


def test_steps_replay_into_the_openai_shape() -> None:
    messages = transcript.project([_call_step("c1"), _result_step("c1")], "openrouter")
    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": "conteudo"}


def test_steps_replay_into_the_anthropic_shape() -> None:
    messages = transcript.project([_call_step("c1"), _result_step("c1")], "anthropic")
    assert messages[0]["content"][0]["type"] == "tool_use"
    assert messages[0]["content"][0]["input"] == {"path": "a.txt"}
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][0]["type"] == "tool_result"


def test_a_step_recorded_under_one_provider_replays_under_another() -> None:
    """Switching the conversation's model must not corrupt its history.

    Steps are stored provider-neutrally precisely so the shape is chosen when
    they are read, not when they were written.
    """
    steps = [_call_step("c1"), _result_step("c1")]
    assert transcript.project(steps, "anthropic")[0]["content"][0]["type"] == "tool_use"
    assert transcript.project(steps, "openrouter")[0]["tool_calls"][0]["id"] == "c1"


# -- budgeting --------------------------------------------------------------


def test_a_call_and_its_results_are_kept_or_dropped_together() -> None:
    """Both providers reject a tool result whose call is missing."""
    steps = [_call_step("c1"), _result_step("c1", "y" * 4_000), _call_step("c2"), _result_step("c2")]
    kept = transcript.within_budget(steps, 60)
    call_ids = {str(step["payload"].get("id") or (step["payload"].get("calls") or [{}])[0].get("id")) for step in kept}
    for call_id in call_ids:
        kinds = [step["kind"] for step in kept if call_id in str(step["payload"])]
        assert transcript.STEP_ASSISTANT_TOOL_CALL in kinds


def test_the_budget_keeps_the_most_recent_work() -> None:
    """A follow-up is about what just happened, not about the oldest step."""
    steps = [_call_step("old"), _result_step("old", "y" * 8_000), _call_step("new"), _result_step("new", "recente")]
    kept = transcript.within_budget(steps, 200)
    replayed = str(transcript.project(kept, "openrouter"))
    assert "recente" in replayed
    assert "y" * 100 not in replayed


def test_a_zero_budget_replays_nothing() -> None:
    assert transcript.within_budget([_call_step("c1"), _result_step("c1")], 0) == []


def test_an_orphan_result_at_the_head_is_dropped() -> None:
    """A trajectory whose first call was already evicted cannot be replayed."""
    kept = transcript.within_budget([_result_step("gone"), _call_step("c1"), _result_step("c1")], 10_000)
    assert all(step["kind"] != transcript.STEP_TOOL_RESULT or "c1" in str(step["payload"]) for step in kept)


def test_an_assistant_step_without_calls_is_not_replayed() -> None:
    """An assistant message with no tool calls belongs to the chat transcript."""
    empty = {"kind": transcript.STEP_ASSISTANT_TOOL_CALL, "payload": transcript.assistant_tool_call_payload("texto", [])}
    assert transcript.project([empty], "openrouter") == []


# -- runtime wiring ---------------------------------------------------------


class _TranscriptStore:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
            "provider": "openrouter", "model_id": "local-model",
            "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
        }
        self.steps: list[dict[str, object]] = []

    def load(self, turn_id: str): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "leia o arquivo"}]
    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...

    def record_step(self, turn, *, kind, payload, **fields) -> None:
        self.steps.append({"kind": kind, "payload": payload, **fields})


class _OneToolProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        from agentos.agentic.provider_stream import normalize_sse

        self.calls += 1
        if self.calls == 1:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":'
                '{"name":"read_file","arguments":"{\\"path\\":\\"a.txt\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse([
            'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ], provider="openrouter")


class _ReadToolset:
    def schemas(self): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})

    def invoke(self, name, arguments):
        from agentos.agentic.agent_tools import ToolOutcome

        return ToolOutcome("succeeded", "lido", "Total: 45320.10", {})


def test_the_runtime_records_the_call_and_its_result() -> None:
    from agentos.agentic.runtime import AgenticTurnRuntime

    store = _TranscriptStore()
    AgenticTurnRuntime(store=store, provider=_OneToolProvider(), toolset=_ReadToolset()).run("turn-1")

    kinds = [step["kind"] for step in store.steps]
    assert kinds == [transcript.STEP_ASSISTANT_TOOL_CALL, transcript.STEP_TOOL_RESULT]
    assert store.steps[0]["payload"]["calls"][0]["name"] == "read_file"
    assert store.steps[1]["payload"]["content"] == "Total: 45320.10"
    assert store.steps[1]["tool_call_id"] == "call-1"


def test_a_store_that_cannot_record_steps_still_completes_the_turn() -> None:
    from agentos.agentic.runtime import AgenticTurnRuntime

    class _Failing(_TranscriptStore):
        def record_step(self, turn, **values):
            raise RuntimeError("disk is full")

    result = AgenticTurnRuntime(store=_Failing(), provider=_OneToolProvider(), toolset=_ReadToolset()).run("turn-1")
    assert result.state == "completed"
