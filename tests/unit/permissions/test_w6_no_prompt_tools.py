"""W6 (W4-R26): tools that never prompt are not read-only, and plan mode keeps only the harmless ones."""
from __future__ import annotations

from pathlib import Path

from agentos.code_mode.models import CodePermissionMode as M
from agentos.permissions.engine import NO_PROMPT_TOOLS, READ_ONLY_TOOLS

from tests.unit.permissions.test_engine import _engine


def test_the_lists_do_not_overlap() -> None:
    assert not NO_PROMPT_TOOLS & READ_ONLY_TOOLS
    assert {"ask_user", "push_notification", "use_skill", "schedule_wakeup"} <= NO_PROMPT_TOOLS


def test_no_prompt_tools_run_unprompted_outside_plan_mode(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for name in NO_PROMPT_TOOLS:
        assert engine.decide(name, {}, M.ASK_PERMISSIONS).behavior == "allow", name
        assert engine.decide(name, {}, M.DONT_ASK).behavior == "allow", name


def test_plan_mode_refuses_a_wakeup_but_keeps_asking_the_person(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine.decide("schedule_wakeup", {"delay_seconds": 60}, M.PLAN_MODE).behavior == "deny"
    assert engine.decide("schedule_wakeup", {}, M.ACCEPT_EDITS, plan_mode_active=True).behavior == "deny"
    for name in ("ask_user", "push_notification", "use_skill", "read_file"):
        assert engine.decide(name, {}, M.PLAN_MODE).behavior == "allow", name


def test_a_deny_rule_still_wins(tmp_path: Path) -> None:
    engine = _engine(tmp_path, user={"permissions": {"deny": ["PushNotification"]}})
    assert engine.decide("push_notification", {}, M.ASK_PERMISSIONS).behavior == "deny"
