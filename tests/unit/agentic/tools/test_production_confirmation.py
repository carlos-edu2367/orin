"""Code mode's production guard: decided by what a command does, approvable, and explicit about what it blocked."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.shell.confirmation import dialogue_approves, dialogue_pairs, parse_answer_blocks
from agentos.agentic.tools.shell.production import production_actions, sql_is_read_only, strip_heredocs
from agentos.agentic.tools.shell.publish import publish_actions
from agentos.agentic.workspace import ConversationWorkspace

ANSWER = "Respostas às perguntas do agente:\n\nPosso fazer o deploy das duas edge functions em produção?\nSeleção: Pode aplicar tudo"


def kinds(command: str, tool: str = "run_command", cwd: Path | None = None) -> set[str]:
    return {action.kind for action in production_actions(command, tool, cwd=cwd)}


# -- classification by effect, never by a word ---------------------------------------------------------------


@pytest.mark.parametrize("command", [
    'cd x; git status; git log --oneline -5; grep -n "push\\|deploy" .github/workflows/deploy.yml',
    'git add -A && git commit -m "feat: deploy em producao do clickup"',
    "git commit -F - <<'EOF'\nnpm publish and vercel --prod\nEOF",
    'grep -rn "vercel deploy" $(git ls-files)',
    "cat deploy.sh",
    "npm run build",
    "terraform plan",
    "psql -h localhost -c 'delete from t'",
    "vercel dev",
    "kubectl apply --dry-run=client -f k.yaml",
])
def test_reading_or_mentioning_production_words_is_not_a_production_action(command: str) -> None:
    assert kinds(command) == set()


@pytest.mark.parametrize(("command", "kind"), [
    ("npx supabase functions deploy sync --project-ref abc", "deploy"),
    ("supabase db push", "db_write"),
    ("vercel --prod", "deploy"),
    ("netlify deploy --prod", "deploy"),
    ("firebase deploy --only hosting", "deploy"),
    ("npm publish", "publish"),
    ("pnpm dlx wrangler deploy", "deploy"),
    ("docker push ghcr.io/x/y:1", "publish"),
    ("terraform apply -auto-approve", "infra"),
    ("kubectl apply -f k.yaml", "infra"),
    ("gh release create v1.0.0", "release"),
    ("npm run deploy:prod", "deploy"),
    ("node scripts/deploy.js", "deploy"),
    ('bash -c "npm publish"', "publish"),
    ("echo $(vercel --prod)", "deploy"),
    ("aws s3 sync dist s3://site", "deploy"),
    ('psql "postgresql://u:p@db.abc.supabase.co:5432/postgres" -c "delete from t"', "db_write"),
    ("curl -s https://api.supabase.com/v1/projects/abc/api-keys -H 'Authorization: Bearer x'", "credentials"),
])
def test_production_actions_are_recognised_by_effect(command: str, kind: str) -> None:
    assert kind in kinds(command)


def test_sql_sent_to_the_production_database_is_a_write_unless_it_only_reads() -> None:
    url = "https://api.supabase.com/v1/projects/abc/database/query"
    write = f"curl -s -X POST {url} -H 'Authorization: Bearer t' -d '{{\"query\":\"alter table x add column y int\"}}'"
    read = f"curl -s -X POST {url} -d '{{\"query\":\"select count(*) from x\"}}'"
    assert kinds(write) == {"db_write"}
    assert kinds(read) == set()


def test_sql_in_a_file_is_read_to_decide(tmp_path: Path) -> None:
    (tmp_path / "q.sql").write_text("create table t (id int);", encoding="utf-8")
    (tmp_path / "r.sql").write_text("select 1;", encoding="utf-8")
    url = "https://api.supabase.com/v1/projects/abc/database/query"
    assert kinds(f"curl -X POST {url} --data-binary @q.sql", cwd=tmp_path) == {"db_write"}
    assert kinds(f"curl -X POST {url} --data-binary @r.sql", cwd=tmp_path) == set()
    assert kinds(f"curl -X POST {url} --data-binary @missing.sql", cwd=tmp_path) == {"db_write"}


@pytest.mark.parametrize(("sql", "read_only"), [
    ("select * from t", True),
    ("with a as (select 1) select * from a", True),
    ("explain select 1; show search_path", True),
    ("select 'drop table x' as s", True),
    ("insert into t values (1)", False),
    ("select * into t2 from t", False),
    ("with d as (delete from t returning *) select * from d", False),
    ("alter table t add c int", False),
    ("", False),
])
def test_sql_is_read_only(sql: str, read_only: bool) -> None:
    assert sql_is_read_only(sql) is read_only


def test_heredoc_bodies_are_data() -> None:
    assert strip_heredocs("cat <<'EOF'\nnpm publish\nEOF\necho ok") == "cat <<'EOF'\n\necho ok"


def test_push_is_read_from_commands_not_from_messages() -> None:
    assert publish_actions('git commit -m "push and deploy"', "run_command") == set()
    assert publish_actions("git commit -F - <<'EOF'\ngit push origin main\nEOF", "run_command") == set()
    assert publish_actions('bash -c "git push origin main"', "run_command") == {"push"}
    assert publish_actions("gh pr view 12", "run_command") == set()
    assert publish_actions("gh pr create --fill", "run_command") == {"pr"}


# -- approval from the conversation --------------------------------------------------------------------------


def test_the_answer_to_an_ask_user_question_about_the_deploy_approves_it() -> None:
    pairs = parse_answer_blocks(ANSWER)
    assert pairs == [("Posso fazer o deploy das duas edge functions em produção?", "Pode aplicar tudo")]
    assert dialogue_approves(pairs, {"deploy"})


@pytest.mark.parametrize(("question", "answer", "approved"), [
    ("Faço o deploy agora?", "sim", True),
    ("Faço o deploy agora?", "não, ainda não", False),
    # Any negation fails closed: the approval card still asks.
    ("Faço o deploy agora?", "pode, mas não faça push", False),
    ("", "faça o deploy da função sync", True),
    ("", "pode subir", True),
    ("", "o deploy deu erro, faça uma correção", False),
    ("", "não faça o deploy", False),
    ("Quer que eu rode os testes?", "sim", False),
    ("", "investigue o deploy", False),
])
def test_dialogue_approval(question: str, answer: str, approved: bool) -> None:
    assert dialogue_approves([(question, answer)], {"deploy"}) is approved


def test_dialogue_pairs_take_the_question_the_agent_asked_last() -> None:
    history = [
        {"role": "user", "content": "corrija o bug"},
        {"role": "assistant", "content": "Corrigi o bug e os testes passam. Quer que eu faça o deploy em produção?"},
        {"role": "user", "content": "pode"},
    ]
    pairs = dialogue_pairs(history)
    assert pairs == [("Quer que eu faça o deploy em produção?", "pode")]
    assert dialogue_approves(pairs, {"deploy"})


# -- the guard in the toolset --------------------------------------------------------------------------------


@pytest.fixture()
def no_process(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    started: list[Any] = []

    def recorded(args, *positional, **kwargs):
        started.append(args)
        raise RuntimeError("not started in tests")

    monkeypatch.setattr(subprocess, "Popen", recorded)
    return started


def make(tmp_path: Path, **kwargs: Any) -> AgentToolset:
    return AgentToolset(ConversationWorkspace.at_root(tmp_path), code_mode_active=True, interactive_permissions=False, **kwargs)


def test_a_blocked_command_says_nothing_ran_what_was_blocked_and_how_to_approve(tmp_path: Path, no_process: list[Any]) -> None:
    outcome = make(tmp_path).invoke("run_command", {"command": "git status && supabase functions deploy sync"})
    assert outcome.error_code == "CODE_DEPLOY_CONFIRMATION_REQUIRED"
    assert "Nada foi executado" in outcome.content
    assert "supabase functions deploy" in outcome.content
    assert "AskUserQuestion" in outcome.content
    assert no_process == []


def test_read_only_commands_with_production_words_run(tmp_path: Path, no_process: list[Any]) -> None:
    outcome = make(tmp_path).invoke("run_command", {"command": 'git status; grep -n "push\\|deploy" deploy.yml'})
    assert outcome.error_code not in {"CODE_DEPLOY_CONFIRMATION_REQUIRED", "CODE_PUSH_CONFIRMATION_REQUIRED"}
    assert no_process, "the command should have been started"


def test_an_in_turn_answer_approves_the_deploy(tmp_path: Path, no_process: list[Any]) -> None:
    toolset = make(tmp_path)
    toolset.note_user_answer('{"questions": [{"question": "Posso fazer o deploy?"}]}', ANSWER)
    outcome = toolset.invoke("run_command", {"command": "supabase functions deploy sync"})
    assert outcome.error_code != "CODE_DEPLOY_CONFIRMATION_REQUIRED", outcome.content
    assert no_process


def test_the_turns_dialogue_approves_the_deploy(tmp_path: Path, no_process: list[Any]) -> None:
    toolset = make(tmp_path)
    toolset._approval_dialogue = lambda: dialogue_pairs([{"role": "user", "content": ANSWER}])
    outcome = toolset.invoke("run_command", {"command": "supabase functions deploy sync"})
    assert outcome.error_code != "CODE_DEPLOY_CONFIRMATION_REQUIRED", outcome.content


def test_full_autonomy_permits_push_but_not_a_deploy(tmp_path: Path, no_process: list[Any]) -> None:
    toolset = make(tmp_path, permission_mode="bypassPermissions")
    assert toolset.invoke("run_command", {"command": "git push origin main"}).error_code != "CODE_PUSH_CONFIRMATION_REQUIRED"
    assert toolset.invoke("run_command", {"command": "vercel --prod"}).error_code == "CODE_DEPLOY_CONFIRMATION_REQUIRED"


@pytest.mark.parametrize(("decision", "runs"), [("allow_once", True), ("deny", False)])
def test_the_approval_card_decides(tmp_path: Path, no_process: list[Any], decision: str, runs: bool) -> None:
    from agentos.code_mode.permissions import ToolPermissionBroker

    class _Broker(ToolPermissionBroker):
        requests: list[dict[str, Any]]

        def request_and_wait(self, **kwargs: Any) -> dict[str, Any]:
            self.requests.append(kwargs)
            return {"decision": decision, "request_id": "perm_1", "status": "resolved"}

    toolset = make(tmp_path)
    broker = _Broker(ConversationWorkspace.at_root(tmp_path))
    broker.requests = []
    toolset._permission_broker = broker  # type: ignore[assignment]
    toolset._interactive_permissions = True
    outcome = toolset.invoke("run_command", {"command": "npm publish"})
    assert broker.requests and broker.requests[0]["sensitive"]["kinds"] == ["publish"]
    assert (outcome.error_code != "CODE_DEPLOY_CONFIRMATION_REQUIRED") is runs
    if not runs:
        assert "Nada foi executado" in outcome.content
    # Approved once, the same command does not ask again in this turn.
    if runs:
        toolset.invoke("run_command", {"command": "npm publish"})
        assert len(broker.requests) == 1


def test_refusals_that_wait_on_a_person_never_end_the_turn_as_repeated_failures() -> None:
    from agentos.agentic.native_turn import refusal_message
    from agentos.agentic.runtime import POLICY_REFUSAL_CODES

    assert {"CODE_DEPLOY_CONFIRMATION_REQUIRED", "CODE_PUSH_CONFIRMATION_REQUIRED", "CODE_PERMISSION_DENIED"} <= POLICY_REFUSAL_CODES
    message = refusal_message("REPEATED_TOOL_FAILURE")
    assert "failed 3 times" in message and "next message" in message


def test_a_sensitive_permission_request_never_becomes_a_rule(tmp_path: Path) -> None:
    from agentos.code_mode.permissions import ToolPermissionBroker

    broker = ToolPermissionBroker(ConversationWorkspace.at_root(tmp_path))
    record = broker.create_permission_request(
        tool_name="run_command", arguments={"command": "vercel --prod"},
        sensitive={"kinds": ["deploy"], "labels": ["Deploy em produção (vercel)"]},
    )
    assert record["suggested_rule"] is None
    assert record["sensitive"]["labels"] == ["Deploy em produção (vercel)"]
    resolved = broker.resolve_request(record["request_id"], decision="allow_always")
    assert resolved["persisted_rule"] is None
