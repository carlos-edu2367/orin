import logging

import pytest
from fastapi.testclient import TestClient

from agentos.bootstrap.production import ProductionSettings, compose_production_services, create_production_app
from agentos.configuration.mode import RuntimeMode
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.sqlite import create_local_engine

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="
ORIGIN = "https://orin.test"


@pytest.fixture()
def server_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<html><head><meta name="agentos-auth-mode" content=""></head><body></body></html>', encoding="utf-8")
    url = f"sqlite:///{tmp_path / 'orin.db'}"
    engine = create_local_engine(url)
    upgrade(engine)
    settings = ProductionSettings(DATABASE_URL=url, ORIN_MODE="server", ORIN_PUBLIC_URL=ORIGIN, WEB_DIST_DIR=str(web), AGENTOS_ENV="production")
    services = compose_production_services(engine, mode=RuntimeMode.SERVER, public_origin=settings.public_origin)
    yield create_production_app(settings, services=services), services
    reset_cached_paths()


def test_server_mode_composes_accounts_and_server_capabilities(server_app):
    _, services = server_app
    assert services.accounts is not None
    assert services.security is services.accounts.sessions
    assert services.capabilities.user_admin and not services.capabilities.shell
    assert services.public_origin == ORIGIN


def test_server_mode_serves_the_web_client_in_session_mode(server_app):
    app, _ = server_app
    with TestClient(app, base_url=ORIGIN) as api:
        page = api.get("/")
    assert 'content="session"' in page.text


def test_startup_prints_a_setup_token_that_works(server_app, caplog):
    app, services = server_app
    with caplog.at_level(logging.WARNING, logger="orin.setup"):
        with TestClient(app, base_url=ORIGIN) as api:
            assert api.get("/v1/auth/me").json()["error"]["code"] == "setup_required"
    lines = [record.getMessage() for record in caplog.records if record.name == "orin.setup"]
    token = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("token:"))
    assert services.accounts.setup.verify(token)


def test_local_mode_composition_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    reset_cached_paths()
    engine = create_local_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    services = compose_production_services(engine, localhost_trust_enabled=True)
    assert services.accounts is None
    assert services.capabilities.shell is True
    assert getattr(services.security, "requires_loopback_client", False) is True
    reset_cached_paths()
