"""PostgreSQL authority for Dispatch and DispatchAttempt state.

Schema application remains an explicit administrative call to
``agentos.persistence.postgres.migrate.upgrade``; this adapter never creates
or migrates tables.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agentos.persistence.postgres.schema import dispatch_attempts, dispatches

from .models import DispatchAttempt, DispatchAttemptState, DispatchState, WorkItem


class DispatchConflictError(RuntimeError):
    ...


class PostgresDispatchStore:
    def __init__(self, engine: Engine) -> None:
        self._Session = sessionmaker(bind=engine, future=True)

    def create(self, item: WorkItem) -> DispatchAttempt:
        now = datetime.now(UTC)
        with self._Session.begin() as session:
            existing = session.execute(select(dispatch_attempts).where(dispatch_attempts.c.dispatch_attempt_id == item.dispatch_attempt_id)).mappings().one_or_none()
            if existing is not None:
                return self._row(existing)
            session.execute(dispatches.insert().values(
                dispatch_id=item.dispatch_id, execution_id=item.execution_id, user_id=item.context.user_id,
                workspace_id=item.context.workspace_id, agent_id=item.context.agent_id, pool=item.pool.value,
                work_kind=item.work_kind.value, state=DispatchState.PENDING.value, version=1,
                idempotency_key=item.idempotency_key, payload_ref=item.payload_ref, created_at=now, updated_at=now,
            ))
            session.execute(dispatch_attempts.insert().values(
                dispatch_attempt_id=item.dispatch_attempt_id, dispatch_id=item.dispatch_id, attempt_number=item.attempt_number,
                state=DispatchAttemptState.ENQUEUED.value, version=1, not_before=item.not_before, expires_at=item.expires_at,
                created_at=now, updated_at=now,
            ))
        return DispatchAttempt.enqueued(item.dispatch_attempt_id, item.dispatch_id, item.attempt_number)

    def lease(self, dispatch_attempt_id: str, *, worker_id: str, lease_id: str, fence: int, expected_version: int, expires_at: datetime) -> DispatchAttempt:
        return self._cas(dispatch_attempt_id, expected_version, DispatchAttemptState.ENQUEUED, DispatchAttemptState.LEASED, lease_id=lease_id, worker_id=worker_id, fence=fence, expires_at=expires_at)

    def acknowledge(self, dispatch_attempt_id: str, *, lease_id: str, fence: int, expected_version: int) -> DispatchAttempt:
        return self._cas(dispatch_attempt_id, expected_version, DispatchAttemptState.LEASED, DispatchAttemptState.ACKNOWLEDGED, lease_id=lease_id, fence=fence)

    def recover_expired(self, *, observed_at: datetime) -> tuple[DispatchAttempt, ...]:
        """Return leased attempts whose durable expiry requires reconciliation."""
        with self._Session() as session:
            rows = session.execute(select(dispatch_attempts).where(dispatch_attempts.c.state == DispatchAttemptState.LEASED.value, dispatch_attempts.c.lease_expires_at < observed_at)).mappings()
            return tuple(self._row(row) for row in rows)

    def _cas(self, attempt_id: str, expected_version: int, source: DispatchAttemptState, destination: DispatchAttemptState, *, lease_id: str, fence: int, worker_id: str | None = None, expires_at: datetime | None = None) -> DispatchAttempt:
        now = datetime.now(UTC)
        criteria = [dispatch_attempts.c.dispatch_attempt_id == attempt_id, dispatch_attempts.c.version == expected_version, dispatch_attempts.c.state == source.value]
        if source is DispatchAttemptState.LEASED:
            criteria += [dispatch_attempts.c.lease_id == lease_id, dispatch_attempts.c.fencing_token == fence]
        values: dict[str, object] = {"state": destination.value, "version": expected_version + 1, "updated_at": now}
        if destination is DispatchAttemptState.LEASED:
            values.update(lease_id=lease_id, worker_id=worker_id, fencing_token=fence, lease_expires_at=expires_at)
        else:
            values.update(lease_id=None, worker_id=None, fencing_token=None, lease_expires_at=None)
        with self._Session.begin() as session:
            result = session.execute(update(dispatch_attempts).where(*criteria).values(**values))
            if result.rowcount != 1:
                raise DispatchConflictError("dispatch attempt version, lease, or fencing token is no longer current")
            row = session.execute(select(dispatch_attempts).where(dispatch_attempts.c.dispatch_attempt_id == attempt_id)).mappings().one()
        return self._row(row)

    @staticmethod
    def _row(row) -> DispatchAttempt:
        expires_at = row["lease_expires_at"]
        if expires_at is not None and (expires_at.tzinfo is None or expires_at.utcoffset() is None):
            expires_at = expires_at.replace(tzinfo=UTC)
        return DispatchAttempt(
            dispatch_attempt_id=row["dispatch_attempt_id"], dispatch_id=row["dispatch_id"], attempt_number=row["attempt_number"],
            state=DispatchAttemptState(row["state"]), version=row["version"], lease_id=row["lease_id"], worker_id=row["worker_id"],
            fencing_token=row["fencing_token"], lease_expires_at=expires_at, reason_code=row["reason_code"],
        )


__all__ = ["DispatchConflictError", "PostgresDispatchStore"]
