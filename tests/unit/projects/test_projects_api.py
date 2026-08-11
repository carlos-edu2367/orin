from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.persistence.postgres.schema import metadata
from agentos.projects import PostgresProjectStore


def test_project_api_creates_lists_and_hides_other_users_projects() -> None:
    """Removing API ownership filtering would disclose a private project."""
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("owner", AuthenticatedPrincipal("owner", "cred", frozenset({"api"})))
    security.add_pat("other", AuthenticatedPrincipal("other", "cred", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, projects=PostgresProjectStore(engine))))

    created = client.post("/v1/projects", headers={"Authorization": "Bearer owner", "Idempotency-Key": "create"}, json={"name": "AgentOS", "description": "Local runtime"})
    assert created.status_code == 201
    project_id = created.json()["project_id"]
    assert client.get("/v1/projects", headers={"Authorization": "Bearer owner"}).json()["items"][0]["name"] == "AgentOS"
    assert client.get(f"/v1/projects/{project_id}", headers={"Authorization": "Bearer other"}).status_code == 404
