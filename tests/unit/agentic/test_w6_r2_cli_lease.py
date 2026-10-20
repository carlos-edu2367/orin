"""W6-R2: the liveness lease of an `orin code` turn (renew, expiry, drop, id validation)."""
from __future__ import annotations

import os
import time

import pytest

from agentos.agentic import cli_lease
from agentos.agentic.cli_lease import HEARTBEAT_SECONDS, LEASE_SECONDS, drop, expired, leases_root, renew


def test_the_lease_timings_are_fifteen_and_five_seconds() -> None:
    assert LEASE_SECONDS == 15 and HEARTBEAT_SECONDS == 5


def test_a_missing_lease_is_expired() -> None:
    assert expired("turn_missing") is True


def test_a_renewed_lease_is_fresh_and_lives_under_the_data_folder() -> None:
    renew("turn_fresh")
    assert expired("turn_fresh") is False
    assert (leases_root() / "turn_fresh").is_file()


def test_an_old_lease_is_expired_and_renewing_it_makes_it_fresh_again() -> None:
    renew("turn_old")
    path = leases_root() / "turn_old"
    old = time.time() - (LEASE_SECONDS + 5)
    os.utime(path, (old, old))
    assert expired("turn_old") is True
    renew("turn_old")
    assert expired("turn_old") is False


def test_the_ttl_is_a_parameter() -> None:
    renew("turn_ttl")
    path = leases_root() / "turn_ttl"
    old = time.time() - 60
    os.utime(path, (old, old))
    assert expired("turn_ttl", ttl=120) is False
    assert expired("turn_ttl", ttl=30) is True


def test_drop_removes_the_lease_and_a_second_drop_is_harmless() -> None:
    renew("turn_drop")
    drop("turn_drop")
    assert expired("turn_drop") is True
    drop("turn_drop")


@pytest.mark.parametrize("bad", ["", "../x", "turn/1", "turn 1", "a.b", "x" * 129, "turn\\x"])
def test_an_id_outside_letters_digits_underscore_and_dash_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        renew(bad)
    with pytest.raises(ValueError):
        expired(bad)


def test_the_lease_root_is_in_the_isolated_data_folder() -> None:
    import os as _os

    assert str(leases_root()).startswith(_os.environ["ORIN_DATA_DIR"])
    assert cli_lease.leases_root().name == "cli-leases"
