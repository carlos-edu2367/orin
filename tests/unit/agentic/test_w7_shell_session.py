"""W7: the session runs a shell turn's command without a prompt — except in plan mode or when policy disables bypass."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.session import TurnSession
from agentos.settings.paths import orin_managed_dir

from tests.unit.cli_code.test_cli_turn_session import _Agents, _Store


def _session(tmp_path: Path, **turn) -> TurnSession:
    store = _Store()
    store.history_for_turn = lambda current: [{"role": "user", "content": "!git status"}]  # type: ignore[method-assign]
    return TurnSession(turn={**store.turn, "code_mode": "code", "turn_kind": "shell", **turn}, store=store, agents_store=_Agents(),
                       memory_store=None, provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False)


def test_a_shell_turn_runs_its_command_unprompted(tmp_path: Path) -> None:
    session = _session(tmp_path, permission_mode="ask_permissions")
    runtime = session.build_runtime()
    assert runtime.shell_command == "git status" and session.permission_mode == "full_autonomy"


def test_plan_mode_keeps_refusing(tmp_path: Path) -> None:
    session = _session(tmp_path, permission_mode="plan_mode")
    session.build_runtime()
    assert session.permission_mode == "plan_mode"


def test_a_policy_that_disables_bypass_still_wins(tmp_path: Path) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
    session = _session(tmp_path, permission_mode="ask_permissions")
    session.build_runtime()
    assert session.permission_mode == "ask_permissions"


def test_an_ordinary_turn_is_not_a_shell_turn(tmp_path: Path) -> None:
    session = _session(tmp_path, turn_kind=None)
    assert session.build_runtime().shell_command is None
