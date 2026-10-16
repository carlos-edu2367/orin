from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def test_conversation_created_as_code_is_forever_code_and_defaults_to_accept_edits() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    first = store.create(
        user_id="user-1",
        message="olá, vamos começar um projeto",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="code-1",
        code_mode="code",
    )
    assert first.mode == "code"
    assert first.permission_mode == "accept_edits"

    conv = store.get(first.conversation_id, "user-1")
    assert conv["mode"] == "code"
    assert conv["permission_mode"] == "accept_edits"
    assert conv["turns"][0]["code_mode"] == "code"

    listed = store.list("user-1")
    assert listed["items"][0]["mode"] == "code"
    assert listed["items"][0]["permission_mode"] == "accept_edits"

    # Follow-up turn in a code conversation stays code even if caller passes code_mode="chat"
    second = store.create(
        user_id="user-1",
        conversation_id=first.conversation_id,
        message="obrigado, explique isso em texto simples",
        provider="",
        model_id="",
        idempotency_key="code-2",
        code_mode="chat",
    )
    assert second.mode == "code"
    conv_after = store.get(first.conversation_id, "user-1")
    assert conv_after["mode"] == "code"
    assert [t["code_mode"] for t in conv_after["turns"]] == ["code", "code"]


def test_conversation_created_as_chat_is_forever_chat_and_never_auto_switches_to_code() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    # Even with a message that previously triggered detect_code_request, chat mode stays chat
    first = store.create(
        user_id="user-1",
        message="corrija o bug no frontend e faça uma refatoração do endpoint",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="chat-1",
        code_mode="chat",
    )
    assert first.mode == "chat"
    assert first.permission_mode == "ask_permissions"

    conv = store.get(first.conversation_id, "user-1")
    assert conv["mode"] == "chat"
    assert conv["turns"][0]["code_mode"] is None

    # Follow-up turn in a chat conversation stays chat even if caller passes code_mode="code"
    second = store.create(
        user_id="user-1",
        conversation_id=first.conversation_id,
        message="implemente o componente agora",
        provider="",
        model_id="",
        idempotency_key="chat-2",
        code_mode="code",
    )
    assert second.mode == "chat"
    conv_after = store.get(first.conversation_id, "user-1")
    assert conv_after["mode"] == "chat"
    assert [t["code_mode"] for t in conv_after["turns"]] == [None, None]


def test_conversation_permission_mode_cycles_and_hydrates_into_claimed_turn() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    receipt = store.create(
        user_id="user-1",
        message="implement auth",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="perm-1",
        code_mode="code",
    )
    assert receipt.permission_mode == "accept_edits"

    for next_mode in ("plan_mode", "ask_permissions", "full_autonomy", "accept_edits"):
        updated = store.set_permission_mode(receipt.conversation_id, "user-1", next_mode)
        assert updated["permission_mode"] == next_mode
        assert store.get(receipt.conversation_id, "user-1")["permission_mode"] == next_mode

    store.set_permission_mode(receipt.conversation_id, "user-1", "plan_mode")
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None
    assert claimed["conversation_mode"] == "code"
    assert claimed["permission_mode"] == "plan_mode"

    with pytest.raises(ValueError):
        store.set_permission_mode(receipt.conversation_id, "user-1", "invalid_mode")
