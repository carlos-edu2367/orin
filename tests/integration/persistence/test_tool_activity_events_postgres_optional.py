from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select

from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.schema import tool_activity_events
from agentos.persistence.postgres.tool_activity import PostgresToolActivitySink
from agentos.resources.models import ResourceCapability, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.tool_runtime import (
    AtomicTool,
    DataClassification,
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


class EchoTool(AtomicTool):
    def execute(self, call):
        return {"value": call.input["value"]}


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        ToolRef("echo", 1), "echo", "bounded echo",
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        (), (ResourceRequirement(ResourceType.FILESYSTEM, (ResourceCapability.INSPECT,)),),
        ToolLimits(timedelta(seconds=5), 1024, 1024, 3), False, True,
        ToolIdempotency.IDEMPOTENT_WITH_KEY, DataClassification.INTERNAL, ToolStatus.ACTIVE,
    )


def test_a_real_tool_invocation_through_the_injected_sink_is_durably_recorded() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    invocation_id = f"inv-durable-{uuid4().hex}"
    context = SensitiveOperationContext(user_id, "workspace-1", "agent-1", execution_id, "correlation-1", "test.purpose", "user:" + user_id)
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(_descriptor(), EchoTool(), integrity="sha256:trusted")
    sink = PostgresToolActivitySink(engine)
    runtime = ToolRuntimeService(registry, ResourceManagerService(), sink=sink)

    runtime.invoke(ToolInvocationRequest(invocation_id, ToolRef("echo", 1), context, {"value": "ok"}, "key-durable"))

    with engine.connect() as connection:
        rows = connection.execute(
            select(tool_activity_events).where(tool_activity_events.c.invocation_id == invocation_id).order_by(tool_activity_events.c.id)
        ).mappings().all()
    assert [row["event_type"] for row in rows] == ["ToolStarted", "ToolFinished"]
    assert all(row["execution_id"] == execution_id for row in rows)
    assert all(row["user_id"] == user_id for row in rows)
    assert rows[1]["event"]["payload"]["outcome"] == "SUCCEEDED"
