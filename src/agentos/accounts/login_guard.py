"""Slow down password guessing per username and per client address."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import auth_login_attempts

from .clock import aware, utcnow
from .errors import LoginLocked

_SHORT_WINDOW = timedelta(minutes=15)
_LONG_WINDOW = timedelta(hours=1)
_USERNAME_SHORT_LIMIT = 5
_USERNAME_LONG_LIMIT = 10
_IP_SHORT_LIMIT = 20
_RETENTION = timedelta(days=1)


def _user_key(username: str) -> str:
    return f"user:{username.strip().lower()[:300]}"


def _ip_key(ip: str) -> str:
    return f"ip:{ip.strip()[:300]}"


class LoginGuard:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._engine = engine
        self._clock = clock

    def _failures(self, connection, key: str, since: datetime, *, reset_on_success: bool) -> list[datetime]:
        floor = since
        if reset_on_success:
            last_success = connection.execute(select(auth_login_attempts.c.occurred_at).where(
                auth_login_attempts.c.key == key, auth_login_attempts.c.succeeded.is_(True),
            ).order_by(auth_login_attempts.c.occurred_at.desc()).limit(1)).scalar_one_or_none()
            if last_success is not None:
                floor = max(since, aware(last_success))
        rows = connection.execute(select(auth_login_attempts.c.occurred_at).where(and_(
            auth_login_attempts.c.key == key, auth_login_attempts.c.succeeded.is_(False),
            auth_login_attempts.c.occurred_at > floor,
        )).order_by(auth_login_attempts.c.occurred_at)).scalars().all()
        return [aware(item) for item in rows]

    def check(self, *, username: str, ip: str) -> None:
        now = self._clock()
        retry_after = 0
        with self._engine.connect() as connection:
            user_hour = self._failures(connection, _user_key(username), now - _LONG_WINDOW, reset_on_success=True)
            user_short = [item for item in user_hour if item > now - _SHORT_WINDOW]
            if len(user_hour) >= _USERNAME_LONG_LIMIT:
                retry_after = max(retry_after, int((user_hour[-1] + _LONG_WINDOW - now).total_seconds()))
            elif len(user_short) >= _USERNAME_SHORT_LIMIT:
                retry_after = max(retry_after, int((user_short[-1] + _SHORT_WINDOW - now).total_seconds()))
            # A success never clears the address: signing in to one's own
            # account between guesses must not reset the per-IP budget.
            ip_short = self._failures(connection, _ip_key(ip), now - _SHORT_WINDOW, reset_on_success=False)
            if len(ip_short) >= _IP_SHORT_LIMIT:
                retry_after = max(retry_after, int((ip_short[-1] + _SHORT_WINDOW - now).total_seconds()))
        if retry_after > 0:
            raise LoginLocked(retry_after=retry_after)

    def record(self, *, username: str, ip: str, succeeded: bool) -> None:
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(delete(auth_login_attempts).where(auth_login_attempts.c.occurred_at < now - _RETENTION))
            connection.execute(insert(auth_login_attempts), [
                {"key": _user_key(username), "succeeded": succeeded, "occurred_at": now},
                {"key": _ip_key(ip), "succeeded": succeeded, "occurred_at": now},
            ])


__all__ = ["LoginGuard"]
