from pathlib import Path

from agentos.capabilities.models import CapabilityEventType
from . import __name__ as _package_marker


def test_capability_package_is_an_execution_and_port_boundary():
    root = Path("src/agentos/capabilities")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "agentos.execution" in source
    assert "CapabilityToolPort" in source
    assert "CapabilityStatePort" in source
    assert "TransactionalPersistence" not in source
    forbidden = ("FastAPI", "SQLAlchemy", "Redis", "subprocess", "playwright", "openai", "anthropic", "google", "httpx")
    assert not any(term in source for term in forbidden)


def test_all_capability_event_names_are_publicly_prepared():
    expected = {
        "CapabilityStarted", "CapabilityStepStarted", "CapabilityStepFinished", "CapabilityCheckpointCreated",
        "CapabilityChildExecutionCreated", "CapabilityCompensationFinished", "CapabilityFinished",
        "CapabilityFailed", "CapabilityCancelled",
    }
    assert {item.value for item in CapabilityEventType} == expected

