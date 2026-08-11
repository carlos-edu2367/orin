from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_tool_records, metadata


@pytest.fixture()
def store() -> PostgresChatStore:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "execution_id": "execution-1", "agent_id": "agent-1",
    }


def test_a_tool_call_is_recorded_with_an_increasing_sequence(store: PostgresChatStore) -> None:
    store.record_tool_call(_turn(), tool_name="write_file", arguments={"path": "a.md"}, status="succeeded", summary="Escreveu a.md")
    store.record_tool_call(_turn(), tool_name="read_file", arguments={"path": "a.md"}, status="succeeded", summary="Leu a.md")

    with store._engine.connect() as connection:
        rows = connection.execute(select(conversation_tool_records).order_by(conversation_tool_records.c.sequence)).mappings().all()

    assert [row["tool_name"] for row in rows] == ["write_file", "read_file"]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["status"] == "succeeded"


def test_recording_never_raises_when_the_payload_is_not_serializable(store: PostgresChatStore) -> None:
    store.record_tool_call(_turn(), tool_name="write_file", arguments={"handle": object()}, status="succeeded", summary="ok")

    with store._engine.connect() as connection:
        rows = connection.execute(select(conversation_tool_records)).mappings().all()

    assert len(rows) == 1


class _ExplodingString:
    def __str__(self) -> str:
        raise RuntimeError("rendering failed")


def test_recording_swallows_argument_rendering_failures(store: PostgresChatStore) -> None:
    store.record_tool_call(_turn(), tool_name="write_file", arguments={"handle": _ExplodingString()}, status="succeeded", summary="ok")

    with store._engine.connect() as connection:
        rows = connection.execute(select(conversation_tool_records)).mappings().all()

    assert len(rows) == 1


def test_recording_swallows_insert_failures(store: PostgresChatStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_begin():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store._engine, "begin", fail_begin)

    store.record_tool_call(_turn(), tool_name="write_file", arguments={"path": "a.md"}, status="succeeded", summary="ok")
