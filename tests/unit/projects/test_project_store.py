from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata
from agentos.projects.store import PostgresProjectStore


def test_project_has_one_workspace_and_groups_its_chats() -> None:
    """A wrong project/workspace link must not let sibling chats diverge."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    projects = PostgresProjectStore(engine)
    project = projects.create(user_id="user-a", name="AgentOS", description="Runtime")
    chats = PostgresChatStore(engine)
    first = chats.create(
        user_id="user-a", message="First", provider="openrouter", model_id="model-a",
        idempotency_key="first", project_id=project.project_id,
    )
    second = chats.create(
        user_id="user-a", message="Second", provider="openrouter", model_id="model-a",
        idempotency_key="second", project_id=project.project_id,
    )

    assert projects.workspace_for_conversation(first.conversation_id, "user-a") == project.workspace_id
    sidebar = projects.sidebar("user-a")
    assert len(sidebar) == 1
    assert {item.conversation_id for item in sidebar[0].chats} == {first.conversation_id, second.conversation_id}


def test_project_workspace_cannot_be_resolved_by_another_user() -> None:
    """Removing owner filtering would expose another user's project workspace."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    project = PostgresProjectStore(engine).create(user_id="owner", name="Private", description=None)
    chat = PostgresChatStore(engine).create(
        user_id="owner", message="Secret", provider="openrouter", model_id="model-a",
        idempotency_key="secret", project_id=project.project_id,
    )

    assert PostgresProjectStore(engine).workspace_for_conversation(chat.conversation_id, "other") is None
