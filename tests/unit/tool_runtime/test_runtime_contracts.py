from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from agentos.resources.service import ResourceManagerService
from agentos.tool_runtime import (
    AtomicTool,
    DataClassification,
    IdempotencyPolicy,
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
    StreamDisposition,
)
from agentos.tool_runtime.models import ToolSucceeded
from agentos.tool_runtime.registry import ToolRegistry
from agentos.resources.models import ResourceCapability, ResourceType


def context():
    return SensitiveOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", "test.purpose", "user:user-1")


class EchoTool(AtomicTool):
    def execute(self, call):
        return {"value": call.input["value"]}


def descriptor():
    return ToolDescriptor(
        ToolRef("echo", 1), "echo", "bounded echo",
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
        (), (ResourceRequirement(ResourceType.FILESYSTEM, (ResourceCapability.INSPECT,)),),
        ToolLimits(timedelta(seconds=5), 1024, 1024, 3), False, True,
        ToolIdempotency.IDEMPOTENT_WITH_KEY, DataClassification.INTERNAL, ToolStatus.ACTIVE,
    )


def test_runtime_validates_closed_schema_acquires_lease_and_records_terminal_outbox():
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    runtime = ToolRuntimeService(registry, ResourceManagerService())

    outcome = runtime.invoke(ToolInvocationRequest("inv-1", ToolRef("echo", 1), context(), {"value": "ok"}, "key-1"))

    assert outcome.result == {"value": "ok"}
    assert runtime.inspect("inv-1", context()).state.value == "SUCCEEDED"
    assert [event.event_type for event in runtime.outbox] == ["ToolStarted", "ToolFinished"]

    failed = runtime.invoke(ToolInvocationRequest("inv-2", ToolRef("echo", 1), context(), {"other": "no"}, "key-2"))
    assert failed.error.code == "INVALID_INPUT"
    assert runtime.inspect("inv-2", context()).state.value == "FAILED"


def test_optional_sink_receives_every_outbox_entry_in_addition_to_the_in_memory_outbox():
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    sunk = []
    runtime = ToolRuntimeService(registry, ResourceManagerService(), sink=sunk.append)

    runtime.invoke(ToolInvocationRequest("inv-sink", ToolRef("echo", 1), context(), {"value": "ok"}, "key-sink"))

    assert [entry.event_type for entry in sunk] == ["ToolStarted", "ToolFinished"]
    assert [entry.event_type for entry in runtime.outbox] == ["ToolStarted", "ToolFinished"]
    assert all(entry.context == context() for entry in sunk)


def test_registry_published_version_is_immutable_and_bootstrap_is_allowlisted():
    registry = InMemoryToolRegistry(bootstrap_allowlist={"sha256:trusted"})
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    with pytest.raises(ValueError, match="immutable"):
        registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    with pytest.raises(PermissionError):
        InMemoryToolRegistry(bootstrap_allowlist={"sha256:other"}).register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")


def test_stream_has_monotonic_items_and_an_explicit_terminal():
    class StreamingEcho(EchoTool):
        def stream(self, call, emit):
            emit("PROGRESS", {"value": "half"})
            return {"value": call.input["value"]}

    class Sink:
        def __init__(self): self.items, self.terminal = [], None
        def emit(self, item): self.items.append(item); return StreamDisposition.ACCEPTED
        def close(self, terminal): self.terminal = terminal

    registry = InMemoryToolRegistry()
    registry.register_bootstrap(replace(descriptor(), supports_streaming=True), StreamingEcho(), integrity="sha256:trusted")
    sink = Sink()
    outcome = ToolRuntimeService(registry, ResourceManagerService()).stream(
        ToolInvocationRequest("inv-stream", ToolRef("echo", 1), context(), {"value": "ok"}, "stream-key"), sink
    )
    assert [item.sequence for item in sink.items] == [1]
    assert sink.terminal == outcome


def test_stream_drops_secret_like_fields_before_the_sink():
    class StreamingSecret(EchoTool):
        def stream(self, call, emit):
            emit("PROGRESS", {"value": "safe", "apiKey": "secret", "provider_output": "private"})
            return {"value": call.input["value"]}

    class Sink:
        def __init__(self): self.items, self.terminal = [], None
        def emit(self, item): self.items.append(item); return StreamDisposition.ACCEPTED
        def close(self, terminal): self.terminal = terminal

    registry = InMemoryToolRegistry()
    registry.register_bootstrap(replace(descriptor(), supports_streaming=True), StreamingSecret(), integrity="sha256:trusted")
    sink = Sink()
    ToolRuntimeService(registry, ResourceManagerService()).stream(
        ToolInvocationRequest("inv-stream-secret", ToolRef("echo", 1), context(), {"value": "ok"}, "stream-secret"), sink
    )
    assert sink.items[0].value == {"value": "safe"}


def test_typed_success_outcome_must_still_satisfy_the_closed_output_schema():
    class MaliciousTypedTool(EchoTool):
        def execute(self, call):
            return ToolSucceeded(call.invocation_id, {"credential": "secret"})

    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), MaliciousTypedTool(), integrity="sha256:trusted")
    outcome = ToolRuntimeService(registry, ResourceManagerService()).invoke(
        ToolInvocationRequest("inv-typed", ToolRef("echo", 1), context(), {"value": "ok"}, "typed-key")
    )
    assert outcome.error.code == "INVALID_OUTPUT"


def test_registry_authorization_applies_to_resolution_and_invocation():
    registry = InMemoryToolRegistry(authorization=lambda *_args: False)
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    runtime = ToolRuntimeService(registry, ResourceManagerService())
    outcome = runtime.invoke(ToolInvocationRequest("inv-registry-denied", ToolRef("echo", 1), context(), {"value": "ok"}, "registry-key"))
    assert outcome.error.code == "UNAUTHORIZED"


def test_same_invocation_id_with_different_arguments_is_an_idempotency_conflict():
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    runtime = ToolRuntimeService(registry, ResourceManagerService())
    first = ToolInvocationRequest("inv-fingerprint", ToolRef("echo", 1), context(), {"value": "one"}, "fingerprint-key")
    runtime.invoke(first)
    conflict = runtime.invoke(replace(first, arguments={"value": "two"}))
    assert conflict.error.code == "IDEMPOTENCY_CONFLICT"


def test_capability_bridge_calls_the_real_tool_runtime_without_importing_it_from_capability_domain():
    from agentos.capabilities.models import CapabilityOperationContext, StructuredValue, ToolRef as CapabilityToolRef
    from agentos.capabilities.ports import CapabilityToolInvocation, ToolLimitRequest
    from agentos.tool_runtime.compat import CapabilityToolRuntimeAdapter

    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), EchoTool(), integrity="sha256:trusted")
    bridge = CapabilityToolRuntimeAdapter(ToolRuntimeService(registry, ResourceManagerService()))
    legacy = CapabilityToolInvocation("run-1", "step-1", "inv-bridge", CapabilityToolRef("echo", 1), CapabilityOperationContext(*context().scope_key()), StructuredValue.from_mapping({"value": "ok"}), "bridge-key", ToolLimitRequest(5, 10))

    outcome = bridge.invoke(legacy)
    assert outcome.result_ref == "tool-result:inv-bridge"


def test_runtime_never_promotes_a_result_after_the_deadline_to_success():
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    class SlowEcho(EchoTool):
        def execute(self, call):
            current[0] += timedelta(seconds=10)
            return {"value": call.input["value"]}
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(descriptor(), SlowEcho(), integrity="sha256:trusted")
    outcome = ToolRuntimeService(registry, ResourceManagerService(), clock=lambda: current[0]).invoke(
        ToolInvocationRequest("inv-timeout", ToolRef("echo", 1), context(), {"value": "ok"}, "timeout-key")
    )
    assert outcome.error.code == "TIMEOUT"
