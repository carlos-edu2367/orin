import pytest
from sqlalchemy import create_engine, func, select

from agentos.accounts.errors import InvalidSetupToken, SetupCompleted
from agentos.accounts.setup import SetupTokens
from agentos.accounts.store import UserStore
from agentos.persistence.postgres.schema import instance_setup, metadata


@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return engine, SetupTokens(engine), UserStore(engine)


def _rows(engine):
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(instance_setup)).scalar_one()


def test_a_fresh_instance_issues_a_token_and_stores_only_its_digest(world):
    engine, tokens, users = world
    token = tokens.issue_if_needed(users)
    assert token and len(token) >= 40
    with engine.connect() as connection:
        stored = connection.execute(select(instance_setup.c.token_digest)).scalar_one()
    assert token not in stored and len(stored) == 64
    assert tokens.verify(token) and not tokens.verify("wrong")


def test_reissuing_replaces_the_previous_token(world):
    engine, tokens, users = world
    first = tokens.issue_if_needed(users)
    second = tokens.issue_if_needed(users)
    assert first != second
    assert not tokens.verify(first) and tokens.verify(second)
    assert _rows(engine) == 1


def test_consume_checks_the_token_and_clears_it(world):
    engine, tokens, users = world
    token = tokens.issue_if_needed(users)
    with pytest.raises(InvalidSetupToken):
        tokens.consume("nope", users)
    tokens.consume(token, users)
    assert _rows(engine) == 0


def test_no_token_once_an_account_exists(world):
    engine, tokens, users = world
    tokens.issue_if_needed(users)
    users.create(username="carla", password="a long password", role="admin")
    assert tokens.issue_if_needed(users) is None
    assert _rows(engine) == 0
    with pytest.raises(SetupCompleted):
        tokens.consume("anything", users)
