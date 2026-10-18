from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.session import TurnSession, _workspace_guidance_files
from agentos.settings.paths import orin_user_dir
from agentos.settings.watcher import reset_snapshots

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_session", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant",
}


class Store:
    def __init__(self, history: list[dict[str, object]]) -> None:
        self.history = history
        self.activity: list[tuple[AgentActivityEventType, str]] = []

    def load(self, turn_id: str) -> dict:
        return TURN

    def history_for_turn(self, turn) -> list[dict[str, object]]:
        return list(self.history)

    def delta(self, turn, text: str) -> None:
        pass

    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        self.activity.append((event_type, summary))

    def main_agent_id(self, turn) -> str:
        return f"agent:{turn['conversation_id']}:main"


class Agents:
    def list(self) -> list[dict]:
        return []


def _make_session(tmp_path: Path, history: list[dict[str, object]], **kwargs) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=Store(history), agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, **kwargs,
    )


def _bus():
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    return bus, events


def test_guidance_files_are_listed(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("more", encoding="utf-8")
    (tmp_path / "CONVENTIONS.md").write_text("", encoding="utf-8")  # empty files are not instructions
    assert {path.name for path in _workspace_guidance_files(tmp_path)} == {"CLAUDE.md", "AGENTS.md"}


def test_first_turn_emits_session_start_prompt_and_instructions(tmp_path: Path) -> None:
    reset_snapshots()
    bus, events = _bus()
    session = _make_session(tmp_path, [{"role": "user", "content": "build it"}], lifecycle=bus)
    (session.workspace.root / "CLAUDE.md").write_text("rules", encoding="utf-8")
    session.build_runtime()
    names = [event.name for event in events]
    assert HookEvent.SESSION_START in names
    assert HookEvent.USER_PROMPT_SUBMIT in names
    assert HookEvent.INSTRUCTIONS_LOADED in names
    prompt = dict(next(e for e in events if e.name is HookEvent.USER_PROMPT_SUBMIT).payload)
    assert prompt["prompt"] == "build it"
    assert dict(next(e for e in events if e.name is HookEvent.SESSION_START).payload)["source"] == "startup"


def test_later_turn_has_no_session_start(tmp_path: Path) -> None:
    bus, events = _bus()
    history = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "two"}]
    session = _make_session(tmp_path, history, lifecycle=bus)
    session.build_runtime()
    names = [event.name for event in events]
    assert HookEvent.SESSION_START not in names
    assert HookEvent.USER_PROMPT_SUBMIT in names


def test_experimental_flag_turns_on_public_names(tmp_path: Path) -> None:
    settings_file = orin_user_dir() / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"experimental": {"claudeToolNames": True}}), encoding="utf-8")
    session = _make_session(tmp_path, [{"role": "user", "content": "x"}])
    runtime = session.build_runtime()
    assert runtime.toolset.name_translator is not None
    assert session.effective_settings.experimental("claudeToolNames") is True


def test_config_change_between_turns(tmp_path: Path) -> None:
    reset_snapshots()
    bus, events = _bus()
    first = _make_session(tmp_path, [{"role": "user", "content": "x"}], lifecycle=bus)
    first.build_runtime()
    assert HookEvent.CONFIG_CHANGE not in [event.name for event in events]
    (first.workspace.root / ".orin").mkdir(exist_ok=True)
    (first.workspace.root / ".orin" / "settings.json").write_text("{}", encoding="utf-8")
    events.clear()
    _make_session(tmp_path, [{"role": "user", "content": "y"}], lifecycle=bus).build_runtime()
    change = [dict(e.payload) for e in events if e.name is HookEvent.CONFIG_CHANGE]
    assert change and change[0]["source"] == "project_settings" and change[0]["file_path"].endswith("settings.json")


def test_repository_cannot_turn_on_public_names(tmp_path: Path) -> None:
    session = _make_session(tmp_path, [{"role": "user", "content": "x"}])
    _write_json(session.workspace.root / ".claude" / "settings.json", {"experimental": {"claudeToolNames": True}})
    _write_json(session.workspace.root / ".orin" / "settings.local.json", {"experimental": {"claudeToolNames": True}})
    runtime = session.build_runtime()
    assert runtime.toolset.name_translator is None
    assert session.effective_settings.experimental("claudeToolNames") is False


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_crashed_subagent_still_emits_subagent_stop_with_public_type(tmp_path: Path) -> None:
    import pytest

    bus, events = _bus()
    session = _make_session(tmp_path, [{"role": "user", "content": "x"}], lifecycle=bus)

    def crash(**_kwargs):
        raise RuntimeError("subagent crashed")

    session._execute_subagent_run_inner = crash
    with pytest.raises(RuntimeError):
        session._execute_subagent_run(agent_id="agent-1", clean="explore", builtin_key="explore")
    starts = [dict(e.payload) for e in events if e.name is HookEvent.SUBAGENT_START]
    stops = [dict(e.payload) for e in events if e.name is HookEvent.SUBAGENT_STOP]
    assert len(starts) == 1 and len(stops) == 1
    assert starts[0]["agent_type"] == "Explore" and stops[0]["agent_type"] == "Explore"
    assert stops[0]["status"] == "failed" and stops[0]["agent_id"] == "agent-1"
