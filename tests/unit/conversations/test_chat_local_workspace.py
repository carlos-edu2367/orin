from pathlib import Path

from agentos.agentic.session import resolve_effective_workspace_id
from agentos.agentic.workspace import resolve_workspace


def test_a_project_turn_resolves_the_projects_local_folder(tmp_path: Path) -> None:
    """The turn carries the root so the worker never queries it a second time."""
    chosen = tmp_path / "repo"
    chosen.mkdir()
    turn = {"conversation_id": "chat_a", "project_id": "project_a", "project_workspace_id": "workspace:project_a", "workspace_root_path": str(chosen)}

    workspace = resolve_workspace(resolve_effective_workspace_id(turn), managed_root=tmp_path / "managed", local_root=turn["workspace_root_path"])

    assert workspace.root == chosen.resolve()


def test_a_turn_without_a_local_root_keeps_the_managed_folder(tmp_path: Path) -> None:
    turn = {"conversation_id": "chat_a", "project_id": None, "project_workspace_id": None, "workspace_root_path": None}

    workspace = resolve_workspace(resolve_effective_workspace_id(turn), managed_root=tmp_path / "managed", local_root=turn["workspace_root_path"])

    assert workspace.root == (tmp_path / "managed" / "chat_a").resolve()


def test_claim_brings_the_local_root_on_the_turn() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import create_engine, insert
    from sqlalchemy.pool import StaticPool

    from agentos.conversations.chat import PostgresChatStore
    from agentos.persistence.postgres.schema import metadata, workspace_roots

    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="owner", message="oi", provider="openrouter", model_id="m", idempotency_key="k1")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(workspace_roots).values(workspace_id=receipt.conversation_id, user_id="owner", root_path="/tmp/escolhida", created_at=now, updated_at=now))

    turn = store.claim(receipt.turn_id)

    assert turn is not None
    assert turn["workspace_root_path"] == "/tmp/escolhida"
