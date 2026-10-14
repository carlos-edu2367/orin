"""Unit tests for Orin Code Mode Phase D (Worktree, Preview Server, @file Mentions)."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys

from agentos.agentic.agent_tools import AgentToolset, ReadFileRecord
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.mentions import (
    expand_file_mentions,
    extract_file_mentions,
    inject_file_mentions_into_history,
    list_workspace_files,
)
from agentos.code_mode.preview_server import PreviewServerManager


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@orin.local"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Orin Test"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_enter_worktree_creates_branch_and_copies_worktreeinclude(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".worktreeinclude").write_text(".env.local\nconfig/*.secret\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("API_KEY=secret123\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.secret").write_text("TOKEN=xyz\n", encoding="utf-8")

    workspace = ConversationWorkspace.at_root(tmp_path)
    toolset = AgentToolset(workspace, enable_terminal=True, code_mode_active=True)

    outcome = toolset.invoke("enter_worktree", {"name": "feat-x"})
    assert outcome.status == "succeeded", outcome.content
    payload = json.loads(outcome.content)
    assert payload["status"] == "entered"
    wt_info = payload["worktree"]
    assert wt_info["name"] == "feat-x"
    assert wt_info["branch"] == "orin/feat-x"

    wt_path = tmp_path / ".orin" / "worktrees" / "feat-x"
    assert wt_path.is_dir()
    assert toolset.workspace.root.resolve() == wt_path.resolve()
    assert (wt_path / ".env.local").read_text(encoding="utf-8") == "API_KEY=secret123\n"
    assert (wt_path / "config" / "app.secret").read_text(encoding="utf-8") == "TOKEN=xyz\n"

    # Verify git branch inside worktree
    branch_res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wt_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert branch_res.stdout.strip() == "orin/feat-x"


def test_exit_worktree_refuses_dirty_removal_without_force(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    workspace = ConversationWorkspace.at_root(tmp_path)
    toolset = AgentToolset(workspace, enable_terminal=True, code_mode_active=True)

    enter_outcome = toolset.invoke("enter_worktree", {"name": "dirty-wt"})
    assert enter_outcome.status == "succeeded", enter_outcome.content
    wt_path = tmp_path / ".orin" / "worktrees" / "dirty-wt"
    assert toolset.workspace.root.resolve() == wt_path.resolve()

    # Modify a tracked file without committing
    (wt_path / "README.md").write_text("# Dirty change\n", encoding="utf-8")

    # Attempt removal without force -> should fail and preserve worktree
    fail_outcome = toolset.invoke("exit_worktree", {"action": "remove", "force": False})
    assert fail_outcome.status == "failed"
    assert "uncommitted changes" in fail_outcome.content
    assert wt_path.exists()
    assert toolset.workspace.root.resolve() == wt_path.resolve()

    # Removal with force=True -> should succeed and restore workspace.root
    ok_outcome = toolset.invoke("exit_worktree", {"action": "remove", "force": True})
    assert ok_outcome.status == "succeeded", ok_outcome.content
    assert toolset.workspace.root.resolve() == tmp_path.resolve()
    assert not wt_path.exists()


def test_preview_start_reads_launch_json_and_waits_for_port(tmp_path: Path) -> None:
    port = _free_port()
    orin_dir = tmp_path / ".orin"
    orin_dir.mkdir(parents=True, exist_ok=True)
    launch_cfg = {
        "version": "0.0.1",
        "configurations": [
            {
                "name": "Test HTTP Server",
                "runtimeExecutable": sys.executable,
                "runtimeArgs": ["-m", "http.server", str(port), "--bind", "127.0.0.1"],
                "port": port,
            }
        ],
    }
    (orin_dir / "launch.json").write_text(json.dumps(launch_cfg), encoding="utf-8")

    navigated_urls: list[str] = []

    class _DummyBrowser:
        def navigate(self, url: str) -> dict[str, str]:
            navigated_urls.append(url)
            return {"url": url, "title": "Directory listing", "text": "Index of /"}

    workspace = ConversationWorkspace.at_root(tmp_path)
    toolset = AgentToolset(
        workspace,
        browser=_DummyBrowser(),
        enable_terminal=True,
        code_mode_active=True,
    )

    start_outcome = toolset.invoke("preview_start", {"name": "Test HTTP Server", "wait_timeout": 8.0})
    assert start_outcome.status == "succeeded", start_outcome.content
    start_data = json.loads(start_outcome.content)
    try:
        assert start_data["port"] == port
        assert start_data["ready"] is True
        assert start_data["status"] == "running"
        assert navigated_urls == [f"http://127.0.0.1:{port}"]

        logs_outcome = toolset.invoke("preview_logs", {"name": "Test HTTP Server", "tail_lines": 50})
        assert logs_outcome.status == "succeeded"
        logs_data = json.loads(logs_outcome.content)
        assert logs_data["name"] == "Test HTTP Server"
    finally:
        stop_outcome = toolset.invoke("preview_stop", {"name": "Test HTTP Server"})
        assert stop_outcome.status == "succeeded"
        stop_data = json.loads(stop_outcome.content)
        assert stop_data["stopped"] is True


def test_preview_server_autodetects_package_json_and_pyproject(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite --port 5173"}}),
        encoding="utf-8",
    )
    manager = PreviewServerManager(tmp_path)
    configs = manager.load_configurations()
    assert len(configs) >= 1
    assert configs[0].port == 5173


def test_at_file_mention_populates_read_file_state_and_enables_edit(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    app_file = src_dir / "app.py"
    app_file.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    mentions = extract_file_mentions("Please update @src/app.py and ignore user@example.com.")
    assert mentions == ["src/app.py"]

    workspace = ConversationWorkspace.at_root(tmp_path)
    read_file_state: dict[str, ReadFileRecord] = {}
    reminder = expand_file_mentions("Please update @src/app.py to return world", workspace, read_file_state)

    assert "<system-reminder>" in reminder
    assert "src/app.py" in reminder
    assert "src/app.py" in read_file_state

    # Verify that edit_file works immediately without calling read_file first
    toolset = AgentToolset(
        workspace,
        enable_terminal=True,
        code_mode_active=True,
        read_file_state=read_file_state,
    )
    edit_outcome = toolset.invoke(
        "edit_file",
        {
            "path": "src/app.py",
            "old_string": "return 'hello'",
            "new_string": "return 'world'",
        },
    )
    assert edit_outcome.status == "succeeded", edit_outcome.content
    assert "return 'world'" in app_file.read_text(encoding="utf-8")


def test_session_history_injects_at_file_mentions(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    workspace = ConversationWorkspace.at_root(tmp_path)
    read_state: dict[str, ReadFileRecord] = {}
    history = [{"role": "user", "content": "Refactor @hello.py please"}]
    updated = inject_file_mentions_into_history(history, workspace, read_state)
    assert "hello.py" in read_state
    assert "<system-reminder>" in updated[-1]["content"]
    assert "x = 1" in updated[-1]["content"]

    files = list_workspace_files(workspace, query="hello")
    assert "hello.py" in files
