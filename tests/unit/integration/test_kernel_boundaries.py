from pathlib import Path


def _source(package: str) -> str:
    root = Path("src/agentos") / package
    # Provider HTTP and legacy-runtime adapters are explicit composition edges;
    # the provider model/ports remain infrastructure-free.
    excluded = {"http.py", "compat.py"} if package == "providers" else set()
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py") if path.name not in excluded)


def test_kernel_domains_do_not_import_concrete_infrastructure():
    forbidden = (
        "FastAPI",
        "fastapi",
        "HTTP",
        "openai",
        "anthropic",
        "google",
        "SQLAlchemy",
        "sqlalchemy",
        "Redis",
        "redis",
        "filesystem",
        "ArtifactStorage",
        "requests",
        "httpx",
        "kafka",
        "rabbit",
    )
    for package in ("execution", "runtime", "context", "events", "providers"):
        source = _source(package)
        assert not any(term in source for term in forbidden), package


def test_runtime_has_no_event_bus_or_persistence_dependency():
    source = _source("runtime")
    assert "EventBus" not in source
    assert "TransactionalPersistence" not in source


def test_context_and_provider_use_ports_not_concrete_storage():
    assert "Persistence" not in _source("context")
    assert "Persistence" not in _source("providers")


def test_provider_http_is_confined_to_the_adapter_edge():
    adapter = Path("src/agentos/providers/http.py").read_text(encoding="utf-8")
    assert "import httpx" in adapter
    assert "httpx" not in _source("providers")
