from __future__ import annotations

import pytest

from agentos.agentic.agent_tools import AgentToolset, ConversationWorkspace, ToolOutcome
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.tool_deferral import ALWAYS_LOADED_CODE_TOOLS


def _names(runtime: AgenticTurnRuntime) -> set[str]:
    return {s["function"]["name"] for s in runtime._tool_schemas({"code_mode": "code"})}


@pytest.fixture
def toolset(tmp_path) -> AgentToolset:
    return AgentToolset(
        ConversationWorkspace(tmp_path, "c1"),
        code_mode_active=True,
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
    )


@pytest.fixture
def runtime(toolset: AgentToolset) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)


def test_code_mode_publishes_only_the_always_loaded_core(toolset: AgentToolset, runtime: AgenticTurnRuntime) -> None:
    assert _names(runtime) == ALWAYS_LOADED_CODE_TOOLS & {d.name for d in toolset.definitions()}
    assert "fetch_url" in toolset.deferred_tool_names and "list_files" in toolset.deferred_tool_names


def test_a_discovered_tool_is_published_from_the_next_request(toolset: AgentToolset, runtime: AgenticTurnRuntime) -> None:
    assert toolset.discover(["WebFetch", "nope", "read_file"]) == ["fetch_url"]
    assert "fetch_url" in _names(runtime) and "fetch_url" not in toolset.deferred_tool_names


def test_calling_a_deferred_tool_directly_runs_it_and_publishes_it(
    toolset: AgentToolset, runtime: AgenticTurnRuntime, tmp_path
) -> None:
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    outcome = toolset.invoke("ListFiles", {})
    assert outcome.status == "succeeded"
    assert "list_files" in _names(runtime)


def test_follow_up_tools_are_discovered_after_their_parent_succeeds(
    toolset: AgentToolset, runtime: AgenticTurnRuntime
) -> None:
    toolset.invoke("run_command", {"command": "echo hi"})
    assert {"read_process_output", "stop_process"} <= _names(runtime)


def test_headless_tools_option_disables_deferral(toolset: AgentToolset, runtime: AgenticTurnRuntime) -> None:
    runtime.offered_tools = frozenset({"read_file", "fetch_url"})
    assert _names(runtime) == {"read_file", "fetch_url"}


def test_env_off_publishes_the_whole_code_mode_set(
    toolset: AgentToolset, runtime: AgenticTurnRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIN_CODE_TOOL_SEARCH", "off")
    assert "fetch_url" in _names(runtime) and "list_files" in _names(runtime)


def test_deferral_can_be_turned_off_per_call(toolset: AgentToolset, runtime: AgenticTurnRuntime) -> None:
    names = {s["function"]["name"] for s in runtime._tool_schemas({"code_mode": "code"}, allow_deferral=False)}
    assert "fetch_url" in names
