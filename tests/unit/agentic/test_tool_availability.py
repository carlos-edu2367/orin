"""A core tool offered last turn and gone this turn is announced; Plan Mode toggles refresh tools/list."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.tool_availability import clear_previous_tools, lost_tools_notice, note_lost_tools, offered_core_tools
from agentos.agentic.workspace import ConversationWorkspace


@pytest.fixture(autouse=True)
def _fresh():
    clear_previous_tools()
    yield
    clear_previous_tools()


def _toolset(tmp_path: Path, **kwargs) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_tools"), **kwargs)


def test_nothing_is_announced_on_the_first_turn_or_when_nothing_changed(tmp_path: Path) -> None:
    assert lost_tools_notice("c1", _toolset(tmp_path)) == ""
    assert lost_tools_notice("c1", _toolset(tmp_path)) == ""


def test_a_tool_lost_to_plan_mode_is_named_with_its_reason(tmp_path: Path) -> None:
    lost_tools_notice("c1", _toolset(tmp_path, public_tool_names=True))
    notice = lost_tools_notice("c1", _toolset(tmp_path, public_tool_names=True, permission_mode="plan_mode"))
    assert notice.startswith("Tools no longer available this turn: ")
    assert "Bash (plan mode)" in notice and "Edit (plan mode)" in notice and "Write (plan mode)" in notice
    assert "Read" not in notice


def test_a_tool_lost_because_the_terminal_is_off_and_it_is_said_once(tmp_path: Path) -> None:
    lost_tools_notice("c1", _toolset(tmp_path))
    notice = lost_tools_notice("c1", _toolset(tmp_path, enable_terminal=False))
    assert "run_command (the terminal is off)" in notice
    assert lost_tools_notice("c1", _toolset(tmp_path, enable_terminal=False)) == ""  # already gone: not news


def test_conversations_do_not_share_their_previous_tools(tmp_path: Path) -> None:
    lost_tools_notice("c1", _toolset(tmp_path))
    assert lost_tools_notice("c2", _toolset(tmp_path, enable_terminal=False)) == ""


def test_the_notice_becomes_a_system_reminder(tmp_path: Path) -> None:
    queue = ReminderQueue()
    note_lost_tools(queue, "c1", _toolset(tmp_path))
    note_lost_tools(queue, "c1", _toolset(tmp_path, enable_terminal=False))
    texts = [item.text for item in queue.drain()]
    assert len(texts) == 1 and texts[0].startswith("Tools no longer available this turn")


def test_plan_mode_blocks_the_mutating_core_tools_only(tmp_path: Path) -> None:
    names = offered_core_tools(_toolset(tmp_path, permission_mode="plan_mode"))
    assert "read_file" in names and not names & {"run_command", "edit_file", "write_file"}


def test_entering_plan_mode_changes_the_tools_list_snapshot(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    executor = SimpleNamespace(runtime=SimpleNamespace(toolset=toolset))
    before = RuntimeToolExecutor._snapshot_discovered(executor)
    toolset.enter_plan_mode("look first")
    after = RuntimeToolExecutor._snapshot_discovered(executor)
    assert before != after
    toolset.exit_plan_mode()
    assert RuntimeToolExecutor._snapshot_discovered(executor) == before
