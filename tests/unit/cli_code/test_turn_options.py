"""W6 Task 6: the options `orin code` attaches to a turn — validated, accepted only with the CLI token, stored."""
from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService
from agentos.code_mode.cli_options import CliOptionsError, CliTurnOptions, load_cli_options
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def test_options_validate_and_round_trip() -> None:
    options = CliTurnOptions(settings={"permissions": {"deny": ["Bash"]}, "maxTurns": 3}, tools=["Read", " Edit "],
                             setting_sources=["user"], append_system_prompt="Be brief.", permission_prompts="none")
    loaded = load_cli_options(options.dumps())
    assert loaded == CliTurnOptions.model_validate(json.loads(options.dumps()))
    assert loaded.tools == ["Read", "Edit"] and loaded.permission_prompts == "none" and loaded.headless is True
    assert load_cli_options(None) is None and load_cli_options("") is None


@pytest.mark.parametrize("bad", [
    {"unknown": 1},
    {"settings": {"x": "y" * 70000}},
    {"settings": {"x": math.nan}},
    {"tools": ["Read", "  "]},
    {"setting_sources": ["cloud"]},
    {"permission_prompts": "sometimes"},
])
def test_invalid_options_are_refused(bad: dict) -> None:
    with pytest.raises(ValueError):
        CliTurnOptions.model_validate(bad)


def test_a_stored_value_that_no_longer_validates_stops_the_turn() -> None:
    with pytest.raises(CliOptionsError):
        load_cli_options('{"tools": 5}')
    with pytest.raises(CliOptionsError):
        load_cli_options("not json")


class _Application:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def allocate_conversation_id(self) -> str:
        return "chat_test"

    def create(self, context, **kwargs):
        self.calls.append(("create", kwargs))
        return {"conversation_id": "chat_test", "title": "t", "turn_id": "turn_1", "message_id": "m", "state": "queued"}

    def send(self, user_id, conversation_id, message, idempotency_key, **kwargs):
        self.calls.append(("send", kwargs))
        return {"conversation_id": conversation_id, "turn_id": "turn_2", "state": "queued"}


def _client(application: _Application) -> TestClient:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=application))
    return TestClient(app, client=("127.0.0.1", 40000))


CREATE = {"message": "hi", "selection": {"provider": "ollama", "model_id": "m"}, "code_mode": "code",
          "cli": {"settings": {"maxTurns": 2}, "permission_prompts": "none"}}


def test_the_cli_token_may_attach_options_on_create_and_send() -> None:
    application = _Application()
    client = _client(application)
    headers = {"Authorization": "Bearer tok", "Idempotency-Key": "k1"}
    assert client.post("/v1/conversations", json=CREATE, headers=headers).status_code == 201
    sent = client.post("/v1/conversations/chat_test/messages", json={"message": "more", "cli": {"tools": ["Read"]}},
                       headers={**headers, "Idempotency-Key": "k2"})
    assert sent.status_code == 201
    created_options = json.loads(application.calls[0][1]["cli_options"])
    assert created_options["settings"] == {"maxTurns": 2} and created_options["permission_prompts"] == "none"
    assert json.loads(application.calls[1][1]["cli_options"])["tools"] == ["Read"]


def test_without_the_token_cli_options_are_refused() -> None:
    application = _Application()
    response = _client(application).post("/v1/conversations", json=CREATE, headers={"Idempotency-Key": "k1"})
    assert response.status_code == 403
    assert application.calls == []


def test_a_turn_without_options_stores_none() -> None:
    application = _Application()
    body = {key: value for key, value in CREATE.items() if key != "cli"}
    assert _client(application).post("/v1/conversations", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "k1"}).status_code == 201
    assert "cli_options" not in application.calls[0][1]


def test_the_store_keeps_the_options_with_the_turn() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    stored = CliTurnOptions(tools=["Read"]).dumps()
    receipt = store.create(user_id="u", message="hi", provider="ollama", model_id="m", idempotency_key="k", code_mode="code", cli_options=stored)
    turn = store.claim(receipt.turn_id)
    assert turn is not None and load_cli_options(turn["cli_options"]).tools == ["Read"]
    plain = store.create(user_id="u", message="again", provider="ollama", model_id="m", idempotency_key="k2", conversation_id=receipt.conversation_id)
    store.finish(turn)
    assert store.claim(plain.turn_id)["cli_options"] is None


def test_a_database_that_skipped_the_migration_gets_the_column() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE conversation_turns DROP COLUMN cli_options"))
    PostgresChatStore(engine)
    assert "cli_options" in {column["name"] for column in inspect(engine).get_columns("conversation_turns")}


class _Roots:
    def __init__(self) -> None:
        self.roots: dict[tuple[str, str], str] = {}

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        self.roots[(workspace_id, user_id)] = root_path

    def clear_root(self, workspace_id: str, user_id: str) -> bool:
        return self.roots.pop((workspace_id, user_id), None) is not None

    def root_for(self, workspace_id: str, user_id: str):
        return self.roots.get((workspace_id, user_id))


def test_trust_is_recorded_before_the_turn_is_queued(tmp_path) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    folder = tmp_path / "project"
    folder.mkdir()
    seen: list[bool] = []

    class _Recording(_Application):
        def create(self, context, **kwargs):
            seen.append(WorkspaceTrustStore().is_trusted("local-user", folder.resolve()))
            return super().create(context, **kwargs)

    application = _Recording()
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=application,
                                 local_workspaces=_Roots()))
    client = TestClient(app, client=("127.0.0.1", 40000))
    body = {**CREATE, "workspace_path": str(folder), "cli": {"trust_workspace": True}}
    response = client.post("/v1/conversations", json=body, headers={"Authorization": "Bearer tok", "Idempotency-Key": "t1"})
    assert response.status_code == 201, response.text
    assert seen == [True]


def test_without_the_token_nothing_is_trusted(tmp_path) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    folder = tmp_path / "project"
    folder.mkdir()
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Application(),
                                 local_workspaces=_Roots()))
    body = {**CREATE, "workspace_path": str(folder), "cli": {"trust_workspace": True}}
    response = TestClient(app, client=("127.0.0.1", 40000)).post("/v1/conversations", json=body, headers={"Idempotency-Key": "t2"})
    assert response.status_code == 403
    assert WorkspaceTrustStore().is_trusted("local-user", folder.resolve()) is False


class _Existing(_Application):
    def get(self, conversation_id, user_id):
        return {"conversation_id": conversation_id, "project_id": None}


@pytest.mark.parametrize("bound", [True, False])
def test_trust_on_send_only_covers_a_bound_local_folder(tmp_path, bound: bool) -> None:
    from agentos.permissions.trust import WorkspaceTrustStore

    folder = tmp_path / "project"
    folder.mkdir()
    roots = _Roots()
    if bound:
        roots.set_root("chat_test", "local-user", str(folder))
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Existing(),
                                 local_workspaces=roots))
    response = TestClient(app, client=("127.0.0.1", 40000)).post(
        "/v1/conversations/chat_test/messages", json={"message": "again", "cli": {"trust_workspace": True}},
        headers={"Authorization": "Bearer tok", "Idempotency-Key": "s1"},
    )
    assert response.status_code == 201, response.text
    assert WorkspaceTrustStore().is_trusted("local-user", folder.resolve()) is bound
    if not bound:  # a managed workspace has nothing to trust: no folder at all is recorded
        assert not (WorkspaceTrustStore()._read().get("users") or {}).get("local-user")
