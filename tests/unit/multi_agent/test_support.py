from datetime import datetime, timezone
from types import SimpleNamespace

from agentos.events import DataClassification
from agentos.multi_agent import Collaboration, CollaborationPolicy, InMemoryMultiAgentStore, MultiAgentCoordinatorService


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


class FakeResolver:
    def resolve(self, **kwargs):
        return SimpleNamespace(agent_id=kwargs["agent_id"], config_version=2)


class FakeExecution:
    def __init__(self):
        self.children = []
        self.deliveries = []
        self.pauses = []
        self.resumes = []
        self.cancellations = []

    def create_child(self, **kwargs):
        self.children.append(kwargs)
        return SimpleNamespace(execution_id=kwargs["execution_id"], state_version=1)

    def create_delivery(self, **kwargs):
        self.deliveries.append(kwargs)
        return SimpleNamespace(execution_id=kwargs["execution_id"], state_version=1)

    def request_pause(self, context, **kwargs):
        self.pauses.append(context.execution_id)
        return SimpleNamespace(resulting_version=2)

    def request_resume(self, context, **kwargs):
        self.resumes.append(context.execution_id)
        return SimpleNamespace(resulting_version=3)

    def request_cancel(self, context, **kwargs):
        self.cancellations.append(context.execution_id)
        return SimpleNamespace(resulting_version=2)


class FakeSharing:
    def resolve_handoff(self, ref, **kwargs):
        return ref


def build_service():
    store = InMemoryMultiAgentStore()
    store.save_collaboration(Collaboration(
        collaboration_id="collab:1", user_id="user:1", workspace_id="workspace:1", owner="actor:1",
        participant_agent_ids=("agent:source", "agent:target"), coordinator_agent_id="agent:source",
        policy=CollaborationPolicy(4, ("task.delegation",), DataClassification.CONFIDENTIAL),
        correlation_id="corr:1", created_at=NOW, version=1,
    ), idempotency_key="collab:1")
    execution = FakeExecution()
    service = MultiAgentCoordinatorService(
        store=store, resolver=FakeResolver(), administration=SimpleNamespace(), execution=execution,
        sharing=FakeSharing(), events=store, clock=lambda: NOW,
    )
    return service, store, execution, None, None
