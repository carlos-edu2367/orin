import pytest

from agentos.agentic.session import ProjectWorkspaceResolutionError, resolve_effective_workspace_id
from agentos.agentic.workspace import ConversationWorkspace


def test_project_workspace_is_used_instead_of_the_chat_workspace() -> None:
    """Removing the project branch would make chats stop sharing files."""
    assert resolve_effective_workspace_id({"conversation_id": "chat-a", "project_id": "project-a", "project_workspace_id": "workspace:project-a"}) == "workspace:project-a"
    assert resolve_effective_workspace_id({"conversation_id": "chat-standalone", "project_id": None, "project_workspace_id": None}) == "chat-standalone"


def test_project_chat_without_a_resolved_project_workspace_fails_closed() -> None:
    """Falling back to a chat workspace would silently break project semantics."""
    with pytest.raises(ProjectWorkspaceResolutionError):
        resolve_effective_workspace_id({"conversation_id": "chat-a", "project_id": "project-a", "project_workspace_id": None})


def test_project_workspace_identifier_maps_to_a_stable_safe_directory(tmp_path) -> None:
    workspace_id = resolve_effective_workspace_id({
        "conversation_id": "chat-a",
        "project_id": "project-a",
        "project_workspace_id": "workspace:project-a",
    })

    first = ConversationWorkspace(tmp_path, workspace_id)
    second = ConversationWorkspace(tmp_path, workspace_id)

    assert first.root.is_dir()
    assert first.root.parent == tmp_path.resolve()
    assert ":" not in first.root.name
    assert second.root == first.root
