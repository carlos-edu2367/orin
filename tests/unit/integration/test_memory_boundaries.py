from pathlib import Path

from agentos.memory import MemoryContextSource


def _source(package: str) -> str:
    root = Path("src/agentos") / package
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_memory_has_no_concrete_persistence_or_delivery_imports():
    source = _source("memory")
    forbidden_imports = (
        "from agentos.persistence",
        "import agentos.persistence",
        "from agentos.events.in_memory",
        "import sqlalchemy",
        "import redis",
        "from agentos.artifact",
        "from agentos.provider",
    )
    assert not any(token in source.lower() for token in forbidden_imports)


def test_memory_reuses_existing_context_memory_source_kind():
    assert MemoryContextSource.source_kind.value == "MEMORY"
    assert "MEMORY" in _source("context")


def test_context_package_does_not_import_memory_mutations():
    source = _source("context")
    assert "agentos.memory" not in source
    assert ".save(" not in source
    assert "save_memory" not in source.lower()
