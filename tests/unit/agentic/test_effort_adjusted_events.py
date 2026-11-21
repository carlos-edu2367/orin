from __future__ import annotations

from pathlib import Path
from typing import Any

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.command import build_antigravity_command
from agentos.providers.cli_adapter import CliEffortAdjustment, CliTurnRequest
from agentos.providers.codex_cli.adapter import CodexCliAdapter
from agentos.providers.codex_cli.command import build_codex_command


def test_codex_command_clamps_unsupported_max_and_records_adjustment(tmp_path: Path) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("System instructions", encoding="utf-8")

    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6-compact",
        effort="max",
        persist_session=False,
    )
    argv = build_codex_command(req, supported_efforts=("low", "medium", "high"))
    assert 'model_reasoning_effort="high"' in argv
    assert req.effort_adjustment == CliEffortAdjustment(
        requested="max",
        applied="high",
        reason="unsupported_level",
    )


def test_codex_adapter_clamps_ultra_for_luna_and_records_adjustment(tmp_path: Path) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("System instructions", encoding="utf-8")

    adapter = CodexCliAdapter()
    req = CliTurnRequest(
        binary="codex",
        system_prompt_file=prompt_file,
        model="gpt-6-luna",
        effort="ultra",
        persist_session=False,
    )
    argv = adapter.build_command(req)
    assert 'model_reasoning_effort="max"' in argv
    assert req.effort_adjustment == CliEffortAdjustment(
        requested="ultra",
        applied="max",
        reason="unsupported_level",
    )


def test_antigravity_slug_fixed_omits_flag_and_records_adjustment(tmp_path: Path) -> None:
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("System instructions", encoding="utf-8")

    req = CliTurnRequest(
        binary="agy",
        system_prompt_file=prompt_file,
        model="gemini-4-argon-high",
        effort="low",
    )
    argv = build_antigravity_command(req)
    assert "--effort" not in argv
    assert req.effort_adjustment == CliEffortAdjustment(
        requested="low",
        applied="high",
        reason="model_slug_fixed",
    )

    adapter = AntigravityCliAdapter()
    req2 = CliTurnRequest(
        binary="agy",
        system_prompt_file=prompt_file,
        model="gemini-4-argon-high",
        effort="low",
    )
    argv2 = adapter.build_command(req2)
    assert "--effort" not in argv2
    assert req2.effort_adjustment == CliEffortAdjustment(
        requested="low",
        applied="high",
        reason="model_slug_fixed",
    )


class _EventStore:
    def __init__(self, provider: str, model_id: str) -> None:
        self.turn = {
            "turn_id": "t-1",
            "conversation_id": "c-1",
            "user_id": "u-1",
            "execution_id": "e-1",
            "provider": provider,
            "model_id": model_id,
            "user_message_id": "um-1",
            "assistant_message_id": "am-1",
        }
        self.events: list[tuple[Any, str, dict]] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "hi"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record(self, turn, event_type, summary, payload=None, **kwargs):
        self.events.append((event_type, summary, dict(payload or {})))

    def main_agent_id(self, turn):
        return "agent:c-1:main"


def test_session_emits_effort_adjusted_event_for_antigravity_slug_mismatch(tmp_path: Path) -> None:
    class _Agents:
        def list(self):
            return []

    store = _EventStore("google-antigravity", "gemini-4-argon-high")
    session = TurnSession(
        turn={**store.turn, "effort": "low"},
        store=store,
        agents_store=_Agents(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        enable_subagents=False,
    )
    runtime = session.build_runtime()
    assert runtime.effort == "high"
    adjusted = [
        (ev_type, payload)
        for ev_type, _summary, payload in store.events
        if ev_type == AgentActivityEventType.EFFORT_ADJUSTED
    ]
    assert len(adjusted) == 1
    assert adjusted[0][1] == {
        "requested": "low",
        "applied": "high",
        "reason": "model_slug_fixed",
        "model": "gemini-4-argon-high",
    }


def test_runtime_fallback_clamps_effort_and_emits_effort_adjusted() -> None:
    store = _EventStore("anthropic", "claude-opus-5-5")
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        effort="xhigh",
        fallback_models=("claude-opus-4-6",),
        fallback_provider_factory=lambda model_id: object(),
    )
    switched = runtime._switch_to_fallback(store.turn, reason="rate_limited")
    assert switched is True
    assert store.turn["model_id"] == "claude-opus-4-6"
    # claude-opus-4-6 supports low, medium, high, max (not xhigh) -> clamped to high
    assert runtime.effort == "high"
    adjusted = [
        payload
        for ev_type, _summary, payload in store.events
        if ev_type == AgentActivityEventType.EFFORT_ADJUSTED
    ]
    assert adjusted == [
        {
            "requested": "xhigh",
            "applied": "high",
            "reason": "unsupported_level",
            "model": "claude-opus-4-6",
        }
    ]
