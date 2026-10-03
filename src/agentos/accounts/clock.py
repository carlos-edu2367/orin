from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything Orin writes is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
