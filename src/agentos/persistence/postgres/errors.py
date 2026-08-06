from __future__ import annotations

from dataclasses import dataclass

from agentos.persistence.models import PersistenceErrorCode, Retryability


@dataclass(frozen=True, slots=True)
class NormalizedDatabaseError:
    code: PersistenceErrorCode
    retryability: Retryability

    def __str__(self) -> str:
        return f"persistence error: {self.code.value}"


def normalize_database_error(error: BaseException, *, during_commit: bool = False) -> NormalizedDatabaseError:
    if during_commit:
        return NormalizedDatabaseError(PersistenceErrorCode.CONNECTION, Retryability.POLICY_DEPENDENT)
    name = type(error).__name__.lower()
    text = str(error).lower()
    if "deadlock" in name or "deadlock" in text:
        return NormalizedDatabaseError(PersistenceErrorCode.DEADLOCK, Retryability.POLICY_DEPENDENT)
    if "serialization" in name or "serializ" in text:
        return NormalizedDatabaseError(PersistenceErrorCode.SERIALIZATION_FAILURE, Retryability.POLICY_DEPENDENT)
    if "timeout" in name or "timeout" in text:
        return NormalizedDatabaseError(PersistenceErrorCode.TIMEOUT, Retryability.POLICY_DEPENDENT)
    if "integrity" in name or "constraint" in text or "unique" in text:
        return NormalizedDatabaseError(PersistenceErrorCode.CONSTRAINT_VIOLATION, Retryability.NEVER)
    if "connection" in name or "connection" in text or "operational" in name:
        return NormalizedDatabaseError(PersistenceErrorCode.CONNECTION, Retryability.POLICY_DEPENDENT)
    return NormalizedDatabaseError(PersistenceErrorCode.UNKNOWN, Retryability.POLICY_DEPENDENT)


__all__ = ["NormalizedDatabaseError", "normalize_database_error"]
