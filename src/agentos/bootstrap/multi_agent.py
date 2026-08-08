"""Composition root for the multi-agent event bridge (frontend Fase B.1).

There is no production composition of ``agentos.multi_agent.service.
MultiAgentCoordinatorService`` anywhere in this codebase today: besides
``events`` (satisfied below), the service also requires ``store``,
``resolver``, ``administration``, ``execution`` and ``sharing`` ports, none
of which have a durable Postgres adapter yet, and no HTTP route constructs
or calls a coordinator. Fase B's scope is specifically the event-recorder
bridge (docs/frontend/PROJECT_CLOSEOUT_ROADMAP.md, Fase B.1 "Trabalho
contido"), not standing up the rest of multi-agent in production — doing so
would mean inventing adapters for ports this session never investigated.

This module exposes exactly the one durable piece Fase B.1 adds, so a
future session composing the rest of ``MultiAgentCoordinatorService`` has a
ready ``events`` argument instead of rediscovering
``PostgresMultiAgentEventRecorder``.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from agentos.persistence.postgres.multi_agent_events import PostgresMultiAgentEventRecorder


def compose_multi_agent_event_recorder(engine: Engine) -> PostgresMultiAgentEventRecorder:
    """Durable ``MultiAgentEventRecorder`` for a future coordinator composition."""
    return PostgresMultiAgentEventRecorder(engine)


__all__ = ["compose_multi_agent_event_recorder"]
