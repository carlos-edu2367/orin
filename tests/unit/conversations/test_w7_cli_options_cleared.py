"""W7 Batch F (W6-R14): a finished CLI turn keeps no copy of its options, which may hold tokens."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_turns, metadata

OPTIONS = '{"mcp_servers": {"gh": {"headers": {"Authorization": "Bearer ghp_secret"}}}}'


def _turn(tmp_path: Path, key: str):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / f'{key}.db').as_posix()}", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="user-1", message="hi", provider="openrouter", model_id="m", idempotency_key=key, code_mode="code", cli_options=OPTIONS)
    with engine.connect() as connection:
        row = connection.execute(select(conversation_turns).where(conversation_turns.c.turn_id == receipt.turn_id)).mappings().one()
    return engine, store, dict(row)


def _stored(engine, turn_id: str):
    with engine.connect() as connection:
        return connection.execute(select(conversation_turns.c.cli_options).where(conversation_turns.c.turn_id == turn_id)).scalar()


@pytest.mark.parametrize("failed, code", [(False, None), (True, "PROVIDER_ERROR"), (True, "TURN_CANCELLED")])
def test_a_finished_turn_forgets_its_cli_options(tmp_path: Path, failed: bool, code: str | None) -> None:
    engine, store, turn = _turn(tmp_path, f"k-{code}")
    assert _stored(engine, turn["turn_id"]) == OPTIONS
    store.finish(turn, failed=failed, code=code)
    assert _stored(engine, turn["turn_id"]) is None


def test_a_turn_waiting_for_the_person_keeps_them_to_run_again(tmp_path: Path) -> None:
    engine, store, turn = _turn(tmp_path, "waiting")
    store.finish(turn, code="WAITING_USER")
    assert _stored(engine, turn["turn_id"]) == OPTIONS
