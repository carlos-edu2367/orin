from datetime import UTC, datetime

import pytest

from agentos.agents.models import (
    AgentConfiguration,
    AgentPresentation,
    DataClassification,
    MemoryScopeReference,
    OpaqueAgentReference,
    PromptSpecification,
)


@pytest.fixture
def now():
    return datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def configuration_factory(now):
    def make(**overrides):
        values = {
            "agent_id": "agent:1",
            "config_version": 1,
            "model_profile_ref": OpaqueAgentReference("model-profile:balanced"),
            "prompt": PromptSpecification(
                prompt_ref=OpaqueAgentReference("prompt:1"),
                prompt_version=1,
                instruction_classification=DataClassification.INTERNAL,
            ),
            "presentation": AgentPresentation(avatar_ref=None, color="#123456"),
            "tool_grants": (OpaqueAgentReference("tool-grant:1"),),
            "capability_grants": (),
            "skill_grants": (),
            "execution_policy_ref": OpaqueAgentReference("execution-policy:1"),
            "context_policy_ref": OpaqueAgentReference("context-policy:1"),
            "memory_policy_ref": OpaqueAgentReference("memory-policy:1"),
            "workspace_assignments": (),
            "created_by": "actor:1",
            "created_at": now,
            "supersedes_version": None,
        }
        values.update(overrides)
        return AgentConfiguration(**values)

    return make


@pytest.fixture
def memory_scope():
    return MemoryScopeReference(
        scope_ref=OpaqueAgentReference("memory-scope:agent-1"),
        user_id="user:1",
        agent_id="agent:1",
        workspace_id=None,
        classification=DataClassification.CONFIDENTIAL,
        provenance_ref=OpaqueAgentReference("provenance:1"),
        retention_policy_ref=OpaqueAgentReference("retention:private"),
    )


@pytest.fixture
def agent_fixture(now, configuration_factory, memory_scope):
    from types import SimpleNamespace

    from agentos.agents.in_memory import (
        InMemoryAgentAdministration,
        InMemoryAgentGrantPolicy,
        InMemoryAgentRegistry,
        InMemoryAgentTransactionalPersistence,
        InMemoryAdministrativeExecutionRequester,
    )
    from agentos.agents.ports import (
        AgentAccessContext,
        AuthorizedAgentQuery,
        CreateAgent,
    )

    persistence = InMemoryAgentTransactionalPersistence()
    execution_gate = InMemoryAdministrativeExecutionRequester()
    administration = InMemoryAgentAdministration(
        persistence=persistence,
        execution_requester=execution_gate,
        now=lambda: now,
    )
    execution_gate.bind(administration)
    grant_policy = InMemoryAgentGrantPolicy()
    registry = InMemoryAgentRegistry(persistence=persistence, policy=grant_policy)
    configuration = configuration_factory()
    command = CreateAgent(
        actor="actor:1",
        user_id="user:1",
        workspace_id=None,
        agent_id="agent:1",
        correlation_id="correlation:1",
        idempotency_key="create:1",
        requested_at=now,
        owner="actor:1",
        display_name="Agent One",
        initial_configuration=configuration,
        private_memory_scope=memory_scope,
    )
    query = AuthorizedAgentQuery(
        user_id="user:1",
        workspace_id=None,
        actor="actor:1",
        purpose="agent.read",
    )
    actor = AgentAccessContext(user_id="user:1", workspace_id=None, actor="actor:1")

    def confirm_created():
        reference = administration.request_create(command)
        return execution_gate.confirm(reference)

    return SimpleNamespace(
        now=now,
        persistence=persistence,
        execution_gate=execution_gate,
        administration=administration,
        registry=registry,
        grant_policy=grant_policy,
        configuration=configuration,
        create_command=command,
        query=query,
        actor=actor,
        confirm_created=confirm_created,
    )


@pytest.fixture
def execution_factory(now):
    from agentos.execution.models import Execution, ExecutionLimits, ExecutionState, ExecutionUsage, Ownership, TaskSnapshot

    def make(**overrides):
        values = {
            "execution_id": "execution:1",
            "ownership": Ownership("user:1", None),
            "agent_id": "agent:1",
            "task": TaskSnapshot("task:1", 1),
            "state": ExecutionState.QUEUED,
            "state_version": 1,
            "correlation_id": "correlation:1",
            "causation_id": None,
            "parent_execution_id": None,
            "context_manifest_ref": None,
            "result": None,
            "failure": None,
            "cancellation_reason": None,
            "limits": ExecutionLimits(60, 10),
            "usage": ExecutionUsage(),
            "iteration_count": 0,
            "created_at": now,
            "queued_at": now,
            "started_at": None,
            "updated_at": now,
            "finished_at": None,
            "checkpoint_ref": None,
            "agent_config_version": None,
        }
        values.update(overrides)
        return Execution(**values)

    return make
