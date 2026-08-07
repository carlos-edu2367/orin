from pathlib import Path


def test_artifact_domain_does_not_import_concrete_infrastructure():
    root = Path(__file__).parents[3] / "src" / "agentos" / "artifact_storage"
    forbidden = ("FastAPI", "fastapi", "HTTP", "openai", "anthropic", "google", "SQLAlchemy", "sqlalchemy", "Alembic", "alembic", "Redis", "redis", "filesystem", "requests", "httpx", "kafka", "rabbit", "broker", "scheduler", "worker")
    matches = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                matches.append(f"{path}:{term}")
    assert matches == []
