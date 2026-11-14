"""propose_code_session: full_autonomy support and project association (explicit / by workspace path)."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.conversations.code_milestone_watcher import (
    CodeSessionMilestoneWatcherRegistry,
)
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata
from agentos.projects.store import PostgresProjectStore

USER = "user-1"


@pytest.fixture
def world(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'propose_code.db'}", future=True)
    metadata.create_all(engine)
    chat_store = PostgresChatStore(engine)
    project_store = PostgresProjectStore(engine)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    friend = chat_store.create(
        user_id=USER,
        message="Oi Orin Friend",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="friend-init",
        code_mode="friend",
    )
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, friend.conversation_id),
        code_mode_active=False,
        conversation_id=friend.conversation_id,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
        chat_store=chat_store,
        project_store=project_store,
        local_workspaces=local_workspaces,
        project_user_id=USER,
    )
    toolset._milestone_registry = CodeSessionMilestoneWatcherRegistry()
    assert toolset._project_id is None  # global (Friend) conversation
    return toolset, chat_store, project_store, local_workspaces


def _project_with_folder(project_store, local_workspaces, folder: Path, name: str):
    folder.mkdir(parents=True, exist_ok=True)
    project = project_store.create(user_id=USER, name=name, description=None)
    local_workspaces.set_root(project.workspace_id, USER, str(folder))
    return project


@pytest.mark.parametrize("mode", ["full_autonomy", "bypassPermissions"])
def test_start_with_full_autonomy_is_accepted_and_persisted(world, mode: str) -> None:
    toolset, chat_store, _, _ = world

    outcome = toolset.invoke(
        "propose_code_session",
        {"action": "start", "title": "Autônomo", "prompt": "Rodar a suíte e corrigir.", "permission_mode": mode},
    )

    assert outcome.status == "succeeded", outcome
    assert outcome.payload["permission_mode"] == "full_autonomy"
    persisted = chat_store.get(outcome.payload["code_conversation_id"], USER)
    assert persisted["permission_mode"] == "full_autonomy"


def test_propose_with_full_autonomy_returns_card(world) -> None:
    toolset, _, _, _ = world
    outcome = toolset.propose_code_session(title="Card", prompt="Faça X.", permission_mode="full_autonomy")
    assert outcome.payload["code_session_proposal"]["permission_mode"] == "full_autonomy"


def test_schema_exposes_full_autonomy_and_project_id(world) -> None:
    toolset, _, _, _ = world
    definition = next(item for item in toolset.definitions() if item.name == "propose_code_session")
    params = definition.parameters if hasattr(definition, "parameters") else definition.input_schema
    props = params["properties"]
    assert "full_autonomy" in props["permission_mode"]["enum"]
    assert "bypassPermissions" in props["permission_mode"]["enum"]
    assert "project_id" in props


def test_start_with_explicit_project_id_links_conversation_in_db(world, tmp_path: Path) -> None:
    toolset, chat_store, project_store, local_workspaces = world
    project_dir = tmp_path / "repo-a"
    project = _project_with_folder(project_store, local_workspaces, project_dir, "Repo A")

    outcome = toolset.invoke(
        "propose_code_session",
        {"action": "start", "title": "No projeto", "prompt": "Implementar X.", "project_id": project.project_id},
    )

    assert outcome.status == "succeeded", outcome
    assert outcome.payload["project_id"] == project.project_id
    # Without workspace_path, the project's folder becomes the session's folder.
    assert outcome.payload["workspace_path"] == str(project_dir)
    persisted = chat_store.get(outcome.payload["code_conversation_id"], USER)
    assert persisted["project_id"] == project.project_id
    sidebar = {item.project_id: item for item in project_store.sidebar(USER)}
    assert outcome.payload["code_conversation_id"] in {
        chat.conversation_id for chat in sidebar[project.project_id].chats
    }


def test_explicit_unknown_project_id_is_rejected(world) -> None:
    toolset, _, _, _ = world
    with pytest.raises(AgentToolError):
        toolset.propose_code_session(
            action="start", title="X", prompt="Y", project_id="project_does_not_exist"
        )


def test_start_resolves_project_from_workspace_path(world, tmp_path: Path) -> None:
    toolset, chat_store, project_store, local_workspaces = world
    _project_with_folder(project_store, local_workspaces, tmp_path / "other", "Other")
    outer = _project_with_folder(project_store, local_workspaces, tmp_path / "mono", "Mono")
    inner = _project_with_folder(project_store, local_workspaces, tmp_path / "mono" / "pkg", "Pkg")

    exact = toolset.propose_code_session(
        action="start", title="Exato", prompt="Trabalhar no mono.", workspace_path=str(tmp_path / "mono")
    )
    assert exact.payload["project_id"] == outer.project_id
    assert chat_store.get(exact.payload["code_conversation_id"], USER)["project_id"] == outer.project_id

    # A subfolder resolves to the most specific project root; trailing slash/backslashes tolerated.
    nested_path = str(tmp_path / "mono" / "pkg" / "src").replace("/", "\\") + "\\"
    nested = toolset.propose_code_session(
        action="start", title="Aninhado", prompt="Trabalhar no pkg.", workspace_path=nested_path
    )
    assert nested.payload["project_id"] == inner.project_id
    assert nested.payload["workspace_path"] == nested_path.strip()


def test_unmatched_workspace_path_falls_back_to_current_project(world, tmp_path: Path) -> None:
    toolset, chat_store, project_store, local_workspaces = world
    current = _project_with_folder(project_store, local_workspaces, tmp_path / "current", "Current")
    toolset._project_id = current.project_id

    outcome = toolset.propose_code_session(
        action="start", title="Solto", prompt="Algo.", workspace_path=str(tmp_path / "unrelated")
    )
    assert outcome.payload["project_id"] == current.project_id

    toolset._project_id = None
    loose = toolset.propose_code_session(
        action="start", title="Solto 2", prompt="Algo.", workspace_path=str(tmp_path / "unrelated")
    )
    assert loose.payload["project_id"] is None
    assert chat_store.get(loose.payload["code_conversation_id"], USER)["project_id"] is None
