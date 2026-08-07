from pathlib import Path

from agentos.context import ContextSharingService
from agentos.memory import InMemoryMemorySharingService, MemoryContextSource


def _source(package: str) -> str:
    root = Path("src/agentos") / package
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_memory_share_adapter_exposes_the_canonical_context_sharing_surface():
    assert ContextSharingService.__name__ == "ContextSharingService"
    assert all(hasattr(InMemoryMemorySharingService, name) for name in ("authorize", "create_reference", "create_handoff", "resolve", "revoke", "expire"))
    assert MemoryContextSource.source_kind.value == "MEMORY"


def test_memory_sharing_boundary_has_no_concrete_infrastructure_or_context_write_path():
    memory_source = _source("memory").lower()
    context_source = _source("context").lower()
    forbidden = ("sqlalchemy", "alembic", "redis", "fastapi", "httpx", "artifactstorage", "openai", "anthropic")

    assert not any(token in memory_source for token in forbidden)
    assert "agentos.memory" not in context_source
    assert ".save(" not in context_source
