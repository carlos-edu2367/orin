from __future__ import annotations

import subprocess
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.workspace import ConversationWorkspace


def _init_git_repo(root: Path) -> None:
    for args in (
        ["git", "init"],
        ["git", "config", "user.email", "test@orin.local"],
        ["git", "config", "user.name", "Orin Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)


def test_worktree_remove_carries_the_worktree_name(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    bus = LifecycleBus()
    events: list[tuple[HookEvent, dict]] = []
    bus.subscribe(lambda event: events.append((event.name, dict(event.payload))))
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True, code_mode_active=True, lifecycle=bus)

    assert toolset.invoke("enter_worktree", {"name": "gone-wt"}).status == "succeeded"
    assert toolset.invoke("exit_worktree", {"action": "remove", "force": True}).status == "succeeded"

    removed = [payload for name, payload in events if name is HookEvent.WORKTREE_REMOVE]
    assert len(removed) == 1
    assert removed[0]["worktree_name"] == "gone-wt"
