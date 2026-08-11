from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from agentos.events import DataClassification, EventEnvelope, EventContext, InMemoryEventBus, ReplayPolicy, ReplayRequest
from agentos.events.in_memory import InMemoryEventArchive
from agentos.events.models import DeliveryDisposition
from agentos.events.ports import OrderingRequirement, OwnershipScope, SubscriptionSpec, VersionRange
from agentos.providers.http import OpenRouterHTTPAdapter, ProviderHTTPSettings
from agentos.providers.models import GenerationFailed, GenerationSucceeded, ProviderErrorCategory, ProviderRef, Retryability
from agentos.runtime.models import FailedOutcome, RuntimeErrorCategory, RuntimeLimits, RuntimeRequest
from tests.fixtures.agentic.provider_server import DeterministicProviderServer, make_provider_request


def test_transient_provider_failure_is_safe_to_retry_and_second_attempt_succeeds() -> None:
    with DeterministicProviderServer("retry_then_success") as provider:
        adapter = OpenRouterHTTPAdapter(ProviderHTTPSettings(ProviderRef("provider:deterministic"), provider.base_url, "sk-agentic-retry-secret", "deterministic-model"))
        try:
            first = adapter.generate(make_provider_request(invocation_id="invocation:retry-1"))
            second = adapter.generate(make_provider_request(invocation_id="invocation:retry-2"))
        finally:
            adapter.close()

    assert isinstance(first, GenerationFailed)
    assert first.error.category is ProviderErrorCategory.PROVIDER_INTERNAL
    assert first.error.retryability is Retryability.SAFE
    assert isinstance(second, GenerationSucceeded)
    assert provider.call_count == 2


def test_runtime_budget_blocks_provider_effect_before_call() -> None:
    from tests.unit.runtime.conftest import FakeAction, FakeBudget, FakeCheckpoint, FakeClock, FakeContext, FakeControl, FakeProvider, FakeResolver, make_execution
    from agentos.runtime.service import RuntimeService

    now = datetime(2026, 8, 10, tzinfo=UTC)
    control, context, resolver, provider = FakeControl(make_execution()), FakeContext(), FakeResolver(), FakeProvider()
    action, checkpoints, clock = FakeAction(), FakeCheckpoint(), FakeClock()
    request = RuntimeRequest("execution-1", "user-1", "workspace-1", "agent-1", "actor-1", "worker-1", "correlation-1", "agentic-budget", "requirements:1", requested_at=now)
    runtime = RuntimeService(control=control, context_manager=context, model_resolver=resolver, provider=provider, action_port=action, checkpoint_port=checkpoints, clock=clock, budget_policy=FakeBudget())
    runtime._limits = RuntimeLimits(max_provider_tokens=0)

    outcome = runtime.execute(request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.LIMIT
    assert provider.calls == []


def test_watchdog_marks_unacquired_turn_retryable_for_recovery() -> None:
    from agentos.conversations.runtime import InMemoryChatRuntime

    started = datetime(2026, 8, 10, tzinfo=UTC)
    runtime = InMemoryChatRuntime(now=lambda: started)
    receipt = runtime.create(user_id="user:agentic", message="recover me", provider="deterministic", model_id="deterministic-model", idempotency_key="recovery-1")

    expired = runtime.watchdog(started + timedelta(seconds=30), acquire_timeout=timedelta(seconds=30))
    conversation = runtime.get(receipt.conversation_id, "user:agentic")

    assert expired == (receipt.turn_id,)
    assert conversation.turns[-1].state == "failed"
    assert conversation.messages[-1].retryable is True


@dataclass
class _Consumer:
    deliveries: list[object]

    def handle(self, delivery: object) -> DeliveryDisposition:
        self.deliveries.append(delivery)
        return DeliveryDisposition.ACKNOWLEDGED


def test_replay_preserves_event_identity_and_does_not_delete_history() -> None:
    context = EventContext("user:agentic", "workspace:agentic", "agent:agentic", "execution:agentic", "correlation:agentic", "agentic-replay")
    event = EventEnvelope(
        event_id="event:agentic:1",
        event_type="AgenticTurnCompleted",
        event_version=1,
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        source="agentic-harness",
        correlation_id="correlation:agentic",
        causation_id="turn:agentic",
        sequence=1,
        user_id="user:agentic",
        workspace_id="workspace:agentic",
        agent_id="agent:agentic",
        execution_id="execution:agentic",
        classification=DataClassification.INTERNAL,
        payload={"state": "COMPLETED"},
    )
    consumer = _Consumer([])
    bus = InMemoryEventBus()
    subscription = bus.subscribe(SubscriptionSpec("agentic-replay", ("Agentic*",), (VersionRange(1, 1),), OwnershipScope("user:agentic", "workspace:agentic"), OrderingRequirement.NONE, DataClassification.INTERNAL, ReplayPolicy.ALLOWED), consumer)
    archive = InMemoryEventArchive(bus)
    archive.append((event,))

    job = archive.replay(ReplayRequest(context, subscription, event_ids=(event.event_id,)))
    result = archive.run_replay(job)

    assert result.status.value == "COMPLETED"
    assert consumer.deliveries[-1].event.event_id == event.event_id
    assert consumer.deliveries[-1].replay is True
    assert archive.query(__import__("agentos.events", fromlist=["AuthorizedEventQuery"]).AuthorizedEventQuery(context)).events[0].event_id == event.event_id
