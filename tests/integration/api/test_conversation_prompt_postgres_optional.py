from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from agentos.conversations.persistence import PostgresConversationPromptStore
from agentos.persistence.postgres import upgrade


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def test_prompt_store_materializes_an_opaque_reference_without_returning_message_text() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    store = PostgresConversationPromptStore(engine)

    reference = store.save("conversation-owner", "Mensagem privada de conversa")

    assert reference.startswith("prompt:")
    with engine.connect() as connection:
        message = connection.exec_driver_sql("SELECT message FROM conversation_prompts WHERE user_id = 'conversation-owner' ORDER BY id DESC LIMIT 1").scalar_one()
    assert message == "Mensagem privada de conversa"
