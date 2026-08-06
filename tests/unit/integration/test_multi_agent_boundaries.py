import ast
from pathlib import Path

import pytest

from agentos.multi_agent import MultiAgentCoordinator, MultiAgentCoordinatorService


ROOT = Path(__file__).parents[2].parents[0] / "src" / "agentos" / "multi_agent"
FORBIDDEN = {
    "fastapi", "sqlalchemy", "alembic", "redis", "requests", "httpx",
    "kafka", "rabbitmq", "openai", "anthropic", "google",
}


def test_multi_agent_package_has_no_concrete_infrastructure_imports():
    violations = []
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(token in name.lower() for token in FORBIDDEN):
                    violations.append((path.name, name))
    assert violations == []


def test_public_coordinator_is_a_protocol_and_service_is_the_application_adapter():
    assert getattr(MultiAgentCoordinator, "_is_protocol", False) is True
    assert callable(MultiAgentCoordinatorService)


def test_multi_agent_does_not_define_execution_state_or_call_event_bus_directly():
    forbidden_tokens = {"EventBus", "TransactionalPersistence", "execution.state =", "provider", "llm"}
    violations = []
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            if token.lower() in text:
                violations.append((path.name, token))
    assert violations == []
