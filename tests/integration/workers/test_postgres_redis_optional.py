"""Real-service smoke test; enabled only when AGENTOS_POSTGRES_URL is supplied."""
import os

import pytest


@pytest.mark.skipif(not os.getenv("AGENTOS_POSTGRES_URL"), reason="requires AGENTOS_POSTGRES_URL and a migrated PostgreSQL instance")
def test_real_postgres_is_explicitly_opt_in() -> None:
    from sqlalchemy import create_engine
    from agentos.persistence.postgres.migrate import upgrade

    engine = create_engine(os.environ["AGENTOS_POSTGRES_URL"], future=True)
    upgrade(engine)
    assert engine.dialect.name == "postgresql"
