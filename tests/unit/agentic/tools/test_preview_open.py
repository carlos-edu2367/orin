"""PreviewOpen: shows an already running local dev server in the Bancada (UI only)."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tool_deferral import CODE_MODE_CORE_TOOLS, FOLLOW_UP_TOOLS
from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.tools.orin.preview import SPECS, _has_hmr, normalize_preview_url
from agentos.agentic.tools.public_names import PUBLIC_NAMES, internal_name
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.engine import READ_ONLY_TOOLS


@pytest.fixture()
def listening_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def _closed_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@pytest.mark.parametrize(
    ("raw", "path", "expected"),
    [
        ("http://localhost:5173", None, "http://localhost:5173/"),
        ("localhost:3000", None, "http://localhost:3000/"),
        ("http://127.0.0.1:8000/app?x=1", None, "http://127.0.0.1:8000/app?x=1"),
        ("http://127.4.5.6:8000", None, "http://127.4.5.6:8000/"),
        ("http://[::1]:5173/", "settings", "http://[::1]:5173/settings"),
        ("http://0.0.0.0:5173", "/a/b?tab=2#top", "http://127.0.0.1:5173/a/b?tab=2#top"),
        ("https://192.168.1.20:8443/", None, "https://192.168.1.20:8443/"),
        ("http://10.0.0.5:3000", None, "http://10.0.0.5:3000/"),
        ("http://172.20.1.1:3000", None, "http://172.20.1.1:3000/"),
        ("HTTP://LOCALHOST:5173", None, "http://localhost:5173/"),
    ],
)
def test_normalize_accepts_local_and_lan_urls(raw: str, path: str | None, expected: str) -> None:
    assert normalize_preview_url(raw, path) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://example.com",
        "http://8.8.8.8:80",
        "http://172.32.0.1:3000",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost.evil.com:5173",
        "javascript:alert(1)",
        "file:///C:/Windows/win.ini",
        "ftp://localhost/",
        "http://user:pass@localhost:5173",
        "http://localhost:5173/\x00",
        "http://localhost:99999",
    ],
)
def test_normalize_rejects_remote_or_unsafe_urls(raw: str) -> None:
    with pytest.raises(AgentToolError):
        normalize_preview_url(raw)


@pytest.mark.parametrize("path", ["//evil.com/x", "http://evil.com", "\\\\evil\\share"])
def test_normalize_rejects_paths_that_change_the_host(path: str) -> None:
    with pytest.raises(AgentToolError):
        normalize_preview_url("http://localhost:5173", path)


def test_preview_open_payload_matches_preview_start_shape(tmp_path: Path, listening_port: int) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True, code_mode_active=True)
    outcome = toolset.invoke("preview_open", {"url": f"http://localhost:{listening_port}", "path": "/dashboard"})
    assert outcome.status == "succeeded", outcome.content
    preview = outcome.payload["preview"]
    assert preview["url"] == f"http://localhost:{listening_port}/dashboard"
    assert preview["status"] == "running"
    assert preview["name"] == f"localhost:{listening_port}"
    assert outcome.payload["tool_kind"] == "terminal"
    assert json.loads(outcome.content)["url"] == preview["url"]


def test_preview_open_accepts_the_public_name(tmp_path: Path, listening_port: int) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True, code_mode_active=True)
    outcome = toolset.invoke("PreviewOpen", {"url": f"http://127.0.0.1:{listening_port}"})
    assert outcome.status == "succeeded", outcome.content


def test_preview_open_fails_clearly_when_nothing_listens(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True, code_mode_active=True)
    outcome = toolset.invoke("preview_open", {"url": f"http://127.0.0.1:{_closed_port()}", "wait_timeout": 0})
    assert outcome.status == "failed"
    assert "nothing is listening" in outcome.content
    assert "preview" not in outcome.payload


def test_preview_open_rejects_remote_hosts(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True, code_mode_active=True)
    outcome = toolset.invoke("preview_open", {"url": "https://example.com"})
    assert outcome.status == "failed"
    assert "not allowed" in outcome.content


def test_preview_open_is_registered_like_preview_start() -> None:
    assert PUBLIC_NAMES["preview_open"] == "PreviewOpen"
    assert internal_name("PreviewOpen") == "preview_open"
    assert "preview_open" in CODE_MODE_CORE_TOOLS
    assert "preview_open" in FOLLOW_UP_TOOLS["preview_start"]
    assert "preview_open" in FOLLOW_UP_TOOLS["run_command"]
    assert "preview_open" in READ_ONLY_TOOLS
    spec = next(item for item in SPECS if item.key == "preview_open")
    assert spec.read_only is True
    assert spec.legacy_schema["required"] == ["url"]
    start = next(item for item in SPECS if item.key == "preview_start")
    assert "Bancada" in str(start.description) and "PreviewOpen" in str(start.description)
    assert "Bancada" in str(spec.description)


def test_hmr_detection_from_command() -> None:
    assert _has_hmr("npm run dev -- vite --port 5173") is True
    assert _has_hmr("next dev") is True
    assert _has_hmr("webpack-dev-server --hot") is True
    assert _has_hmr("python -m http.server 8000") is False
    assert _has_hmr(None) is False


def test_preview_start_api_starts_server_from_package_json_and_records_activity_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from agentos.agentic.settings import AgentRuntimeSettingsStore
    from agentos.api.gateway import ApiServices, create_app
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
    from agentos.code_mode.preview_server import PreviewServerManager
    from agentos.conversations.chat import ChatApplication, PostgresChatStore
    from agentos.persistence.postgres.schema import metadata
    from agentos.persistence.sqlite import create_local_engine

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'preview_api.db').as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        chat_app = ChatApplication(store)
        user_id = "00000000-0000-0000-0000-000000000777"
        security = InMemorySecurityService()
        security.add_pat(
            "pat",
            AuthenticatedPrincipal(
                user_id=user_id,
                credential_ref="pat-1",
                scopes=frozenset({"api"}),
            ),
        )
        workspaces_root = tmp_path / "workspaces"
        services = ApiServices(
            security=security,
            conversation_application=chat_app,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=workspaces_root,
        )
        client = TestClient(create_app(services))
        headers = {"Authorization": "Bearer pat"}

        receipt = chat_app.create(
            user_id=user_id,
            message="Criar app web",
            provider="openai",
            model_id="gpt-5.4",
            idempotency_key="idem-preview-1",
            code_mode="code",
        )
        ws = ConversationWorkspace(
            services.managed_root_for(user_id), receipt.conversation_id
        )
        (ws.root / "package.json").write_text(
            json.dumps({"name": "demo-app", "scripts": {"dev": "vite --port 5173"}}),
            encoding="utf-8",
        )

        started_roots: list[Path] = []

        def fake_start_server(self: PreviewServerManager, name: str | None = None, wait_timeout: float = 10.0) -> dict[str, object]:
            started_roots.append(self.workspace_root)
            detected = self.load_configurations()
            assert len(detected) >= 1
            return {
                "name": "npm:dev",
                "command": "vite --port 5173",
                "url": "http://127.0.0.1:5173",
                "port": 5173,
                "pid": 43210,
                "status": "running",
                "logs_tail": "ready in 100ms",
            }

        monkeypatch.setattr(PreviewServerManager, "start_server", fake_start_server)

        resp = client.post(
            f"/v1/conversations/{receipt.conversation_id}/preview/start",
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        assert len(started_roots) == 1
        assert (started_roots[0] / "package.json").is_file()

        snapshot = chat_app.get(receipt.conversation_id, user_id)
        activities = snapshot["activities"]
        preview_events = [
            ev
            for ev in activities
            if ev.get("event_type") == "tool.finished"
            and isinstance(ev.get("payload"), dict)
            and ev["payload"].get("tool_name") == "preview_start"
        ]
        assert len(preview_events) == 1
        preview_payload = preview_events[0]["payload"]["preview"]
        assert preview_payload["url"] == "http://127.0.0.1:5173"
        assert preview_payload["name"] == "npm:dev"
        assert preview_payload["status"] == "running"
        assert preview_payload["hmr"] is True
    finally:
        engine.dispose()

