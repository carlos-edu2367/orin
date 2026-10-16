from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.agentic.workspace import ConversationWorkspace
from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.api.contracts import ApplicationNotFoundError
from agentos.conversations.chat import ChatApplication, PostgresChatStore, migrate_managed_workspace_files
from agentos.local_workspace.store import PostgresLocalWorkspaceStore
from agentos.persistence.postgres.schema import metadata
from agentos.projects import PostgresProjectStore


def _build_env(tmp_path: Path):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    security = InMemorySecurityService()
    security.add_pat("owner", AuthenticatedPrincipal("owner", "cred-owner", frozenset({"api"})))
    security.add_pat("other", AuthenticatedPrincipal("other", "cred-other", frozenset({"api"})))

    chat_store = PostgresChatStore(engine)
    project_store = PostgresProjectStore(engine)
    local_workspaces = PostgresLocalWorkspaceStore(engine)
    managed_root = tmp_path / "managed_workspaces"
    managed_root.mkdir(parents=True, exist_ok=True)

    services = ApiServices(
        security=security,
        conversation_application=ChatApplication(chat_store),
        projects=project_store,
        local_workspaces=local_workspaces,
        workspace_root=managed_root,
    )
    client = TestClient(create_app(services))
    return client, chat_store, project_store, local_workspaces, managed_root


def test_get_conversation_includes_project_id_and_project_name(tmp_path: Path) -> None:
    client, chat_store, project_store, _, _ = _build_env(tmp_path)
    project = project_store.create(user_id="owner", name="Orin Core", description="Main repo")

    standalone = chat_store.create(
        user_id="owner",
        message="Standalone question",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-standalone",
    )
    in_project = chat_store.create(
        user_id="owner",
        message="Project task",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-project",
        project_id=project.project_id,
        code_mode="code",
    )

    res_standalone = client.get(
        f"/v1/conversations/{standalone.conversation_id}",
        headers={"Authorization": "Bearer owner"},
    )
    assert res_standalone.status_code == 200
    body_standalone = res_standalone.json()
    assert body_standalone["project_id"] is None
    assert body_standalone["project_name"] is None
    assert body_standalone["workspace"]["scope"] == "chat"

    res_project = client.get(
        f"/v1/conversations/{in_project.conversation_id}",
        headers={"Authorization": "Bearer owner"},
    )
    assert res_project.status_code == 200
    body_project = res_project.json()
    assert body_project["project_id"] == project.project_id
    assert body_project["project_name"] == "Orin Core"
    assert body_project["workspace"]["scope"] == "project"
    assert body_project["workspace"]["project_name"] == "Orin Core"


def test_patch_conversation_project_moves_session_inherits_local_folder_and_migrates_files(tmp_path: Path) -> None:
    client, chat_store, project_store, local_workspaces, managed_root = _build_env(tmp_path)

    # Create standalone Code conversation with a local workspace folder AND scratch files in managed workspace
    local_dir = tmp_path / "my_local_repo"
    local_dir.mkdir()
    (local_dir / "existing.txt").write_text("original-local", encoding="utf-8")

    conv = chat_store.create(
        user_id="owner",
        message="Build feature X",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-move-1",
        code_mode="code",
    )
    local_workspaces.set_root(conv.conversation_id, "owner", str(local_dir))

    # Also write a scratch file in the conversation's managed workspace
    scratch_ws = ConversationWorkspace(managed_root, conv.conversation_id)
    (scratch_ws.root / "notes" / "spec.md").parent.mkdir(parents=True, exist_ok=True)
    (scratch_ws.root / "notes" / "spec.md").write_text("# Spec X", encoding="utf-8")
    (scratch_ws.root / "existing.txt").write_text("should-not-overwrite", encoding="utf-8")

    project = project_store.create(user_id="owner", name="Feature X Project", description="Handoff target")

    res = client.patch(
        f"/v1/conversations/{conv.conversation_id}/project",
        headers={"Authorization": "Bearer owner"},
        json={"project_id": project.project_id},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["conversation_id"] == conv.conversation_id
    assert payload["project_id"] == project.project_id
    assert payload["project_name"] == "Feature X Project"
    assert "notes/spec.md" in payload["migrated_files"]
    assert "existing.txt" not in payload["migrated_files"]
    assert payload["workspace"] == {
        "kind": "local",
        "path": str(local_dir),
        "folder_name": "my_local_repo",
        "scope": "project",
        "project_name": "Feature X Project",
    }

    # Verify the project inherited the conversation's local workspace root
    assert local_workspaces.root_for(project.workspace_id, "owner") == str(local_dir)
    # Verify scratch file was migrated to the effective target root and existing file was NOT overwritten
    assert (local_dir / "notes" / "spec.md").read_text(encoding="utf-8") == "# Spec X"
    assert (local_dir / "existing.txt").read_text(encoding="utf-8") == "original-local"

    # Verify standalone list no longer includes the moved conversation, and project sidebar does
    standalone_items = client.get("/v1/conversations", headers={"Authorization": "Bearer owner"}).json()["items"]
    assert all(item["conversation_id"] != conv.conversation_id for item in standalone_items)

    sidebar_items = client.get("/v1/projects/sidebar", headers={"Authorization": "Bearer owner"}).json()["items"]
    assert len(sidebar_items) == 1
    assert sidebar_items[0]["chats"][0]["conversation_id"] == conv.conversation_id
    assert sidebar_items[0]["chats"][0]["mode"] == "code"
    assert sidebar_items[0]["workspace"]["kind"] == "local"
    assert sidebar_items[0]["workspace"]["path"] == str(local_dir)

    # Detach conversation from project (project_id = None)
    detach_res = client.patch(
        f"/v1/conversations/{conv.conversation_id}/project",
        headers={"Authorization": "Bearer owner"},
        json={"project_id": None},
    )
    assert detach_res.status_code == 200
    assert detach_res.json()["project_id"] is None
    assert detach_res.json()["project_name"] is None
    assert detach_res.json()["workspace"]["scope"] == "chat"


def test_red_team_workspace_migration_blocks_symlinks_sensitive_dirs_and_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "source_ws"
    target = tmp_path / "target_ws"
    outside = tmp_path / "outside_secret"
    source.mkdir()
    target.mkdir()
    outside.mkdir()

    # Valid files
    (source / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (source / "src" / "main.py").write_text("print('ok')", encoding="utf-8")

    # Sensitive directories and files that must NEVER be copied
    for forbidden_dir in (".git", ".orin", "node_modules", ".venv", "__pycache__"):
        d = source / forbidden_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "secret_or_junk.txt").write_text("forbidden", encoding="utf-8")

    (source / ".env").write_text("SECRET_KEY=123", encoding="utf-8")
    (source / ".ENV").write_text("SECRET_KEY_UPPER=123", encoding="utf-8")
    (source / ".env.local").write_text("LOCAL_SECRET=456", encoding="utf-8")
    (source / "src" / ".Env.Production").write_text("PROD_SECRET=789", encoding="utf-8")

    # Existing file in target must never be overwritten
    (target / "README.md").write_text("target-readme", encoding="utf-8")
    (source / "README.md").write_text("source-readme-attempt-overwrite", encoding="utf-8")

    # Outside file for symlink tests
    secret_file = outside / "passwd.txt"
    secret_file.write_text("root:x:0:0", encoding="utf-8")
    try:
        (source / "symlink_file.txt").symlink_to(secret_file)
        (source / "symlink_dir").symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows without developer mode privileges may disallow symlink creation; skip symlink assertion only if OS blocks creation
        pass

    copied = migrate_managed_workspace_files(source, target)

    assert copied == ["src/main.py"]
    assert (target / "src" / "main.py").read_text(encoding="utf-8") == "print('ok')"
    assert (target / "README.md").read_text(encoding="utf-8") == "target-readme"
    assert not (target / ".env").exists()
    assert not (target / ".env.local").exists()
    assert not (target / "src" / ".Env.Production").exists()
    assert not (target / ".git").exists()
    assert not (target / ".orin").exists()
    assert not (target / "node_modules").exists()
    assert not (target / ".venv").exists()
    assert not (target / "__pycache__").exists()
    assert not (target / "symlink_file.txt").exists()
    assert not (target / "symlink_dir").exists()


def test_red_team_cross_user_project_handoff_and_conversation_read_blocked(tmp_path: Path) -> None:
    client, chat_store, project_store, _, _ = _build_env(tmp_path)

    owner_conv = chat_store.create(
        user_id="owner",
        message="Owner secret chat",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="owner-c1",
    )
    other_conv = chat_store.create(
        user_id="other",
        message="Other secret chat",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="other-c1",
    )
    owner_project = project_store.create(user_id="owner", name="Owner Project", description=None)
    other_project = project_store.create(user_id="other", name="Other Project", description=None)

    # Other user cannot move owner's conversation
    res1 = client.patch(
        f"/v1/conversations/{owner_conv.conversation_id}/project",
        headers={"Authorization": "Bearer other"},
        json={"project_id": other_project.project_id},
    )
    assert res1.status_code == 404

    # Owner cannot move their conversation into other user's project
    res2 = client.patch(
        f"/v1/conversations/{owner_conv.conversation_id}/project",
        headers={"Authorization": "Bearer owner"},
        json={"project_id": other_project.project_id},
    )
    assert res2.status_code == 404

    # Owner cannot move conversation into an archived project
    archived = project_store.create(user_id="owner", name="Archived", description=None)
    project_store.archive(archived.project_id, "owner")
    res_archived = client.patch(
        f"/v1/conversations/{owner_conv.conversation_id}/project",
        headers={"Authorization": "Bearer owner"},
        json={"project_id": archived.project_id},
    )
    assert res_archived.status_code == 404

    # read_project_conversation returns messages for owner and blocks other user
    history = chat_store.read_project_conversation(owner_conv.conversation_id, "owner", limit=10)
    assert history["conversation_id"] == owner_conv.conversation_id
    assert history["messages"][0]["content"] == "Owner secret chat"

    with pytest.raises(ApplicationNotFoundError):
        chat_store.read_project_conversation(other_conv.conversation_id, "owner")


def test_create_project_with_workspace_path_and_source_conversation_id(tmp_path: Path) -> None:
    client, chat_store, _, local_workspaces, managed_root = _build_env(tmp_path)

    repo_dir = tmp_path / "project_repo"
    repo_dir.mkdir()

    conv = chat_store.create(
        user_id="owner",
        message="Initial architecture discussion",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-source-conv",
    )
    scratch_ws = ConversationWorkspace(managed_root, conv.conversation_id)
    (scratch_ws.root / "architecture.md").write_text("# Architecture", encoding="utf-8")

    created = client.post(
        "/v1/projects",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "proj-from-session"},
        json={
            "name": "Architecture Project",
            "description": "Created from session",
            "workspace_path": str(repo_dir),
            "workspace_acknowledged_risk": False,
            "source_conversation_id": conv.conversation_id,
        },
    )
    assert created.status_code == 201
    proj_body = created.json()
    project_id = proj_body["project_id"]
    assert proj_body["workspace"] == {
        "kind": "local",
        "path": str(repo_dir),
        "folder_name": "project_repo",
        "scope": "project",
        "project_name": "Architecture Project",
    }

    # Conversation is now linked to the created project and its scratch file was migrated to repo_dir
    conv_snapshot = client.get(
        f"/v1/conversations/{conv.conversation_id}",
        headers={"Authorization": "Bearer owner"},
    ).json()
    assert conv_snapshot["project_id"] == project_id
    assert conv_snapshot["project_name"] == "Architecture Project"
    assert (repo_dir / "architecture.md").read_text(encoding="utf-8") == "# Architecture"
    assert local_workspaces.root_for(f"workspace:{project_id}", "owner") == str(repo_dir)


def test_project_workspace_attach_detach_and_sidebar_exposure(tmp_path: Path) -> None:
    client, _, project_store, _, _ = _build_env(tmp_path)
    folder = tmp_path / "shared_workspace"
    folder.mkdir()

    project = project_store.create(user_id="owner", name="Shared Space", description=None)

    # Initially managed
    get_initial = client.get(f"/v1/projects/{project.project_id}", headers={"Authorization": "Bearer owner"})
    assert get_initial.status_code == 200
    assert get_initial.json()["workspace"] == {
        "kind": "managed",
        "path": None,
        "folder_name": None,
        "scope": "project",
        "project_name": "Shared Space",
    }

    # Attach local folder via PUT /v1/projects/{id}/workspace
    put_res = client.put(
        f"/v1/projects/{project.project_id}/workspace",
        headers={"Authorization": "Bearer owner"},
        json={"path": str(folder), "acknowledged_risk": False},
    )
    assert put_res.status_code == 200
    assert put_res.json() == {
        "kind": "local",
        "path": str(folder),
        "folder_name": "shared_workspace",
        "scope": "project",
        "project_name": "Shared Space",
    }

    # Other user cannot attach or detach workspace on owner's project
    assert (
        client.put(
            f"/v1/projects/{project.project_id}/workspace",
            headers={"Authorization": "Bearer other"},
            json={"path": str(folder), "acknowledged_risk": False},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/v1/projects/{project.project_id}/workspace",
            headers={"Authorization": "Bearer other"},
        ).status_code
        == 404
    )

    # Sidebar and project list include updated workspace
    sidebar = client.get("/v1/projects/sidebar", headers={"Authorization": "Bearer owner"}).json()["items"]
    assert sidebar[0]["workspace"]["kind"] == "local"
    assert sidebar[0]["workspace"]["path"] == str(folder)

    listed = client.get("/v1/projects", headers={"Authorization": "Bearer owner"}).json()["items"]
    assert listed[0]["workspace"]["kind"] == "local"
    assert listed[0]["workspace"]["path"] == str(folder)

    # Detach local folder via DELETE /v1/projects/{id}/workspace
    del_res = client.delete(
        f"/v1/projects/{project.project_id}/workspace",
        headers={"Authorization": "Bearer owner"},
    )
    assert del_res.status_code == 200
    assert del_res.json()["kind"] == "managed"
    assert del_res.json()["path"] is None


def test_red_team_existing_project_local_folder_not_overwritten_and_invalid_create_aborts_cleanly(tmp_path: Path) -> None:
    client, chat_store, project_store, local_workspaces, _ = _build_env(tmp_path)

    proj_folder = tmp_path / "proj_canonical"
    conv_folder = tmp_path / "conv_temporary"
    proj_folder.mkdir()
    conv_folder.mkdir()

    project = project_store.create(user_id="owner", name="Canonical Project", description=None)
    local_workspaces.set_root(project.workspace_id, "owner", str(proj_folder))

    conv = chat_store.create(
        user_id="owner",
        message="Session with its own folder",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-no-overwrite",
    )
    local_workspaces.set_root(conv.conversation_id, "owner", str(conv_folder))

    move_res = client.patch(
        f"/v1/conversations/{conv.conversation_id}/project",
        headers={"Authorization": "Bearer owner"},
        json={"project_id": project.project_id},
    )
    assert move_res.status_code == 200
    # Must preserve project's canonical local folder, NOT overwrite with conv_folder
    assert local_workspaces.root_for(project.workspace_id, "owner") == str(proj_folder)
    assert move_res.json()["workspace"]["path"] == str(proj_folder)

    # POST /v1/projects with non-existent workspace_path fails with 422 and creates no project
    bad_path_res = client.post(
        "/v1/projects",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "bad-proj-1"},
        json={"name": "Should Not Exist", "workspace_path": str(tmp_path / "missing_dir")},
    )
    assert bad_path_res.status_code == 422
    assert [p.name for p in project_store.list_active("owner")] == ["Canonical Project"]

    # POST /v1/projects with another user's source_conversation_id fails with 404 and creates no project
    other_conv = chat_store.create(
        user_id="other",
        message="Other user chat",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="other-c2",
    )
    bad_source_res = client.post(
        "/v1/projects",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "bad-proj-2"},
        json={"name": "Hijack Attempt", "source_conversation_id": other_conv.conversation_id},
    )
    assert bad_source_res.status_code == 404
    assert [p.name for p in project_store.list_active("owner")] == ["Canonical Project"]

