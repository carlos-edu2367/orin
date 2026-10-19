"""W4: five-field cron expressions."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentos.scheduler.cron import CronError, human_schedule, local_timezone_name, next_after, parse_cron

SP = ZoneInfo("America/Sao_Paulo")


@pytest.mark.parametrize(("expression", "after", "expected"), [
    ("*/15 * * * *", datetime(2026, 10, 19, 9, 7, tzinfo=SP), datetime(2026, 10, 19, 9, 15, tzinfo=SP)),
    ("0 9 * * 1-5", datetime(2026, 10, 17, 10, 0, tzinfo=SP), datetime(2026, 10, 19, 9, 0, tzinfo=SP)),  # Saturday -> Monday
    ("30 8 1 * *", datetime(2026, 10, 2, 0, 0, tzinfo=SP), datetime(2026, 11, 1, 8, 30, tzinfo=SP)),
    ("0 0 * jan sun", datetime(2026, 10, 19, 0, 0, tzinfo=SP), datetime(2027, 1, 3, 0, 0, tzinfo=SP)),
    ("0 12 13 * 5", datetime(2026, 10, 12, 0, 0, tzinfo=SP), datetime(2026, 10, 13, 12, 0, tzinfo=SP)),  # day 13 OR Friday
    ("0 0 * * 7", datetime(2026, 10, 19, 0, 0, tzinfo=SP), datetime(2026, 10, 25, 0, 0, tzinfo=SP)),  # 7 is Sunday
])
def test_next_after(expression: str, after: datetime, expected: datetime) -> None:
    assert next_after(parse_cron(expression), after) == expected


def test_the_next_run_is_strictly_after_the_start() -> None:
    at = datetime(2026, 10, 19, 9, 15, 0, tzinfo=SP)
    assert next_after(parse_cron("*/15 * * * *"), at) == datetime(2026, 10, 19, 9, 30, tzinfo=SP)


@pytest.mark.parametrize("bad", ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "5-1 * * * *",
                                 "*/0 * * * *", "a * * * *", "1,,2 * * * *", "0 0 31 2 *", "x" * 200])
def test_bad_expressions_are_refused(bad: str) -> None:
    with pytest.raises(CronError):
        next_after(parse_cron(bad), datetime(2026, 10, 19, tzinfo=SP))


def test_human_schedules() -> None:
    assert human_schedule(parse_cron("*/5 * * * *"), "UTC") == "every 5 minutes"
    assert human_schedule(parse_cron("0 9 * * 1-5"), "America/Sao_Paulo") == "weekdays at 09:00 (America/Sao_Paulo)"
    assert human_schedule(parse_cron("30 18 * * *"), "UTC") == "every day at 18:30 (UTC)"
    assert human_schedule(parse_cron("0 9 * * mon"), "UTC") == "every Monday at 09:00 (UTC)"
    assert human_schedule(parse_cron("0 9 1,15 * *"), "UTC") == "cron 0 9 1,15 * * (UTC)"


def test_the_timezone_prefers_the_setting_then_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Lisbon")
    assert local_timezone_name("America/Sao_Paulo") == "America/Sao_Paulo"
    assert local_timezone_name("Not/AZone") == "Europe/Lisbon"
    monkeypatch.delenv("TZ")
    name = local_timezone_name(None)
    assert ZoneInfo(name) is not None
