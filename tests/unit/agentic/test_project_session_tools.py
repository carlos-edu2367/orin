"""Unit tests for project management & cross-session agent tools (Task 3.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.phases import PHASE_TOOLS, Phase, tools_for
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.projects.store import ProjectRecord, ProjectSidebarChat, ProjectSidebarItem


class _FakeLocalWorkspaces:
    def __init__(self) -> None:
        self.roots: dict[tuple[str, str], str] = {}

    def root_for(self, workspace_id: str, user_id: str) -> str | None:
        return self.roots.get((workspace_id, user_id))

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        self.roots[(workspace_id, user_id)] = root_path


class _FakeProjectStore:
    def __init__(self, local_workspaces: _FakeLocalWorkspaces | None = None) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.chats_by_project: dict[str, list[ProjectSidebarChat]] = {}
        self._local_workspaces = local_workspaces
        self._seq = 0

    def create(self, *, user_id: str, name: str, description: str | None) -> ProjectRecord:
        if not name or not name.strip():
            raise ValueError("project name must be a bounded non-blank string")
        self._seq += 1
        pid = f"project_{self._seq}"
        now = datetime.now(UTC)
        rec = ProjectRecord(
            project_id=pid,
            user_id=user_id,
            workspace_id=f"workspace:{pid}",
            name=name.strip(),
            description=description.strip() if description else None,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self.projects[pid] = rec
        return rec

    def get(self, project_id: str, user_id: str, *, include_archived: bool = False) -> ProjectRecord | None:
        rec = self.projects.get(project_id)
        if rec is None or rec.user_id != user_id:
            return None
        return rec

    def sidebar(self, user_id: str) -> tuple[ProjectSidebarItem, ...]:
        items: list[ProjectSidebarItem] = []
        for rec in self.projects.values():
            if rec.user_id != user_id:
                continue
            items.append(
                ProjectSidebarItem(
                    project_id=rec.project_id,
                    name=rec.name,
                    description=rec.description,
                    chats=tuple(self.chats_by_project.get(rec.project_id, ())),
                    workspace_id=rec.workspace_id,
                )
            )
        return tuple(items)


@dataclass
class _FakeConversation:
    conversation_id: str
    user_id: str
    title: str
    mode: str
    project_id: str | None
    messages: list[dict[str, str]]


class _FakeChatStore:
    def __init__(self, project_store: _FakeProjectStore, local_workspaces: _FakeLocalWorkspaces) -> None:
        self.project_store = project_store
        self.local_workspaces = local_workspaces
        self.conversations: dict[str, _FakeConversation] = {}

    def set_project(self, conversation_id: str, user_id: str, project_id: str | None) -> dict[str, object]:
        conv = self.conversations.get(conversation_id)
        if conv is None or conv.user_id != user_id:
            raise KeyError(conversation_id)
        project = self.project_store.get(project_id, user_id) if project_id else None
        if project_id and project is None:
            raise KeyError(project_id)
        conv.project_id = project_id
        if project is not None:
            conv_root = self.local_workspaces.root_for(conversation_id, user_id)
            proj_root = self.local_workspaces.root_for(project.workspace_id, user_id)
            if conv_root and not proj_root:
                self.local_workspaces.set_root(project.workspace_id, user_id, conv_root)
        return {
            "conversation_id": conversation_id,
            "project_id": project_id,
            "project_name": project.name if project else None,
            "migrated_files": [],
        }

    def read_project_conversation(self, conversation_id: str, user_id: str, *, limit: int = 20) -> dict[str, object]:
        conv = self.conversations.get(conversation_id)
        if conv is None or conv.user_id != user_id:
            raise KeyError(conversation_id)
        bounded = min(max(int(limit), 1), 50)
        return {
            "conversation_id": conv.conversation_id,
            "title": conv.title,
            "mode": conv.mode,
            "project_id": conv.project_id,
            "messages": conv.messages[-bounded:],
        }


class _DummyTurnStore:
    def main_agent_id(self, turn):
        return "agent:main"

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "Continue o trabalho da outra sessão do projeto"}]


class _DummyAgentsStore:
    def list(self):
        return []


def test_list_projects_returns_user_projects_with_workspace_and_chats(tmp_path: Path) -> None:
    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)

    rec = proj_store.create(user_id="user-1", name="Orin Core", description="Backend & UI")
    local_ws.set_root(rec.workspace_id, "user-1", str(tmp_path))
    proj_store.chats_by_project[rec.project_id] = [
        ProjectSidebarChat(
            conversation_id="chat-1",
            title="Planejamento",
            state="completed",
            updated_at=datetime.now(UTC),
            mode="chat",
        ),
        ProjectSidebarChat(
            conversation_id="code-1",
            title="Implementar API",
            state="completed",
            updated_at=datetime.now(UTC),
            mode="code",
        ),
    ]
    # Another user's project must not appear
    proj_store.create(user_id="user-2", name="Other User Project", description=None)

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="chat-1",
    )

    result = toolset.list_projects()
    projects = json.loads(result["content"])
    assert len(projects) == 1
    assert projects[0]["project_id"] == rec.project_id
    assert projects[0]["name"] == "Orin Core"
    assert projects[0]["workspace_path"] == str(tmp_path)
    assert len(projects[0]["chats"]) == 2
    assert projects[0]["chats"][1] == {
        "conversation_id": "code-1",
        "title": "Implementar API",
        "mode": "code",
    }


def test_create_project_from_session_creates_links_workspace_and_moves_session(tmp_path: Path) -> None:
    project_folder = tmp_path / "repo"
    project_folder.mkdir()

    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)
    chat_store.conversations["chat-standalone"] = _FakeConversation(
        conversation_id="chat-standalone",
        user_id="user-1",
        title="Sessão Inicial",
        mode="chat",
        project_id=None,
        messages=[],
    )

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="chat-standalone",
        default_workspace_path=str(project_folder),
    )

    res = toolset.create_project_from_session(
        name="Novo Projeto Orin",
        description="Criado a partir do chat",
    )
    payload = res["payload"]
    project_id = payload["project_id"]
    assert payload["name"] == "Novo Projeto Orin"
    assert payload["workspace_path"] == str(project_folder.resolve())
    assert payload["project_action"] == "created"
    assert chat_store.conversations["chat-standalone"].project_id == project_id
    assert local_ws.root_for(f"workspace:{project_id}", "user-1") == str(project_folder.resolve())
    assert toolset._project_id == project_id


def test_create_project_from_session_rejects_invalid_inputs_without_orphan_project(tmp_path: Path) -> None:
    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="chat-1",
    )

    with pytest.raises(AgentToolError):
        toolset.create_project_from_session(name="   ")

    with pytest.raises(AgentToolError):
        toolset.create_project_from_session(name="Projeto Inválido", workspace_path="relative/path")

    with pytest.raises(AgentToolError):
        toolset.create_project_from_session(
            name="Projeto Pasta Inexistente",
            workspace_path=str(tmp_path / "does_not_exist_12345"),
        )

    assert len(proj_store.projects) == 0


def test_move_session_to_project_moves_and_enforces_user_ownership(tmp_path: Path) -> None:
    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)

    target_project = proj_store.create(user_id="user-1", name="Projeto Alvo", description=None)
    other_project = proj_store.create(user_id="user-2", name="Projeto Alheio", description=None)

    chat_store.conversations["chat-1"] = _FakeConversation(
        conversation_id="chat-1",
        user_id="user-1",
        title="Conversa Atual",
        mode="code",
        project_id=None,
        messages=[],
    )
    local_ws.set_root("chat-1", "user-1", str(tmp_path))

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="chat-1",
    )

    # Moving to another user's project must fail
    with pytest.raises(AgentToolError):
        toolset.move_session_to_project(project_id=other_project.project_id)

    # Moving to own project succeeds and inherits workspace root
    res = toolset.move_session_to_project(project_id=target_project.project_id)
    assert res["payload"]["project_id"] == target_project.project_id
    assert res["payload"]["project_name"] == "Projeto Alvo"
    assert res["payload"]["workspace_path"] == str(tmp_path)
    assert chat_store.conversations["chat-1"].project_id == target_project.project_id
    assert toolset._project_id == target_project.project_id


def test_read_project_conversation_reads_sibling_and_blocks_other_user(tmp_path: Path) -> None:
    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)

    chat_store.conversations["chat-sibling"] = _FakeConversation(
        conversation_id="chat-sibling",
        user_id="user-1",
        title="Discussão de Arquitetura",
        mode="chat",
        project_id="project_1",
        messages=[
            {"role": "user", "content": "Qual banco usar?"},
            {"role": "assistant", "content": "Vamos usar PostgreSQL com SQLAlchemy."},
        ],
    )
    chat_store.conversations["chat-other-user"] = _FakeConversation(
        conversation_id="chat-other-user",
        user_id="user-2",
        title="Segredo de outro usuário",
        mode="chat",
        project_id="project_2",
        messages=[{"role": "user", "content": "segredo"}],
    )

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="code-current",
    )

    res = toolset.read_project_conversation(conversation_id="chat-sibling", max_messages=10)
    data = json.loads(res["content"])
    assert data["conversation_id"] == "chat-sibling"
    assert data["title"] == "Discussão de Arquitetura"
    assert len(data["messages"]) == 2
    assert "PostgreSQL" in data["messages"][1]["content"]

    with pytest.raises(AgentToolError):
        toolset.read_project_conversation(conversation_id="chat-other-user")


def test_project_tools_exposed_in_phases_code_mode_and_turn_session_prompt(tmp_path: Path) -> None:
    expected = {
        "list_projects",
        "create_project_from_session",
        "move_session_to_project",
        "read_project_conversation",
    }
    assert expected.issubset(set(PHASE_TOOLS[Phase.ORIENT]))
    assert expected.issubset(set(PHASE_TOOLS[Phase.EXECUTE]))
    assert {"list_projects", "read_project_conversation"}.issubset(set(PHASE_TOOLS[Phase.PLAN]))
    assert expected.issubset(CODE_MODE_CORE_TOOLS)

    assert expected.issubset(tools_for(Phase.ORIENT))

    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)
    proj = proj_store.create(user_id="user-1", name="Orin Plataforma", description="Main repo")
    proj_store.chats_by_project[proj.project_id] = [
        ProjectSidebarChat(
            conversation_id="chat-current",
            title="Sessão Atual",
            state="running",
            updated_at=datetime.now(UTC),
            mode="code",
        ),
        ProjectSidebarChat(
            conversation_id="chat-sibling-spec",
            title="Spec de Autenticação",
            state="completed",
            updated_at=datetime.now(UTC),
            mode="chat",
        ),
    ]

    turn = {
        "turn_id": "turn-1",
        "conversation_id": "chat-current",
        "execution_id": "exec-1",
        "user_id": "user-1",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "code_mode": "code",
        "project_id": proj.project_id,
        "project_workspace_id": proj.workspace_id,
    }
    session = TurnSession(
        turn=turn,
        store=_DummyTurnStore(),
        agents_store=_DummyAgentsStore(),
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
    )
    runtime = session.build_runtime()
    tool_names = {item.name for item in runtime.toolset.definitions()}
    assert expected.issubset(tool_names)
    assert "chat-sibling-spec" in runtime.volatile_prompt
    assert "Spec de Autenticação" in runtime.volatile_prompt
    assert "read_project_conversation" in runtime.volatile_prompt


def test_project_tools_red_team_edge_cases_and_read_only_mode(tmp_path: Path) -> None:
    from agentos.agentic.agent_tools import coerce_tool_arguments

    local_ws = _FakeLocalWorkspaces()
    proj_store = _FakeProjectStore(local_ws)
    chat_store = _FakeChatStore(proj_store, local_ws)

    # 1. File path instead of directory in create_project_from_session
    file_target = tmp_path / "not_a_dir.txt"
    file_target.write_text("hello", encoding="utf-8")

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id=None,
    )

    with pytest.raises(AgentToolError):
        toolset.create_project_from_session(name="Proj", workspace_path=str(file_target))

    # 2. Overlong project name (>120 chars)
    with pytest.raises(AgentToolError):
        toolset.create_project_from_session(name="A" * 121)

    assert len(proj_store.projects) == 0

    # 3. move_session_to_project without active conversation_id
    proj = proj_store.create(user_id="user-1", name="Valid Project", description=None)
    with pytest.raises(AgentToolError):
        toolset.move_session_to_project(project_id=proj.project_id)

    # 4. Invalid limit in read_project_conversation
    with pytest.raises(AgentToolError):
        toolset.read_project_conversation(conversation_id="any", limit="not-an-int")  # type: ignore[arg-type]

    coerced, _ = coerce_tool_arguments(
        "read_project_conversation",
        {"conversation_id": "conv-1", "max_messages": 15},
    )
    assert coerced == {"conversation_id": "conv-1", "limit": 15}

    # 5. Read-only mode strips mutating project tools while preserving read-only ones
    ro_toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="user-1",
        conversation_id="chat-1",
        read_only_mode=True,
    )
    ro_names = {item.name for item in ro_toolset.definitions()}
    assert "list_projects" in ro_names
    assert "read_project_conversation" in ro_names
    assert "create_project_from_session" not in ro_names
    assert "move_session_to_project" not in ro_names

    # 6. Whitespace user_id does not expose project tools
    blank_user_toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-1"),
        project_store=proj_store,
        chat_store=chat_store,
        local_workspaces=local_ws,
        project_user_id="   ",
        conversation_id="chat-1",
    )
    blank_names = {item.name for item in blank_user_toolset.definitions()}
    assert "list_projects" not in blank_names
    assert "create_project_from_session" not in blank_names
    assert "move_session_to_project" not in blank_names
    assert "read_project_conversation" not in blank_names

