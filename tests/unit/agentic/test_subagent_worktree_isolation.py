"""TDD tests for WS-B1: subagent worktree isolation through TurnSession & Agent tool."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agentos.agentic.agent_definitions import parse_agent_file
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.settings import EffectiveSettings, SettingsLayer
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "orin-test@example.com")
    _git(repo, "config", "user.name", "Orin Test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text(".orin/\nnode_modules/\n.venv/\n", encoding="utf-8")
    (repo / "base.txt").write_text("original\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "base.txt")
    _git(repo, "commit", "-m", "init")
    return repo


class _ScriptedProvider:
    """Provider that runs a fixed sequence of stream batches."""

    def __init__(self, batches: list[list[NormalizedStreamItem]], *, on_request=None) -> None:
        self._batches = list(batches)
        self._on_request = on_request

    def stream(self, request: dict[str, Any]):
        if callable(self._on_request):
            self._on_request(request)
        items = self._batches.pop(0) if self._batches else _text_batch("done")
        return iter(items)


def _make_settings(worktree_enabled: bool, *, link_dirs: list[str] | None = None) -> EffectiveSettings:
    data: dict[str, Any] = {
        "orchestration": {
            "worktree": {
                "enabled": worktree_enabled,
                **({"linkDirs": link_dirs} if link_dirs is not None else {}),
            }
        }
    }
    return EffectiveSettings(
        data=data,
        provenance={},
        layers=(SettingsLayer("user", None, data),),
        errors=(),
        trusted=True,
    )


def _make_session(
    root: Path,
    *,
    child_provider_factory,
    effective_settings: EffectiveSettings,
    store: _RecordingStore | None = None,
) -> tuple[TurnSession, _RecordingStore]:
    rec = store or _RecordingStore()
    turn = {**TURN, "local_workspace_root": str(root), "permission_mode": "full_autonomy"}
    session = TurnSession(
        turn=turn,
        store=rec,
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: _ScriptedProvider([_text_batch("main")]),
        workspace_root=root,
        child_provider_factory=child_provider_factory,
    )
    session.workspace = ConversationWorkspace.at_root(root)
    session.effective_settings = effective_settings
    session.permission_mode = "full_autonomy"
    return session, rec


def test_agent_definition_validates_isolation_field(tmp_path: Path) -> None:
    good = tmp_path / "worker.md"
    good.write_text(
        "---\nname: worker\ndescription: Works in a worktree\nisolation: worktree\n---\nDo the task.\n",
        encoding="utf-8",
    )
    defn = parse_agent_file(good, origin="user")
    assert defn.isolation == "worktree"

    none_file = tmp_path / "plain.md"
    none_file.write_text(
        "---\nname: plain\ndescription: No isolation\nisolation: none\n---\nDo the task.\n",
        encoding="utf-8",
    )
    assert parse_agent_file(none_file, origin="user").isolation == "none"

    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nname: bad\ndescription: Bad isolation\nisolation: docker\n---\nDo the task.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="isolation"):
        parse_agent_file(bad, origin="user")


def test_criterion_1_disabled_setting_ignores_isolation_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-disabled")

    def child_factory(_model: str | None = None):
        return _ScriptedProvider([
            _parallel_tool_calls_batch([("tc-1", "write_file", {"path": "direct.txt", "content": "written directly"})]),
            _text_batch("Finished directly."),
        ])

    session, store = _make_session(
        repo,
        child_provider_factory=child_factory,
        effective_settings=_make_settings(False),
    )
    outcome = session._run_subagent("general", "Write direct.txt", description="write", isolation="worktree")
    assert outcome.status == "succeeded"
    assert (repo / "direct.txt").read_text(encoding="utf-8") == "written directly"
    types = [ev[0] for ev in store.activity]
    assert AgentActivityEventType.WORKTREE_CREATED not in types
    assert AgentActivityEventType.ISOLATION_UNAVAILABLE not in types
    assert "worktree" not in outcome.payload


def test_non_git_workspace_runs_without_isolation_and_emits_unavailable(tmp_path: Path) -> None:
    plain_dir = tmp_path / "not-a-git-repo"
    plain_dir.mkdir()

    def child_factory(_model: str | None = None):
        return _ScriptedProvider([
            _parallel_tool_calls_batch([("tc-1", "write_file", {"path": "out.txt", "content": "fallback ok"})]),
            _text_batch("Done without git."),
        ])

    session, store = _make_session(
        plain_dir,
        child_provider_factory=child_factory,
        effective_settings=_make_settings(True),
    )
    outcome = session._run_subagent("general", "Write out.txt", description="write", isolation="worktree")
    assert outcome.status == "succeeded"
    assert (plain_dir / "out.txt").read_text(encoding="utf-8") == "fallback ok"
    unavailable = [ev for ev in store.activity if ev[0] == AgentActivityEventType.ISOLATION_UNAVAILABLE]
    assert len(unavailable) == 1
    assert unavailable[0][2].get("reason") == "not_git_repo"


def test_criterion_5_child_with_no_changes_cleans_up_worktree_and_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-no-changes")
    seen_roots: list[Path] = []
    seen_prompts: list[str] = []

    def capture_prompt(req: dict[str, Any]) -> None:
        msgs = req.get("messages") or []
        sys_texts = [str(m.get("content") or "") for m in msgs if m.get("role") == "system"]
        seen_prompts.append("\n".join(sys_texts) + str(req.get("system") or ""))

    def child_factory(_model: str | None = None):
        return _ScriptedProvider([_text_batch("Nothing to edit.")], on_request=capture_prompt)

    session, store = _make_session(
        repo,
        child_provider_factory=child_factory,
        effective_settings=_make_settings(True, link_dirs=["node_modules", ".venv"]),
    )
    orig_toolset = session._toolset

    def spy_toolset(*args: Any, **kwargs: Any):
        ts = orig_toolset(*args, **kwargs)
        seen_roots.append(ts.workspace.root)
        return ts

    session._toolset = spy_toolset  # type: ignore[method-assign]
    outcome = session._run_subagent("general", "Just inspect", description="inspect", isolation="worktree")
    assert outcome.status == "succeeded"

    # Child toolset had workspace.root pointing to a worktree, not the parent repo!
    assert seen_roots
    assert seen_roots[-1] != repo.resolve()
    assert ".orin" in str(seen_roots[-1]) or "orin-wt" in str(seen_roots[-1])

    # Prompt warns child about linkDirs.
    assert seen_prompts and "node_modules" in seen_prompts[-1] and ".venv" in seen_prompts[-1]

    # Worktree & branch cleaned up because child made no changes.
    assert not seen_roots[-1].exists()
    branches = _git(repo, "branch", "--list", "orin/agent-*").stdout.strip()
    assert branches == ""

    types = [ev[0] for ev in store.activity]
    assert AgentActivityEventType.WORKTREE_CREATED in types
    assert AgentActivityEventType.WORKTREE_REMOVED in types
    assert outcome.payload.get("worktree", {}).get("merge_state") == "empty"


def test_child_toolset_writes_inside_worktree_not_directly_into_parent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-child-write")
    parent_had_file_during_child: list[bool] = []
    wt_had_file_during_child: list[bool] = []
    child_roots: list[Path] = []

    def child_factory(_model: str | None = None):
        class _CheckingProvider:
            def __init__(self) -> None:
                self.step = 0

            def stream(self, _request: dict[str, Any]):
                self.step += 1
                if self.step == 1:
                    return iter(
                        _parallel_tool_calls_batch(
                            [("tc-write", "write_file", {"path": "isolated.txt", "content": "from child"})]
                        )
                    )
                # While child is still running (before merge), parent MUST NOT have isolated.txt!
                parent_had_file_during_child.append((repo / "isolated.txt").exists())
                wt_had_file_during_child.append((child_roots[-1] / "isolated.txt").exists())
                return iter(_text_batch("Wrote isolated.txt."))

        return _CheckingProvider()

    session, _store = _make_session(
        repo,
        child_provider_factory=child_factory,
        effective_settings=_make_settings(True),
    )
    orig_toolset = session._toolset

    def spy_toolset(*args: Any, **kwargs: Any):
        ts = orig_toolset(*args, **kwargs)
        child_roots.append(ts.workspace.root)
        return ts

    session._toolset = spy_toolset  # type: ignore[method-assign]
    outcome = session._run_subagent("general", "Create isolated.txt", description="create", isolation="worktree")
    assert outcome.status == "succeeded"
    assert parent_had_file_during_child == [False]
    assert wt_had_file_during_child == [True]
    # After merge, the new file created by child is applied to parent!
    assert (repo / "isolated.txt").read_text(encoding="utf-8") == "from child"
