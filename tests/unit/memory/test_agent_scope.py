"""Unit tests for AgentMemoryScope and agent memory mode resolution (WS-D5 / D-RF9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.memory.agent_scope import (
    VALID_AGENT_MEMORY_MODES,
    AgentMemoryScope,
    resolve_agent_memory_mode,
    workspace_root_hash,
)


def test_valid_agent_memory_modes_matches_spec() -> None:
    assert VALID_AGENT_MEMORY_MODES == ("project", "user", "local", "none")


def test_project_scope_builds_project_key_and_preserves_project_id() -> None:
    scope = AgentMemoryScope(
        user_id="user-1",
        project_id="proj-sql",
        workspace_hash="abc123",
        agent_name="Revisor-SQL",
        mode="project",
    )
    assert scope.scope_type == "agent"
    assert scope.mode == "project"
    assert scope.agent_name == "revisor-sql"
    assert scope.agent_key == "revisor-sql"
    assert scope.project_id == "proj-sql"
    assert scope.effective_project_id == "proj-sql"
    assert scope.scope_id == "project:proj-sql:revisor-sql"


def test_project_scope_without_project_id_falls_back_to_local() -> None:
    ws_hash = workspace_root_hash(Path("/tmp/my-repo"))
    scope = AgentMemoryScope(
        user_id="user-1",
        project_id=None,
        workspace_hash=ws_hash,
        agent_name="revisor-sql",
        mode="project",
    )
    assert scope.scope_type == "agent"
    assert scope.mode == "local"
    assert scope.project_id is None
    assert scope.effective_project_id is None
    assert scope.scope_id == f"local:{ws_hash}:revisor-sql"


def test_user_scope_clears_project_id_and_uses_user_agent_key() -> None:
    scope = AgentMemoryScope(
        user_id="user-1",
        project_id="proj-ignored",
        workspace_hash="ws-ignored",
        agent_name="revisor-sql",
        mode="user",
    )
    assert scope.scope_type == "agent"
    assert scope.mode == "user"
    assert scope.project_id is None
    assert scope.effective_project_id is None
    assert scope.scope_id == "user:revisor-sql"


def test_local_scope_uses_stable_normalized_workspace_hash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    h1 = workspace_root_hash(root)
    h2 = workspace_root_hash(str(root / "."))
    assert h1 == h2
    assert len(h1) >= 16

    scope = AgentMemoryScope.from_context(
        user_id="user-1",
        agent_name="revisor-sql",
        mode="local",
        project_id="proj-1",
        workspace_root=root,
    )
    assert scope is not None
    assert scope.mode == "local"
    assert scope.project_id is None
    assert scope.scope_id == f"local:{h1}:revisor-sql"


def test_resolve_agent_memory_mode_defaults_for_custom_verifier_and_builtins() -> None:
    # Custom agent with omitted memory defaults to project
    assert resolve_agent_memory_mode("revisor-sql", None, is_builtin=False) == "project"
    assert resolve_agent_memory_mode("revisor-sql", "user", is_builtin=False) == "user"
    assert resolve_agent_memory_mode("revisor-sql", "local", is_builtin=False) == "local"
    assert resolve_agent_memory_mode("revisor-sql", "none", is_builtin=False) == "none"

    # Builtin verifier defaults to project; explore/plan/general/fork default to none
    assert resolve_agent_memory_mode("verifier", None, is_builtin=True) == "project"
    for builtin in ("explore", "plan", "general", "general-purpose", "fork"):
        assert resolve_agent_memory_mode(builtin, None, is_builtin=True) == "none"

    with pytest.raises(ValueError, match="memory"):
        resolve_agent_memory_mode("revisor-sql", "global", is_builtin=False)


def test_from_context_returns_none_when_mode_is_none() -> None:
    assert (
        AgentMemoryScope.from_context(
            user_id="user-1",
            agent_name="explore",
            mode=None,
            project_id="proj-1",
            is_builtin=True,
        )
        is None
    )
    assert (
        AgentMemoryScope.from_context(
            user_id="user-1",
            agent_name="revisor-sql",
            mode="none",
            project_id="proj-1",
            is_builtin=False,
        )
        is None
    )
