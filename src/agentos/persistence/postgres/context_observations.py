"""Durable store for learned model context-window observations (`model_context_observations`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine

from .schema import model_context_observations

VALID_OBSERVATION_SOURCES: frozenset[str] = frozenset({"error_raw", "estimate"})


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ContextObservation:
    user_id: str
    provider: str
    model_id: str
    observed_limit_tokens: int
    source: str
    first_message_tokens: int | None
    cli_version: str | None
    account_credits_on: bool | None
    observed_at: datetime
    invalidated_at: datetime | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.observed_limit_tokens,
            "source": self.source,
            "observed_at": self.observed_at.isoformat(),
        }


def _row_to_observation(row: Any) -> ContextObservation:
    raw_credits = row["account_credits_on"]
    credits: bool | None = None if raw_credits is None else bool(raw_credits)
    return ContextObservation(
        user_id=str(row["user_id"]),
        provider=str(row["provider"]),
        model_id=str(row["model_id"]),
        observed_limit_tokens=int(row["observed_limit_tokens"]),
        source=str(row["source"]),
        first_message_tokens=(
            int(row["first_message_tokens"])
            if row["first_message_tokens"] is not None
            else None
        ),
        cli_version=(
            str(row["cli_version"])
            if row["cli_version"] is not None
            else None
        ),
        account_credits_on=credits,
        observed_at=_as_utc(row["observed_at"]) or datetime.now(UTC),
        invalidated_at=_as_utc(row["invalidated_at"]),
    )


class ContextObservationStore:
    """SQLAlchemy-backed persistence for `model_context_observations` (E-RF13)."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    def get(
        self,
        user_id: str,
        provider: str,
        model_id: str,
    ) -> ContextObservation | None:
        """Return the active (non-invalidated) observation for `(user_id, provider, model_id)`."""
        t = model_context_observations
        stmt = select(t).where(
            and_(
                t.c.user_id == str(user_id).strip(),
                t.c.provider == str(provider).strip(),
                t.c.model_id == str(model_id).strip(),
                t.c.invalidated_at.is_(None),
            )
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return _row_to_observation(row) if row is not None else None

    def learn(
        self,
        user_id: str,
        provider: str,
        model_id: str,
        observed_limit_tokens: int,
        source: str,
        *,
        first_message_tokens: int | None = None,
        cli_version: str | None = None,
        account_credits_on: bool | None = None,
        now: datetime | None = None,
    ) -> ContextObservation:
        """Upsert an active observation, clearing any prior `invalidated_at`."""
        uid = str(user_id).strip()
        prov = str(provider).strip()
        mdl = str(model_id).strip()
        if not uid or not prov or not mdl:
            raise ValueError("user_id, provider, and model_id must be non-empty")
        if (
            not isinstance(observed_limit_tokens, int)
            or isinstance(observed_limit_tokens, bool)
            or observed_limit_tokens <= 0
        ):
            raise ValueError("observed_limit_tokens must be a positive integer")
        clean_source = str(source or "").strip().lower()
        if clean_source not in VALID_OBSERVATION_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(VALID_OBSERVATION_SOURCES)}, got {source!r}"
            )
        clean_first: int | None = None
        if first_message_tokens is not None:
            if (
                not isinstance(first_message_tokens, int)
                or isinstance(first_message_tokens, bool)
                or first_message_tokens < 0
            ):
                raise ValueError("first_message_tokens must be a non-negative integer")
            clean_first = int(first_message_tokens)
        clean_cli_ver = str(cli_version).strip()[:32] if cli_version else None
        clean_credits = bool(account_credits_on) if isinstance(account_credits_on, bool) else None
        observed_at = _as_utc(now if now is not None else self._clock()) or datetime.now(UTC)

        values: dict[str, Any] = {
            "observed_limit_tokens": int(observed_limit_tokens),
            "source": clean_source,
            "first_message_tokens": clean_first,
            "cli_version": clean_cli_ver,
            "account_credits_on": clean_credits,
            "observed_at": observed_at,
            "invalidated_at": None,
        }

        t = model_context_observations
        pk_cond = and_(
            t.c.user_id == uid,
            t.c.provider == prov,
            t.c.model_id == mdl,
        )
        with self._engine.begin() as conn:
            existing = conn.execute(select(t.c.user_id).where(pk_cond)).first()
            if existing is not None:
                conn.execute(update(t).where(pk_cond).values(**values))
            else:
                conn.execute(
                    insert(t).values(
                        user_id=uid,
                        provider=prov,
                        model_id=mdl,
                        **values,
                    )
                )

        return ContextObservation(
            user_id=uid,
            provider=prov,
            model_id=mdl,
            observed_limit_tokens=int(observed_limit_tokens),
            source=clean_source,
            first_message_tokens=clean_first,
            cli_version=clean_cli_ver,
            account_credits_on=clean_credits,
            observed_at=observed_at,
            invalidated_at=None,
        )

    def invalidate(
        self,
        user_id: str,
        provider: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Mark an active observation invalidated; returns True if an active row was updated."""
        uid = str(user_id).strip()
        prov = str(provider).strip()
        mdl = str(model_id).strip()
        ts = _as_utc(now if now is not None else self._clock()) or datetime.now(UTC)
        t = model_context_observations
        stmt = (
            update(t)
            .where(
                and_(
                    t.c.user_id == uid,
                    t.c.provider == prov,
                    t.c.model_id == mdl,
                    t.c.invalidated_at.is_(None),
                )
            )
            .values(invalidated_at=ts)
        )
        with self._engine.begin() as conn:
            result = conn.execute(stmt)
        return bool(result.rowcount and result.rowcount > 0)

    def invalidate_if_environment_changed(
        self,
        user_id: str,
        provider: str,
        model_id: str,
        cli_version: str | None,
        account_credits_on: bool | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Invalidate the active observation if CLI version or account credits state changed (E-RF13 b/c)."""
        obs = self.get(user_id, provider, model_id)
        if obs is None:
            return False

        changed = False
        if (
            isinstance(account_credits_on, bool)
            and obs.account_credits_on is not None
            and obs.account_credits_on != account_credits_on
        ):
            changed = True

        clean_ver = str(cli_version).strip() if cli_version else None
        if clean_ver and obs.cli_version and obs.cli_version.strip() != clean_ver:
            changed = True

        if not changed:
            return False
        return self.invalidate(user_id, provider, model_id, now=now)

    def list(
        self,
        user_id: str,
        *,
        include_invalidated: bool = False,
    ) -> list[ContextObservation]:
        """Return observations for `user_id` (active only by default)."""
        t = model_context_observations
        conditions = [t.c.user_id == str(user_id).strip()]
        if not include_invalidated:
            conditions.append(t.c.invalidated_at.is_(None))
        stmt = select(t).where(and_(*conditions)).order_by(t.c.provider, t.c.model_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_observation(r) for r in rows]


__all__ = [
    "ContextObservation",
    "ContextObservationStore",
    "VALID_OBSERVATION_SOURCES",
]
