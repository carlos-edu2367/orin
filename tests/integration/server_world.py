"""A real server-mode API over SQLite, with helpers to sign profiles in."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from agentos.api import create_app
from agentos.bootstrap.production import compose_production_services
from agentos.configuration.mode import RuntimeMode
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.sqlite import create_local_engine

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="
ORIGIN = "https://orin.test"
PASSWORD = "a long password"


@dataclass
class Session:
    api: TestClient
    csrf: str
    user_id: str

    def headers(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": self.csrf, "Idempotency-Key": uuid4().hex}


@dataclass
class ServerWorld:
    app: object
    services: object
    engine: object
    home: Path

    def client(self) -> TestClient:
        # Probes judge routes by their HTTP answer; an unavailable adapter is
        # a 500 in production, not an exception raised into the test.
        return TestClient(self.app, base_url=ORIGIN, raise_server_exceptions=False)

    def admin(self, username: str = "carla") -> Session:
        api = self.client()
        token = self.services.accounts.setup.issue_if_needed(self.services.accounts.users)
        body = api.post("/v1/auth/setup", json={"token": token, "username": username, "password": PASSWORD}, headers={"Origin": ORIGIN}).json()
        return Session(api, body["csrf_token"], body["user"]["user_id"])

    def member(self, username: str, *, admin: Session | None = None) -> Session:
        admin = admin or self._admin_session()
        created = admin.api.post("/v1/admin/users", json={"username": username}, headers=admin.headers()).json()
        api = self.client()
        signed = api.post("/v1/auth/login", json={"username": username, "password": created["temporary_password"]}, headers={"Origin": ORIGIN}).json()
        session = Session(api, signed["csrf_token"], signed["user"]["user_id"])
        changed = api.post("/v1/auth/password", json={"current_password": created["temporary_password"], "new_password": PASSWORD}, headers=session.headers())
        assert changed.status_code == 200, changed.text
        return session

    def _admin_session(self) -> Session:
        api = self.client()
        body = api.post("/v1/auth/login", json={"username": "carla", "password": PASSWORD}, headers={"Origin": ORIGIN}).json()
        return Session(api, body["csrf_token"], body["user"]["user_id"])


def build_server_world(tmp_path: Path, monkeypatch) -> ServerWorld:
    home = tmp_path / "home"
    monkeypatch.setenv("ORIN_HOME", str(home))
    monkeypatch.setenv("ORIN_MODE", "server")
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    engine = create_local_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    services = compose_production_services(engine, mode=RuntimeMode.SERVER, public_origin=ORIGIN)
    return ServerWorld(create_app(services), services, engine, home)
