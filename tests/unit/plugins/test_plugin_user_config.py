"""W5: a plugin's options (userConfig) — declared in its manifest, kept encrypted, filled into its MCP servers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from agentos.persistence.postgres.schema import metadata, plugin_settings
from agentos.plugins.manifest import parse_plugin_manifest
from agentos.plugins.service import PluginService, PluginServiceError
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary

KEY = "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA="


def _package(root: Path, *args: str) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "weather", "version": "1.0.0",
        "userConfig": {
            "region": {"description": "Region", "required": True},
            "api_key": {"description": "Key", "sensitive": True},
            "units": {"description": "Units", "default": "metric"},
        },
        "mcpServers": {"api": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/srv.js", *args]}},
    }), encoding="utf-8")
    return root


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", KEY)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    mcp = FakeMcpService()
    plugins = PluginService(engine, plugin_root=tmp_path / "plugins", skill_library=FakeSkillLibrary(), mcp_service=mcp)
    plugins.fake_mcp = mcp
    return plugins


def test_the_manifest_declares_options() -> None:
    manifest = parse_plugin_manifest({"name": "w", "userConfig": {
        "api_key": {"description": "Key", "sensitive": True}, "bad key": {}, "x": "not a mapping",
    }})
    assert [(item.key, item.sensitive, item.required) for item in manifest.user_config] == [("api_key", True, False)]


def test_options_are_kept_encrypted_and_a_sensitive_one_is_never_shown(service, tmp_path: Path) -> None:
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "src", "--region", "${user_config.region}")))
    service.set_user_config(user_id="u1", plugin_id="weather", values={"region": "europe-west", "api_key": "s3cr3t-value"})
    with service.engine.connect() as connection:
        stored = str(connection.execute(select(plugin_settings.c.values_ciphertext)).scalar())
    assert "s3cr3t-value" not in stored and "europe-west" not in stored
    view = {item["key"]: item for item in service.user_config(user_id="u1", plugin_id="weather")["options"]}
    assert view["api_key"]["value"] == "********" and view["api_key"]["is_set"] is True
    assert view["region"]["value"] == "europe-west" and view["units"]["is_set"] is False
    with pytest.raises(PluginServiceError):
        service.set_user_config(user_id="u1", plugin_id="weather", values={"nope": "x"})
    service.set_user_config(user_id="u1", plugin_id="weather", values={"api_key": None})
    assert {item["key"]: item for item in service.user_config(user_id="u1", plugin_id="weather")["options"]}["api_key"]["is_set"] is False


def test_turning_a_plugin_on_needs_its_required_options_and_fills_them_in(service, tmp_path: Path) -> None:
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "src", "--region", "${user_config.region}", "--units", "${user_config.units}")))
    with pytest.raises(PluginServiceError, match="region"):
        service.approve(user_id="u1", plugin_id="weather")
    service.set_user_config(user_id="u1", plugin_id="weather", values={"region": "europe-west"})
    service.approve(user_id="u1", plugin_id="weather")
    assert service.fake_mcp.proposed[0]["args"][1:] == ["--region", "europe-west", "--units", "metric"]


def test_a_sensitive_option_never_reaches_a_command_line(service, tmp_path: Path) -> None:
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "src", "--key", "${user_config.api_key}")))
    service.set_user_config(user_id="u1", plugin_id="weather", values={"region": "eu", "api_key": "s3cr3t-value"})
    with pytest.raises(PluginServiceError, match="sensitive"):
        service.approve(user_id="u1", plugin_id="weather")
    assert service.fake_mcp.proposed == []


def test_options_too_large_to_encrypt_are_refused_with_a_clear_error(service, tmp_path: Path) -> None:
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "src", "--region", "${user_config.region}")))
    with pytest.raises(PluginServiceError, match="too large"):
        service.set_user_config(user_id="u1", plugin_id="weather", values={"region": "x" * 4000, "units": "y" * 4000})


def test_the_config_routes() -> None:
    from agentos.api.gateway import ApiServices, create_app
    from tests.unit.api.test_plugin_routes import Security

    seen: dict = {}

    class Plugins:
        def user_config(self, *, user_id, plugin_id):
            return {"plugin_id": plugin_id, "options": []}

        def set_user_config(self, *, user_id, plugin_id, values):
            seen.update(values)
            return {"plugin_id": plugin_id, "options": []}

    client = TestClient(create_app(ApiServices(security=Security(), plugins=Plugins())))
    assert client.get("/v1/plugins/weather/config").json() == {"plugin_id": "weather", "options": []}
    assert client.put("/v1/plugins/weather/config", json={"values": {"region": "eu", "api_key": None}}).status_code == 200
    assert seen == {"region": "eu", "api_key": None}
