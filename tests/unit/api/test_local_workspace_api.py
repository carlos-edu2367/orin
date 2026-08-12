from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.api.contracts import ApplicationNotFoundError
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata


class _StubConversations:
    """Only the ownership contract the workspace routes depend on.

    The real store (``PostgresChatStore.get``) raises ``ApplicationNotFoundError``
    for a conversation that does not belong to the caller, which maps to 404 —
    not the generic ``LookupError`` it subclasses, which maps to 422.
    """

    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        if user_id != "owner":
            raise ApplicationNotFoundError(conversation_id)
        return {"conversation_id": conversation_id, "title": "Chat", "state": "idle", "project_id": None, "messages": [], "turns": []}


def _client(tmp_path: Path, *, loopback: bool = False) -> tuple[TestClient, PostgresLocalWorkspaceStore]:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("owner", AuthenticatedPrincipal("owner", "cred", frozenset({"api"})))
    security.add_pat("other", AuthenticatedPrincipal("other", "cred", frozenset({"api"})))
    store = PostgresLocalWorkspaceStore(engine)
    services = ApiServices(security=security, conversation_application=_StubConversations(), local_workspaces=store, workspace_root=tmp_path / "managed")
    app = create_app(services)
    client_peer = ("127.0.0.1", 12345) if loopback else ("testclient", 50000)
    return TestClient(app, client=client_peer), store


def test_inspect_reports_a_typed_folder(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("x", encoding="utf-8")

    response = client.post("/v1/conversations/chat_a/workspace/inspect", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i1"}, json={"path": str(folder)})

    assert response.status_code == 200
    body = response.json()
    assert body["is_directory"] is True and body["entry_count"] == 1 and body["risk"] == "none"


def test_attach_requires_acknowledgement_only_for_a_risky_folder(tmp_path: Path) -> None:
    """A broad folder stays possible; it just cannot happen by accident."""
    client, store = _client(tmp_path)
    plain = tmp_path / "site"
    plain.mkdir()

    ok = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i2"}, json={"path": str(plain), "acknowledged_risk": False})
    assert ok.status_code == 200
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    root = Path(tmp_path.anchor)
    refused = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i3"}, json={"path": str(root), "acknowledged_risk": False})
    assert refused.status_code == 409
    assert store.root_for("chat_a", "owner") == str(plain.resolve())

    accepted = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i4"}, json={"path": str(root), "acknowledged_risk": True})
    assert accepted.status_code == 200
    assert store.root_for("chat_a", "owner") == str(root)


def test_attach_refuses_a_missing_folder(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i5"}, json={"path": str(tmp_path / "nao-existe"), "acknowledged_risk": True})

    assert response.status_code == 422


def test_detach_restores_the_managed_folder_and_is_idempotent(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()
    client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i6"}, json={"path": str(folder), "acknowledged_risk": False})

    first = client.delete("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i7"})
    second = client.delete("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i8"})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["kind"] == "managed"
    assert store.root_for("chat_a", "owner") is None


def test_another_user_cannot_read_or_attach(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()

    assert client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer other", "Idempotency-Key": "i9"}, json={"path": str(folder), "acknowledged_risk": False}).status_code == 404
    assert client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer other"}).status_code == 404


def test_conversation_get_carries_the_workspace_block(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    folder = tmp_path / "site"
    folder.mkdir()

    before = client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer owner"}).json()
    assert before["workspace"] == {"kind": "managed", "path": None, "folder_name": None, "scope": "chat", "project_name": None}

    client.put("/v1/conversations/chat_a/workspace", headers={"Authorization": "Bearer owner", "Idempotency-Key": "i10"}, json={"path": str(folder), "acknowledged_risk": False})
    after = client.get("/v1/conversations/chat_a", headers={"Authorization": "Bearer owner"}).json()
    assert after["workspace"]["kind"] == "local" and after["workspace"]["folder_name"] == "site"


def test_the_native_dialog_runs_through_a_threadpool(tmp_path: Path, monkeypatch) -> None:
    """A dialog left open behind another window must never hold an API worker.

    ``choose_folder`` is a blocking subprocess call. Calling it directly inside
    the ``async def`` route would stall the single event loop — and every other
    request on it — for as long as the dialog stays open. Routing it through
    ``run_in_threadpool`` is what keeps the loop free while the dialog waits.
    """
    from agentos.api import gateway as gateway_module
    from agentos.local_workspace.picker import PickResult

    client, _ = _client(tmp_path, loopback=True)

    marker = object()

    def fake_choose_folder(**_kwargs: object) -> PickResult:
        return PickResult(path=None, cancelled=True, available=True)

    fake_choose_folder.marker = marker  # type: ignore[attr-defined]
    monkeypatch.setattr(gateway_module, "choose_folder", fake_choose_folder)

    calls: list[object] = []
    original_run_in_threadpool = gateway_module.run_in_threadpool

    async def spying_run_in_threadpool(func, *args, **kwargs):
        calls.append(func)
        return await original_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(gateway_module, "run_in_threadpool", spying_run_in_threadpool)

    response = client.post("/v1/conversations/chat_a/workspace/inspect", headers={"Authorization": "Bearer owner", "Idempotency-Key": "dlg"}, json={})

    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert getattr(gateway_module.choose_folder, "marker", None) is marker
    assert any(getattr(call, "marker", None) is marker for call in calls), "choose_folder must be awaited through run_in_threadpool, not called directly"
