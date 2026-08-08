from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.api.events import CursorError
from agentos.api.security import AuthenticatedPrincipal
from agentos.events import DataClassification, EventEnvelope
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.event_stream import PostgresClientEventStream
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter
from agentos.persistence.postgres.multi_agent_events import PostgresMultiAgentEventRecorder
from agentos.persistence.postgres.tool_activity import PostgresToolActivitySink
from agentos.resources.models import ResourceCapability, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.tool_runtime import (
    AtomicTool,
    InMemoryToolRegistry,
    ResourceRequirement,
    SensitiveOperationContext,
    ToolDescriptor,
    ToolIdempotency,
    ToolInvocationRequest,
    ToolLimits,
    ToolRef,
    ToolRuntimeService,
    ToolStatus,
)


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def _engine():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine


def _create(engine, execution_id: str, user_id: str) -> None:
    ExecutionApplicationAdapter(engine).create({
        "operation_id": f"op_{uuid4().hex}",
        "context": {"user_id": user_id, "workspace_id": None, "agent_id": "agent-1", "execution_id": execution_id},
        "task_ref": "task-ref-1",
        "limits": {},
        "expected_agent_version": None,
        "idempotency_key": f"idem_{uuid4().hex}",
        "requested_at": datetime.now(UTC),
    })


def test_a_real_execution_creation_is_readable_as_a_client_event_through_the_public_stream() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    stream = PostgresClientEventStream(engine)

    binding, cursor = stream.open(principal, [execution_id], epoch=0)
    events, next_cursor = stream.read(principal, binding.stream_id, cursor, epoch=0)

    assert len(events) == 1
    assert events[0].execution_id == execution_id
    assert events[0].event_type == "ExecutionQueued"
    assert events[0].sequence == 1
    assert next_cursor != cursor


def test_reading_again_with_the_same_cursor_returns_no_duplicate_events() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    stream = PostgresClientEventStream(engine)
    binding, cursor = stream.open(principal, [execution_id], epoch=0)
    _, next_cursor = stream.read(principal, binding.stream_id, cursor, epoch=0)

    events_again, _ = stream.read(principal, binding.stream_id, next_cursor, epoch=0)

    assert events_again == []


def test_tampered_cursor_is_rejected() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    stream = PostgresClientEventStream(engine)
    binding, cursor = stream.open(principal, [execution_id], epoch=0)

    with pytest.raises(CursorError):
        stream.read(principal, binding.stream_id, cursor[:-2] + "00", epoch=0)


def test_read_after_revocation_epoch_change_is_denied() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    stream = PostgresClientEventStream(engine)
    binding, cursor = stream.open(principal, [execution_id], epoch=0)

    with pytest.raises(PermissionError):
        stream.read(principal, binding.stream_id, cursor, epoch=1)
    assert stream.delivery_permitted(principal, binding.stream_id, epoch=1) is False
    assert stream.delivery_permitted(principal, binding.stream_id, epoch=0) is True


def test_a_stranger_cannot_read_someone_elses_stream() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    owner_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, owner_id)
    owner = AuthenticatedPrincipal(owner_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    stranger = AuthenticatedPrincipal(f"user:{uuid4().hex}", f"cred:{uuid4().hex}", frozenset({"api"}))
    stream = PostgresClientEventStream(engine)
    binding, cursor = stream.open(owner, [execution_id], epoch=0)

    with pytest.raises(PermissionError):
        stream.read(stranger, binding.stream_id, cursor, epoch=0)


class _EchoTool(AtomicTool):
    def execute(self, call):
        return {"value": call.input["value"]}


def _tool_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        ToolRef("echo", 1), "echo", "bounded echo",
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        (), (ResourceRequirement(ResourceType.FILESYSTEM, (ResourceCapability.INSPECT,)),),
        ToolLimits(timedelta(seconds=5), 1024, 1024, 3), False, True,
        ToolIdempotency.IDEMPOTENT_WITH_KEY, DataClassification.INTERNAL, ToolStatus.ACTIVE,
    )


def test_a_tool_event_and_a_delegation_event_cross_the_bridge_into_the_same_client_event_stream() -> None:
    # Fase B exit criterion: a Tool fact and a Delegation fact, written through
    # their own durable sinks (tool_activity_events / multi_agent_events),
    # are readable through the exact same public stream/read() as the
    # existing execution-lifecycle outbox events.
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))

    delegation_event = EventEnvelope(
        event_id=f"event:{uuid4().hex}",
        event_type="DelegationCreated",
        event_version=1,
        occurred_at=datetime.now(UTC),
        source="multi-agent",
        correlation_id=f"corr_{execution_id}",
        causation_id="idem-1",
        sequence=1,
        user_id=user_id,
        workspace_id=None,
        execution_id=execution_id,
        classification=DataClassification.INTERNAL,
        payload={"delegation_id": f"delegation:{uuid4().hex}", "parent_execution_id": f"exe_{uuid4().hex}"},
        agent_id="agent-child",
    )
    PostgresMultiAgentEventRecorder(engine).record_event(delegation_event)

    tool_context = SensitiveOperationContext(user_id, None, "agent-1", execution_id, "correlation-1", "test.purpose", "user:" + user_id)
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(_tool_descriptor(), _EchoTool(), integrity="sha256:trusted")
    tool_runtime = ToolRuntimeService(registry, ResourceManagerService(), sink=PostgresToolActivitySink(engine))
    tool_runtime.invoke(ToolInvocationRequest("inv-bridge", ToolRef("echo", 1), tool_context, {"value": "ok"}, "key-bridge"))

    stream = PostgresClientEventStream(engine)
    binding, cursor = stream.open(principal, [execution_id], epoch=0)
    events, _next_cursor = stream.read(principal, binding.stream_id, cursor, epoch=0, maximum_events=100)

    event_types = [event.event_type for event in events]
    assert "ExecutionQueued" in event_types
    assert "DelegationCreated" in event_types
    assert "ToolStarted" in event_types
    assert "ToolFinished" in event_types

    delegation_client_event = next(event for event in events if event.event_type == "DelegationCreated")
    assert delegation_client_event.execution_id == execution_id
    assert delegation_client_event.payload["child_execution_id"] == execution_id
    assert delegation_client_event.payload["child_agent_id"] == "agent-child"

    tool_started = next(event for event in events if event.event_type == "ToolStarted")
    assert tool_started.execution_id == execution_id
    assert tool_started.payload["invocation_id"] == "inv-bridge"
    assert tool_started.payload["tool_kind"] == "echo"


def test_binding_persists_across_a_second_stream_instance() -> None:
    # production is stateless across requests/processes; the binding must be
    # durable, not held in the adapter instance's memory.
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    _create(engine, execution_id, user_id)
    principal = AuthenticatedPrincipal(user_id, f"cred:{uuid4().hex}", frozenset({"api"}))
    binding, cursor = PostgresClientEventStream(engine).open(principal, [execution_id], epoch=0)

    events, _ = PostgresClientEventStream(engine).read(principal, binding.stream_id, cursor, epoch=0)

    assert len(events) == 1
