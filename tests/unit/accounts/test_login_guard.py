from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import LoginLocked
from agentos.accounts.login_guard import LoginGuard
from agentos.persistence.postgres.schema import metadata


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def guard(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    clock = Clock()
    return LoginGuard(engine, clock=clock), clock


def _fail(guard, times, *, username="carla", ip="203.0.113.7"):
    for _ in range(times):
        guard.record(username=username, ip=ip, succeeded=False)


def test_five_failures_lock_the_username_for_fifteen_minutes(guard):
    guard, clock = guard
    _fail(guard, 4)
    guard.check(username="carla", ip="203.0.113.7")
    _fail(guard, 1)
    with pytest.raises(LoginLocked) as raised:
        guard.check(username="carla", ip="198.51.100.1")
    assert 0 < raised.value.retry_after <= 900
    clock.now += timedelta(minutes=15, seconds=1)
    guard.check(username="carla", ip="198.51.100.1")


def test_ten_failures_in_an_hour_lock_for_an_hour(guard):
    guard, clock = guard
    _fail(guard, 5)
    clock.now += timedelta(minutes=16)
    _fail(guard, 5)
    with pytest.raises(LoginLocked) as raised:
        guard.check(username="carla", ip="203.0.113.7")
    assert 900 < raised.value.retry_after <= 3600


def test_a_success_resets_the_username_count(guard):
    guard, _ = guard
    _fail(guard, 4)
    guard.record(username="carla", ip="203.0.113.7", succeeded=True)
    _fail(guard, 4)
    guard.check(username="carla", ip="203.0.113.7")


def test_twenty_failures_from_one_ip_lock_that_ip_across_usernames(guard):
    guard, _ = guard
    for index in range(20):
        guard.record(username=f"user{index}", ip="203.0.113.7", succeeded=False)
    with pytest.raises(LoginLocked):
        guard.check(username="fresh", ip="203.0.113.7")
    guard.check(username="fresh", ip="198.51.100.1")


def test_usernames_are_compared_case_insensitively(guard):
    guard, _ = guard
    _fail(guard, 5, username="Carla")
    with pytest.raises(LoginLocked):
        guard.check(username="CARLA", ip="198.51.100.1")


def test_a_success_from_an_ip_does_not_reset_that_ip(guard):
    guard, _ = guard
    for index in range(19):
        guard.record(username=f"user{index}", ip="203.0.113.7", succeeded=False)
    guard.record(username="mallory", ip="203.0.113.7", succeeded=True)
    guard.record(username="victim", ip="203.0.113.7", succeeded=False)
    with pytest.raises(LoginLocked):
        guard.check(username="fresh", ip="203.0.113.7")
