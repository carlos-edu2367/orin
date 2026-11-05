from pathlib import Path


# Provider adapters are explicit composition edges: the HTTP adapter, the
# legacy-runtime adapter and the Claude CLI adapter packages (spec R2.2 puts
# them under providers/). The provider model/ports remain infrastructure-free.
_PROVIDER_EDGE_FILES = {"http.py", "compat.py"}
_PROVIDER_EDGE_PACKAGES = {"claude_cli", "cli_tools_mcp"}


def _is_provider_edge(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return relative.name in _PROVIDER_EDGE_FILES and len(relative.parts) == 1 or relative.parts[0] in _PROVIDER_EDGE_PACKAGES


def _source(package: str) -> str:
    root = Path("src/agentos") / package
    paths = [path for path in root.rglob("*.py") if not (package == "providers" and _is_provider_edge(root, path))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_provider_kernel_does_not_import_its_adapter_edges():
    kernel = _source("providers")
    for edge in ("providers.http", "providers.compat", *(f"providers.{name}" for name in _PROVIDER_EDGE_PACKAGES)):
        assert edge not in kernel, edge
    for name in _PROVIDER_EDGE_PACKAGES:
        assert f"from .{name}" not in kernel and f"from . import {name}" not in kernel, name


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
