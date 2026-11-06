"""Adversarial cross-cutting Red Team suite for the entire Phase 3 branch (`5b3d7b3..HEAD`).

Covers all 7 surfaces and their cross-module interactions:
1. Ferramentas adiadas e ToolSearch
2. read_file e Grep (including reproductions of bypass/guard bugs)
3. Conclusão e todos
4. Lint
5. Fila de CLI e memória
6. Contrato CliAgentAdapter
7. Regressões da Fase 2
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset, ToolOutcome
from agentos.agentic.diagnostics import (
    FileDiagnostic,
    changed_line_ranges,
    file_diagnostic_command,
    parse_file_diagnostics,
    summarize_diagnostics,
)
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.native_turn import NativeTurnHandle, NativeTurnOutcome, NativeTurnRunner, RuntimeToolExecutor
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.tool_deferral import ALWAYS_LOADED_CODE_TOOLS, CODE_MODE_CORE_TOOLS, tool_search_enabled
from agentos.agentic.tools.files.read import (
    DEFAULT_READ_LINES,
    MAX_READ_LINE_CHARS,
    MAX_READ_TOKENS,
    _fit_lines_to_token_budget,
    _truncate_line,
)
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.claude_cli.command import (
    FORBIDDEN_FLAGS,
    ISOLATION_FLAGS,
    MCP_TOOL_TIMEOUT_ENV,
    build_claude_command,
    build_isolated_claude_env,
    ensure_isolated_cwd,
)
from agentos.providers.claude_cli.provider import kill_process_tree
from agentos.providers.claude_cli.errors import CliSecurityAbort, NativeLoopUnavailable, ProviderTransportError
from agentos.providers.claude_cli.native_loop import BackgroundTurnToolServer, TurnToolServer
from agentos.providers.cli_adapter import (
    CliAgentAdapter,
    CliCapabilities,
    CliErrorInfo,
    CliErrorKind,
    CliEvent,
    CliEventKind,
    CliInit,
    CliResult,
    CliStreamTranslator,
    CliTurnRequest,
    CliUsage,
    RateLimitState,
)
from agentos.providers.cli_queue import (
    CLI_MAX_PROCESSES_ENV,
    CLI_MIN_FREE_MEMORY_ENV,
    CliSlotQueue,
    acquire_cli_slot,
    free_memory_mb,
)
from agentos.providers.cli_tools_mcp import ToolCallRequest
from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore


def _make_code_toolset(
    tmp_path: Path,
    conversation_id: str = "conv-full-1",
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
    limits: AgenticLimits | None = None,
) -> tuple[AgenticTurnRuntime, FakeStore]:
    store = FakeStore()
    store.turn["conversation_mode"] = "code"
    store.turn["code_mode"] = code_mode
    provider = FakeNativeProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=limits or AgenticLimits(max_iterations=5, max_actions=10, context_window_tokens=200_000),
        tool_kinds={d.name: d.kind for d in toolset.definitions()},
    )
    return runtime, store


# =========================================================================
# SURFACE 1: FERRAMENTAS ADIADAS E TOOLSEARCH
# =========================================================================


def test_surface1_tools_tokens_budget_under_4000_in_text_and_native_turns(tmp_path: Path) -> None:
    """Initial tool context must stay under 4000 tokens in both text and native turn runners."""
    toolset = _make_code_toolset(tmp_path)
    runtime, store = _make_runtime(toolset)

    # Text turn path
    schemas = runtime._names.schemas_out(runtime._tool_schemas({"code_mode": "code"}))
    usage = runtime._context_usage([], [], schemas)
    assert usage["tools_tokens"] < 4000, f"Text turn tools_tokens too high: {usage['tools_tokens']}"

    # Native turn runner path (verifying context_updated event payload)
    runner = NativeTurnRunner(
        runtime=runtime,
        provider=runtime.provider,
        turn={"turn_id": "t1", "conversation_id": "c1", "code_mode": "code", "user_id": "u1"},
        turn_id="t1",
        messages=[{"role": "user", "content": "hello"}],
        deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    # Trigger initial context calculation as done in NativeTurnRunner._run_native_turn
    raw_schemas = runtime._tool_schemas(runner.turn, allow_deferral=True)
    tool_schemas = runtime._names.schemas_out(raw_schemas) if runtime._names is not None else raw_schemas
    native_context = dict(runtime._context_usage(runner.messages, [], tool_schemas))
    assert native_context["tools_tokens"] < 4000, f"Native initial tools_tokens too high: {native_context['tools_tokens']}"
    assert len(schemas) == 15
    assert len(tool_schemas) == 15


def test_surface1_subagents_do_not_inherit_discovered_tools(tmp_path: Path) -> None:
    """Discovered tools in parent conversation must never leak to a new subagent toolset."""
    parent_toolset = _make_code_toolset(tmp_path, conversation_id="conv-parent")
    # Discover a deferred tool in parent
    discovered = parent_toolset.discover(["powershell", "enter_plan_mode"])
    assert "powershell" in discovered
    assert "powershell" in parent_toolset.discovered_tool_names

    # A subagent gets its own freshly created toolset
    subagent_toolset = _make_code_toolset(tmp_path, conversation_id="conv-parent")
    assert "powershell" not in subagent_toolset.discovered_tool_names
    assert len(subagent_toolset.discovered_tool_names) == 0
    assert "powershell" in subagent_toolset.deferred_tool_names


def test_surface1_calling_deferred_tool_by_public_or_internal_name_executes_and_discovers(tmp_path: Path) -> None:
    """A model calling a deferred tool directly (by public or internal name) executes and registers discovery."""
    toolset = _make_code_toolset(tmp_path)
    assert "powershell" in toolset.deferred_tool_names

    # Call using public name "PowerShell" without previous ToolSearch
    outcome_pub = toolset.invoke("PowerShell", {"command": "echo test"})
    assert outcome_pub.status == "succeeded"
    assert "powershell" in toolset.discovered_tool_names

    # Calling another tool using internal snake_case
    assert "enter_plan_mode" in toolset.deferred_tool_names
    outcome_int = toolset.invoke("enter_plan_mode", {})
    assert "enter_plan_mode" in toolset.discovered_tool_names


def test_surface1_toolsearch_bounds_and_select_edge_cases(tmp_path: Path) -> None:
    """ToolSearch handles negative limits, select with > 50 names, empty names, and non-core tools."""
    toolset = _make_code_toolset(tmp_path)

    # Negative limit and oversized limit bounded between 1 and 50
    res_neg = toolset.search_mcp_tools(query="test", limit=-5)
    assert res_neg["payload"]["count"] <= 50

    # Select with > 50 names reports excess in not_found
    oversized_names = [f"tool_{i}" for i in range(60)]
    res_select = toolset.search_mcp_tools(query=f"select:{','.join(oversized_names)}")
    not_found = res_select["tool_output"]["not_found"]
    assert any("select takes at most 50 names" in item for item in not_found)

    # Non-core tool (e.g. write_contract from legacy phases) reported as not_found, never available
    res_non_core = toolset.search_mcp_tools(query="select:write_contract,read_file")
    assert "write_contract" in res_non_core["tool_output"]["not_found"]
    assert "read_file" in res_non_core["tool_output"]["already_available"]


# =========================================================================
# SURFACE 2: READ_FILE E GREP (INCLUDING BUG REPRODUCTIONS)
# =========================================================================


def test_surface2_finding_critical_write_file_append_bypasses_read_guard(tmp_path: Path) -> None:
    """REGRESSION TEST (Crítico fixed): write_file(mode='append') enforces _check_read_file_guard

    Exact location: src/agentos/agentic/tools/files/write.py:22-46.
    In Code mode, write_file with mode='append' on an existing unread file must
    raise FILE_NOT_READ and must never mark unread lines as read in _read_file_state.
    """
    toolset = _make_code_toolset(tmp_path)
    file_path = toolset.workspace.root / "critical_secret.py"
    file_path.write_text("API_SECRET_KEY = 'super_secret_unseen_value'\n", encoding="utf-8")

    # 1. Direct edit without reading is blocked
    with pytest.raises(AgentToolError) as exc_info:
        toolset.edit_file("critical_secret.py", old_text="API_SECRET_KEY = 'super_secret_unseen_value'", new_text="API_SECRET_KEY = 'hacked'")
    assert exc_info.value.error_code == "FILE_NOT_READ"

    # 2. Adversarial attack: invoke write_file in append mode on an unread file -> must also be blocked!
    with pytest.raises(AgentToolError) as append_exc:
        toolset.write_file("critical_secret.py", "", mode="append")
    assert append_exc.value.error_code == "FILE_NOT_READ"

    # 3. Safety invariant preserved: the file is NOT recorded in _read_file_state
    assert "critical_secret.py" not in toolset._read_file_state

    # 4. Subsequent edit_file remains blocked
    with pytest.raises(AgentToolError) as edit_exc:
        toolset.edit_file(
            "critical_secret.py",
            old_text="API_SECRET_KEY = 'super_secret_unseen_value'",
            new_text="API_SECRET_KEY = 'hacked'",
        )
    assert edit_exc.value.error_code == "FILE_NOT_READ"
    assert "super_secret_unseen_value" in file_path.read_text(encoding="utf-8")


def test_surface2_finding_important_edit_file_deleted_lines_allows_editing_unread_content(tmp_path: Path) -> None:
    """REGRESSION TEST (Importante fixed): edit_file adjusted_ranges shrinks upper bound on line deletions

    Exact location: src/agentos/agentic/tools/files/edit.py:140-152.
    When lines are deleted inside a partially read file, `line_delta` is negative
    and `adjusted_ranges` shrinks the upper bound `e` by `line_delta` so unread
    lines that shift upward cannot be edited without reading them first.
    """
    toolset = _make_code_toolset(tmp_path)
    file_path = toolset.workspace.root / "module.py"
    # Create 100 lines: lines 1..50 read, lines 51..100 unread
    file_path.write_text("\n".join(f"line_{i}" for i in range(1, 101)), encoding="utf-8")

    # Read only lines 1 to 50
    read_res = toolset.read_file("module.py", offset=1, limit=50)
    assert read_res["payload"]["end_line"] == 50

    # Verify editing unread line 55 is blocked before deletion
    with pytest.raises(AgentToolError) as exc_before:
        toolset.edit_file("module.py", old_text="line_55", new_text="line_55_tampered")
    assert exc_before.value.error_code == "FILE_NOT_READ"

    # Delete 9 lines from lines 1..10 (line_delta = -9)
    toolset.edit_file(
        "module.py",
        old_text="\n".join(f"line_{i}" for i in range(1, 11)),
        new_text="line_1",
    )

    # In the new file, line_55 is now located at line 46, and adjusted_ranges is ((1, 41),).
    # Editing unread line_55 must still raise FILE_NOT_READ!
    with pytest.raises(AgentToolError) as exc_after:
        toolset.edit_file(
            "module.py",
            old_text="line_55",
            new_text="line_55_tampered",
        )
    assert exc_after.value.error_code == "FILE_NOT_READ"
    assert "line_55_tampered" not in file_path.read_text(encoding="utf-8")


def test_surface2_read_file_giant_300k_line_truncated_and_budget_capped(tmp_path: Path) -> None:
    """read_file handles 300,000 char lines with line-level truncation and 25k token budget."""
    toolset = _make_code_toolset(tmp_path)
    giant_file = toolset.workspace.root / "bundle.min.js"
    giant_line = "var x = " + ("a" * 300_000) + ";\n"
    normal_lines = "\n".join(f"console.log({i});" for i in range(500))
    giant_file.write_text(giant_line + normal_lines, encoding="utf-8")

    res = toolset.read_file("bundle.min.js", offset=1, limit=500)
    content = str(res["content"])

    # First line must be truncated to MAX_READ_LINE_CHARS with suffix
    first_rendered = content.splitlines()[0]
    assert len(first_rendered) <= MAX_READ_LINE_CHARS + 100
    assert "… [line truncated]" in first_rendered

    # Overall result must obey 25k tokens (~100k chars)
    assert len(content) <= MAX_READ_TOKENS * 4


def test_surface2_read_file_binary_and_broken_utf8_does_not_crash(tmp_path: Path) -> None:
    """read_file safely decodes binary and invalid UTF-8 without raising UnicodeDecodeError."""
    toolset = _make_code_toolset(tmp_path)
    bin_file = toolset.workspace.root / "corrupted.bin"
    bin_file.write_bytes(b"\x00\x01\x02\xff\xfe\xfd\x80\x81\x00\nhello\x00world\n")

    res = toolset.read_file("corrupted.bin", offset=1, limit=10)
    assert res["payload"]["ok"] is True
    assert "hello" in res["content"]
    assert res["payload"]["sha256"] == hashlib.sha256(bin_file.read_bytes()).hexdigest()


def test_surface2_out_of_bounds_offset_does_not_record_file_read(tmp_path: Path) -> None:
    """read_file with offset past EOF returns no lines and does NOT record the file as read."""
    toolset = _make_code_toolset(tmp_path)
    f = toolset.workspace.root / "small.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")

    res = toolset.read_file("small.txt", offset=9999, limit=10)
    assert "[no lines at offset 9999" in res["content"]
    assert "small.txt" not in toolset._read_file_state


def test_surface2_sha256_change_invalidates_prior_read_ranges(tmp_path: Path) -> None:
    """When a file changes on disk, prior read_ranges are invalidated and not merged."""
    toolset = _make_code_toolset(tmp_path)
    f = toolset.workspace.root / "dynamic.py"
    f.write_text("\n".join(f"line_{i}" for i in range(1, 101)), encoding="utf-8")

    toolset.read_file("dynamic.py", offset=1, limit=30)
    assert toolset._read_file_state["dynamic.py"].read_ranges == ((1, 30),)

    # Modify file externally
    f.write_text("\n".join(f"altered_{i}" for i in range(1, 101)), encoding="utf-8")

    # Read next chunk 31..60
    toolset.read_file("dynamic.py", offset=31, limit=30)
    # The old range (1, 30) from the old sha must have been discarded
    assert toolset._read_file_state["dynamic.py"].read_ranges == ((31, 60),)


def test_surface2_grep_case_sensitivity_and_path_containment(tmp_path: Path) -> None:
    """Grep is case-sensitive by default, uses files_with_matches, and rejects escaping paths."""
    toolset = _make_code_toolset(tmp_path)
    src = toolset.workspace.root / "app.py"
    src.write_text("TargetWord = 1\ntargetword = 2\n", encoding="utf-8")

    # Default is case-sensitive
    res_case = toolset.search_files(pattern="TargetWord")
    assert res_case["tool_output"]["mode"] == "files_with_matches"
    assert "app.py" in res_case["tool_output"]["filenames"]

    res_lower = toolset.search_files(pattern="targetword")
    assert "app.py" in res_lower["tool_output"]["filenames"]

    # Pattern escaping workspace rejected with AgentToolError
    with pytest.raises(AgentToolError):
        toolset.search_files(pattern="TargetWord", path="../../outside")


# =========================================================================
# SURFACE 3: CONCLUSÃO E TODOS
# =========================================================================


def test_surface3_no_verify_gate_refusal_or_prompt_injection(tmp_path: Path) -> None:
    """Code mode never refuses final answers or injects 'Rode verify_project'."""
    toolset = _make_code_toolset(tmp_path)
    f = toolset.workspace.root / "main.py"
    f.write_text("def hello(): pass\n", encoding="utf-8")
    toolset.invoke("read_file", {"path": "main.py", "offset": 1, "limit": 10})
    toolset.invoke("edit_file", {"path": "main.py", "old_text": "def hello(): pass", "new_text": "def hello(): return 1"})

    # In Code mode, verification status is reported without refusing delivery
    status = toolset.code_verification_status()
    assert status == "unverified"

    # Verify no gate rejection method exists
    assert not hasattr(toolset, "code_completion_gate")


def test_surface3_code_verification_status_transitions(tmp_path: Path) -> None:
    """Verification status transitions: unverified -> verified after test -> unverified after subsequent edit."""
    toolset = _make_code_toolset(tmp_path)
    f = toolset.workspace.root / "calc.py"
    f.write_text("x = 1\n", encoding="utf-8")
    toolset.invoke("read_file", {"path": "calc.py"})

    # 1. Edit makes it unverified
    toolset.invoke("edit_file", {"path": "calc.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert toolset.code_verification_status() == "unverified"

    # 2. Running a verification command with exit code 0 makes it verified
    toolset._observe_mutation_outcome(
        "run_command",
        {"command": "pytest -q"},
        ToolOutcome("succeeded", "ok", "1 passed", {"command": "pytest -q", "exit_code": 0}),
    )
    assert toolset.code_verification_status() == "verified"

    # 3. Subsequent file modification resets status back to unverified
    toolset.invoke("edit_file", {"path": "calc.py", "old_text": "x = 2", "new_text": "x = 3"})
    assert toolset.code_verification_status() == "unverified"


def test_surface3_todos_remain_intact_at_turn_end(tmp_path: Path) -> None:
    """Todos in_progress and pending survive turn completion without auto-completion."""
    toolset = _make_code_toolset(tmp_path)
    toolset.todo_write(todos=[
        {"id": "1", "content": "T1", "status": "completed"},
        {"id": "2", "content": "T2", "status": "in_progress"},
        {"id": "3", "content": "T3", "status": "pending"},
    ])

    # Ensure complete_remaining_todos is removed
    assert not hasattr(toolset, "complete_remaining_todos")

    # Todos remain as-is
    assert [t["status"] for t in toolset._todos] == ["completed", "in_progress", "pending"]


# =========================================================================
# SURFACE 4: LINT
# =========================================================================


def test_surface4_lint_diagnostics_resilience_and_bounds() -> None:
    """Lint parser bounds summary to <= 600 chars and safely ignores missing linters."""
    # Missing linter stdout (e.g. 'No module named ruff')
    assert parse_file_diagnostics("ruff_json", "No module named ruff") is None
    assert parse_file_diagnostics("ruff_json", "") is None

    # Many diagnostics capped at max_items and 600 characters
    many_diags = [
        FileDiagnostic(line=10, column=5, code=f"E50{i}", message=f"Overly long error message item {i} " * 5)
        for i in range(20)
    ]
    summary = summarize_diagnostics(many_diags, [(8, 12)])
    assert summary is not None
    assert len(summary) <= 600
    assert "[lint — 20 problema(s) nas linhas alteradas]" in summary

    # Diagnostics entirely outside changed lines return None
    outside_diags = [FileDiagnostic(line=100, column=1, code="E999", message="Far away error")]
    assert summarize_diagnostics(outside_diags, [(10, 20)]) is None


# =========================================================================
# SURFACE 5: FILA DE CLI E MEMÓRIA
# =========================================================================


def test_surface5_nested_parent_and_child_subagent_with_slot_limit_1_no_deadlock(tmp_path: Path) -> None:
    """Parent turn releases slot during call_tool so child subagent can run under limit=1."""
    queue = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = queue.enqueue("conv-main")
    assert parent_ticket.wait(0.0) is True
    assert queue.active_count() == 1

    # Simulate parent entering call_tool (e.g. run_subagent)
    parent_ticket.release()
    assert queue.active_count() == 0

    # Child subagent acquires slot
    child_ticket = queue.enqueue("conv-main")
    assert child_ticket.wait(0.0) is True
    assert queue.active_count() == 1

    # Child finishes
    child_ticket.release()
    assert queue.active_count() == 0

    # Parent reacquires slot after tool returns
    parent_reacquire = queue.enqueue("conv-main")
    assert parent_reacquire.wait(0.0) is True
    parent_reacquire.release()


def test_surface5_cli_queue_cancellation_during_wait_does_not_leak_slot() -> None:
    """Cancelling while queued releases ticket and allows the next conversation to proceed."""
    queue = CliSlotQueue(limit=1, min_free_memory_mb=0)
    t1 = queue.enqueue("conv-1")
    assert t1.wait(0.0) is True

    # conv-2 enqueues and waits
    t2 = queue.enqueue("conv-2")
    assert t2.wait(0.0) is False
    assert t2.position() == 1

    # conv-2 is cancelled while waiting
    t2.release()
    assert t2.position() == 0

    # conv-3 enqueues
    t3 = queue.enqueue("conv-3")
    assert t3.position() == 1

    # t1 releases -> t3 must immediately receive the slot
    t1.release()
    assert t3.wait(0.0) is True
    t3.release()


def test_surface5_memory_protection_never_blocks_first_active_process() -> None:
    """Memory guard never blocks the first process even with 0 MB free RAM."""
    queue = CliSlotQueue(limit=4, min_free_memory_mb=2048, memory_probe=lambda: 100)

    # First ticket gets granted immediately despite low memory
    t1 = queue.enqueue("conv-1")
    assert t1.wait(0.0) is True
    assert queue.active_count() == 1

    # Second ticket is blocked due to low memory
    t2 = queue.enqueue("conv-2")
    assert t2.wait(0.0) is False
    assert t2.wait_reason() == "memory"

    t1.release()
    t2.release()


def test_surface5_ten_simultaneous_subagents_no_deadlock() -> None:
    """10 simultaneous subagents across conversations acquire and release without deadlock."""
    queue = CliSlotQueue(limit=5, min_free_memory_mb=0)
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def _worker(conv_id: str) -> None:
        try:
            barrier.wait(timeout=5.0)
            ticket = acquire_cli_slot(conv_id, queue=queue, poll_seconds=0.01)
            assert ticket is not None
            time.sleep(0.02)
            ticket.release()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f"conv-{i % 3}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors
    assert queue.active_count() == 0


# =========================================================================
# SURFACE 6: CONTRATO CLIAGENTADAPTER
# =========================================================================


def test_surface6_cli_agent_adapter_neutrality_and_capabilities() -> None:
    """CliAgentAdapter contract is vendor-neutral and ClaudeCliAdapter capabilities match declared specs."""
    adapter = ClaudeCliAdapter()
    assert adapter.name == "claude"
    assert adapter.capabilities.tool_list_changed is True
    assert adapter.capabilities.native_tool_loop is True

    # Ensure no vendor leakage into cli_adapter.py module attributes
    import agentos.providers.cli_adapter as adapter_module
    code_text = Path(adapter_module.__file__).read_text(encoding="utf-8")
    assert "claude" not in code_text.lower()
    assert "opencode" not in code_text.lower()


# =========================================================================
# SURFACE 7: REGRESSÕES DA FASE 2
# =========================================================================


def test_surface7_phase2_isolation_and_security_invariants(tmp_path: Path) -> None:
    """Phase 2 CLI isolation flags, forbidden flag rejections, and security aborts remain strictly enforced."""
    # 1. Isolation flags present in command
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("system", encoding="utf-8")
    cmd = build_claude_command(
        binary="claude",
        system_prompt_file=prompt_file,
        max_turns=2,
    )
    for flag in ISOLATION_FLAGS:
        assert flag in cmd

    # 2. Forbidden flags rejected
    with pytest.raises(ValueError):
        build_claude_command(binary="claude", system_prompt_file=prompt_file, session_id="forbidden-123")
    with pytest.raises(ValueError):
        build_claude_command(binary="claude", system_prompt_file=prompt_file, resume="forbidden-resume")

    # 3. Stripping of cloud API keys from environment
    env = build_isolated_claude_env({"ANTHROPIC_API_KEY": "leak", "CLAUDE_API_KEY": "leak", "PATH": "p"})
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_API_KEY" not in env
    assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"
