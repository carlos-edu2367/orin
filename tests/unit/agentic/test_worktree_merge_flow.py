"""TDD tests for WS-B2: end-to-end subagent worktree merge flow through TurnSession."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem
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
    (repo / "a.txt").write_text("a1\n", encoding="utf-8")
    (repo / "b.txt").write_text("b1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "a.txt", "b.txt")
    _git(repo, "commit", "-m", "init")
    return repo


class _ScriptedProvider:
    def __init__(self, batches: list[list[NormalizedStreamItem]]) -> None:
        self._batches = list(batches)

    def stream(self, _request: dict[str, Any]):
        items = self._batches.pop(0) if self._batches else _text_batch("ok")
        return iter(items)


def _make_session(
    repo: Path,
    *,
    child_provider_factory,
    merge_check_command: str | None = None,
    permission_mode: str = "full_autonomy",
    deny_rules: list[str] | None = None,
) -> tuple[TurnSession, _RecordingStore]:
    store = _RecordingStore()
    data: dict[str, Any] = {
        "orchestration": {
            "worktree": {
                "enabled": True,
                **({"mergeCheckCommand": merge_check_command} if merge_check_command else {}),
            }
        }
    }
    if deny_rules:
        data["permissions"] = {"deny": deny_rules}
    settings = EffectiveSettings(
        data=data,
        provenance={},
        layers=(SettingsLayer("user", None, data),),
        errors=(),
        trusted=True,
    )
    turn = {**TURN, "local_workspace_root": str(repo), "permission_mode": permission_mode}
    session = TurnSession(
        turn=turn,
        store=store,
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: _ScriptedProvider([_text_batch("main")]),
        workspace_root=repo,
        child_provider_factory=child_provider_factory,
    )
    session.workspace = ConversationWorkspace.at_root(repo)
    session.effective_settings = settings
    session.permission_mode = permission_mode
    session._workspace_trusted = lambda: True  # type: ignore[method-assign]
    return session, store


def test_subagent_worktree_merge_applied_payload_and_event(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "flow-applied")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    def factory(_model: str | None = None):
        return _ScriptedProvider([
            _parallel_tool_calls_batch([("tc-read", "read_file", {"path": "a.txt"})]),
            _parallel_tool_calls_batch([("tc-write", "write_file", {"path": "a.txt", "content": "a2 from subagent\n"})]),
            _text_batch("Updated a.txt."),
        ])

    session, store = _make_session(repo, child_provider_factory=factory)
    outcome = session._run_subagent("general", "Update a.txt", description="update a", isolation="worktree")

    assert outcome.status == "succeeded"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a2 from subagent\n"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before

    # K19 contract: ToolOutcome.payload["worktree"] == {"merge_state": ..., "merge_detail": ...}
    wt_payload = outcome.payload.get("worktree")
    assert isinstance(wt_payload, dict)
    assert wt_payload["merge_state"] == "applied"
    assert "a.txt" in wt_payload["merge_detail"]["files"]

    applied_events = [ev for ev in store.activity if ev[0] == AgentActivityEventType.MERGE_APPLIED]
    assert len(applied_events) == 1
    assert "a.txt" in applied_events[0][2].get("files", [])


def test_subagent_worktree_merge_conflict_emits_event_and_keeps_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "flow-conflict")

    def factory(_model: str | None = None):
        class _ConflictingChild:
            def __init__(self) -> None:
                self.step = 0

            def stream(self, _request: dict[str, Any]):
                self.step += 1
                if self.step == 1:
                    return iter(_parallel_tool_calls_batch([("tc-read", "read_file", {"path": "a.txt"})]))
                if self.step == 2:
                    # Simulate concurrent edit in parent while child is running.
                    (repo / "a.txt").write_text("parent conflicting line\n", encoding="utf-8")
                    return iter(
                        _parallel_tool_calls_batch(
                            [("tc-write", "write_file", {"path": "a.txt", "content": "child conflicting line\n"})]
                        )
                    )
                return iter(_text_batch("Edited a.txt."))

        return _ConflictingChild()

    session, store = _make_session(repo, child_provider_factory=factory)
    outcome = session._run_subagent("general", "Edit a.txt", description="edit a", isolation="worktree")

    assert outcome.status == "succeeded"
    wt_payload = outcome.payload.get("worktree")
    assert isinstance(wt_payload, dict)
    assert wt_payload["merge_state"] == "conflict"
    assert wt_payload.get("worktree_path")
    assert Path(str(wt_payload["worktree_path"])).is_dir()
    assert (repo / "a.txt").read_text(encoding="utf-8") == "parent conflicting line\n"

    conflict_events = [ev for ev in store.activity if ev[0] == AgentActivityEventType.MERGE_CONFLICT]
    assert len(conflict_events) == 1


def test_subagent_worktree_merge_check_failed_reverts_and_emits_event(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "flow-check-fail")
    failing_cmd = f'"{sys.executable}" -c "import sys; sys.exit(1)"'

    def factory(_model: str | None = None):
        return _ScriptedProvider([
            _parallel_tool_calls_batch([("tc-read", "read_file", {"path": "a.txt"})]),
            _parallel_tool_calls_batch([("tc-write", "write_file", {"path": "a.txt", "content": "a broken\n"})]),
            _text_batch("Edited a.txt."),
        ])

    session, store = _make_session(repo, child_provider_factory=factory, merge_check_command=failing_cmd)
    outcome = session._run_subagent("general", "Edit a.txt", description="edit a", isolation="worktree")

    assert outcome.status == "succeeded"
    wt_payload = outcome.payload.get("worktree")
    assert isinstance(wt_payload, dict)
    assert wt_payload["merge_state"] == "check_failed"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a1\n"

    check_events = [ev for ev in store.activity if ev[0] == AgentActivityEventType.MERGE_CHECK_FAILED]
    assert len(check_events) == 1


def test_subagent_worktree_merge_check_denied_by_permission_policy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "flow-check-denied")
    marker = tmp_path / "should_not_run.txt"
    cmd = f'"{sys.executable}" -c "from pathlib import Path; Path({str(marker)!r}).write_text(\'boom\')"'

    def factory(_model: str | None = None):
        return _ScriptedProvider([
            _parallel_tool_calls_batch([("tc-read", "read_file", {"path": "a.txt"})]),
            _parallel_tool_calls_batch([("tc-write", "write_file", {"path": "a.txt", "content": "a denied\n"})]),
            _text_batch("Edited a.txt."),
        ])

    session, store = _make_session(
        repo,
        child_provider_factory=factory,
        merge_check_command=cmd,
        permission_mode="ask_permissions",
        deny_rules=["Bash(*)"],
    )
    outcome = session._run_subagent("general", "Edit a.txt", description="edit a", isolation="worktree")

    assert outcome.status == "succeeded"
    assert not marker.exists()
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a1\n"
    assert outcome.payload["worktree"]["merge_state"] == "check_failed"
    assert any(ev[0] == AgentActivityEventType.MERGE_CHECK_FAILED for ev in store.activity)
