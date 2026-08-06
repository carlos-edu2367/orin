from pathlib import Path


def _source(package: str) -> str:
    root = Path("src/agentos") / package
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_kernel_and_domains_do_not_import_sqlalchemy_or_alembic():
    for package in ("execution", "runtime", "context", "events", "providers", "agents"):
        source = _source(package).lower()
        assert "sqlalchemy" not in source, package
        assert "alembic" not in source, package


def test_technology_names_are_confined_to_postgres_adapter_package():
    persistence = _source("persistence").lower()
    public = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("src/agentos/persistence") / "__init__.py", Path("src/agentos/persistence") / "models.py", Path("src/agentos/persistence") / "ports.py", Path("src/agentos/persistence") / "security.py")
    ).lower()

    assert "sqlalchemy" not in public
    assert "alembic" not in public
    assert "sqlalchemy" in persistence
