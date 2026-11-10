"""agy calling one of its own harmless tools (manage_task) resumes the turn; a dangerous one still aborts it.

Regression (10/11/2026, turn_4cb01cd2): Gemini called manage_task (action list) next to mcp_orin_Bash; the
security abort ended the turn as PROVIDER_STREAM_FAILED after one attempt."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from agentos.agentic import native_turn as native_turn_module
from agentos.agentic.native_turn import MAX_BUILTIN_TOOL_DETOURS, builtin_tool_reminder
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.antigravity_cli import AntigravityCliAdapter
from agentos.providers.claude_cli.provider import ClaudeCliProvider, clear_circuit_breaker
from tests.unit.agentic.test_native_turn import FakeStore, _make_toolset

FAKE_ANTIGRAVITY_SCRIPT = str(
    Path(__file__).resolve().parents[1] / "providers" / "antigravity_cli" / "fake_antigravity.py"
)


class _Store(FakeStore):
    def __init__(self, prompt: str) -> None:
        super().__init__(model_id="gemini-3.8-flash-low")
        self.turn["provider"] = "google-antigravity"
        self._prompt = prompt

    def history_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": self._prompt}]


class _Provider(ClaudeCliProvider):
    """The real CLI provider and native loop, on the fake agy; available without a real agy installed."""

    def __init__(self) -> None:
        super().__init__(binary=FAKE_ANTIGRAVITY_SCRIPT, adapter=AntigravityCliAdapter())
        self.started: list[Any] = []

    def native_turn_available(self, turn: Mapping[str, object]) -> bool:
        return True

    def start_native_turn(self, request: Any) -> Any:
        self.started.append(request)
        return super().start_native_turn(request)


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str) -> tuple[Any, _Store, _Provider]:
    clear_circuit_breaker()
    workdir = tmp_path / "agy-workdir"
    monkeypatch.setattr(native_turn_module, "_resolve_native_cwd", lambda adapter_name="claude": workdir)
    monkeypatch.setattr("agentos.agentic.runtime.STREAM_RESUME_BACKOFF_SECONDS", (0.0,), raising=False)
    store = _Store(prompt)
    provider = _Provider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_make_toolset(ConversationWorkspace(tmp_path, "c1")),
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    return runtime.run("t1", turn=store.turn), store, provider


def _user_text(request: Any) -> str:
    return "\n".join(str(block.get("text") or "") for block in request.user_content if isinstance(block, Mapping))


def test_a_harmless_builtin_tool_resumes_the_turn_from_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, store, provider = _run(tmp_path, monkeypatch, "BENIGN_BUILTIN: is the render finished?")

    assert result.state == "completed", (result, store.finished, store.effects_finished)
    assert len(provider.started) == 2
    resumed = _user_text(provider.started[1])
    assert "`manage_task` does not exist here" in resumed
    # It names the Orin tools as the agy sees them (Code mode: Claude Code names behind mcp_orin_).
    assert "`mcp_orin_TaskOutput`" in resumed
    assert "BENIGN_BUILTIN: is the render finished?" in resumed
    provider_effects = [e for e in store.effects_finished if e["effect_id"] in {"eff_1", "eff_2"}]
    assert provider_effects[0]["state"] == "NOT_APPLIED"
    assert provider_effects[0]["error_code"] == "PROVIDER_BUILTIN_TOOL_REFUSED"
    assert "PONG_AGY" in "".join(store.deltas)
    assert not any(item.get("failed") for item in store.finished)
    assert not [p for p in (tmp_path / "agy-workdir").iterdir() if p.name.startswith("run-")]


def test_a_model_that_keeps_calling_it_stops_after_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, store, provider = _run(tmp_path, monkeypatch, "BENIGN_BUILTIN_ALWAYS")

    assert result.state == "failed"
    assert len(provider.started) == 1 + MAX_BUILTIN_TOOL_DETOURS


def test_a_dangerous_builtin_tool_still_aborts_the_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, store, provider = _run(tmp_path, monkeypatch, "Run FORBIDDEN_BUILTIN now")

    assert result.state == "failed"
    assert len(provider.started) == 1
    assert not any(e.get("error_code") == "PROVIDER_BUILTIN_TOOL_REFUSED" for e in store.effects_finished)


def test_the_reminder_names_only_orin_tools() -> None:
    text = builtin_tool_reminder("list_dir", ["ListFiles"], prefix="mcp_orin_")
    assert "`list_dir` does not exist here" in text
    assert "`mcp_orin_ListFiles`" in text
    assert "did not run" in text
    assert "Nothing replaces it" in builtin_tool_reminder("finish", [])
