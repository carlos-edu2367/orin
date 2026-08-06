from pathlib import Path


def test_runtime_boundary_does_not_import_in_memory_agent_adapter():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/agentos/runtime").rglob("*.py")
    )
    assert "agentos.agents.in_memory" not in source


def test_agent_boundary_has_no_concrete_infrastructure_or_provider_imports():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/agentos/agents").rglob("*.py")
    )
    for forbidden in ("FastAPI", "SQLAlchemy", "Redis", "openai", "anthropic", "httpx", "filesystem"):
        assert forbidden not in source
