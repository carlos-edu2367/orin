"""Durable user accounts. The only place that reads or writes ``users``."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.persistence.postgres.schema import users

from .clock import aware, utcnow
from .errors import InvalidUsername, LastAdmin, UserNotFound, UsernameTaken
from .passwords import hash_password, validate_password

# A local install that becomes a server keeps its data: the first account
# owns everything the loopback principal already wrote.
FIRST_USER_ID = "local-user"
ROLES = ("admin", "member")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def normalize_username(raw: str) -> str:
    candidate = (raw or "").strip().lower()
    if not _USERNAME.fullmatch(candidate):
        raise InvalidUsername("username must be 3-64 characters of a-z, 0-9, '.', '_' or '-'")
    return candidate


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, object]:
        return {
            "user_id": self.user_id, "username": self.username, "display_name": self.display_name,
            "role": self.role, "active": self.active, "must_change_password": self.must_change_password,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
            "password_changed_at": self.password_changed_at.isoformat(),
        }


def _record(row) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"], username=row["username"], display_name=row["display_name"],
        role=row["role"], active=bool(row["active"]), must_change_password=bool(row["must_change_password"]),
        created_at=aware(row["created_at"]), updated_at=aware(row["updated_at"]),
        password_changed_at=aware(row["password_changed_at"]),
    )


class UserStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def count(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(users)).scalar_one())

    def create(self, *, username: str, password: str, role: str = "member", must_change_password: bool = False, display_name: str | None = None) -> UserRecord:
        name = normalize_username(username)
        validate_password(password)
        if role not in ROLES:
            raise ValueError("role must be admin or member")
        now = utcnow()
        try:
            with self._engine.begin() as connection:
                existing = int(connection.execute(select(func.count()).select_from(users)).scalar_one())
                user_id = FIRST_USER_ID if existing == 0 else f"usr_{uuid4().hex}"
                connection.execute(insert(users).values(
                    user_id=user_id, username=name, display_name=(display_name or "").strip()[:128] or name,
                    password_hash=hash_password(password), role=role, active=True,
                    must_change_password=must_change_password, created_at=now, updated_at=now, password_changed_at=now,
                ))
        except IntegrityError as error:
            raise UsernameTaken(f"username '{name}' is already taken") from error
        record = self.get(user_id)
        assert record is not None
        return record

    def get(self, user_id: str) -> UserRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
        return _record(row) if row is not None else None

    def find_for_login(self, username: str) -> tuple[UserRecord, str] | None:
        try:
            name = normalize_username(username)
        except InvalidUsername:
            return None
        with self._engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.username == name)).mappings().first()
        return (_record(row), str(row["password_hash"])) if row is not None else None

    def list(self) -> list[UserRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(users).order_by(users.c.created_at, users.c.username)).mappings().all()
        return [_record(row) for row in rows]

    def update(self, user_id: str, *, display_name: str | None = None, role: str | None = None, active: bool | None = None) -> UserRecord:
        if role is not None and role not in ROLES:
            raise ValueError("role must be admin or member")
        with self._engine.begin() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
            if row is None:
                raise UserNotFound(user_id)
            stays_admin = (role or row["role"]) == "admin" and (row["active"] if active is None else active)
            if row["role"] == "admin" and row["active"] and not stays_admin:
                others = int(connection.execute(select(func.count()).select_from(users).where(
                    users.c.role == "admin", users.c.active.is_(True), users.c.user_id != user_id,
                )).scalar_one())
                if others == 0:
                    raise LastAdmin("an instance always keeps one active admin")
            values: dict[str, object] = {"updated_at": utcnow()}
            if display_name is not None:
                values["display_name"] = display_name.strip()[:128] or row["username"]
            if role is not None:
                values["role"] = role
            if active is not None:
                values["active"] = active
            connection.execute(update(users).where(users.c.user_id == user_id).values(**values))
        record = self.get(user_id)
        assert record is not None
        return record

    def set_password(self, user_id: str, password: str, *, temporary: bool) -> UserRecord:
        validate_password(password)
        now = utcnow()
        with self._engine.begin() as connection:
            result = connection.execute(update(users).where(users.c.user_id == user_id).values(
                password_hash=hash_password(password), must_change_password=temporary,
                password_changed_at=now, updated_at=now,
            ))
            if result.rowcount != 1:
                raise UserNotFound(user_id)
        record = self.get(user_id)
        assert record is not None
        return record

    def is_active(self, user_id: str) -> bool:
        record = self.get(user_id)
        return record is not None and record.active


__all__ = ["FIRST_USER_ID", "ROLES", "UserRecord", "UserStore", "normalize_username"]
