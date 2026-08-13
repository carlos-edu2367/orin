from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.uploads.staging import UploadStaging

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_AUTH = {"Authorization": "Bearer pat-test"}


def _client(tmp_path):
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    services = ApiServices(security=security, uploads=UploadStaging(tmp_path / "staging"), workspace_root=tmp_path / "workspaces")
    return TestClient(create_app(services))


def test_upload_returns_the_described_file(tmp_path):
    response = _client(tmp_path).post("/v1/uploads", files={"file": ("foto.png", PNG, "image/png")}, headers=_AUTH)
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "foto.png" and body["kind"] == "image" and body["bytes"] == len(PNG)
    assert body["upload_id"].startswith("upl_")


def test_upload_refuses_a_rejected_type(tmp_path):
    response = _client(tmp_path).post("/v1/uploads", files={"file": ("setup.exe", b"MZ\x90\x00" + b"\x00" * 32, "application/octet-stream")}, headers=_AUTH)
    assert response.status_code == 422


def test_delete_upload_removes_it(tmp_path):
    client = _client(tmp_path)
    upload_id = client.post("/v1/uploads", files={"file": ("foto.png", PNG, "image/png")}, headers=_AUTH).json()["upload_id"]
    assert client.delete(f"/v1/uploads/{upload_id}", headers=_AUTH).status_code == 204
    assert client.delete(f"/v1/uploads/{upload_id}", headers=_AUTH).status_code == 404


def test_create_conversation_rejects_a_blank_message_with_no_attachments(tmp_path):
    response = _client(tmp_path).post(
        "/v1/conversations",
        json={"message": "   ", "selection": {"provider": "anthropic", "model_id": "m"}},
        headers=_AUTH,
    )
    assert response.status_code == 422


def test_send_message_rejects_a_blank_message_with_no_attachments(tmp_path):
    response = _client(tmp_path).post(
        "/v1/conversations/chat_1/messages",
        json={"message": ""},
        headers=_AUTH,
    )
    assert response.status_code == 422
