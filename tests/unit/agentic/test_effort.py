from __future__ import annotations

import json
from pathlib import Path

import httpx

from agentos.agentic.effort import THINKING_BUDGET, apply_effort, normalize_effort, reasoning_supported
from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, StreamKind, normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.settings.paths import orin_user_dir


def test_levels_and_aliases() -> None:
    assert [normalize_effort(value) for value in ("LOW", " max ", "xhigh", "extreme", None, 3)] == ["low", "max", "xhigh", None, None, None]


def test_reasoning_support_by_provider() -> None:
    assert reasoning_supported("openrouter", "x/y", ("reasoning", "tools"))
    assert not reasoning_supported("openrouter", "x/y", ("tools",))
    assert reasoning_supported("anthropic", "claude-sonnet-4-5", ())
    assert reasoning_supported("anthropic", "claude-3-7-sonnet-latest", ())
    assert not reasoning_supported("anthropic", "claude-3-5-haiku-latest", ())
    assert reasoning_supported("openai", "o3-mini", ()) and reasoning_supported("openai", "gpt-5", ())
    assert not reasoning_supported("openai", "gpt-4o", ())
    assert not reasoning_supported("ollama", "qwen3", ("thinking",))


def test_payload_mapping() -> None:
    anthropic = apply_effort("anthropic", {"max_tokens": 8192}, "high")
    assert anthropic["thinking"] == {"type": "enabled", "budget_tokens": THINKING_BUDGET["high"]}
    assert anthropic["max_tokens"] > THINKING_BUDGET["high"]
    assert apply_effort("openrouter", {}, "max") == {"reasoning": {"effort": "high"}}
    assert apply_effort("openai", {}, "low") == {"reasoning_effort": "low"}
    assert apply_effort("ollama", {}, "high") == {}
    assert apply_effort("anthropic", {"max_tokens": 1}, None) == {"max_tokens": 1}


def test_the_transport_sends_the_effort() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, text="", request=request)

    provider = HTTPProviderStreamTransport(base_url="https://x.test", api_key="k", model="claude-sonnet-4-5", provider="anthropic",
                                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    list(provider.stream({"messages": [{"role": "user", "content": "hi"}], "tools": [], "effort": "medium"}))
    assert captured[0]["thinking"]["budget_tokens"] == THINKING_BUDGET["medium"]


def test_signatures_and_redacted_thinking_are_streamed() -> None:
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"plan"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"SIG"}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"redacted_thinking","data":"ENC"}}',
    ]
    items = list(normalize_sse(lines, provider="anthropic"))
    assert [item.signature for item in items if item.signature] == ["SIG"]
    assert [item.redacted_thinking for item in items if item.redacted_thinking] == ["ENC"]


def test_the_assistant_tool_message_carries_the_signed_thinking_block() -> None:
    message = AgenticTurnRuntime._assistant_tool_message(
        {"provider": "anthropic"}, ["text"], {"c1": {"id": "c1", "name": "read_file", "arguments": "{}"}}, ["plan"],
        thinking_signature="SIG", redacted_thinking=["ENC"],
    )
    assert message["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}
    assert message["content"][1] == {"type": "redacted_thinking", "data": "ENC"}
    assert [block["type"] for block in message["content"][2:]] == ["text", "tool_use"]
    plain = AgenticTurnRuntime._assistant_tool_message({"provider": "anthropic"}, [], {"c1": {"id": "c1", "name": "x", "arguments": "{}"}}, ["plan"])
    assert [block["type"] for block in plain["content"]] == ["tool_use"]


class _Store:
    def __init__(self, provider: str, model: str) -> None:
        self.turn = {"turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": provider,
                     "model_id": model, "user_message_id": "um", "assistant_message_id": "am"}
        self.notes: list[str] = []

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass
    def record(self, turn, event_type, summary, payload=None, **kwargs): self.notes.append(summary)
    def main_agent_id(self, turn): return "agent:c:main"


def _effort_for(tmp_path: Path, provider: str, model: str, capabilities=(), repository_settings=None, **turn):
    from agentos.agentic.session import TurnSession

    class Agents:
        def list(self): return []

    store = _Store(provider, model)
    session = TurnSession(turn={**store.turn, **turn}, store=store, agents_store=Agents(), memory_store=None,
                          provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
                          model_capabilities=tuple(capabilities))
    if repository_settings is not None:
        path = session.workspace.root / ".orin" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(repository_settings), encoding="utf-8")
    return session.build_runtime().effort, store.notes


def _user_settings(data: dict) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_session_sends_effort_only_to_models_that_reason(tmp_path: Path) -> None:
    _user_settings({"effort": "high"})
    assert _effort_for(tmp_path, "anthropic", "claude-sonnet-4-5")[0] == "high"
    effort, notes = _effort_for(tmp_path, "ollama", "llama3")
    assert effort is None and any("ignorado" in note for note in notes)


def test_a_repository_cannot_choose_the_effort(tmp_path: Path) -> None:
    effort, _ = _effort_for(tmp_path, "anthropic", "claude-sonnet-4-5", repository_settings={"effort": "max", "effortLevel": "max"})
    assert effort is None


def test_a_turn_level_effort_wins(tmp_path: Path) -> None:
    _user_settings({"effort": "low"})
    assert _effort_for(tmp_path, "anthropic", "claude-sonnet-4-5", effort="max")[0] == "max"


def test_effort_never_raises_an_explicit_cap() -> None:
    # The caller asked for at most 1,000 output tokens: effort must not raise that to 32,000.
    transport = HTTPProviderStreamTransport("https://api.anthropic.com/v1", "k", "claude-sonnet-4-5", provider="anthropic", client=httpx.Client())
    _, _, payload = transport._request_for([{"role": "user", "content": "hi"}], [], None, 1_000, "max")
    assert payload["max_tokens"] == 1_000
    assert "thinking" not in payload

    # With room for it, the thinking budget fits inside the cap and the cap stays where it was.
    fitted = apply_effort("anthropic", {"max_tokens": 8_192}, "max", explicit_cap=8_192)
    assert fitted["max_tokens"] == 8_192
    assert fitted["thinking"] == {"type": "enabled", "budget_tokens": 8_192 - 1_024}


def test_a_cap_below_2048_sends_no_thinking() -> None:
    for cap in (1, 1_000, 2_047):
        assert apply_effort("anthropic", {"max_tokens": cap}, "high", explicit_cap=cap) == {"max_tokens": cap}
    smallest = apply_effort("anthropic", {"max_tokens": 2_048}, "high", explicit_cap=2_048)
    assert smallest["thinking"] == {"type": "enabled", "budget_tokens": 1_024}
    assert smallest["max_tokens"] == 2_048
