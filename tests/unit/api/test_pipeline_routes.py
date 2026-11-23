"""WS-C10: HTTP routes of pipelines, runs and ``/goal`` (authorization, ownership, error shapes, the 409 of a changed definition)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url

AUTH = {"Authorization": "Bearer pat-u1"}
OTHER = {"Authorization": "Bearer pat-u2"}
CLI = {"Authorization": "Bearer pat-cli"}

DOC = (
    "version: 1\nid: meu\nname: Meu\nbudget: {max_tokens: 100000}\ninputs:\n  arquivo: {type: string}\n"
    "steps:\n  - id: verificar\n    gate:\n      all:\n        - command: pytest -q ${{ inputs.arquivo }}\n"
)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "orin-home"))
    monkeypatch.setenv("ORIN_CLAUDE_HOME", str(tmp_path / "claude-home"))
    engine = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    chat = ChatApplication(store)
    security = InMemorySecurityService()
    security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    security.add_pat("pat-u2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))
    security.add_pat("pat-cli", AuthenticatedPrincipal("user-1", "cred-cli", frozenset({"api"}), credential_kind="cli_token"))
    app = create_app(
        ApiServices(
            security=security, conversation_application=chat, agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=tmp_path / "workspaces",
        )
    )

    class World:
        pass

    w = World()
    w.client, w.chat, w.engine, w.store, w.app, w.tmp, w.services_security = TestClient(app), chat, engine, store, app, tmp_path, security
    w.conversation = lambda user="user-1", key="k1": chat.create(  # noqa: E731
        user_id=user, message="oi", provider="openai", model_id="gpt-4o", idempotency_key=key
    ).conversation_id
    yield w
    engine.dispose()


def error_code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def test_every_route_needs_authentication(world) -> None:  # noqa: ANN001
    for method, path in (
        ("get", "/v1/pipelines"), ("post", "/v1/pipeline-runs"), ("get", "/v1/pipeline-runs"), ("get", "/v1/pipeline-runs/x"),
        ("post", "/v1/pipeline-runs/x/pause"), ("get", "/v1/conversations/c/goal"), ("post", "/v1/pipelines/x/approve"),
    ):
        assert getattr(world.client, method)(path).status_code in {401, 403, 422}, path


def test_the_shared_server_has_no_pipeline_files(world) -> None:  # noqa: ANN001
    security = InMemorySecurityService()
    security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    server = TestClient(
        create_app(
            ApiServices(
                security=security, conversation_application=world.chat, capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER),
                workspace_root=world.tmp / "workspaces",
            )
        )
    )
    assert server.get("/v1/pipelines", headers=AUTH).status_code == 404
    assert server.post("/v1/pipeline-runs", json={"pipeline_id": "pesquisa", "conversation_id": "c"}, headers=AUTH).status_code == 404


def test_list_has_the_four_builtin_pipelines_with_the_fields_the_screen_needs(world) -> None:  # noqa: ANN001
    body = world.client.get("/v1/pipelines", headers=AUTH).json()
    assert body["enabled"] is True and body["project_skipped"] == 0 and body["shadowed"] == []
    ids = {item["id"] for item in body["pipelines"]}
    assert {"implementar-verificar", "revisao-multimodelo", "corrigir-bug", "pesquisa"} <= ids
    item = next(i for i in body["pipelines"] if i["id"] == "pesquisa")
    assert item["source"] == "builtin" and item["valid"] is True and item["approved"] is True and item["agent_approved"] is True
    assert {"errors", "warnings", "inputs", "definition_hash"} <= set(item)


def test_detail_shows_the_commands_to_approve(world) -> None:  # noqa: ANN001
    cid = world.conversation()
    detail = world.client.get("/v1/pipelines/implementar-verificar", params={"conversation_id": cid}, headers=AUTH).json()
    assert detail["review"]["commands"] and "version: 1" in detail["text"]
    assert "context" in detail and detail["definition_hash"]
    assert error_code(world.client.get("/v1/pipelines/nao-existe", headers=AUTH)) == "PIPELINE_NOT_FOUND"


def test_validate_reports_errors_with_line_and_code(world) -> None:  # noqa: ANN001
    ok = world.client.post("/v1/pipelines/validate", json={"text": DOC}, headers=AUTH).json()
    assert ok["valid"] is True and ok["id"] == "meu"
    bad = world.client.post("/v1/pipelines/validate", json={"text": DOC.replace("steps:", "passos:")}, headers=AUTH).json()
    assert bad["valid"] is False and {"line", "column", "code", "message"} <= set(bad["errors"][0])
    assert world.client.post("/v1/pipelines/validate", json={"text": DOC, "extra": 1}, headers=AUTH).status_code == 422


def test_save_update_with_if_match_and_delete(world) -> None:  # noqa: ANN001
    created = world.client.put("/v1/pipelines/meu", json={"text": DOC, "scope": "user"}, headers=AUTH)
    assert created.status_code == 200 and created.json()["id"] == "meu"
    again = world.client.put("/v1/pipelines/meu", json={"text": DOC, "scope": "user"}, headers=AUTH)
    assert again.status_code == 409 and error_code(again) == "PIPELINE_CONFLICT"
    current = created.json()["definition_hash"]
    changed = world.client.put("/v1/pipelines/meu", json={"text": DOC.replace("Meu", "Outro"), "scope": "user"}, headers={**AUTH, "If-Match": current})
    assert changed.status_code == 200 and changed.json()["name"] == "Outro"
    assert world.client.put("/v1/pipelines/x..y", json={"text": DOC}, headers=AUTH).status_code in {404, 422}
    assert world.client.delete("/v1/pipelines/meu", params={"scope": "user"}, headers=AUTH).status_code == 200
    assert world.client.delete("/v1/pipelines/meu", params={"scope": "user"}, headers=AUTH).status_code == 404


def test_the_cli_token_runs_but_never_edits_or_approves(world) -> None:  # noqa: ANN001
    assert world.client.put("/v1/pipelines/meu", json={"text": DOC}, headers=CLI).status_code == 403
    assert world.client.post("/v1/pipelines/meu/approve", json={"definition_hash": "0" * 64}, headers=CLI).status_code == 403
    assert world.client.get("/v1/pipelines", headers=CLI).status_code == 200


def test_approve_binds_to_the_reviewed_hash_and_revoke_undoes_it(world) -> None:  # noqa: ANN001
    saved = world.client.put("/v1/pipelines/meu", json={"text": DOC}, headers=AUTH).json()
    stale = world.client.post("/v1/pipelines/meu/approve", json={"definition_hash": "0" * 64}, headers=AUTH)
    assert stale.status_code == 409 and error_code(stale) == "PIPELINE_CHANGED"
    ok = world.client.post("/v1/pipelines/meu/approve", json={"definition_hash": saved["definition_hash"]}, headers=AUTH)
    assert ok.status_code == 200 and ok.json()["approved"] is True
    listed = {i["id"]: i for i in world.client.get("/v1/pipelines", headers=AUTH).json()["pipelines"]}
    assert listed["meu"]["agent_approved"] is True
    revoked = world.client.post("/v1/pipelines/meu/revoke", json={"definition_hash": saved["definition_hash"]}, headers=AUTH)
    assert revoked.json()["approved"] is False
    assert world.client.post("/v1/pipelines/pesquisa/approve", json={"definition_hash": "0" * 64}, headers=AUTH).status_code == 422


def test_estimate_lists_who_would_spend_without_estimating_tokens(world) -> None:  # noqa: ANN001
    cid = world.conversation()
    body = world.client.post("/v1/pipelines/pesquisa/estimate", json={"conversation_id": cid, "inputs": {"pergunta": "x"}}, headers=AUTH).json()
    assert body["steps"] and body["budget"]["max_tokens"] == 500000 and "quota" in body
    assert not any(key.startswith("estimated") for key in body)


def test_start_list_detail_pause_cancel_and_ownership(world) -> None:  # noqa: ANN001
    cid = world.conversation()
    started = world.client.post("/v1/pipeline-runs", json={"pipeline_id": "pesquisa", "conversation_id": cid, "inputs": {"pergunta": "o que é X?"}}, headers=AUTH)
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    assert started.json()["budget"]["max_tokens"] == 500000
    listed = world.client.get("/v1/pipeline-runs", params={"conversation_id": cid}, headers=AUTH).json()["runs"]
    assert [r["run_id"] for r in listed] == [run_id]
    detail = world.client.get(f"/v1/pipeline-runs/{run_id}", headers=AUTH).json()
    assert detail["state"] == "running" and detail["summary"]["run_id"] == run_id and detail["steps"] == []
    assert world.client.get(f"/v1/pipeline-runs/{run_id}", headers=OTHER).status_code == 404
    assert world.client.post(f"/v1/pipeline-runs/{run_id}/pause", headers=OTHER).status_code == 404
    assert world.client.get(f"/v1/pipeline-runs/{run_id}/steps/dividir/output", headers=AUTH).status_code == 404
    assert world.client.post(f"/v1/pipeline-runs/{run_id}/pause", headers=AUTH).status_code == 202
    assert world.client.post(f"/v1/pipeline-runs/{run_id}/cancel", headers=AUTH).status_code == 202
    assert world.client.post(f"/v1/pipeline-runs/{run_id}/resume", headers=AUTH).status_code == 409  # still "running" in the journal


def test_start_refusals_have_a_stable_code(world) -> None:  # noqa: ANN001
    cid = world.conversation()
    missing = world.client.post("/v1/pipeline-runs", json={"pipeline_id": "pesquisa", "conversation_id": cid}, headers=AUTH)
    assert missing.status_code == 422 and error_code(missing) == "PIPELINE_INPUT_REQUIRED"
    unknown = world.client.post("/v1/pipeline-runs", json={"pipeline_id": "fantasma", "conversation_id": cid}, headers=AUTH)
    assert unknown.status_code == 404 and error_code(unknown) == "PIPELINE_NOT_FOUND"
    foreign = world.client.post("/v1/pipeline-runs", json={"pipeline_id": "pesquisa", "conversation_id": cid, "inputs": {"pergunta": "x"}}, headers=OTHER)
    assert foreign.status_code == 404


def test_a_resume_after_a_change_answers_409_with_the_options(world) -> None:  # noqa: ANN001
    from agentos.pipelines.model import compute_definition_hash
    from agentos.pipelines.store import PipelineStore

    cid = world.conversation()
    world.client.put("/v1/pipelines/meu", json={"text": DOC}, headers=AUTH)
    store = PipelineStore(world.engine)
    store.create_run(
        run_id="r-old", user_id="user-1", conversation_id=cid, workspace_root=str(world.tmp), pipeline_id="meu",
        definition_hash=compute_definition_hash(DOC), source="user", inputs={}, definition_snapshot=DOC, state="interrupted",
    )
    world.client.put(
        "/v1/pipelines/meu", json={"text": DOC.replace("Meu", "Novo"), "scope": "user"},
        headers={**AUTH, "If-Match": compute_definition_hash(DOC)},
    )
    refused = world.client.post("/v1/pipeline-runs/r-old/resume", headers=AUTH)
    assert refused.status_code == 409 and error_code(refused) == "PIPELINE_DEFINITION_CHANGED"
    assert [o["id"] for o in refused.json()["error"]["details"]["options"]] == ["original", "new_run"]
    accepted = world.client.post("/v1/pipeline-runs/r-old/resume", json={"definition": "original", "rerun_gates": True}, headers=AUTH)
    assert accepted.status_code == 202 and accepted.json()["resumed"] is True


def test_goal_status_start_clear_and_the_conflict(world) -> None:  # noqa: ANN001
    cid = world.conversation()
    assert world.client.get(f"/v1/conversations/{cid}/goal", headers=AUTH).json() == {"goal": None}
    started = world.client.post(f"/v1/conversations/{cid}/goal", json={"objective": "terminar a tela", "max_tokens": 50000}, headers=AUTH)
    assert started.status_code == 202 and started.json()["max_tokens"] == 50000
    again = world.client.post(f"/v1/conversations/{cid}/goal", json={"objective": "outro"}, headers=AUTH)
    assert again.status_code == 409 and error_code(again) == "GOAL_ACTIVE"
    status = world.client.get(f"/v1/conversations/{cid}/goal", headers=AUTH).json()["goal"]
    assert status["state"] == "running" and status["objective"] == "terminar a tela"
    assert error_code(world.client.post(f"/v1/conversations/{cid}/goal/resume", json={}, headers=AUTH)) == "GOAL_ACTIVE"
    cleared = world.client.post(f"/v1/conversations/{cid}/goal/clear", json={}, headers=AUTH)
    assert cleared.status_code == 200 and cleared.json()["turn_cancelled"] is False
    assert error_code(world.client.post(f"/v1/conversations/{cid}/goal/clear", json={}, headers=AUTH)) == "GOAL_NOT_FOUND"
    low = world.client.post(f"/v1/conversations/{cid}/goal", json={"objective": "x", "max_tokens": 5}, headers=AUTH)
    assert low.status_code == 422 and error_code(low) == "GOAL_MAX_TOKENS_INVALID"
    assert world.client.get(f"/v1/conversations/{cid}/goal", headers=OTHER).status_code in {403, 404}
