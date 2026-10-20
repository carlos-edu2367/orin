"""W7-R8: a waiting turn that the person's next message closes forgets its CLI options, as a finished turn does."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_turns, metadata

OPTIONS = '{"mcp_servers": {"gh": {"headers": {"Authorization": "Bearer ghp_secret"}}}}'


def test_a_waiting_turn_closed_by_the_next_message_forgets_its_cli_options(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'r8.db').as_posix()}", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    first = store.create(user_id="user-1", message="hi", provider="openrouter", model_id="m", idempotency_key="first",
                         code_mode="code", cli_options=OPTIONS)
    with engine.connect() as connection:
        turn = dict(connection.execute(select(conversation_turns).where(conversation_turns.c.turn_id == first.turn_id)).mappings().one())
    store.finish(turn, code="WAITING_USER")

    store.create(user_id="user-1", message="next", provider="openrouter", model_id="m", idempotency_key="second",
                 conversation_id=turn["conversation_id"], code_mode="code")

    with engine.connect() as connection:
        state, options = connection.execute(
            select(conversation_turns.c.state, conversation_turns.c.cli_options).where(conversation_turns.c.turn_id == first.turn_id)
        ).one()
    assert state == "completed"
    assert options is None
