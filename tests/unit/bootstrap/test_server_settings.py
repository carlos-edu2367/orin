import pytest
from pydantic import ValidationError

from agentos.bootstrap.production import ProductionSettings
from agentos.configuration.mode import RuntimeMode

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("ORIN_MODE", "ORIN_PUBLIC_URL", "ORIN_TRUSTED_PROXIES", "LOCALHOST_TRUST_ENABLED", "WEB_DIST_DIR", "AGENTOS_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)


def _server(**overrides):
    values = {"DATABASE_URL": "sqlite:///x.db", "ORIN_MODE": "server", "ORIN_PUBLIC_URL": "https://orin.example.com", "WEB_DIST_DIR": "/srv/web", "AGENTOS_ENV": "production"}
    values.update(overrides)
    return ProductionSettings(**values)


def test_server_mode_accepts_a_complete_configuration():
    settings = _server()
    assert settings.ORIN_MODE is RuntimeMode.SERVER
    assert settings.public_origin == "https://orin.example.com"


def test_public_origin_drops_path_and_keeps_port():
    assert _server(ORIN_PUBLIC_URL="https://orin.example.com:8443/app/").public_origin == "https://orin.example.com:8443"


@pytest.mark.parametrize("url", [None, "orin.example.com", "ftp://orin.example.com", "http://orin.example.com"])
def test_server_mode_requires_an_absolute_https_public_url(url):
    with pytest.raises(ValidationError):
        _server(ORIN_PUBLIC_URL=url)


def test_plain_http_is_allowed_only_for_loopback_development():
    assert _server(ORIN_PUBLIC_URL="http://127.0.0.1:49200").public_origin == "http://127.0.0.1:49200"
    assert _server(ORIN_PUBLIC_URL="http://localhost:49200").public_origin == "http://localhost:49200"


def test_server_mode_refuses_loopback_trust():
    with pytest.raises(ValidationError):
        _server(LOCALHOST_TRUST_ENABLED=True, AGENTOS_ENV="local")


def test_server_mode_requires_the_web_build():
    with pytest.raises(ValidationError):
        _server(WEB_DIST_DIR=None)


def test_server_mode_requires_a_stable_encryption_key(monkeypatch):
    monkeypatch.delenv("AGENTOS_PROVIDER_ENCRYPTION_KEY")
    monkeypatch.delenv("APP_MASTER_KEY", raising=False)
    with pytest.raises(ValidationError):
        _server()


def test_local_mode_is_unchanged():
    settings = ProductionSettings(DATABASE_URL="sqlite:///x.db")
    assert settings.ORIN_MODE is RuntimeMode.LOCAL
    assert settings.public_origin is None
