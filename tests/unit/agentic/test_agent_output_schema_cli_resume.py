"""Spike S5 (WS-C4): resuming the same child agent to correct its JSON works for a Claude, a Codex and an
Antigravity child, with CLI fakes (no quota is spent).

A resumed child is a fresh run of the same ``agent_id`` whose history Orin replays to the CLI: the native turn of
the Claude CLI builds its user content from ``history_for_turn`` and the Codex and Antigravity providers receive the
same history in ``messages``. No CLI-side session id is involved (``resume_session_id`` is local to one run's tool
loop). So the first answer and the correction note must reach every provider on the second run.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.structured_output import canonical_json
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliUsage
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_agent_provider_param import _make_session
from tests.unit.agentic.test_native_turn import ScriptedHandle, _make_init, _make_result

SCHEMA = {
    "type": "object",
    "required": ["veredito", "itens"],
    "properties": {"veredito": {"type": "string", "enum": ["ok", "falha"]}, "itens": {"type": "array", "items": {"type": "string"}}},
}
GOOD_VALUE = {"veredito": "ok", "itens": ["a", "b"]}
FIRST = '```json\n{"veredito": "talvez", "itens": []}\n```'
SECOND = f"```json\n{json.dumps(GOOD_VALUE)}\n```"

FAVORITES = {
    "anthropic": ("claude-opus-5-5", "claude-sonnet-5-5"),
    "openai-codex": ("gpt-6-luna",),
    "google-antigravity": ("gemini-3.8-flash-high",),
}
CONFIGURED = ("anthropic", "openai-codex", "google-antigravity")


class _ClassicCli:
    """A Codex/Antigravity-style provider: a streamed completion over ``request['messages']``."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any]):
        self.requests.append(request)
        text = self.answers.pop(0)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=text), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])

    def seen_texts(self) -> list[str]:
        return [" ".join(str(m.get("content")) for m in request["messages"]) for request in self.requests]


class _NativeClaudeCli:
    """A Claude-CLI-style provider whose native turn gets the history as user content."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.requests: list[NativeTurnRequest] = []
        self.lock = threading.Lock()

    def native_turn_available(self, turn: Any) -> bool:
        return True

    def start_native_turn(self, req: NativeTurnRequest) -> ScriptedHandle:
        with self.lock:
            self.requests.append(req)
            answer = self.answers.pop(0)

        def _round(r: NativeTurnRequest, sink: NativeTurnSink, handle: ScriptedHandle) -> NativeTurnOutcome:
            init = _make_init(r.model)
            sink.on_init(init)
            sink.on_text(answer)
            sink.on_assistant_usage("msg_1", CliUsage(input_tokens=10, output_tokens=5))
            return NativeTurnOutcome(result=_make_result(text=answer), init=init, final_text=answer, exit_code=0)

        return ScriptedHandle(req, [_round])

    def stream(self, request: Any):  # pragma: no cover - the native path is taken
        raise AssertionError("the native turn must be used")

    def seen_texts(self) -> list[str]:
        return [json.dumps(req.user_content, ensure_ascii=False) for req in self.requests]


def _run(tmp_path: Path, cli: Any, provider: str, model: str) -> Any:
    routed: list[tuple[str, str | None]] = []

    def factory(model_id: str, provider: str | None = None):
        routed.append((model_id, provider))
        return cli

    session = _make_session(
        tmp_path, child_provider_factory=factory, favorites_by_provider=FAVORITES, configured_providers=CONFIGURED,
    )
    engine = create_local_engine(f"sqlite:///{(tmp_path / 's5.db').as_posix()}")
    metadata.create_all(engine)
    turn = session.turn
    session.agents_store = ConversationAgentStore(engine, conversation_id=turn["conversation_id"], user_id=turn["user_id"])
    try:
        outcome = session._run_subagent(
            "general", "Revise o diff e responda no esquema", provider=provider, model_id=model, output_schema=SCHEMA,
        )
    finally:
        engine.dispose()
    return outcome, routed


@pytest.mark.parametrize(
    ("kind", "provider", "model"),
    [
        ("claude-native", "anthropic", "claude-sonnet-5-5"),
        ("codex", "openai-codex", "gpt-6-luna"),
        ("antigravity", "google-antigravity", "gemini-3.8-flash-high"),
    ],
)
def test_resuming_the_same_child_to_correct_its_json_works_for_each_cli(tmp_path: Path, kind: str, provider: str, model: str) -> None:
    cli: Any = _NativeClaudeCli([FIRST, SECOND]) if kind == "claude-native" else _ClassicCli([FIRST, SECOND])
    outcome, routed = _run(tmp_path, cli, provider, model)
    assert outcome.status == "succeeded", outcome.content
    assert outcome.payload["structured_output"] == GOOD_VALUE
    assert outcome.content == canonical_json(GOOD_VALUE)
    assert outcome.payload["structured_output_attempts"] == 2
    assert outcome.payload["resumed"] is True  # the second run was the same agent resumed, not a new child
    assert {item[1] for item in routed} <= {provider, None}
    seen = cli.seen_texts()
    assert len(seen) == 2
    assert "Revise o diff" in seen[1] and "talvez" in seen[1]  # first answer is in the replayed history
    assert "veredito" in seen[1] and "ok, falha" not in seen[1]
    assert "Your JSON does not validate against the schema" in seen[1]  # and so is the correction note
    assert "Your JSON does not validate" not in seen[0]


@pytest.mark.parametrize("provider_kind", ["claude-native", "codex", "antigravity"])
def test_plan_b_is_not_needed_the_child_keeps_one_agent_id_across_corrections(tmp_path: Path, provider_kind: str) -> None:
    provider, model = {
        "claude-native": ("anthropic", "claude-sonnet-5-5"),
        "codex": ("openai-codex", "gpt-6-luna"),
        "antigravity": ("google-antigravity", "gemini-3.8-flash-high"),
    }[provider_kind]
    bad = [FIRST, FIRST, FIRST, SECOND]
    cli: Any = _NativeClaudeCli(list(bad)) if provider_kind == "claude-native" else _ClassicCli(list(bad))
    outcome, _routed = _run(tmp_path, cli, provider, model)
    assert outcome.status == "succeeded" and outcome.payload["structured_output_attempts"] == 4
    assert len(cli.seen_texts()) == 4
    # The fourth run still carries the first task and every earlier answer: nothing was dropped on resume.
    assert cli.seen_texts()[3].count("talvez") >= 3
