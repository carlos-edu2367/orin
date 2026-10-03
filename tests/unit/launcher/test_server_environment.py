from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.installation.paths import OrinPaths
from agentos.launcher.environment import ConfigurationError, load_server_environment


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def _profile(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    return SimpleNamespace(environment_files=lambda config: tuple(sorted(config.glob("*.env"))), web_dist=web, root=tmp_path, is_development=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("ORIN_MODE", "ORIN_PUBLIC_URL", "LOCALHOST_TRUST_ENABLED", "DATABASE_URL", "AGENTOS_ENV", "ORIN_BACKEND_HOST", "AGENTOS_PROVIDER_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_server_environment_forces_server_mode_and_creates_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    paths = _paths(tmp_path)
    environment = load_server_environment(paths, _profile(tmp_path))
    values = environment.values
    assert values["ORIN_MODE"] == "server"
    assert values["LOCALHOST_TRUST_ENABLED"] == "false"
    assert values["AGENTOS_ENV"] == "production"
    assert values["ORIN_BACKEND_HOST"] == "127.0.0.1"
    assert values["DATABASE_URL"].startswith("sqlite") and values["DATABASE_URL"].endswith("orin.db")
    assert values["AGENTOS_PROVIDER_ENCRYPTION_KEY"]
    assert (paths.config / "orin.env").is_file()


def test_operator_values_win(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    monkeypatch.setenv("ORIN_BACKEND_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCALHOST_TRUST_ENABLED", "true")
    values = load_server_environment(_paths(tmp_path), _profile(tmp_path)).values
    assert values["ORIN_BACKEND_HOST"] == "0.0.0.0"
    assert values["LOCALHOST_TRUST_ENABLED"] == "false"


def test_the_public_url_is_required(tmp_path):
    with pytest.raises(ConfigurationError) as raised:
        load_server_environment(_paths(tmp_path), _profile(tmp_path))
    assert "ORIN_PUBLIC_URL" in str(raised.value)


def test_only_sqlite_is_supported(tmp_path, monkeypatch):
    monkeypatch.setenv("ORIN_PUBLIC_URL", "https://orin.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    with pytest.raises(ConfigurationError):
        load_server_environment(_paths(tmp_path), _profile(tmp_path))
