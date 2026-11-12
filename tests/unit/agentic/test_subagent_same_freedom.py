"""Subagents (also those on Codex and Antigravity) work with the main agent's freedom, never more.

Codex told its model "read-only filesystem, cwd = an isolated folder" and the subagent prompt said nothing about where
it works or what it may do, so the codex and antigravity subagents only read and never edited.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.agentic.session import SubagentCaller
from agentos.providers.claude_cli.native_loop import isolated_cli_note, with_isolated_cli_note
from tests.unit.agentic.test_nested_subagents import (
    _record_toolsets,
    _Scripted,
    _session,
    _TreeStore,
    agents_store,  # noqa: F401 - pytest fixture
)
from tests.unit.agentic.test_parallel_and_background_subagents import _text_batch


def _child_prompt(child: _Scripted) -> str:
    return str(child.requests[-1])


def test_a_subagent_is_told_where_it_works_and_that_it_has_the_main_agents_freedom(
    tmp_path: Path, agents_store, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    builds = _record_toolsets(monkeypatch)
    child = _Scripted([_text_batch("ok")])
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), child)
    session.permission_mode = "full_autonomy"
    session.host_folders = True

    assert session._run_subagent("general", "edite o arquivo").status == "succeeded"

    prompt = _child_prompt(child)
    assert str(tmp_path).replace("\\", "\\\\") in prompt or str(tmp_path) in prompt
    assert "bypassPermissions" in prompt
    assert "same freedom as the main agent" in prompt
    assert "do not use absolute paths" not in prompt
    assert builds[-1]["permission_mode"] == "full_autonomy"
    assert builds[-1]["read_only_mode"] is False


def test_a_read_only_subagent_is_told_it_is_read_only(
    tmp_path: Path, agents_store, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    child = _Scripted([_text_batch("ok")])
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), child)
    session.permission_mode = "full_autonomy"

    assert session._run_subagent("explore", "ache o arquivo").status == "succeeded"

    prompt = _child_prompt(child)
    assert "You are read-only" in prompt
    assert "same freedom as the main agent" not in prompt


def test_push_and_pr_grants_follow_the_parent_only_in_the_same_mode(tmp_path: Path, agents_store) -> None:  # noqa: F811
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    parent_tools = SimpleNamespace(
        _code_mode_permits_push=True, _code_mode_permits_pr=True,
        _code_mode_requires_approval=False, _code_mode_work_kind="implementation",
    )
    caller = SubagentCaller(agent_id="agent:main", name="Main", depth=0, permission_mode="full_autonomy", toolset=parent_tools)

    same = session._inherited_code_grants(caller, "full_autonomy", read_only=False)
    assert same["code_mode_permits_push"] is True and same["code_mode_permits_pr"] is True

    narrowed = session._inherited_code_grants(caller, "accept_edits", read_only=False)
    assert narrowed["code_mode_permits_push"] is False and narrowed["code_mode_permits_pr"] is False

    assert session._inherited_code_grants(caller, "plan_mode", read_only=True) == {}

    session.turn["code_mode_push_authorized"] = True  # a push the user authorized holds for every agent of the turn
    assert session._inherited_code_grants(caller, "accept_edits", read_only=False)["code_mode_permits_push"] is True

    no_push_parent = SubagentCaller(
        agent_id="agent:main", name="Main", depth=0, permission_mode="accept_edits",
        toolset=SimpleNamespace(_code_mode_permits_push=False, _code_mode_permits_pr=False),
    )
    session.turn["code_mode_push_authorized"] = False
    grants = session._inherited_code_grants(no_push_parent, "accept_edits", read_only=False)
    assert grants["code_mode_permits_push"] is False, "a subagent never gets more than its parent"


def test_every_cli_turn_is_told_the_clis_own_sandbox_does_not_limit_it() -> None:
    prompt = with_isolated_cli_note("You are Orin.", "orin")
    assert prompt.startswith("You are Orin.")
    assert "read-only filesystem" in prompt and "does not apply to you" in prompt
    assert "`orin` MCP server" in prompt
    assert with_isolated_cli_note(prompt, "orin") == prompt, "added once"
    assert isolated_cli_note("") == isolated_cli_note("orin")


def test_the_cli_note_states_the_orin_permission_mode_and_workspace(tmp_path) -> None:
    bypass = with_isolated_cli_note(
        "You are Orin.", "orin", permission_mode="full_autonomy", workspace_root=tmp_path
    )
    assert "Orin permission mode: bypassPermissions." in bypass
    assert str(tmp_path) in bypass
    assert "mcp__orin" in bypass and "functions.exec" in bypass
    assert "read-only" not in bypass.split("## Orin session", 1)[1]

    plan = with_isolated_cli_note("You are Orin.", "orin", permission_mode="plan_mode", workspace_root=tmp_path)
    assert "Orin permission mode: plan." in plan and "this turn is read-only" in plan.lower()

    # The public names Claude Code uses are accepted as well, and a read-only subagent says so.
    assert "Orin permission mode: acceptEdits." in with_isolated_cli_note("x", "orin", permission_mode="acceptEdits")
    assert "read-only agent" in with_isolated_cli_note("x", "orin", read_only=True)
    # Added once, also with the session facts.
    assert with_isolated_cli_note(bypass, "orin", permission_mode="full_autonomy", workspace_root=tmp_path) == bypass
    # Without facts the note is the plain one (backward compatible).
    assert "## Orin session" not in isolated_cli_note("orin")


def test_native_turn_request_carries_the_effective_permission_mode() -> None:
    from agentos.agentic.native_turn import NativeTurnRunner
    from agentos.code_mode.models import CodePermissionMode

    def mode_of(toolset) -> str | None:
        runner = NativeTurnRunner.__new__(NativeTurnRunner)
        runner.runtime = SimpleNamespace(toolset=toolset)
        return runner._effective_permission_mode()

    assert mode_of(SimpleNamespace(_permission_mode=CodePermissionMode.FULL_AUTONOMY, _plan_mode_active=False)) == "full_autonomy"
    assert mode_of(SimpleNamespace(_permission_mode=CodePermissionMode.ASK_PERMISSIONS, _plan_mode_active=True)) == "plan_mode"
    assert mode_of(SimpleNamespace()) is None
