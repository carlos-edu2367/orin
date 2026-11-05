from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.git_exclude import ensure_orin_excluded, find_git_dir, git_common_dir
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.paths import is_protected_path


def _repo(path: Path) -> Path:
    (path / ".git" / "info").mkdir(parents=True)
    return path


def _exclude_lines(git_dir: Path) -> list[str]:
    return (git_dir / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_the_exclude_gets_one_orin_line_and_keeps_what_was_there(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".git" / "info" / "exclude").write_text("*.log\n", encoding="utf-8")
    assert ensure_orin_excluded(repo) is True
    assert ensure_orin_excluded(repo) is False
    lines = _exclude_lines(repo / ".git")
    assert lines[0] == "*.log" and lines.count(".orin/") == 1


def test_a_folder_that_is_not_a_repository_is_left_alone(tmp_path: Path) -> None:
    assert ensure_orin_excluded(tmp_path) is False
    assert not (tmp_path / ".git").exists()


def test_a_subfolder_workspace_writes_the_repo_exclude(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "packages" / "app").mkdir(parents=True)
    assert ensure_orin_excluded(repo / "packages" / "app") is True
    assert ".orin/" in _exclude_lines(repo / ".git")


def test_a_worktree_writes_the_common_exclude(tmp_path: Path) -> None:
    main = _repo(tmp_path / "main")
    worktree_git = main / ".git" / "worktrees" / "wt"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
    assert ensure_orin_excluded(worktree) is True
    assert ".orin/" in _exclude_lines(main / ".git")


def test_uploads_inside_orin_are_not_protected_paths(tmp_path: Path) -> None:
    assert is_protected_path(tmp_path / ".orin" / "uploads" / "a.csv", workspace_root=tmp_path) is False


def test_new_and_legacy_uploads_are_both_read_as_uploads(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    body = b"".join(f"line {i}\n".encode() for i in range(1, 1001))
    for relative in (".orin/uploads/new.txt", "uploads/old.txt"):
        target = toolset.workspace.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        assert toolset.invoke("read_file", {"path": relative}).payload["end_line"] == 1000


def test_session_init_excludes_orin_from_git(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    class Store:
        def main_agent_id(self, turn): return "agent:c1:main"
        def history_for_turn(self, turn): return []
        def record(self, *args, **kw): return None

    class Agents:
        def list(self): return []

    TurnSession(
        turn={
            "turn_id": "t1",
            "conversation_id": "c1",
            "user_id": "local-user",
            "execution_id": "e1",
            "code_mode": "code",
            "provider": "ollama",
            "model_id": "m",
            "workspace_root_path": str(repo),
        },
        store=Store(),
        agents_store=Agents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
        cancelled=lambda _turn: False,
    )
    assert ".orin/" in _exclude_lines(repo / ".git")


def test_find_git_dir_and_common_dir(tmp_path: Path) -> None:
    assert find_git_dir(tmp_path / "nonexistent") is None
    repo = _repo(tmp_path / "repo")
    assert find_git_dir(repo) == (repo / ".git").resolve()
    assert find_git_dir(repo / "sub" / "deep") == (repo / ".git").resolve()
    assert git_common_dir(repo / ".git") == (repo / ".git").resolve()

    # Relative gitdir in worktree
    wt = tmp_path / "wt_rel"
    wt.mkdir()
    worktree_git = tmp_path / "repo" / ".git" / "worktrees" / "wt_rel"
    worktree_git.mkdir(parents=True, exist_ok=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    (wt / ".git").write_text("gitdir: ../repo/.git/worktrees/wt_rel\n", encoding="utf-8")

    found = find_git_dir(wt)
    assert found == worktree_git.resolve()
    assert git_common_dir(found) == (repo / ".git").resolve()


def test_exclude_handles_missing_newline_and_variants(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    exclude = repo / ".git" / "info" / "exclude"

    # File without trailing newline
    exclude.write_text("*.tmp", encoding="utf-8")
    assert ensure_orin_excluded(repo) is True
    lines = _exclude_lines(repo / ".git")
    assert lines == ["*.tmp", "# Orin: local agent state and attachments", ".orin/"]

    # Variants: .orin, /.orin/, /.orin already present -> no-op
    for variant in [".orin", "/.orin/", "/.orin"]:
        repo_v = _repo(tmp_path / f"repo_{variant.replace('/', '_')}")
        (repo_v / ".git" / "info" / "exclude").write_text(f"{variant}\n", encoding="utf-8")
        assert ensure_orin_excluded(repo_v) is False
        assert _exclude_lines(repo_v / ".git") == [variant]
