from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

current_agent_id: ContextVar[str | None] = ContextVar("current_agent_id", default=None)


def get_current_agent_id() -> str | None:
    value = current_agent_id.get()
    if not value:
        return None
    normalized = str(value).strip()
    if not normalized or normalized == "main":
        return None
    return normalized


@contextmanager
def bind_agent_id(agent_id: str | None) -> Iterator[None]:
    normalized = str(agent_id).strip() if agent_id else None
    if not normalized or normalized == "main":
        normalized = None
    token = current_agent_id.set(normalized)
    try:
        yield
    finally:
        current_agent_id.reset(token)
