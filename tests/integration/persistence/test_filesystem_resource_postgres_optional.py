from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")
def test_postgres_filesystem_resource_round_trip_is_explicitly_opt_in() -> None:
    pytest.fail("PostgreSQL adapter integration is enabled only when the configured environment test is provided")
