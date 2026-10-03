import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from agentos.api import ApiServices, create_app
from agentos.installation import orin_paths, reset_cached_paths
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

from tests.integration.server_world import build_server_world


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield build_server_world(tmp_path, monkeypatch)
    reset_cached_paths()


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _conversation(world, user_id):
    store = PostgresChatStore(world.engine, PostgresAgenticActivityStore(world.engine, "cursor-secret"))
    return store.create(user_id=user_id, message="oi", provider="openrouter", model_id="m", idempotency_key=f"seed-{user_id}").conversation_id


def test_folders_import_listing_and_download(world):
    admin = world.admin()
    assert admin.api.post("/v1/files/folders", json={"parent": "", "name": "projetos"}, headers=admin.headers()).json() == {"path": "projetos"}
    imported = admin.api.post(
        "/v1/files/import", data={"folder_name": "app"},
        files={"file": ("app.zip", _zip({"src/main.py": b"print(1)\n"}), "application/zip")},
        headers=admin.headers(),
    )
    assert imported.status_code == 201, imported.text
    assert imported.json() == {"path": "app"}
    names = [item["name"] for item in admin.api.get("/v1/files", params={"path": ""}).json()["entries"]]
    assert names == ["app", "projetos"]
    download = admin.api.get("/v1/files/download", params={"path": "app/src/main.py"})
    assert download.status_code == 200 and download.content == b"print(1)\n"
    assert "attachment" in download.headers["content-disposition"]


def test_a_zip_slip_archive_is_rejected(world):
    admin = world.admin()
    response = admin.api.post(
        "/v1/files/import", files={"file": ("evil.zip", _zip({"../evil.txt": b"x"}), "application/zip")},
        headers=admin.headers(),
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "archive_rejected")


def test_each_profile_sees_only_its_own_files(world):
    admin = world.admin()
    admin.api.post("/v1/files/folders", json={"parent": "", "name": "segredo-a"}, headers=admin.headers())
    member = world.member("bruno", admin=admin)
    assert member.api.get("/v1/files", params={"path": ""}).json()["entries"] == []
    assert member.api.get("/v1/files", params={"path": "segredo-a"}).status_code == 404
    assert member.api.get("/v1/files/download", params={"path": "../local-user/files/segredo-a"}).status_code == 404


def test_inspection_and_binding_use_the_profile_area(world):
    admin = world.admin()
    admin.api.post("/v1/files/folders", json={"parent": "", "name": "proj"}, headers=admin.headers())
    inspected = admin.api.post("/v1/workspaces/inspect", json={"path": "proj"}, headers=admin.headers()).json()
    assert (inspected["path"], inspected["is_directory"], inspected["risk"]) == ("proj", True, "none")
    for raw in ("../escape", "/etc"):
        assert admin.api.post("/v1/workspaces/inspect", json={"path": raw}, headers=admin.headers()).status_code == 422

    conversation_id = _conversation(world, admin.user_id)
    attached = admin.api.put(f"/v1/conversations/{conversation_id}/workspace", json={"path": "proj", "acknowledged_risk": False}, headers=admin.headers())
    assert attached.status_code == 200, attached.text
    assert (attached.json()["kind"], attached.json()["path"], attached.json()["folder_name"]) == ("local", "proj", "proj")
    stored = PostgresLocalWorkspaceStore(world.engine).root_for(conversation_id, admin.user_id)
    assert stored == str((orin_paths().user_files(admin.user_id) / "proj").resolve())


def test_a_legacy_host_folder_shows_as_unavailable(world, tmp_path):
    admin = world.admin()
    conversation_id = _conversation(world, admin.user_id)
    PostgresLocalWorkspaceStore(world.engine).set_root(conversation_id, admin.user_id, str(tmp_path / "host-folder"))
    workspace = admin.api.get(f"/v1/conversations/{conversation_id}").json()["workspace"]
    assert (workspace["kind"], workspace["path"], workspace["folder_name"]) == ("unavailable", None, "host-folder")


def test_files_routes_do_not_exist_in_local_mode():
    response = TestClient(create_app(ApiServices())).get("/v1/files")
    assert response.json()["error"]["code"] == "capability_unavailable"
