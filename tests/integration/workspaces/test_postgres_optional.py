from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(not os.environ.get("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def test_workspace_postgres_optional_gate_is_explicitly_conditioned_on_dsn() -> None:
    assert os.environ["AGENTOS_TEST_POSTGRES_DSN"]

