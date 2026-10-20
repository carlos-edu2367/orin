"""W7: a conversation can be forked (whole, or before one of the person's messages) and renamed; `!command` makes a
shell turn in a Code conversation."""
from __future__ import annotations

import pytest
from sqlalchemy import select, update

from agentos.api.contracts import ApplicationNotFoundError
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns, conversations
from agentos.provider_catalog.models import ProviderCatalogContext

from tests.unit.conversations.test_chat_application import _store


def _answered(store: PostgresChatStore, conversation_id: str, answer: str) -> None:
    with store._engine.begin() as c:
        c.execute(update(conversation_messages).where(
            conversation_messages.c.conversation_id == conversation_id, conversation_messages.c.status != "completed",
        ).values(status="completed", content=answer))
        c.execute(update(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id).values(state="completed"))


def _conversation(store: PostgresChatStore, mode: str = "code") -> tuple[ChatApplication, str, list[str]]:
    application = ChatApplication(store)
    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"), message="primeira pergunta", provider="openrouter",
        model_id="model-a", workspace_id=None, idempotency_key="k1", code_mode=mode,
    )
    _answered(store, receipt.conversation_id, "primeira resposta")
    application.send("user-1", receipt.conversation_id, "segunda pergunta", "k2")
    _answered(store, receipt.conversation_id, "segunda resposta")
    with store._engine.connect() as c:
        ids = [row.message_id for row in c.execute(select(conversation_messages.c.message_id).where(
            conversation_messages.c.conversation_id == receipt.conversation_id).order_by(conversation_messages.c.sequence))]
    return application, receipt.conversation_id, ids


def _messages(store: PostgresChatStore, conversation_id: str) -> list[tuple[str, str]]:
    with store._engine.connect() as c:
        return [(row.role, row.content) for row in c.execute(select(conversation_messages).where(
            conversation_messages.c.conversation_id == conversation_id).order_by(conversation_messages.c.sequence))]


def test_a_whole_fork_copies_the_finished_messages_and_the_settings() -> None:
    store = _store()
    application, conversation_id, _ids = _conversation(store)
    fork = application.fork(conversation_id, "user-1")
    assert fork["messages"] == 4 and fork["prompt"] is None and fork["title"].endswith("(ramo)")
    assert _messages(store, fork["conversation_id"]) == _messages(store, conversation_id)
    with store._engine.connect() as c:
        original = c.execute(select(conversations).where(conversations.c.conversation_id == conversation_id)).mappings().one()
        copy = c.execute(select(conversations).where(conversations.c.conversation_id == fork["conversation_id"])).mappings().one()
    assert (copy["mode"], copy["permission_mode"], copy["model_id"], copy["state"]) == (original["mode"], original["permission_mode"], "model-a", "completed")


def test_a_fork_before_a_message_keeps_what_came_before_and_returns_the_message() -> None:
    store = _store()
    application, conversation_id, ids = _conversation(store)
    fork = application.fork(conversation_id, "user-1", before_message_id=ids[2], title="Voltar à segunda pergunta")
    assert fork["prompt"] == "segunda pergunta" and fork["title"] == "Voltar à segunda pergunta"
    assert _messages(store, fork["conversation_id"]) == [("user", "primeira pergunta"), ("assistant", "primeira resposta")]
    with pytest.raises(ApplicationNotFoundError):
        application.fork(conversation_id, "user-1", before_message_id=ids[1])  # an answer is not a rewind point
    with pytest.raises(ApplicationNotFoundError):
        application.fork(conversation_id, "someone-else")


def test_rename_trims_and_refuses_blank_or_foreign() -> None:
    store = _store()
    application, conversation_id, _ids = _conversation(store)
    assert application.rename(conversation_id, "user-1", "  Novo   nome ")["title"] == "Novo nome"
    with pytest.raises(ValueError):
        application.rename(conversation_id, "user-1", "   ")
    with pytest.raises(ApplicationNotFoundError):
        application.rename(conversation_id, "someone-else", "x")


def test_a_shell_command_is_a_shell_turn_only_in_a_code_conversation() -> None:
    store = _store()
    application, conversation_id, _ids = _conversation(store)
    receipt = application.shell("user-1", conversation_id, "  git status  ", "k3")
    with store._engine.connect() as c:
        turn = c.execute(select(conversation_turns).where(conversation_turns.c.turn_id == receipt.turn_id)).mappings().one()
    assert turn["turn_kind"] == "shell"
    assert _messages(store, conversation_id)[-2] == ("user", "!git status")
    chat_application, chat_id, _ = _conversation(_store(), mode="chat")
    with pytest.raises(ValueError):
        chat_application.shell("user-1", chat_id, "ls", "k4")
    with pytest.raises(ValueError):
        application.shell("user-1", conversation_id, "   ", "k5")
