from __future__ import annotations

from pathlib import Path


def test_workspace_domain_has_no_concrete_infrastructure_imports() -> None:
    root = Path(__file__).parents[3] / "src" / "agentos" / "workspaces"
    forbidden = ("sqlalchemy", "alembic", "fastapi", "httpx", "redis", "requests", "physical_path", "root_path")
    matches = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        matches.extend((path.name, token) for token in forbidden if token in text)
    assert matches == []

