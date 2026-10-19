"""W5: the agent team mailbox, in the database and in memory."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.conversations.mailbox import MAX_MESSAGE_CHARS, AgentMailbox, InMemoryMailbox
from agentos.persistence.postgres.schema import metadata


@pytest.fixture(params=["database", "memory"])
def mailbox(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryMailbox()
        return
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'mail.db').as_posix()}", future=True)
    metadata.create_all(engine)
    yield AgentMailbox(engine)
    engine.dispose()


def test_messages_are_taken_once_in_order_per_recipient(mailbox) -> None:
    common = {"user_id": "u1", "conversation_id": "c1", "team_name": "core"}
    mailbox.post(**common, sender="lead", recipient="alice", content="first", summary="one")
    mailbox.post(**common, sender="bob", recipient="alice", content="second")
    mailbox.post(**common, sender="lead", recipient="bob", content="for bob")
    taken = mailbox.take(user_id="u1", conversation_id="c1", recipient="alice")
    assert [(item["sender"], item["content"]) for item in taken] == [("lead", "first"), ("bob", "second")]
    assert taken[0]["summary"] == "one"
    assert mailbox.take(user_id="u1", conversation_id="c1", recipient="alice") == []
    assert [item["content"] for item in mailbox.take(user_id="u1", conversation_id="c1", recipient="bob")] == ["for bob"]


def test_another_conversation_or_person_sees_nothing(mailbox) -> None:
    mailbox.post(user_id="u1", conversation_id="c1", team_name="t", sender="lead", recipient="alice", content="x")
    assert mailbox.take(user_id="u2", conversation_id="c1", recipient="alice") == []
    assert mailbox.take(user_id="u1", conversation_id="c2", recipient="alice") == []


def test_messages_are_bounded(mailbox) -> None:
    mailbox.post(user_id="u1", conversation_id="c1", team_name="t", sender="lead", recipient="alice", content="x" * (MAX_MESSAGE_CHARS + 50))
    assert len(mailbox.take(user_id="u1", conversation_id="c1", recipient="alice")[0]["content"]) == MAX_MESSAGE_CHARS


def test_the_history_keeps_what_was_said(mailbox) -> None:
    mailbox.post(user_id="u1", conversation_id="c1", team_name="t", sender="lead", recipient="alice", content="x")
    mailbox.take(user_id="u1", conversation_id="c1", recipient="alice")
    history = mailbox.history(user_id="u1", conversation_id="c1")
    assert len(history) == 1 and history[0]["delivered"] is True
