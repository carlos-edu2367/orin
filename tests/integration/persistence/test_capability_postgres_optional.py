import os

import pytest


def test_capability_postgres_boundary_is_explicitly_optional():
    if not os.getenv("AGENTOS_TEST_POSTGRES_DSN"):
        pytest.skip("AGENTOS_TEST_POSTGRES_DSN is not configured")
    pytest.fail("Capability persistence is technology-neutral and has no concrete PostgreSQL adapter in this gate")

