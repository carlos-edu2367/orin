"""W6-R4: a turn that asks for bypass mode runs as ask_permissions while policy disables bypass; subagents follow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.agent_definitions import child_permission_mode
from agentos.agentic.session import TurnSession
from agentos.settings.paths import orin_managed_dir


class _Store:
    def __init__(self) -> None:
        self.events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []

    def main_agent_id(self, turn: Mapping[str, Any]) -> str:
        return f"agent:{turn.get('conversation_id', 'conv')}:main"

    def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "hello"}]

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        self.events.append((event_type, summary, dict(payload or {})))


class _Agents:
    def list(self) -> list[dict[str, Any]]:
        return []


def _session(tmp_path: Path, store: _Store, permission_mode: str | None, code_mode: str = "code") -> TurnSession:
    turn: dict[str, Any] = {
        "turn_id": "turn-r4", "conversation_id": "conv-r4", "user_id": "user-1", "execution_id": "exec-r4",
        "code_mode": code_mode, "provider": "anthropic", "model_id": "claude-sonnet-4-6",
    }
    if permission_mode is not None:
        turn["permission_mode"] = permission_mode
    return TurnSession(turn=turn, store=store, agents_store=_Agents(), memory_store=None,
                       provider_factory=lambda: None, workspace_root=tmp_path)


def test_managed_disable_downgrades_full_autonomy_and_says_so(tmp_path: Path) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
    store = _Store()
    session = _session(tmp_path, store, "full_autonomy")
    runtime = session.build_runtime()
    assert session.permission_mode == "ask_permissions"
    assert session._in_plan_mode is False
    assert getattr(runtime.toolset._permission_broker, "mode", None) == "ask_permissions"
    notices = [summary for _type, summary, _payload in store.events if "bypass mode is disabled by policy" in summary]
    assert len(notices) == 1


def test_managed_disable_downgrades_the_code_mode_default_too(tmp_path: Path) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
    session = _session(tmp_path, _Store(), None)
    session.build_runtime()
    assert session.permission_mode != "full_autonomy"


def test_without_policy_full_autonomy_is_kept(tmp_path: Path) -> None:
    store = _Store()
    session = _session(tmp_path, store, "full_autonomy")
    session.build_runtime()
    assert session.permission_mode == "full_autonomy"
    assert not [summary for _type, summary, _payload in store.events if "disabled by policy" in summary]


def test_a_subagent_cannot_regain_bypass_under_policy(tmp_path: Path) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
    session = _session(tmp_path, _Store(), "full_autonomy")
    session.build_runtime()
    assert child_permission_mode(session.permission_mode, "bypassPermissions") != "full_autonomy"
    assert child_permission_mode(session.permission_mode, "full_autonomy") != "full_autonomy"
