"""Composition helpers for durable multi-agent coordination."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from agentos.persistence.postgres.multi_agent_events import PostgresMultiAgentEventRecorder
from agentos.multi_agent.compat import AgentAdministrationAdapter, AgentResolverAdapter
from agentos.multi_agent.production import DurableMultiAgentStore
from agentos.multi_agent.service import MultiAgentCoordinatorService


def compose_multi_agent_event_recorder(engine: Engine) -> PostgresMultiAgentEventRecorder:
    """Durable ``MultiAgentEventRecorder`` for a future coordinator composition."""
    return PostgresMultiAgentEventRecorder(engine)


def compose_multi_agent_store(persistence, *, event_recorder=None) -> DurableMultiAgentStore:
    """Compose the durable domain store behind the public persistence port."""

    return DurableMultiAgentStore(persistence, event_recorder=event_recorder)


def compose_multi_agent_coordinator(
    persistence,
    *,
    agent_registry,
    agent_administration,
    execution,
    sharing,
    events,
    clock,
    model_policy=None,
    maximum_depth: int = 8,
    maximum_fanout: int = 16,
    maximum_duration_seconds: int | None = None,
    maximum_iterations: int | None = None,
):
    """Compose the existing coordinator without introducing a second domain."""

    resolver = AgentResolverAdapter(agent_registry)
    administration = AgentAdministrationAdapter(agent_administration)
    store = compose_multi_agent_store(persistence, event_recorder=events)
    return MultiAgentCoordinatorService(
        store=store,
        resolver=resolver,
        administration=administration,
        execution=execution,
        sharing=sharing,
        events=events,
        clock=clock,
        model_policy=model_policy,
        maximum_depth=maximum_depth,
        maximum_fanout=maximum_fanout,
        maximum_duration_seconds=maximum_duration_seconds,
        maximum_iterations=maximum_iterations,
    )


__all__ = [
    "compose_multi_agent_coordinator",
    "compose_multi_agent_event_recorder",
    "compose_multi_agent_store",
]
