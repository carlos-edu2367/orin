"""WS-C10: ``orin pipeline`` against a real local API (temporary database): list, status, resume, cancel and the refusals."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.runtime import OrinClient
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.installation.console import Console
from agentos.launcher.cli import build_parser, main
from agentos.launcher.pipelines import EXIT_NOT_RUNNING, EXIT_REFUSED, command_pipeline
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.model import compute_definition_hash
from agentos.pipelines.store import PipelineStore

DOC = "version: 1\nid: meu\nname: Meu\nbudget: {max_tokens: 1000}\nsteps:\n  - id: g\n    gate:\n      all:\n        - command: pytest -q\n"


class World:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "orin-home"))
        monkeypatch.setenv("ORIN_CLAUDE_HOME", str(tmp_path / "claude-home"))
        self.engine = create_local_engine(sqlite_url(tmp_path / "orin.db"))
        metadata.create_all(self.engine)
        self.chat = ChatApplication(PostgresChatStore(self.engine))
        security = InMemorySecurityService()
        security.add_pat("pat-cli", AuthenticatedPrincipal("user-1", "cred-cli", frozenset({"api"}), credential_kind="cli_token"))
        app = create_app(
            ApiServices(
                security=security, conversation_application=self.chat, agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "r.json"),
                workspace_root=tmp_path / "workspaces",
            )
        )
        self.app = app
        self.store = PipelineStore(self.engine)
        self.tmp = tmp_path
        self.conversation = self.chat.create(user_id="user-1", message="oi", provider="openai", model_id="gpt-4o", idempotency_key="k").conversation_id
        self.out = io.StringIO()
        self.console = Console(self.out, colour=False)

    def new_client(self) -> OrinClient:  # the command closes its client, like the real one
        return OrinClient("http://testserver", "pat-cli", http=TestClient(self.app))

    def run(self, *argv: str, connect=None) -> int:  # noqa: ANN001
        arguments = build_parser().parse_args(["pipeline", *argv])
        return command_pipeline(arguments, None, self.console, connect=connect or self.new_client)  # type: ignore[arg-type]

    def make_run(self, run_id: str, state: str, **kwargs) -> None:  # noqa: ANN003
        values = dict(
            run_id=run_id, user_id="user-1", conversation_id=self.conversation, workspace_root=str(self.tmp), pipeline_id="meu",
            definition_hash=compute_definition_hash(DOC), source="user", inputs={}, definition_snapshot=DOC, state=state,
        )
        values.update(kwargs)
        folder = self.tmp / "orin-home" / "pipelines"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meu.yaml").write_text(DOC, encoding="utf-8")
        self.store.create_run(**values)

    @property
    def text(self) -> str:
        return self.out.getvalue()


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    built = World(tmp_path, monkeypatch)
    yield built
    built.engine.dispose()


def test_the_parser_knows_the_pipeline_commands() -> None:
    arguments = build_parser().parse_args(["pipeline", "resume", "r1", "--rerun-gates", "--budget-override", "--definition", "original"])
    assert (arguments.command, arguments.pipeline_command, arguments.run_id) == ("pipeline", "resume", "r1")
    assert arguments.rerun_gates and arguments.budget_override and arguments.definition == "original"
    assert build_parser().parse_args(["pipeline", "list", "--state", "failed"]).state == "failed"


def test_list_and_status_show_runs_and_the_resume_hint(world: World) -> None:
    assert world.run("list") == 0 and "Nenhuma execução" in world.text
    world.make_run("r1", "interrupted")
    assert world.run("list") == 0 and "r1  meu  interrupted" in world.text
    assert world.run("list", "--state", "failed") == 0
    assert world.run("status", "r1") == 0 and "orin pipeline resume r1" in world.text


def test_resume_creates_the_pipeline_turn(world: World) -> None:
    world.make_run("r1", "interrupted")
    assert world.run("resume", "r1", "--rerun-gates") == 0
    assert "retomada" in world.text
    with world.engine.connect() as connection:
        from sqlalchemy import select

        from agentos.persistence.postgres.schema import conversation_turns

        kinds = [row[0] for row in connection.execute(select(conversation_turns.c.turn_kind)).all()]
    assert "pipeline" in kinds


def test_cancel_a_paused_run(world: World) -> None:
    world.make_run("r1", "interrupted")
    assert world.run("cancel", "r1") == 0
    assert world.store.get_run("r1").state == "cancelled"


def test_refusals_exit_with_a_pt_br_message(world: World) -> None:
    assert world.run("status", "nao-existe") == EXIT_REFUSED and "Execução não encontrada" in world.text
    world.make_run("r1", "budget_exceeded")
    assert world.run("resume", "r1") == EXIT_REFUSED and "--budget-override" in world.text
    world.make_run("r2", "succeeded")
    assert world.run("resume", "r2") == EXIT_REFUSED and "não pode ser retomada" in world.text
    assert world.run("cancel", "r2") == EXIT_REFUSED


def test_a_changed_definition_explains_the_two_ways_out(world: World) -> None:
    world.make_run("r1", "failed", definition_hash="f" * 64)
    (world.tmp / "orin-home" / "pipelines" / "meu.yaml").write_text(DOC.replace("Meu", "Novo"), encoding="utf-8")
    assert world.run("resume", "r1") == EXIT_REFUSED
    assert "--definition original" in world.text and "new_run" in world.text
    assert world.run("resume", "r1", "--definition", "original") == 0


def test_without_a_running_orin_it_says_so_and_exits_with_2(world: World) -> None:
    def down():  # noqa: ANN202
        raise CliRuntimeError("Orin is not running")

    assert world.run("list", connect=down) == EXIT_NOT_RUNNING
    assert "orin start" in world.text


def test_main_dispatches_the_pipeline_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr("agentos.launcher.pipelines.command_pipeline", lambda arguments, paths, console: seen.append(arguments.pipeline_command) or 0)
    assert main(["pipeline", "list"]) == 0
    assert seen == ["list"]
