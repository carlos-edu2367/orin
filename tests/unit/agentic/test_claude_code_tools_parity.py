from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from agentos.agentic.agent_tools import AgentToolset, _terminate_pid
from agentos.agentic.workspace import ConversationWorkspace


def test_read_file_unchanged_deduplication_and_edit_guard(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-read-dedup")
    target = workspace.root / "src" / "module.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line1 = 1\nline2 = 2\nline3 = 3\nline4 = 4\nline5 = 5\n", encoding="utf-8")

    toolset = AgentToolset(workspace, code_mode_active=True)

    # First read returns full content and unchanged=False
    first = toolset.invoke("read_file", {"path": "src/module.py", "offset": 1, "limit": 400})
    assert first.status == "succeeded"
    assert first.payload["ok"] is True
    assert first.payload["unchanged"] is False
    assert first.payload["line_count"] == 5
    assert first.payload["start_line"] == 1
    assert first.payload["end_line"] == 5
    assert "line1 = 1" in first.content
    first_sha = first.payload["sha256"]
    assert isinstance(first_sha, str) and len(first_sha) == 64

    # Second read with identical (offset, limit) and unchanged file on disk returns compact stub
    second = toolset.invoke("read_file", {"path": "src/module.py", "offset": 1, "limit": 400})
    assert second.status == "succeeded"
    assert second.payload["ok"] is True
    assert second.payload["unchanged"] is True
    assert second.payload["sha256"] == first_sha
    assert second.payload["line_count"] == 5
    assert second.payload["start_line"] == 1
    assert second.payload["end_line"] == 5
    assert "[File unchanged since last read" in second.content
    assert "line1 = 1" not in second.content

    # Reading a different slice (offset=2, limit=2) returns full content for that slice
    slice_read = toolset.invoke("read_file", {"path": "src/module.py", "offset": 2, "limit": 2})
    assert slice_read.status == "succeeded"
    assert slice_read.payload["unchanged"] is False
    assert slice_read.payload["start_line"] == 2
    assert slice_read.payload["end_line"] == 3
    assert "line2 = 2" in slice_read.content

    # Edit guard still succeeds after deduplicated/sliced read
    edit_res = toolset.invoke(
        "edit_file",
        {"path": "src/module.py", "old_text": "line1 = 1", "new_text": "line1 = 100"},
    )
    assert edit_res.status == "succeeded"

    # Reading after edit_file returns updated full content (not unchanged stub)
    after_edit = toolset.invoke("read_file", {"path": "src/module.py", "offset": 1, "limit": 400})
    assert after_edit.status == "succeeded"
    assert after_edit.payload["unchanged"] is False
    assert "line1 = 100" in after_edit.content
    assert after_edit.payload["sha256"] != first_sha


def test_read_file_pdf_pages_selection(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    workspace = ConversationWorkspace(tmp_path, "conv-read-pdf")
    pdf_path = workspace.root / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    toolset = AgentToolset(workspace)
    res = toolset.invoke("read_file", {"path": "sample.pdf", "pages": "1-2"})
    assert res.status == "succeeded"
    assert res.payload["ok"] is True
    assert res.payload["pages"] == [1, 2]
    assert res.payload["total_pages"] == 3


def test_glob_files_matches_patterns_sorted_by_mtime_and_ignores_noise(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-glob")
    f_old = workspace.root / "src" / "old_mod.py"
    f_new = workspace.root / "src" / "sub" / "new_mod.py"
    f_ts = workspace.root / "src" / "sub" / "comp.tsx"
    f_ignored_nm = workspace.root / "node_modules" / "pkg" / "ignored.py"
    f_ignored_git = workspace.root / ".git" / "hooks" / "ignored.py"

    for p in (f_old, f_new, f_ts, f_ignored_nm, f_ignored_git):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# content\n", encoding="utf-8")

    now = time.time()
    os.utime(f_old, (now - 100, now - 100))
    os.utime(f_new, (now - 10, now - 10))

    toolset = AgentToolset(workspace)
    schema_names = {d.name for d in toolset.definitions()}
    assert "glob_files" in schema_names

    outcome = toolset.invoke("glob_files", {"pattern": "**/*.py"})
    assert outcome.status == "succeeded"
    assert outcome.payload["ok"] is True
    assert outcome.payload["matches"] == ["src/sub/new_mod.py", "src/old_mod.py"]
    assert outcome.payload["count"] == 2
    assert outcome.payload["truncated"] is False

    # Scoped to subdirectory via path parameter and called via execute()
    sub_res = toolset.execute("glob_files", {"pattern": "*.tsx", "path": "src/sub"})
    assert sub_res["ok"] is True
    assert sub_res["matches"] == ["src/sub/comp.tsx"]
    assert sub_res["count"] == 1


def test_search_files_grep_modes_context_lines_and_multiline(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-grep")
    alpha = workspace.root / "src" / "alpha.py"
    beta = workspace.root / "src" / "beta.py"
    alpha.parent.mkdir(parents=True, exist_ok=True)
    alpha.write_text(
        "import os\n"
        "def target_func():\n"
        "    # TARGET_MARKER one\n"
        "    return 1\n"
        "# TARGET_MARKER two\n",
        encoding="utf-8",
    )
    beta.write_text(
        "def other():\n"
        "    # TARGET_MARKER three\n"
        "    return 2\n",
        encoding="utf-8",
    )

    toolset = AgentToolset(workspace)

    # 1. output_mode="files_with_matches"
    files_out = toolset.invoke(
        "search_files",
        {"pattern": "TARGET_MARKER", "output_mode": "files_with_matches"},
    )
    assert files_out.status == "succeeded"
    assert files_out.payload["ok"] is True
    assert files_out.payload["files"] == ["src/alpha.py", "src/beta.py"]
    assert files_out.payload["count"] == 2

    # 2. output_mode="count"
    count_out = toolset.invoke(
        "search_files",
        {"pattern": "TARGET_MARKER", "output_mode": "count"},
    )
    assert count_out.status == "succeeded"
    assert count_out.payload["ok"] is True
    assert count_out.payload["counts"] == {"src/alpha.py": 2, "src/beta.py": 1}

    # 3. output_mode="content" with before_context and after_context
    ctx_out = toolset.invoke(
        "search_files",
        {
            "pattern": "TARGET_MARKER one",
            "path": "src/alpha.py",
            "output_mode": "content",
            "before_context": 1,
            "after_context": 1,
        },
    )
    assert ctx_out.status == "succeeded"
    matches = ctx_out.payload["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "src/alpha.py"
    assert matches[0]["line"] == 3
    assert matches[0]["before_context"] == ["def target_func():"]
    assert matches[0]["after_context"] == ["    return 1"]

    # 4. multiline=True across lines
    multi_out = toolset.invoke(
        "search_files",
        {
            "pattern": r"def target_func\(\):\s+# TARGET_MARKER one",
            "multiline": True,
        },
    )
    assert multi_out.status == "succeeded"
    assert multi_out.payload["count"] == 1
    assert multi_out.payload["matches"][0]["path"] == "src/alpha.py"

    # 5. Backward compatibility with query, file_pattern, is_regex=False, case_sensitive=True, head_limit
    compat_out = toolset.search_files(
        query="target_func()",
        file_pattern="*.py",
        is_regex=False,
        case_sensitive=True,
        head_limit=1,
    )
    assert compat_out["ok"] is True
    assert compat_out["query"] == "target_func()"
    assert compat_out["count"] == 1


def test_notebook_edit_replace_insert_and_delete_cells(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-nb")
    nb_file = workspace.root / "notebooks" / "demo.ipynb"
    nb_file.parent.mkdir(parents=True, exist_ok=True)
    initial_nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 7,
                "id": "cell-0",
                "metadata": {},
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["old\n"]}],
                "source": ["x = 1\n"],
            },
            {
                "cell_type": "markdown",
                "id": "cell-1",
                "metadata": {},
                "source": ["# Notes\n"],
            },
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    nb_file.write_text(json.dumps(initial_nb, indent=1), encoding="utf-8")

    toolset = AgentToolset(workspace, code_mode_active=True)

    # 1. Replace cell-0 source and clear outputs/execution_count
    rep = toolset.invoke(
        "notebook_edit",
        {
            "notebook_path": "notebooks/demo.ipynb",
            "cell_id": "cell-0",
            "new_source": "x = 42\nprint(x)",
            "edit_mode": "replace",
        },
    )
    assert rep.status == "succeeded", rep.content
    assert rep.payload["ok"] is True
    assert rep.payload["edit_mode"] == "replace"
    assert rep.payload["cell_count"] == 2
    saved = json.loads(nb_file.read_text(encoding="utf-8"))
    assert saved["cells"][0]["source"] == ["x = 42\n", "print(x)"]
    assert saved["cells"][0]["outputs"] == []
    assert saved["cells"][0]["execution_count"] is None

    # 2. Insert a new code cell
    ins = toolset.invoke(
        "notebook_edit",
        {
            "notebook_path": "notebooks/demo.ipynb",
            "cell_id": "cell-0",
            "cell_type": "code",
            "new_source": "y = x + 1\n",
            "edit_mode": "insert",
        },
    )
    assert ins.status == "succeeded", ins.content
    assert ins.payload["cell_count"] == 3
    saved = json.loads(nb_file.read_text(encoding="utf-8"))
    assert len(saved["cells"]) == 3
    assert saved["cells"][1]["source"] == ["y = x + 1\n"]

    # 3. Delete markdown cell-1
    dele = toolset.invoke(
        "notebook_edit",
        {
            "notebook_path": "notebooks/demo.ipynb",
            "cell_id": "cell-1",
            "edit_mode": "delete",
        },
    )
    assert dele.status == "succeeded", dele.content
    assert dele.payload["cell_count"] == 2
    saved = json.loads(nb_file.read_text(encoding="utf-8"))
    assert len(saved["cells"]) == 2


def test_todo_write_optional_id_and_task_crud_tools(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-tasks")
    updates: list[list[dict[str, Any]]] = []
    todos_state: list[dict[str, Any]] = []
    toolset = AgentToolset(
        workspace,
        todos_state=todos_state,
        on_todo_update=lambda items: updates.append(list(items)),
    )

    # 1. todo_write with omitted id and camelCase activeForm
    tw = toolset.invoke(
        "todo_write",
        {
            "todos": [
                {"content": "Step one", "status": "completed"},
                {"content": "Step two", "status": "in_progress", "activeForm": "Working on step two"},
            ]
        },
    )
    assert tw.status == "succeeded", tw.content
    assert len(todos_state) == 2
    assert todos_state[0]["id"] == "todo_1"
    assert todos_state[1]["id"] == "todo_2"
    assert todos_state[1]["active_form"] == "Working on step two"
    assert len(updates) == 1

    # 2. task_create
    tc = toolset.invoke(
        "task_create",
        {
            "subject": "Write unit tests",
            "description": "Cover all 7 tool parity features",
            "active_form": "Writing unit tests",
        },
    )
    assert tc.status == "succeeded", tc.content
    created_task = tc.payload["task"]
    task_id = created_task["id"]
    assert created_task["status"] == "pending"
    assert created_task["subject"] == "Write unit tests"
    assert len(todos_state) == 3
    assert len(updates) == 2

    # 3. task_get
    tg = toolset.invoke("task_get", {"task_id": task_id})
    assert tg.status == "succeeded"
    assert tg.payload["task"]["id"] == task_id
    assert tg.payload["task"]["description"] == "Cover all 7 tool parity features"

    # 4. task_update to in_progress
    tu = toolset.invoke(
        "task_update",
        {
            "task_id": task_id,
            "status": "in_progress",
            "subject": "Write comprehensive unit tests",
        },
    )
    assert tu.status == "succeeded"
    assert tu.payload["task"]["status"] == "in_progress"
    assert tu.payload["task"]["content"] == "Write comprehensive unit tests"

    # 5. task_list
    tl = toolset.invoke("task_list", {})
    assert tl.status == "succeeded"
    assert tl.payload["count"] == 3
    assert len(tl.payload["todos"]) == 3

    # 6. task_update status="deleted" removes item
    td = toolset.invoke("task_update", {"task_id": task_id, "status": "deleted"})
    assert td.status == "succeeded"
    assert len(todos_state) == 2

    # 7. camelCase aliases via toolset.execute() (activeForm, taskId, addBlockedBy, metadata, owner)
    tc_camel = toolset.execute(
        "task_create",
        {
            "subject": "Verify camelCase task aliases",
            "activeForm": "Verifying camelCase task aliases",
            "metadata": {"priority": "high"},
        },
    )
    camel_id = tc_camel["task"]["id"]
    assert tc_camel["task"]["active_form"] == "Verifying camelCase task aliases"
    assert tc_camel["task"]["metadata"] == {"priority": "high"}

    tu_camel = toolset.execute(
        "task_update",
        {
            "taskId": camel_id,
            "addBlockedBy": ["todo_1"],
            "owner": "main",
        },
    )
    assert tu_camel["task"]["owner"] == "main"
    assert tu_camel["task"]["blocked_by"] == ["todo_1"]

    tg_camel = toolset.execute("task_get", {"taskId": camel_id})
    assert tg_camel["task"]["id"] == camel_id
    assert tg_camel["task"]["owner"] == "main"
    assert tg_camel["task"]["metadata"] == {"priority": "high"}


def test_run_command_description_and_dev_server_auto_backgrounding(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-bash-bg")
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy")

    # 1. Normal non-server command runs to completion and preserves description and output_tail
    normal = toolset.invoke(
        "run_command",
        {
            "command": f'"{sys.executable}" -c "print(\'hello-normal\')"',
            "description": "Print normal greeting",
        },
    )
    assert normal.status == "succeeded"
    assert normal.payload["description"] == "Print normal greeting"
    assert normal.payload["auto_backgrounded"] is False
    assert normal.payload["exit_code"] == 0
    assert "hello-normal" in normal.payload["output_tail"]
    assert "hello-normal" in normal.content

    # 2. Foreground dev server printing readiness line and staying alive is auto-backgrounded quickly
    server_cmd = (
        f'"{sys.executable}" -u -c '
        '"import time; print(\'VITE v5.0.0  ready in 120 ms\\n  ➜  Local:   http://127.0.0.1:5173/\', flush=True); time.sleep(30)"'
    )
    started = time.monotonic()
    bg = toolset.invoke(
        "run_command",
        {
            "command": server_cmd,
            "description": "Start Vite dev server",
        },
    )
    elapsed = time.monotonic() - started
    pid = bg.payload.get("pid")
    try:
        assert bg.status == "succeeded", bg.content
        assert elapsed < 6.0
        assert bg.payload["ok"] is True
        assert bg.payload["description"] == "Start Vite dev server"
        assert bg.payload["background"] is True
        assert bg.payload["auto_backgrounded"] is True
        assert bg.payload["backgrounded_by_readiness"] is True
        assert bg.payload["exit_code"] is None
        assert isinstance(pid, int) and pid > 0
        assert bg.payload["process_id"] == pid
        assert bg.payload["status"] == "running"
        assert bg.payload["shell_id"] == f"bash_{pid}"
        assert "VITE v5.0.0" in bg.payload["output_tail"]
        assert "http://127.0.0.1:5173" in str(bg.payload.get("detected_url") or "")
        assert bg.payload["shell_id"] in toolset._background_shells
    finally:
        if isinstance(pid, int) and pid > 0:
            _terminate_pid(pid)


def test_exit_plan_mode_includes_plan_approval_and_plan_content(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-exit-plan")
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="accept_edits")

    entered = toolset.invoke("enter_plan_mode", {"reason": "Plan before coding"})
    assert entered.status == "succeeded"
    assert entered.payload["plan_mode"] is True

    plan_md = "# Implementation Plan\n\n1. Update agent_tools.py\n2. Run pytest\n"
    w_res = toolset.invoke("write_file", {"path": ".orin/plans/parity.md", "content": plan_md})
    assert w_res.status == "succeeded"
    assert w_res.payload["additions"] > 0
    assert "deletions" in w_res.payload
    assert "structured_patch" in w_res.payload

    exited = toolset.invoke(
        "exit_plan_mode",
        {"plan_path": ".orin/plans/parity.md", "summary": "Parity implementation plan"},
    )
    assert exited.status == "succeeded"
    assert exited.payload["ok"] is True
    assert exited.payload["plan_approval"] is True
    assert exited.payload["plan_mode"] is False
    assert exited.payload["plan_path"] == ".orin/plans/parity.md"
    assert "# Implementation Plan" in exited.payload["plan_content"]


def test_code_mode_core_tools_and_edit_diff_payload_parity(tmp_path: Path) -> None:
    from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS

    expected_tools = {
        "glob_files",
        "notebook_edit",
        "task_create",
        "task_update",
        "task_get",
        "task_list",
        "transcribe_pdf",
        "enter_worktree",
        "exit_worktree",
        "preview_start",
    }
    assert expected_tools.issubset(CODE_MODE_CORE_TOOLS)

    workspace = ConversationWorkspace(tmp_path, "conv-diff-parity")
    toolset = AgentToolset(workspace, code_mode_active=True)

    w_out = toolset.invoke("write_file", {"path": "src/demo.py", "content": "a = 1\nb = 2\n"})
    assert w_out.status == "succeeded"
    assert w_out.payload["additions"] == 2
    assert w_out.payload["deletions"] == 0
    assert isinstance(w_out.payload["structured_patch"], list)

    e_out = toolset.invoke(
        "edit_file",
        {"path": "src/demo.py", "old_text": "b = 2\n", "new_text": "b = 20\nc = 30\n"},
    )
    assert e_out.status == "succeeded"
    assert e_out.payload["additions"] == 2
    assert e_out.payload["deletions"] == 1
    assert isinstance(e_out.payload["structured_patch"], list) and len(e_out.payload["structured_patch"]) >= 1

