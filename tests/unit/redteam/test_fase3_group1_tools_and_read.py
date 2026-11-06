"""Red Team adversarial tests for Phase 3 Group 1 (Tasks 1-5: Tool Deferral, ToolSearch, Read & Grep)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.agent_tools import (
    CODE_MODE_CORE_TOOLS,
    AgentToolset,
    ToolDefinition,
    ToolOutcome,
)
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.tool_deferral import (
    ALWAYS_LOADED_CODE_TOOLS,
    FOLLOW_UP_TOOLS,
    tool_search_enabled,
)
from agentos.agentic.tools.files.read import (
    MAX_READ_LINE_CHARS,
    MAX_READ_TOKENS,
    _fit_lines_to_token_budget,
    _truncate_line,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.system_prompt import CodePromptOptions
from agentos.providers.cli_tools_mcp import ToolCallRequest
from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore
from tests.unit.redteam._platform_tools import (
    DEFERRED_MUTATION_TOOL,
    deferred_mutation_arguments,
)

TURN = {"turn_id": "turn-rt-1", "conversation_id": "conv-rt-1", "user_id": "user-1", "provider": "openrouter", "code_mode": "code"}


def _call(index: int, name: str, arguments: dict[str, Any]) -> dict[str, str]:
    return {"id": f"c{index}", "name": name, "arguments": json.dumps(arguments)}


class _FakeMcpProvider:
    def definitions(self) -> tuple[ToolDefinition, ...]:
        names = ("mcp__github__create_issue", "mcp__github__list_issues", "mcp__slack__send_message")
        return tuple(
            ToolDefinition(
                name,
                f"Create or manage {name} on remote MCP server",
                {"type": "object", "properties": {"title": {"type": "string"}}},
                lambda **_: {"content": "mcp-ok"},
                "mcp",
            )
            for name in names
        )


def _schema_names(runtime: AgenticTurnRuntime, turn: dict[str, Any] | None = None) -> list[str]:
    out: list[str] = []
    for item in runtime._tool_schemas(turn or {"code_mode": "code"}):
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        out.append(str(fn.get("name") or ""))
    return out


def _make_code_toolset(
    tmp_path: Path,
    conversation_id: str = "conv-rt-1",
    **kwargs: Any,
) -> AgentToolset:
    ws = ConversationWorkspace(tmp_path, conversation_id)
    defaults: dict[str, Any] = {
        "code_mode_active": True,
        "public_tool_names": True,
        "permission_mode": "full_autonomy",
        "run_subagent": lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        "check_subagent": lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        "retrieval": object(),
    }
    defaults.update(kwargs)
    return AgentToolset(ws, **defaults)


def _make_runtime(
    toolset: AgentToolset,
    *,
    code_mode: str = "code",
) -> tuple[AgenticTurnRuntime, FakeStore]:
    store = FakeStore()
    store.turn["conversation_mode"] = "code"
    store.turn["code_mode"] = code_mode
    provider = FakeNativeProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=200_000),
    )
    return runtime, store


# ============================================================================
# SURFACE 1: Deferred Tools & ToolSearch (Tasks 1, 2, 3)
# ============================================================================


def test_r3_1_always_loaded_code_tools_exact_15_literal() -> None:
    """Spec R3.1 / Task 1: ALWAYS_LOADED_CODE_TOOLS must match the exact 15 core tools."""
    expected_internal = {
        "read_file",
        "write_file",
        "edit_file",
        "glob_files",
        "search_files",
        "run_command",
        "todo_write",
        "run_subagent",
        "ask_user",
        "search_code",
        "project_map",
        "verify_project",
        "preview_start",
        "preview_stop",
        "search_mcp_tools",
    }
    assert ALWAYS_LOADED_CODE_TOOLS == expected_internal
    assert len(ALWAYS_LOADED_CODE_TOOLS) == 15
    assert ALWAYS_LOADED_CODE_TOOLS <= CODE_MODE_CORE_TOOLS


def test_tool_search_enabled_env_toggle_truthy_and_falsy() -> None:
    """tool_search_enabled defaults to True and only turns off on explicit falsy tokens."""
    assert tool_search_enabled({}) is True
    assert tool_search_enabled({"ORIN_CODE_TOOL_SEARCH": "1"}) is True
    assert tool_search_enabled({"ORIN_CODE_TOOL_SEARCH": "true"}) is True
    for falsy in ("0", "false", "FALSE", "off", "no", "  0  "):
        assert tool_search_enabled({"ORIN_CODE_TOOL_SEARCH": falsy}) is False


def test_calling_deferred_tool_directly_without_tool_search_executes_and_publishes_next_turn(
    tmp_path: Path,
) -> None:
    """Calling a deferred tool directly (by public name or internal name) without ToolSearch
    must execute immediately (never 'Unknown tool') and publish its schema on the next request."""
    toolset = _make_code_toolset(tmp_path)
    ws_file = toolset.workspace.root / "hello.txt"
    ws_file.write_text("hello world\n", encoding="utf-8")

    runtime, store = _make_runtime(toolset)
    initial_names = set(_schema_names(runtime, store.turn))
    assert "list_files" not in initial_names
    assert "list_files" in toolset.deferred_tool_names

    # Call by public name 'ListFiles' directly without ToolSearch
    outcome = toolset.invoke("ListFiles", {"path": "."})
    assert outcome.status == "succeeded", f"Direct deferred tool call failed: {outcome}"
    assert "hello.txt" in outcome.content
    assert "list_files" in toolset.discovered_tool_names
    assert "list_files" not in toolset.deferred_tool_names

    # Next _tool_schemas call must publish list_files
    next_names = set(_schema_names(runtime, store.turn))
    assert "list_files" in next_names


def test_discover_rejects_non_core_and_nonexistent_tools(tmp_path: Path) -> None:
    """Attempting to discover tools outside CODE_MODE_CORE_TOOLS (e.g. write_contract,
    report_verification) or nonexistent tools must never add them to discovered_tool_names."""
    toolset = _make_code_toolset(tmp_path)
    newly = toolset.discover(
        ["write_contract", "WriteContract", "report_verification", "ReportVerification", "evil_tool", ""]
    )
    assert newly == []
    assert "write_contract" not in toolset.discovered_tool_names
    assert "report_verification" not in toolset.discovered_tool_names
    assert "evil_tool" not in toolset.discovered_tool_names


def test_discovery_does_not_leak_across_toolset_instances_or_conversations(tmp_path: Path) -> None:
    """Discovering a tool in one AgentToolset instance must never leak into another turn/conversation."""
    ts1 = _make_code_toolset(tmp_path, conversation_id="conv-1")
    ts2 = _make_code_toolset(tmp_path, conversation_id="conv-2")

    ts1.discover(["WebFetch", "TaskCreate"])
    assert {"fetch_url", "task_create"} <= ts1.discovered_tool_names
    assert ts2.discovered_tool_names == frozenset()
    assert {"fetch_url", "task_create"} <= ts2.deferred_tool_names


def test_tool_schemas_deterministic_order_preserves_prefix_cache(tmp_path: Path) -> None:
    """_tool_schemas must preserve the canonical definitions() order regardless of the order
    in which tools were discovered."""
    ts_a = _make_code_toolset(tmp_path, conversation_id="conv-a")
    ts_b = _make_code_toolset(tmp_path, conversation_id="conv-b")

    ts_a.discover(["WebFetch", "ListFiles", "TaskCreate"])
    ts_b.discover(["TaskCreate", "ListFiles", "WebFetch"])

    rt_a, store_a = _make_runtime(ts_a)
    rt_b, store_b = _make_runtime(ts_b)

    names_a = _schema_names(rt_a, store_a.turn)
    names_b = _schema_names(rt_b, store_b.turn)
    assert names_a == names_b
    assert names_a == _schema_names(rt_a, store_a.turn)


def test_follow_up_tools_only_discovered_when_parent_succeeds(tmp_path: Path) -> None:
    """FOLLOW_UP_TOOLS must only be discovered when the parent tool call has status == 'succeeded'.
    Failed or hook-denied calls must NOT auto-discover follow-up tools."""
    bus = LifecycleBus()
    bus.subscribe(
        lambda ev: LifecycleDecision(permission_decision="deny", blocked=True, reason="blocked by redteam")
        if ev.name is HookEvent.PRE_TOOL_USE
        else None
    )
    toolset = _make_code_toolset(tmp_path, lifecycle=bus)
    assert "exit_worktree" not in toolset.discovered_tool_names
    assert "exit_plan_mode" not in toolset.discovered_tool_names

    # Calling EnterWorktree when blocked by PreToolUse hook must NOT discover exit_worktree
    out_wt = toolset.invoke("EnterWorktree", {"name": "wt1"})
    assert out_wt.status != "succeeded"
    assert out_wt.error_code == "HOOK_DENIED"
    assert "exit_worktree" not in toolset.discovered_tool_names

    # Calling EnterPlanMode when blocked by PreToolUse hook must NOT discover exit_plan_mode
    out_plan = toolset.invoke("EnterPlanMode", {})
    assert out_plan.status != "succeeded"
    assert out_plan.error_code == "HOOK_DENIED"
    assert "exit_plan_mode" not in toolset.discovered_tool_names


def test_deferred_and_discovered_tools_cannot_bypass_permissions_plan_mode_readonly_or_hooks(
    tmp_path: Path,
) -> None:
    """Deferred tools (called directly or after ToolSearch discovery) must still enforce:
    1. dont_ask permission mode refusal on deferred mutation tools
    2. Plan mode read-only barrier on deferred mutation tools
    3. Subagent _read_only_mode barrier on deferred mutation tools
    4. PreToolUse hook deny/stop (HOOK_DENIED)
    """
    # 1. dont_ask mode with non_interactive_ask="deny" refuses deferred mutation tool discovered via ToolSearch
    ts_perm = _make_code_toolset(
        tmp_path,
        conversation_id="conv-perm",
        permission_mode="dont_ask",
        non_interactive_ask="deny",
    )
    ts_perm.invoke("ToolSearch", {"query": f"select:{DEFERRED_MUTATION_TOOL}"})
    assert DEFERRED_MUTATION_TOOL in ts_perm.discovered_tool_names
    out_denied = ts_perm.invoke(
        DEFERRED_MUTATION_TOOL,
        deferred_mutation_arguments(ts_perm.workspace.root),
    )
    assert out_denied.status == "failed"
    assert out_denied.error_code in {"PERMISSION_DENIED", "CODE_PERMISSION_DENIED", "TOOL_REFUSED"}

    # 2. Plan mode read-only barrier on deferred mutation tool
    ts_plan = _make_code_toolset(
        tmp_path,
        conversation_id="conv-plan",
        permission_mode="plan_mode",
    )
    ts_plan.discover([DEFERRED_MUTATION_TOOL])
    out_plan = ts_plan.invoke(
        DEFERRED_MUTATION_TOOL,
        deferred_mutation_arguments(ts_plan.workspace.root),
    )
    assert out_plan.status == "failed"
    assert out_plan.error_code in {"PLAN_MODE_BLOCKED", "CODE_PLAN_MODE_READONLY", "PERMISSION_DENIED", "TOOL_REFUSED"}

    # 3. _read_only_mode barrier on deferred mutation tool
    ts_ro = _make_code_toolset(
        tmp_path,
        conversation_id="conv-ro",
        read_only_mode=True,
    )
    assert DEFERRED_MUTATION_TOOL not in ts_ro.deferred_tool_names
    out_ro = ts_ro.invoke(
        DEFERRED_MUTATION_TOOL,
        deferred_mutation_arguments(ts_ro.workspace.root),
    )
    assert out_ro.status == "failed"
    assert out_ro.error_code == "READ_ONLY_SUBAGENT"

    # Cross-platform deferred file mutation tool (notebook_edit) must also be refused on every OS
    # across dont_ask, plan_mode, and read_only_mode without mutating demo.ipynb on disk.
    nb_args_perm = deferred_mutation_arguments(ts_perm.workspace.root, tool_name="notebook_edit")
    ts_perm.discover(["notebook_edit"])
    out_nb_perm = ts_perm.invoke("notebook_edit", nb_args_perm)
    assert out_nb_perm.status == "failed"
    assert out_nb_perm.error_code == "CODE_PERMISSION_DENIED"
    assert "x = 2" not in (ts_perm.workspace.root / "demo.ipynb").read_text(encoding="utf-8")

    nb_args_plan = deferred_mutation_arguments(ts_plan.workspace.root, tool_name="notebook_edit")
    ts_plan.discover(["notebook_edit"])
    out_nb_plan = ts_plan.invoke("notebook_edit", nb_args_plan)
    assert out_nb_plan.status == "failed"
    assert out_nb_plan.error_code == "CODE_PLAN_MODE_READONLY"
    assert "x = 2" not in (ts_plan.workspace.root / "demo.ipynb").read_text(encoding="utf-8")

    nb_args_ro = deferred_mutation_arguments(ts_ro.workspace.root, tool_name="notebook_edit")
    assert "notebook_edit" not in ts_ro.deferred_tool_names
    out_nb_ro = ts_ro.invoke("notebook_edit", nb_args_ro)
    assert out_nb_ro.status == "failed"
    assert out_nb_ro.error_code == "READ_ONLY_SUBAGENT"
    assert "x = 2" not in (ts_ro.workspace.root / "demo.ipynb").read_text(encoding="utf-8")

    # 4. PreToolUse hook blocking a deferred tool (called directly without ToolSearch)
    bus = LifecycleBus()
    bus.subscribe(
        lambda ev: LifecycleDecision(permission_decision="deny", blocked=True, reason="blocked by hook")
        if ev.name is HookEvent.PRE_TOOL_USE and ev.payload.get("tool_name") == "ListFiles"
        else None
    )
    ts_hook = _make_code_toolset(tmp_path, conversation_id="conv-hook", lifecycle=bus)
    out_hook = ts_hook.invoke("ListFiles", {"path": "."})
    assert out_hook.status == "failed"
    assert out_hook.error_code == "HOOK_DENIED"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell só existe no Windows")
def test_powershell_specific_barriers_on_windows(tmp_path: Path) -> None:
    """Explicitly verify PowerShell refusal across dont_ask, plan_mode, and read_only_mode on Windows."""
    ts_perm = _make_code_toolset(
        tmp_path,
        conversation_id="conv-ps-perm",
        permission_mode="dont_ask",
        non_interactive_ask="deny",
    )
    ts_perm.invoke("ToolSearch", {"query": "select:powershell"})
    assert "powershell" in ts_perm.discovered_tool_names
    out_denied = ts_perm.invoke("powershell", {"command": "Write-Output pwn"})
    assert out_denied.status == "failed"
    assert out_denied.error_code in {"PERMISSION_DENIED", "CODE_PERMISSION_DENIED", "TOOL_REFUSED"}

    ts_plan = _make_code_toolset(
        tmp_path,
        conversation_id="conv-ps-plan",
        permission_mode="plan_mode",
    )
    ts_plan.discover(["powershell"])
    out_plan = ts_plan.invoke("powershell", {"command": "Write-Output mutate"})
    assert out_plan.status == "failed"
    assert out_plan.error_code in {"PLAN_MODE_BLOCKED", "CODE_PLAN_MODE_READONLY", "PERMISSION_DENIED", "TOOL_REFUSED"}

    ts_ro = _make_code_toolset(
        tmp_path,
        conversation_id="conv-ps-ro",
        read_only_mode=True,
    )
    assert "powershell" not in ts_ro.deferred_tool_names
    out_ro = ts_ro.invoke("powershell", {"command": "Write-Output mutate"})
    assert out_ro.status == "failed"
    assert out_ro.error_code == "READ_ONLY_SUBAGENT"


def test_native_turn_runtime_tool_executor_allows_deferred_and_discovered_tools(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (CRITICAL - native_turn.py:328, 370-380, 538-547):
    In the Native CLI Loop (RuntimeToolExecutor), _valid_public_names and _valid_internal_names
    are frozen at __init__ from runtime._tool_schemas(turn) (which only returns the 15
    ALWAYS_LOADED_CODE_TOOLS). When the model calls ToolSearch(select:ListFiles) and then calls
    mcp__orin__ListFiles (or calls a deferred tool directly without ToolSearch),
    RuntimeToolExecutor.call_tool rejects it with 'Unknown tool' because it only checks
    _valid_internal_names and active_mcp_tool_names, ignoring deferred_tool_names and
    discovered_tool_names!"""
    toolset = _make_code_toolset(tmp_path)
    (toolset.workspace.root / "sample.txt").write_text("native loop test\n", encoding="utf-8")
    runtime, store = _make_runtime(toolset)
    executor = RuntimeToolExecutor(runtime=runtime, turn=store.turn)
    try:
        # 1. Discover ListFiles via ToolSearch in the native loop
        ts_res = executor.call_tool(
            ToolCallRequest(
                name="mcp__orin__ToolSearch",
                arguments={"query": "select:ListFiles"},
            )
        )
        assert ts_res.is_error is False
        assert "list_files" in toolset.discovered_tool_names

        # 2. Now invoke the discovered tool mcp__orin__ListFiles through RuntimeToolExecutor
        lf_res = executor.call_tool(
            ToolCallRequest(
                name="mcp__orin__ListFiles",
                arguments={"path": "."},
            )
        )
        assert lf_res.is_error is False, (
            f"Native CLI loop rejected discovered tool ListFiles as unknown: {lf_res.text!r}"
        )
        assert "sample.txt" in lf_res.text
    finally:
        executor.close()


def test_tool_search_select_classifies_already_available_and_non_core_tools_accurately(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (IMPORTANT - tools/mcp/admin.py:191-195):
    1. Native tools whose definition.kind == 'mcp' (such as ToolSearch itself, which is in
       ALWAYS_LOADED_CODE_TOOLS, or InspectMcpServer / CallMcpTool on a second select: call
       after discovery) fall into the `else: not_found.append(name)` branch because line 192
       checks `definition.kind != 'mcp'`!
    2. Internal tools outside CODE_MODE_CORE_TOOLS (such as `write_contract` or
       `report_verification`) have `definition.kind != 'mcp'`, so `select:write_contract` in
       Code Mode falsely reports them under `already_available` even though they are never
       published in Code Mode!"""
    toolset = _make_code_toolset(tmp_path, mcp_provider=_FakeMcpProvider())

    # Case 1: Selecting an already-available native tool with kind='mcp' (ToolSearch)
    # and a discovered native MCP tool (InspectMcpServer) on a second select call.
    toolset.invoke("ToolSearch", {"query": "select:InspectMcpServer"})
    assert "inspect_mcp_server" in toolset.discovered_tool_names

    out_again = toolset.invoke("ToolSearch", {"query": "select:ToolSearch,InspectMcpServer"})
    output_again = out_again.data["tool_output"]
    assert output_again["not_found"] == [], (
        f"Already-available native tools with kind='mcp' were reported as not_found: {output_again}"
    )
    assert set(output_again["already_available"]) == {"ToolSearch", "InspectMcpServer"}

    # Case 2: Selecting `write_contract` (not in CODE_MODE_CORE_TOOLS) in Code Mode
    out_non_core = toolset.invoke("ToolSearch", {"query": "select:write_contract"})
    output_non_core = out_non_core.data["tool_output"]
    assert output_non_core["already_available"] == [], (
        f"Non-core tool write_contract falsely reported as already_available in Code Mode: {output_non_core}"
    )
    assert output_non_core["not_found"] == ["write_contract"]


def test_tool_search_does_not_crowd_out_mcp_tools_with_fake_orin_mcp_prefix(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (IMPORTANT - tools/mcp/admin.py:134-143, 212):
    1. _native_search_documents indexes every native deferred tool with fake server_slug='orin'
       and qualified_name='mcp__orin__<Name>', injecting 'mcp' and 'orin' into name_tokens
       (weight 10.0 each).
    2. admin.py:212 concatenates `(_run_engine(native_engine) + _run_engine(mcp_engine))[:safe_limit]`
       without sorting by score!
    Consequently, a query like 'mcp github create issue' (or '+mcp github') matches all ~35
    native deferred tools on the 'mcp' token and fills all `safe_limit` slots with unrelated
    native tools, returning ZERO real MCP tools!"""
    toolset = _make_code_toolset(tmp_path, mcp_provider=_FakeMcpProvider())

    out = toolset.invoke("ToolSearch", {"query": "mcp github create issue", "max_results": 5})
    assert out.status == "succeeded"
    matches = out.data["tool_output"]["matches"]
    assert "mcp__github__create_issue" in matches, (
        f"Real MCP tool mcp__github__create_issue was crowded out by native tools matching 'mcp': {matches}"
    )


def test_session_snapshot_key_invalidates_when_deferred_tools_change() -> None:
    """BUG REPRODUCTION (MENOR - session.py:4152-4172):
    AgenticService._snapshot_key ignores options.deferred_tool_names, so changing tool deferral
    (e.g. toggling ORIN_CODE_TOOL_SEARCH) returns a stale cached system prompt snapshot."""
    svc = object.__new__(TurnSession)
    svc._memory = None
    svc._code_prompt_options = CodePromptOptions(
        tool_names=frozenset({"read_file", "search_mcp_tools"}),
        public_names=True,
        deferred_tool_names=frozenset({"list_files", "fetch_url"}),
    )
    k_with_deferred = svc._snapshot_key()

    svc._code_prompt_options = CodePromptOptions(
        tool_names=frozenset({"read_file", "search_mcp_tools"}),
        public_names=True,
        deferred_tool_names=frozenset(),
    )
    k_without_deferred = svc._snapshot_key()
    assert k_with_deferred != k_without_deferred, (
        "TurnSession._snapshot_key failed to include options.deferred_tool_names in hash material!"
    )


# ============================================================================
# SURFACE 2: read_file & Grep (Tasks 4 & 5)
# ============================================================================


def test_read_file_giant_minified_line_truncated_and_uses_ellipsis_suffix(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (MENOR/IMPORTANT - tools/files/read.py:47):
    Spec Task 4 requires `_truncate_line` to append `' \u2026 [line truncated]'` (with unicode
    ellipsis U+2026), and a 300,000-char line must be truncated to < 4,000 chars.
    In read.py:47, the ellipsis was corrupted into ASCII question mark `' ? [line truncated]'`."""
    toolset = _make_code_toolset(tmp_path)
    giant_path = toolset.workspace.root / "bundle.min.js"
    giant_path.write_text("var a=" + ("x" * 300_000) + ";\nconsole.log(a);\n", encoding="utf-8")

    outcome = toolset.invoke("Read", {"path": "bundle.min.js"})
    assert outcome.status == "succeeded"
    assert len(outcome.content) < 4_000
    assert "[line truncated]" in outcome.content
    assert "bundle.min.js" in toolset._read_file_state

    # Check exact truncation suffix per Task 4 spec (" \u2026 [line truncated]")
    truncated = _truncate_line("a" * (MAX_READ_LINE_CHARS + 50))
    assert truncated.endswith(" \u2026 [line truncated]"), (
        f"_truncate_line corrupted ellipsis into '?': {truncated[-30:]!r}"
    )


def test_fit_lines_to_token_budget_does_not_double_truncate_already_truncated_line() -> None:
    """Verify _fit_lines_to_token_budget handles already-truncated lines within budget."""
    raw = "x" * 5000
    once = _truncate_line(raw)
    count = _fit_lines_to_token_budget([once], first=1, budget_tokens=MAX_READ_TOKENS)
    assert count == 1


def test_read_file_binary_nul_bytes_and_broken_utf8_do_not_crash(tmp_path: Path) -> None:
    """Binary files (NUL bytes) and invalid UTF-8 sequences must never raise unhandled exceptions."""
    toolset = _make_code_toolset(tmp_path)
    bin_path = toolset.workspace.root / "binary.dat"
    bin_path.write_bytes(b"\x00\x01\x02\xff\xfe\xfdhello\x00world\n")

    bad_utf8_path = toolset.workspace.root / "broken_utf8.txt"
    bad_utf8_path.write_bytes(b"valid line 1\n\x80\x81\xff\xfe invalid utf8 line 2\nline 3\n")

    out_bin = toolset.invoke("Read", {"path": "binary.dat"})
    assert out_bin.status == "succeeded"

    out_utf8 = toolset.invoke("Read", {"path": "broken_utf8.txt"})
    assert out_utf8.status == "succeeded"
    assert "valid line 1" in out_utf8.content
    assert "line 3" in out_utf8.content


def test_read_file_token_budget_caps_at_25k_tokens_and_not_12k_chars(tmp_path: Path) -> None:
    """read_file must allow outputs above 12,000 chars (up to 25,000 tokens / 100,000 chars)
    and stop before exceeding 25,000 estimated tokens."""
    toolset = _make_code_toolset(tmp_path)
    medium_path = toolset.workspace.root / "medium.txt"
    # 300 lines x 100 chars = ~30,000 chars (> 12,000 old cap, < 100,000 new cap)
    medium_path.write_text(
        "\n".join(f"line-{i:04d}-" + ("a" * 90) for i in range(1, 301)) + "\n",
        encoding="utf-8",
    )
    out_med = toolset.invoke("Read", {"path": "medium.txt", "offset": 1, "limit": 300})
    assert out_med.status == "succeeded"
    assert len(out_med.content) > 25_000, "read_file output was prematurely truncated near 12,000 chars"
    assert "line-0300-" in out_med.content

    # Huge file: 2,000 lines x 500 chars = ~1,000,000 chars -> must be capped at <= 25,000 tokens
    huge_path = toolset.workspace.root / "huge.txt"
    huge_path.write_text(
        "\n".join(f"L{i:04d}:" + ("z" * 490) for i in range(1, 2001)) + "\n",
        encoding="utf-8",
    )
    out_huge = toolset.invoke("Read", {"path": "huge.txt", "offset": 1, "limit": 2000})
    assert out_huge.status == "succeeded"
    assert len(out_huge.content) // 4 <= MAX_READ_TOKENS
    assert "continue with Read(" in out_huge.content or "output stopped at line" in out_huge.content


def test_read_file_not_truncated_to_12k_when_hook_adds_additional_context(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (IMPORTANT - agent_tools.py:736 & 748):
    While agent_tools.py:766 uses `limit = max(MAX_TOOL_RESULT_CHARS, int(definition.max_result_chars or 0))`
    (100,000 chars for `read_file`), line 736 in `invoke()` calls
    `_append_reminder(outcome, reminders, MAX_TOOL_RESULT_CHARS)` with the hardcoded 12,000 limit!
    Whenever a PreToolUse or PostToolUse hook emits `additional_context`, a 30,000-char `read_file`
    result is silently sliced down to 12,000 chars while `_read_file_state` still records all
    300 lines as read!"""
    bus = LifecycleBus()
    bus.subscribe(
        lambda ev: LifecycleDecision(additional_context=["hook-note"])
        if ev.name is HookEvent.POST_TOOL_USE
        else None
    )
    toolset = _make_code_toolset(tmp_path, lifecycle=bus)
    medium_path = toolset.workspace.root / "medium_hook.txt"
    medium_path.write_text(
        "\n".join(f"line-{i:04d}-" + ("b" * 90) for i in range(1, 301)) + "\n",
        encoding="utf-8",
    )

    out = toolset.invoke("Read", {"path": "medium_hook.txt", "offset": 1, "limit": 300})
    assert out.status == "succeeded"
    assert "line-0300-" in out.content, (
        f"_append_reminder truncated read_file output to 12,000 chars (len={len(out.content)}) "
        "when hook emitted additional_context!"
    )


def test_read_file_out_of_range_offset_does_not_mark_file_as_read_for_edit(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (IMPORTANT - tools/files/read.py:451-467):
    When `offset` is beyond EOF (e.g. offset=500 on a 10-line file), `read_file` delivers
    0 lines (`[no lines at offset 500; the file has 10 lines]`), yet still calls
    `_record_file_read(path, offset=500, limit=2000, total_lines=10, explicit_read=True)`.
    This bugs out in two ways:
    1. `edit_file` allows editing the file even though 0 lines of its content were ever read!
    2. A second `read_file(offset=500)` returns `[File unchanged since last read (..., lines 10-10)]`
       instead of `[no lines at offset 500; the file has 10 lines]`!"""
    toolset = _make_code_toolset(tmp_path)
    p = toolset.workspace.root / "ten_lines.py"
    p.write_text("\n".join(f"x_{i} = {i}" for i in range(1, 11)) + "\n", encoding="utf-8")

    out_oob = toolset.invoke("Read", {"path": "ten_lines.py", "offset": 500, "limit": 50})
    assert out_oob.status == "succeeded"
    assert "no lines at offset 500" in out_oob.content

    out_oob_again = toolset.invoke("Read", {"path": "ten_lines.py", "offset": 500, "limit": 50})
    assert "no lines at offset 500" in out_oob_again.content

    # Try to edit the file without ever having read any valid lines
    out_edit = toolset.invoke(
        "Edit",
        {"path": "ten_lines.py", "old_string": "x_1 = 1", "new_string": "x_1 = 999"},
    )
    assert out_edit.status == "failed", (
        "edit_file succeeded on a file where only an out-of-bounds offset (0 lines delivered) was read!"
    )
    assert out_edit.error_code == "FILE_NOT_READ"


def test_edit_file_refuses_editing_lines_beyond_token_budget_cut(
    tmp_path: Path,
) -> None:
    """BUG REPRODUCTION (IMPORTANT - tools/files/read.py:222-247 & edit.py:63):
    When `read_file` stops early due to the 25k token budget (e.g. a 140KB file with 350 lines
    of 400 chars where `read_file` delivers only lines 1..242 and cuts lines 243..350),
    `_record_file_read` records `offset=1, limit=242, total_lines=350`.
    However, `_check_read_file_guard` only checks that `record is not None` and `record.sha256`
    matches — it never checks whether the edited lines fall within the read range (or whether
    the file was only partially read)!
    Attempting to edit line 340 (which was cut by the token budget and never delivered) must be
    refused until the model reads that section of the file."""
    toolset = _make_code_toolset(tmp_path)
    p = toolset.workspace.root / "partially_read.py"
    lines = [f"val_{i:04d} = '{'a' * 380}'" for i in range(1, 351)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out_read = toolset.invoke("Read", {"path": "partially_read.py", "offset": 1, "limit": 350})
    assert out_read.status == "succeeded"
    assert "val_0340" not in out_read.content

    rec = toolset._read_file_state.get("partially_read.py")
    assert rec is not None
    assert rec.limit < 350

    # Attempt to edit line 340 which was NEVER delivered in Read output
    out_edit = toolset.invoke(
        "Edit",
        {
            "path": "partially_read.py",
            "old_string": lines[339],
            "new_string": "val_0340 = 'mutated_without_reading'",
        },
    )
    assert out_edit.status == "failed", (
        f"edit_file allowed modifying line 340 even though Read output was cut at line {rec.limit} "
        "by the token budget!"
    )
    assert out_edit.error_code == "FILE_NOT_READ"

    # Reading the remaining window unlocks editing line 340
    out_read_tail = toolset.invoke("Read", {"path": "partially_read.py", "offset": rec.limit + 1, "limit": 200})
    assert out_read_tail.status == "succeeded"
    assert "val_0340" in out_read_tail.content

    out_edit_ok = toolset.invoke(
        "Edit",
        {
            "path": "partially_read.py",
            "old_string": lines[339],
            "new_string": "val_0340 = 'mutated_after_reading'",
        },
    )
    assert out_edit_ok.status == "succeeded"


def test_phase1_regressions_same_batch_guard_stub_release_and_crlf_preservation(
    tmp_path: Path,
) -> None:
    """Phase 1 Regression Suite:
    1. Same-batch read-before-edit refusal (`READ_IN_SAME_BATCH`).
    2. Unchanged-file stub on second read, and release of stub via `forget_read_content`.
    3. CRLF line-ending preservation on `Edit` and `Write`.
    """
    toolset = _make_code_toolset(tmp_path)
    runtime, _ = _make_runtime(toolset)
    crlf_path = toolset.workspace.root / "win_crlf.py"
    crlf_path.write_bytes(b"def alpha():\r\n    return 1\r\n")

    # 1. Same-batch Read + Edit must be refused with READ_IN_SAME_BATCH
    batch_results = runtime._run_toolset(
        TURN,
        [
            _call(1, "read_file", {"path": "win_crlf.py"}),
            _call(2, "edit_file", {"path": "win_crlf.py", "old_text": "return 1", "new_text": "return 2"}),
        ],
    )
    assert batch_results[0]["status"] == "succeeded"
    assert batch_results[1]["status"] == "failed"
    assert batch_results[1]["error_code"] == "READ_IN_SAME_BATCH"

    # 2. Second Read returns unchanged stub; forgetting read content allows full re-read
    r2 = toolset.invoke("Read", {"path": "win_crlf.py"})
    assert r2.status == "succeeded"
    assert "[File unchanged since last read" in r2.content

    assert toolset.forget_read_content("win_crlf.py") is True
    r3 = toolset.invoke("Read", {"path": "win_crlf.py"})
    assert r3.status == "succeeded"
    assert "def alpha():" in r3.content

    # 3. Edit in subsequent call preserves CRLF line endings
    e_ok = toolset.invoke(
        "Edit",
        {"path": "win_crlf.py", "old_string": "return 1", "new_string": "return 2"},
    )
    assert e_ok.status == "succeeded"
    raw_after_edit = crlf_path.read_bytes()
    assert b"\r\n" in raw_after_edit
    assert b"\n" not in raw_after_edit.replace(b"\r\n", b"")

    # Write on existing CRLF file also preserves CRLF
    r4 = toolset.invoke("Read", {"path": "win_crlf.py"})
    assert r4.status == "succeeded"
    w_ok = toolset.invoke(
        "Write",
        {"path": "win_crlf.py", "content": "def alpha():\n    return 3\n"},
    )
    assert w_ok.status == "succeeded"
    raw_after_write = crlf_path.read_bytes()
    assert b"\r\n" in raw_after_write
    assert b"\n" not in raw_after_write.replace(b"\r\n", b"")


def test_grep_defaults_case_sensitivity_aliases_invalid_regex_and_path_traversal(
    tmp_path: Path,
) -> None:
    """Surface 2 Grep checks:
    1. Default `ignore_case=False` (case-sensitive by default) and `output_mode='files_with_matches'`.
    2. `-i: True` and `case_sensitive: False` aliases enable case-insensitive matching.
    3. Invalid regex returns a clean error (`TOOL_REFUSED` / `TOOL_EXECUTION_ERROR`), never an unhandled crash.
    4. Path traversal (`../`) outside the workspace is refused (`TOOL_REFUSED`).
    """
    toolset = _make_code_toolset(tmp_path)
    src_dir = toolset.workspace.root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "upper.py").write_text("SECRET_TOKEN = 'abc'\n", encoding="utf-8")
    (src_dir / "lower.py").write_text("secret_token = 'xyz'\n", encoding="utf-8")

    # 1. Default Grep: case-sensitive (`ignore_case=False`) and `files_with_matches` mode
    out_default = toolset.invoke("Grep", {"pattern": "SECRET_TOKEN"})
    assert out_default.status == "succeeded"
    assert "src/upper.py" in out_default.content
    assert "src/lower.py" not in out_default.content
    # files_with_matches returns only paths, not line contents
    assert "'abc'" not in out_default.content

    # 2a. `-i: True` alias enables case-insensitive search
    out_dash_i = toolset.invoke("Grep", {"pattern": "SECRET_TOKEN", "-i": True})
    assert out_dash_i.status == "succeeded"
    assert "src/upper.py" in out_dash_i.content
    assert "src/lower.py" in out_dash_i.content

    # 2b. `case_sensitive: False` alias enables case-insensitive search
    out_cs_false = toolset.invoke("Grep", {"pattern": "SECRET_TOKEN", "case_sensitive": False})
    assert out_cs_false.status == "succeeded"
    assert "src/upper.py" in out_cs_false.content
    assert "src/lower.py" in out_cs_false.content

    # 3. Malformed regex fails cleanly
    out_bad_re = toolset.invoke("Grep", {"pattern": "[unclosed group("})
    assert out_bad_re.status == "failed"
    assert out_bad_re.error_code in {"TOOL_REFUSED", "TOOL_EXECUTION_ERROR"}

    # 4. Path outside workspace (`../`) is blocked
    out_traversal = toolset.invoke("Grep", {"pattern": "SECRET", "path": "../../outside"})
    assert out_traversal.status == "failed"
    assert out_traversal.error_code == "TOOL_REFUSED"
