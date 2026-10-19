"""Five-field cron expressions for CronCreate: minute, hour, day of month, month, day of week (W4).

``*``, ``*/n``, ``a-b``, ``a-b/n``, ``n/step`` and lists are accepted; months and days of the week may be
written as three-letter names, and day 7 is Sunday like day 0. When both day fields are restricted a day
matches either (the classic cron rule). Times are wall-clock times in the schedule's IANA timezone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_CRON_CHARS = 120
MAX_SEARCH_DAYS = 366 * 5
_MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DAYS = {name: index for index, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


class CronError(ValueError):
    """The expression is not a valid five-field cron expression."""


@dataclass(frozen=True, slots=True)
class CronSpec:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool


def _value(text: str, low: int, high: int, names: dict[str, int] | None) -> int:
    if names and text in names:
        return names[text]
    if not text.isdigit() or len(text) > 2:
        raise CronError(f"'{text}' is not a number")
    number = int(text)
    if number == 7 and high == 6:  # day of week: 7 is Sunday too
        return 0
    if not low <= number <= high:
        raise CronError(f"{number} is outside {low}-{high}")
    return number


def _field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> tuple[frozenset[int], bool]:
    if text == "*":
        return frozenset(range(low, high + 1)), False
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError("empty list item")
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or not 1 <= int(step_text) <= high + 1:
                raise CronError(f"bad step '{step_text}'")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _value(first, low, high, names), _value(last, low, high, names)
            if start > end:
                raise CronError(f"range {base} runs backwards")
        else:
            start = _value(base, low, high, names)
            end = high if slash else start
        values.update(range(start, end + 1, step))
    return frozenset(values), True


def parse_cron(expression: str) -> CronSpec:
    text = " ".join(str(expression or "").split()).lower()
    if not text or len(text) > MAX_CRON_CHARS:
        raise CronError("a cron expression is required")
    parts = text.split(" ")
    if len(parts) != 5:
        raise CronError("a cron expression has five fields: minute hour day-of-month month day-of-week")
    minutes, _ = _field(parts[0], 0, 59)
    hours, _ = _field(parts[1], 0, 23)
    days, days_restricted = _field(parts[2], 1, 31)
    months, _ = _field(parts[3], 1, 12, _MONTHS)
    weekdays, weekdays_restricted = _field(parts[4], 0, 6, _DAYS)
    return CronSpec(text, minutes, hours, days, months, weekdays, days_restricted, weekdays_restricted)


def _day_matches(spec: CronSpec, day: datetime) -> bool:
    in_month = day.day in spec.days
    in_week = ((day.weekday() + 1) % 7) in spec.weekdays  # Python: Monday 0; cron: Sunday 0
    if spec.days_restricted and spec.weekdays_restricted:
        return in_month or in_week
    return in_month and in_week


def next_after(spec: CronSpec, after: datetime) -> datetime:
    """The first matching minute strictly after ``after`` (an aware local datetime)."""
    if after.tzinfo is None:
        raise CronError("the starting time must carry a timezone")
    start = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    midnight = start.replace(hour=0, minute=0)
    hours, minutes = sorted(spec.hours), sorted(spec.minutes)
    for offset in range(MAX_SEARCH_DAYS):
        day = midnight + timedelta(days=offset)
        if day.month not in spec.months or not _day_matches(spec, day):
            continue
        for hour in hours:
            for minute in minutes:
                moment = day.replace(hour=hour, minute=minute)
                if moment >= start:
                    return moment
    raise CronError("the expression never fires")


def human_schedule(spec: CronSpec, timezone: str) -> str:
    """A short description of the common shapes; anything else is shown as the expression itself."""
    minute, hour, dom, month, dow = spec.expression.split(" ")
    zone = f" ({timezone})"
    if spec.expression == "* * * * *":
        return "every minute"
    if minute.startswith("*/") and (hour, dom, month, dow) == ("*", "*", "*", "*"):
        return f"every {minute[2:]} minutes"
    if minute.isdigit() and (hour, dom, month, dow) == ("*", "*", "*", "*"):
        return f"every hour at minute {int(minute)}"
    if minute.isdigit() and hour.isdigit() and (dom, month) == ("*", "*"):
        at = f"{int(hour):02d}:{int(minute):02d}{zone}"
        if dow == "*":
            return f"every day at {at}"
        if dow == "1-5":
            return f"weekdays at {at}"
        if len(spec.weekdays) == 1:
            return f"every {_DAY_NAMES[next(iter(spec.weekdays))]} at {at}"
    return f"cron {spec.expression}{zone}"


def local_timezone_name(configured: str | None = None) -> str:
    """The IANA zone CronCreate times are read in: the operator's setting, TZ, the system zone, else UTC."""
    for candidate in (configured, os.environ.get("TZ")):
        name = str(candidate or "").strip()
        if name:
            try:
                ZoneInfo(name)
                return name
            except (ZoneInfoNotFoundError, ValueError):
                pass
    localtime = Path("/etc/localtime")
    try:
        if localtime.is_symlink():
            parts = localtime.resolve().parts
            if "zoneinfo" in parts:
                name = "/".join(parts[parts.index("zoneinfo") + 1:])
                ZoneInfo(name)
                return name
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    offset = datetime.now().astimezone().utcoffset()
    if offset is not None and offset.total_seconds() % 3600 == 0 and offset.total_seconds() != 0:
        hours = int(offset.total_seconds() // 3600)
        return f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}"  # the Etc/GMT sign is inverted by convention
    return "UTC"


__all__ = ["CronError", "CronSpec", "MAX_CRON_CHARS", "human_schedule", "local_timezone_name", "next_after", "parse_cron"]
