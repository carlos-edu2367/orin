"""Tests for GET/PUT /v1/settings/orchestration (WS-E0)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.settings.paths import orin_managed_dir, orin_user_dir

AUTH = {"Authorization": "Bearer test-pat"}
CLI = {"Authorization": "Bearer cli-token"}


class _Conversations:
    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        return {
            "conversation_id": conversation_id,
            "title": "T",
            "state": "idle",
            "provider": "openai",
            "model_id": "m",
            "messages": [],
            "turns": [],
            "activities": [],
            "activity_cursor": "0",
        }


def _app(tmp_path: Path, mode: RuntimeMode = RuntimeMode.LOCAL):
    security = InMemorySecurityService()
    security.add_pat(
        "test-pat",
        AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})),
    )
    security.add_pat(
        "cli-token",
        AuthenticatedPrincipal(
            user_id="local-user",
            credential_ref="cli",
            scopes=frozenset({"api"}),
            credential_kind="cli_token",
        ),
    )
    services = ApiServices(
        security=security,
        conversation_application=_Conversations(),  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
        workspace_root=tmp_path / "workspaces",
        capabilities=InstanceCapabilities.for_mode(mode),
    )
    return create_app(services)


def _client(tmp_path: Path, mode: RuntimeMode = RuntimeMode.LOCAL) -> TestClient:
    return TestClient(_app(tmp_path, mode=mode))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _by_path(body: dict) -> dict[str, dict]:
    return {item["path"]: item for item in body["items"]}


def test_get_returns_registered_keys_with_defaults_layers_and_filters(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/v1/settings/orchestration", headers=AUTH)
    assert resp.status_code == 200
    items = _by_path(resp.json())
    assert "effort.subagentPolicy" in items
    assert "quota.policy.warnPercent" in items
    assert "context.windowOverrides" in items
    assert items["orchestration.agentMessages.parentTool"]["default"] is True
    assert items["orchestration.agentMessages.parentTool"]["value"] is True
    assert items["orchestration.agentMessages.continueOnUnread"]["default"] is True
    assert items["orchestration.agentMessages.continueOnUnread"]["value"] is True
    assert items["friend.sessionDelivery"]["default"] == "now"
    assert items["friend.sessionDelivery"]["value"] == "now"
    assert items["friend.sessionDelivery"]["choices"] == ["now", "queue"]

    policy = items["effort.subagentPolicy"]
    assert policy["value"] == "model_default"
    assert policy["default"] == "model_default"
    assert policy["layer"] == "default"
    assert policy["user_value"] is None
    assert policy["locked"] is False
    assert policy["choices"] == ["model_default", "inherit", "by_type"]

    # Group filter
    quota_only = client.get("/v1/settings/orchestration", params={"group": "quota"}, headers=AUTH)
    assert quota_only.status_code == 200
    assert all(item["group"] == "quota" for item in quota_only.json()["items"])
    assert len(quota_only.json()["items"]) == 8
    assert {"quota.activeProbe.enabled", "quota.activeProbe.intervalMinutes"} <= {
        item["path"] for item in quota_only.json()["items"]
    }

    # Prefix filter
    prefix_only = client.get("/v1/settings/orchestration", params={"prefix": "effort."}, headers=AUTH)
    assert prefix_only.status_code == 200
    assert all(item["path"].startswith("effort.") for item in prefix_only.json()["items"])


def test_put_writes_user_layer_only_clears_on_null_and_shows_in_effective(tmp_path: Path) -> None:
    client = _client(tmp_path)
    saved = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"effort.subagentPolicy": "inherit", "quota.policy.warnPercent": 70}},
    )
    assert saved.status_code == 200
    items = _by_path(saved.json())
    assert items["effort.subagentPolicy"]["value"] == "inherit"
    assert items["effort.subagentPolicy"]["layer"] == "user"
    assert items["effort.subagentPolicy"]["user_value"] == "inherit"
    assert items["quota.policy.warnPercent"]["value"] == 70
    assert items["quota.policy.warnPercent"]["layer"] == "user"

    user_file = orin_user_dir() / "settings.json"
    raw = json.loads(user_file.read_text(encoding="utf-8"))
    assert raw["effort"]["subagentPolicy"] == "inherit"
    assert raw["quota"]["policy"]["warnPercent"] == 70

    # Provenance also appears in /v1/settings/effective
    effective = client.get("/v1/settings/effective", headers=AUTH).json()
    assert effective["provenance"]["effort.subagentPolicy"] == "user"
    assert effective["provenance"]["quota.policy.warnPercent"] == "user"

    # Setting null removes the override and prunes empty parent dicts
    cleared = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"effort.subagentPolicy": None, "quota.policy.warnPercent": None}},
    )
    assert cleared.status_code == 200
    items_after = _by_path(cleared.json())
    assert items_after["effort.subagentPolicy"]["value"] == "model_default"
    assert items_after["effort.subagentPolicy"]["layer"] == "default"
    assert items_after["effort.subagentPolicy"]["user_value"] is None
    raw_after = json.loads(user_file.read_text(encoding="utf-8"))
    assert "effort" not in raw_after
    assert "quota" not in raw_after


def test_put_migrates_legacy_scalar_effort_when_writing_effort_subkey(tmp_path: Path) -> None:
    user_file = orin_user_dir() / "settings.json"
    _write(user_file, {"effort": "high"})

    client = _client(tmp_path)
    saved = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"effort.subagentPolicy": "by_type"}},
    )
    assert saved.status_code == 200
    raw = json.loads(user_file.read_text(encoding="utf-8"))
    assert raw["effortLevel"] == "high"
    assert raw["effort"] == {"subagentPolicy": "by_type"}

    items = _by_path(saved.json())
    assert items["effortLevel"]["value"] == "high"
    assert items["effort.subagentPolicy"]["value"] == "by_type"


def test_put_rejects_unknown_keys_and_invalid_values_with_422_atomically(tmp_path: Path) -> None:
    client = _client(tmp_path)
    bad_key = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"effort.subagentPolicy": "inherit", "no.such.key": True}},
    )
    assert bad_key.status_code == 422
    assert not (orin_user_dir() / "settings.json").exists()

    bad_range = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"quota.policy.warnPercent": 150}},
    )
    assert bad_range.status_code == 422
    assert bad_range.json()["error"]["details"]["problems"]

    bad_type = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"quota.display.enabled": "yes"}},
    )
    assert bad_type.status_code == 422


def test_managed_layer_marks_setting_locked_and_cli_token_cannot_write(tmp_path: Path) -> None:
    _write(orin_managed_dir() / "managed-settings.json", {"quota": {"policy": {"warnPercent": 60}}})
    client = _client(tmp_path)
    items = _by_path(client.get("/v1/settings/orchestration", headers=AUTH).json())
    assert items["quota.policy.warnPercent"]["value"] == 60
    assert items["quota.policy.warnPercent"]["layer"] == "managed"
    assert items["quota.policy.warnPercent"]["locked"] is True

    # CLI token can read but never write
    assert client.get("/v1/settings/orchestration", headers=CLI).status_code == 200
    assert client.put(
        "/v1/settings/orchestration",
        headers=CLI,
        json={"changes": {"quota.display.enabled": False}},
    ).status_code == 403


def test_orchestration_settings_routes_run_in_threadpool_and_do_not_block_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.api import orchestration_settings_routes as mod

    original = mod.orchestration_report
    slow_seconds = 0.6

    def slow_report(*args, **kwargs):
        time.sleep(slow_seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, "orchestration_report", slow_report)
    app = _app(tmp_path)

    async def _run() -> float:
        transport = ASGITransport(app=app)
        start = time.monotonic()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.ensure_future(client.get("/v1/settings/orchestration", headers=AUTH))
            await asyncio.sleep(0.15)
            fast_resp = await client.get("/v1/settings/effective", headers=AUTH)
            assert fast_resp.status_code == 200
            elapsed = time.monotonic() - start
            slow_resp = await slow
            assert slow_resp.status_code == 200
            return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed < slow_seconds, "slow /v1/settings/orchestration blocked the event loop"
