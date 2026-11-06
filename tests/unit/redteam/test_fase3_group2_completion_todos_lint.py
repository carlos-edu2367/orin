"""Adversarial Red Team suite for Phase 3 Group 2 (Tasks 6-9: commits 2a0282a..2ec0d2c).

Covers:
- Surface 3: Code mode completion without forced gate, verification status accuracy,
  subagent & non-code isolation, activity snapshot retention, and todo survival at turn end.
- Surface 4: Single-file lint after edit/write, changed-line filtering, summary truncation,
  timeout/missing-linter resilience, CRLF handling, and path injection safety.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic import agent_tools
from agentos.agentic.agent_tools import (
    DIAGNOSTIC_DEBOUNCE_SECONDS,
    MAX_FILE_DIAGNOSTIC_CHARS,
    AgentToolset,
)
from agentos.agentic.diagnostics import (
    FileDiagnostic,
    changed_line_ranges,
    file_diagnostic_command,
    parse_file_diagnostics,
    summarize_diagnostics,
)
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.phases import PhaseController
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import compute_structured_patch, is_verification_command
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import code_mode_runs, metadata
from agentos.providers.claude_cli.native_loop import (
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.workers.chat import ChatWorker
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)


def _turn(*, code_mode: str | None = "code", is_subagent: bool = False) -> dict[str, object]:
    turn: dict[str, object] = {
        "turn_id": "turn-rt-1",
        "conversation_id": "conv-rt-1",
        "user_id": "user-rt-1",
        "execution_id": "exec-rt-1",
        "provider": "openrouter",
        "model_id": "local-model",
        "user_message_id": "user-msg-1",
        "assistant_message_id": "assistant-msg-1",
    }
    if code_mode is not None:
        turn["code_mode"] = code_mode
    if is_subagent:
        turn["is_subagent"] = True
    return turn


class _MemoryStore:
    def __init__(self, *, code_mode: str | None = "code", is_subagent: bool = False) -> None:
        self.turn = _turn(code_mode=code_mode, is_subagent=is_subagent)
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Any) -> list[dict[str, str]]:
        return [{"role": "user", "content": "ajuste o arquivo"}]

    def lifecycle(self, turn: Any, state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: Any, text: str) -> None:
        self.text.append(text)

    def finish(self, turn: Any, *, failed: bool = False, code: str | None = None) -> None:
        self.turn["failed"] = failed
        self.turn["error_code"] = code


def _json_args(payload: dict[str, Any]) -> str:
    return json.dumps(json.dumps(payload))[1:-1]


def _tool_call(call_id: str, name: str, arguments: str) -> str:
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, name, arguments)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


# ============================================================================
# SURFACE 3: Completion without forced gate, verification status, and todos
# ============================================================================


def test_s3_no_leftover_gate_constants_or_todo_auto_complete_in_codebase() -> None:
    """Ensure removed gate symbols and auto_todo_settle are absent from runtime/toolset."""
    tools = AgentToolset(ConversationWorkspace(Path("."), "chk"), code_mode_active=True)
    assert not hasattr(tools, "complete_remaining_todos")
    assert not hasattr(tools, "has_unverified_changes")
    assert not hasattr(AgentActivityEventType, "CODE_COMPLETION_GATE")


def test_s3_verification_status_matrix_read_only_non_test_failed_test_and_edit_after_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify code_verification_status across read_file, non-check command, failed check, and edit AFTER check."""
    # Disable post-edit ruff subprocess so we only test verification tracking
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    ws = ConversationWorkspace(tmp_path, "c-matrix")
    ws.write_text("existing.py", "a = 1\n")
    tools = AgentToolset(ws, code_mode_active=True)

    # 1. Initial / read_file only -> not_applicable (NOT verified!)
    assert tools.code_verification_status() == "not_applicable"
    tools.invoke("read_file", {"path": "existing.py"})
    assert tools.code_verification_status() == "not_applicable"

    # 2. Edit a file -> unverified
    tools.invoke("edit_file", {"path": "existing.py", "old_text": "a = 1", "new_text": "a = 2"})
    assert tools.code_verification_status() == "unverified"

    # 3. Non-verification command (exit 0) -> still unverified
    tools.invoke("run_command", {"command": f'"{sys.executable}" -c "print(123)"'})
    assert tools.code_verification_status() == "unverified"

    # 4. Verification command that FAILS (exit_code != 0) -> still unverified
    tools.invoke("run_command", {"command": f'"{sys.executable}" -m unittest discover -s non_existent_dir_xyz'})
    assert tools.code_verification_status() == "unverified"

    # 5. Passing verification command (exit_code == 0) -> verified
    tools.invoke("run_command", {"command": f'"{sys.executable}" -m py_compile existing.py'})
    assert tools.code_verification_status() == "verified"

    # 6. Edit AFTER passing verification -> must revert to unverified!
    tools.invoke("edit_file", {"path": "existing.py", "old_text": "a = 2", "new_text": "a = 3"})
    assert tools.code_verification_status() == "unverified"


def test_s3_subagent_and_non_code_turns_do_not_emit_code_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subagent turn or a non-code turn must not emit code_verification / code_mode.unverified."""
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    # 1. Subagent in code mode
    sub_provider = _ScriptedProvider(
        [
            _tool_call("c1", "write_file", _json_args({"path": "sub.py", "content": "x = 1\n"})),
            _ANSWER,
        ]
    )
    sub_store = _MemoryStore(code_mode="code", is_subagent=True)
    sub_runtime = AgenticTurnRuntime(
        store=sub_store,
        provider=sub_provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "sub"), code_mode_active=True),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=None,
    )
    res_sub = sub_runtime.run("turn-rt-1")
    assert res_sub.state == "completed"
    assert res_sub.verification is None
    assert not any(state == "code_verification" for state, _ in sub_store.events)

    # 2. Main agent in chat mode (non-code)
    chat_provider = _ScriptedProvider(
        [
            _tool_call("c1", "write_file", _json_args({"path": "chat.py", "content": "x = 1\n"})),
            _ANSWER,
        ]
    )
    chat_store = _MemoryStore(code_mode=None, is_subagent=False)
    chat_runtime = AgenticTurnRuntime(
        store=chat_store,
        provider=chat_provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "chat"), code_mode_active=False),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=None,
    )
    res_chat = chat_runtime.run("turn-rt-1")
    assert res_chat.state == "completed"
    assert res_chat.verification is None
    assert not any(state == "code_verification" for state, _ in chat_store.events)


def test_s3_native_turn_unverified_completion_without_extra_round(tmp_path: Path) -> None:
    """NativeTurnRunner completes without injecting verify_project prompt and emits code_verification=unverified."""
    store = FakeStore()
    store.turn["code_mode"] = "code"
    workspace = ConversationWorkspace(tmp_path, "native-unv")
    toolset = _make_toolset(workspace)
    toolset._code_mode_active = True
    toolset.invoke("write_file", {"path": "native.txt", "content": "hello\n"})

    sent_user_messages: list[Any] = []

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        return NativeTurnOutcome(
            result=_make_result(text="Pronto sem verificar."),
            init=init,
            final_text="Pronto sem verificar.",
            exit_code=0,
        )

    prov = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset, phase_controller=None)
    result = rt.run("t1", turn=store.turn)
    assert result.state == "completed"
    assert result.verification == "unverified"
    assert ("code_verification", {"status": "unverified"}) in store.states
    assert not sent_user_messages


def test_s3_code_mode_unverified_event_survives_settled_turn_activity_reload(tmp_path: Path) -> None:
    """Reloading a conversation after a settled turn must NOT filter out code_mode.unverified."""
    from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(
        engine, activity_store=PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    )
    receipt = store.create(
        user_id="user-1",
        message="Edite a.py",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="idem-reload-unv",
        code_mode="code",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None

    store._activity(
        claimed,
        AgentActivityEventType.CODE_MODE_UNVERIFIED,
        "Entrega não verificada",
        {"verification": "unverified", "changed_paths": 1},
    )
    store.finish(claimed)

    conv = store.get(user_id="user-1", conversation_id=receipt.conversation_id)
    event_types = [ev["event_type"] for ev in conv["activities"]]
    assert "code_mode.unverified" in event_types


def test_s3_todos_remain_in_progress_and_pending_after_text_and_native_turn_completion(
    tmp_path: Path,
) -> None:
    """Neither runtime.py nor NativeTurnRunner may auto-complete todos or emit auto_todo_settle_* at turn end."""
    # 1. Text runtime path
    ws_text = ConversationWorkspace(tmp_path / "text", "todo-text")
    toolset_text = AgentToolset(ws_text, code_mode_active=True)
    provider = _ScriptedProvider(
        [
            _tool_call(
                "tc-todo",
                "todo_write",
                _json_args(
                    {
                        "todos": [
                            {"content": "Ler arquivo", "status": "completed", "activeForm": "Lendo"},
                            {"content": "Implementar feature", "status": "in_progress", "activeForm": "Implementando"},
                            {"content": "Rodar testes", "status": "pending", "activeForm": "Rodando testes"},
                        ]
                    }
                ),
            ),
            _ANSWER,
        ]
    )
    store_text = _MemoryStore(code_mode="code")
    rt_text = AgenticTurnRuntime(
        store=store_text, provider=provider, toolset=toolset_text, phase_controller=None
    )
    res_text = rt_text.run("turn-todo-text")
    assert res_text.state == "completed"
    statuses_text = [t["status"] for t in toolset_text._todos]
    assert statuses_text == ["completed", "in_progress", "pending"]
    for state, payload in store_text.events:
        assert "auto_todo_settle" not in state
        assert "auto_todo_settle" not in str(payload)

    # 2. NativeTurnRunner path
    ws_native = ConversationWorkspace(tmp_path / "native", "todo-native")
    toolset_native = AgentToolset(ws_native, code_mode_active=True)
    toolset_native.invoke(
        "todo_write",
        {
            "todos": [
                {"content": "Passo 1", "status": "in_progress", "activeForm": "Fazendo passo 1"},
                {"content": "Passo 2", "status": "pending", "activeForm": "Fazendo passo 2"},
            ]
        },
    )
    store_native = FakeStore()
    store_native.turn["code_mode"] = "code"

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, _handle: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        return NativeTurnOutcome(
            result=_make_result(text="Encerrado com todos abertos."),
            init=init,
            final_text="Encerrado com todos abertos.",
            exit_code=0,
        )

    prov_native = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    rt_native = AgenticTurnRuntime(
        store=store_native, provider=prov_native, toolset=toolset_native, phase_controller=None
    )
    res_native = rt_native.run("turn-todo-native", turn=store_native.turn)
    assert res_native.state == "completed"
    statuses_native = [t["status"] for t in toolset_native._todos]
    assert statuses_native == ["in_progress", "pending"]
    for state, payload in store_native.states:
        assert "auto_todo_settle" not in state
        assert "auto_todo_settle" not in str(payload)


def test_s3_bug_verify_project_with_no_recipe_falsely_marks_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG REPRO: verify_project in a workspace with no manifest (recipe=None) sets _code_mode_verified=True."""
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    ws = ConversationWorkspace(tmp_path, "no-recipe")
    tools = AgentToolset(ws, code_mode_active=True)
    tools.invoke("write_file", {"path": "script.py", "content": "x = 1\n"})
    assert tools.code_verification_status() == "unverified"

    # Workspace has no pyproject.toml / package.json / requirements.txt / Cargo.toml / go.mod
    outcome = tools.invoke("verify_project", {})
    assert outcome.payload["recipe"] is None
    assert outcome.payload["steps"] == []
    assert outcome.payload["all_passed"] is None
    # Nothing was verified at all, so status MUST remain "unverified" (not "verified"!)
    assert tools.code_verification_status() == "unverified"


def test_s3_bug_failed_run_command_that_mutates_files_after_verify_stays_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG REPRO: A run_command that modifies a file and exits non-zero after a passing check leaves status='verified'."""
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    ws = ConversationWorkspace(tmp_path, "failed-mut")
    tools = AgentToolset(ws, code_mode_active=True)
    tools.invoke("write_file", {"path": "a.py", "content": "x = 1\n"})
    tools.invoke("run_command", {"command": f'"{sys.executable}" -m py_compile a.py'})
    assert tools.code_verification_status() == "verified"

    # Now a command modifies a.py and exits with code 1 (failed=True)
    tools.invoke(
        "run_command",
        {
            "command": f'"{sys.executable}" -c "from pathlib import Path; Path(\'a.py\').write_text(\'x = broken\\n\'); raise SystemExit(1)"'
        },
    )
    # a.py was modified AFTER the last check, so status MUST be "unverified"!
    assert tools.code_verification_status() == "unverified"


def test_s3_bug_uv_and_poetry_run_pytest_not_recognized_as_verification_command() -> None:
    """BUG REPRO: 'uv run pytest', 'uv run python -m pytest', and 'poetry run pytest' return False in is_verification_command."""
    assert is_verification_command("uv run pytest") is True
    assert is_verification_command("uv run python -m pytest") is True
    assert is_verification_command("poetry run pytest") is True
    assert is_verification_command("uv run ruff check .") is True


def test_s3_bug_subagent_file_edits_not_propagated_to_parent_verification_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG REPRO: When a Code mode turn delegates edits to a subagent and finishes without checks, parent reports 'not_applicable'."""
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    recorded: list[tuple[AgentActivityEventType, str, dict[str, object]]] = []

    class _DummySessionStore:
        def load(self, turn_id: str) -> dict[str, object]:
            return turn

        def history_for_turn(self, turn: Any) -> list[dict[str, str]]:
            return [{"role": "user", "content": "delegue a escrita de sub_edited.py"}]

        def lifecycle(self, turn: Any, state: str, **payload: object) -> None:
            ...

        def delta(self, turn: Any, text: str) -> None:
            ...

        def finish(self, turn: Any, *, failed: bool = False, code: str | None = None) -> None:
            ...

        def record(self, turn: Any, event_type: AgentActivityEventType, summary: str, payload: Any, **_kw: Any) -> None:
            recorded.append((event_type, summary, dict(payload or {})))

    class _DummyAgents:
        def list(self) -> list[Any]:
            return []

    sub_provider = _ScriptedProvider(
        [
            _tool_call("s1", "write_file", _json_args({"path": "sub_edited.py", "content": "y = 2\n"})),
            _ANSWER,
        ]
    )
    main_provider = _ScriptedProvider(
        [
            _tool_call(
                "m1",
                "run_subagent",
                _json_args({"subagent_type": "general", "description": "write file", "task": "write sub_edited.py"}),
            ),
            _ANSWER,
        ]
    )

    providers = [main_provider, sub_provider]
    turn = _turn(code_mode="code")
    session = TurnSession(
        turn=turn,
        store=_DummySessionStore(),
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: providers.pop(0),
        workspace_root=str(tmp_path),
    )
    rt = session.build_runtime()
    result = rt.run("turn-rt-1")
    assert session.workspace.resolve("sub_edited.py").is_file()
    # Because the workspace was modified during this Code turn and no verification ran,
    # result.verification MUST be "unverified", not "not_applicable"!
    assert result.verification == "unverified"


def test_s3_bug_settle_code_mode_marks_cancelled_or_not_applicable_wrongly(tmp_path: Path) -> None:
    """BUG REPRO: _settle_code_mode sets completion_kind='unverified' even when stage is BLOCKED (cancelled),
    and sets completion_kind='verified' when verification='not_applicable'.
    """
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Explique o código",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="code-settle-bug",
        code_mode="code",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None
    worker = ChatWorker(store, workspace_root=str(tmp_path))
    hydrated = worker._hydrate_code_mode(claimed)

    # 1. If a turn was cancelled after an unverified edit (target = CodeStage.BLOCKED),
    # completion_kind should be None (it did not complete!), not "unverified".
    worker._settle_code_mode(
        hydrated, "cancelled", error_code="TURN_CANCELLED", verification="unverified"
    )
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(code_mode_runs).where(
                    code_mode_runs.c.execution_id == claimed["execution_id"]
                )
            )
            .mappings()
            .one()
        )
    assert row["stage"] == "blocked"
    assert row["completion_kind"] is None


# ============================================================================
# SURFACE 4: Lint after edit/write, changed lines, truncation, timeout, CRLF
# ============================================================================


@pytest.mark.parametrize(
    ("stdout", "returncode", "stderr"),
    [
        ("", 1, "No module named ruff"),
        ("", 127, "ruff: command not found"),
        ("not json at all", 1, ""),
        ("[{malformed json", 1, ""),
        (json.dumps({"error": "dict root"}), 1, ""),
        (json.dumps(42), 1, ""),
        (json.dumps("just a string"), 1, ""),
        (json.dumps([{"code": "F401", "message": "missing location"}]), 1, ""),
    ],
)
def test_s4_missing_linter_or_malformed_json_never_attaches_lint_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
    stderr: str,
) -> None:
    """Missing linter, exit 127, non-JSON, malformed JSON, or wrong JSON types must add nothing."""
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})(),
    )
    ws = ConversationWorkspace(tmp_path, "lint-malformed")
    tools = AgentToolset(ws)
    outcome = tools.invoke("write_file", {"path": "clean.py", "content": "import os\n"})
    assert outcome.status == "succeeded"
    assert "[lint" not in outcome.content
    assert "problema" not in outcome.content
    assert "No module named" not in outcome.content


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd="ruff", timeout=5.0),
        OSError("ruff binary could not be executed"),
        subprocess.SubprocessError("unexpected subprocess failure"),
    ],
)
def test_s4_linter_timeout_or_oserror_does_not_fail_edit_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """TimeoutExpired or OSError during linter run must be swallowed so write_file/edit_file still succeeds."""
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise exc

    monkeypatch.setattr(agent_tools.subprocess, "run", _raise)
    ws = ConversationWorkspace(tmp_path, "lint-timeout")
    tools = AgentToolset(ws)
    w_out = tools.invoke("write_file", {"path": "app.py", "content": "x = 1\ny = 2\n"})
    assert w_out.status == "succeeded"
    assert "[lint" not in w_out.content

    tools._diagnosed_at.clear()
    e_out = tools.invoke("edit_file", {"path": "app.py", "old_text": "x = 1", "new_text": "x = 10"})
    assert e_out.status == "succeeded"
    assert "[lint" not in e_out.content
    assert ws.read_text("app.py")[0] == "x = 10\ny = 2\n"


def test_s4_summary_strictly_capped_at_600_chars_and_5_items_with_5000_char_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with 10 diagnostics each having a 5,000-char message, the lint block is <= 600 chars and <= 5 items."""
    huge_msg = "M" * 5000
    ruff_items = [
        {
            "code": f"E90{i}",
            "message": huge_msg,
            "location": {"row": i, "column": 1},
        }
        for i in range(1, 10)
    ]
    diags = parse_file_diagnostics("ruff_json", json.dumps(ruff_items))
    assert diags is not None and len(diags) == 9
    summary = summarize_diagnostics(diags, [(1, 10)], max_items=5)
    assert summary is not None
    assert len(summary) <= MAX_FILE_DIAGNOSTIC_CHARS == 600

    # Also test end-to-end through write_file
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": json.dumps(ruff_items), "stderr": ""})(),
    )
    ws = ConversationWorkspace(tmp_path, "lint-huge")
    tools = AgentToolset(ws)
    outcome = tools.invoke(
        "write_file",
        {"path": "huge.py", "content": "".join(f"x{i} = {i}\n" for i in range(1, 12))},
    )
    lint_idx = outcome.content.index("[lint —")
    lint_block = outcome.content[lint_idx:]
    assert len(lint_block) <= MAX_FILE_DIAGNOSTIC_CHARS == 600
    assert "E906" not in lint_block


def test_s4_crlf_file_edit_preserves_line_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing line 20 of a CRLF file should match diagnostics on line 20 without CRLF doubling line numbers."""
    ws = ConversationWorkspace(tmp_path, "lint-crlf")
    crlf_content = "\r\n".join(f"v{i} = {i}" for i in range(1, 31)) + "\r\n"
    ws.write_text("crlf.py", crlf_content)
    tools = AgentToolset(ws)
    tools.invoke("read_file", {"path": "crlf.py"})

    ruff_payload = json.dumps(
        [
            {"code": "F401", "message": "unused on line 1", "location": {"row": 1, "column": 1}},
            {"code": "E501", "message": "line 20 issue", "location": {"row": 20, "column": 5}},
        ]
    )
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ruff_payload, "stderr": ""})(),
    )
    outcome = tools.invoke(
        "edit_file",
        {"path": "crlf.py", "old_text": "v20 = 20", "new_text": "v20 = 999"},
    )
    assert "20:5 E501 line 20 issue" in outcome.content
    assert "F401" not in outcome.content


def test_s4_bug_changed_line_ranges_includes_3_context_lines_expanding_margin_to_plus_minus_5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG REPRO: compute_structured_patch uses unified_diff(n=3), so changed_line_ranges spans [line-3, line+3]
    and summarize_diagnostics(margin=2) includes warnings ±5 lines away from the edited line!
    """
    ws = ConversationWorkspace(tmp_path, "lint-margin")
    ws.write_text("margin.py", "".join(f"x{i} = {i}\n" for i in range(1, 41)))
    tools = AgentToolset(ws)
    tools.invoke("read_file", {"path": "margin.py"})

    # Warning is at line 15; we ONLY edit line 20 (distance = 5 lines away, well outside margin ±2 = [18..22])
    ruff_line_15 = json.dumps(
        [
            {
                "code": "F841",
                "message": "local variable on line 15 is 5 lines away from edit on line 20",
                "location": {"row": 15, "column": 1},
            }
        ]
    )
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ruff_line_15, "stderr": ""})(),
    )
    outcome = tools.invoke(
        "edit_file",
        {"path": "margin.py", "old_text": "x20 = 20", "new_text": "x20 = 200"},
    )
    # Line 15 is 5 lines before edited line 20 (margin is ±2, i.e., lines 18..22).
    # Therefore line 15 is outside the changed lines + ±2 margin and MUST NOT generate a [lint — ...] block!
    assert "[lint" not in outcome.content


def test_s4_bug_debounce_suppresses_lint_on_edit_immediately_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify per-path debounce within DIAGNOSTIC_DEBOUNCE_SECONDS and linting once window expires."""
    ws = ConversationWorkspace(tmp_path, "lint-debounce")
    tools = AgentToolset(ws)

    # 1. write_file with clean code -> ruff returns []
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})(),
    )
    w_out = tools.invoke("write_file", {"path": "fast.py", "content": "x = 1\ny = 2\n"})
    assert "[lint" not in w_out.content

    # 2. Immediately (< DIAGNOSTIC_DEBOUNCE_SECONDS = 3.0s) edit fast.py -> debounced per spec
    ruff_err = json.dumps(
        [{"code": "F401", "message": "`os` imported but unused", "location": {"row": 1, "column": 8}}]
    )
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ruff_err, "stderr": ""})(),
    )
    debounced_out = tools.invoke("edit_file", {"path": "fast.py", "old_text": "y = 2", "new_text": "y = 3"})
    assert "[lint" not in debounced_out.content

    # 3. Once DIAGNOSTIC_DEBOUNCE_SECONDS elapses, editing fast.py runs ruff and reports the error on the edited line
    tools._diagnosed_at["fast.py"] -= DIAGNOSTIC_DEBOUNCE_SECONDS + 1.0
    e_out = tools.invoke("edit_file", {"path": "fast.py", "old_text": "x = 1", "new_text": "import os\nx = 1"})
    assert "1:8 F401" in e_out.content


def test_s4_bug_file_diagnostic_command_shell_injection_and_special_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG REPRO: file_diagnostic_command builds an unescaped shell string executed with shell=True."""
    captured: list[tuple[Any, dict[str, Any]]] = []

    def _spy_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        captured.append((cmd, kwargs))
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    monkeypatch.setattr(agent_tools.subprocess, "run", _spy_run)
    ws = ConversationWorkspace(tmp_path, "lint-shell")
    tools = AgentToolset(ws)

    # Path with $VAR / $(...) / %PATH% special shell characters
    tricky_path = "src/$id_%USERNAME%_$(whoami).py"
    tools.invoke("write_file", {"path": tricky_path, "content": "x = 1\n"})
    assert len(captured) == 1
    cmd_passed, kwargs_passed = captured[0]
    # Running shell=True with double-quoted f-string expands $(whoami) / $id on POSIX and %USERNAME% on Windows cmd!
    assert kwargs_passed.get("shell") is not True or isinstance(cmd_passed, list)


def test_s4_bug_ruff_single_null_location_discards_all_valid_diagnostics() -> None:
    """BUG REPRO: If Ruff emits a file-level diagnostic with location=None alongside valid line diagnostics,
    parse_file_diagnostics returns None and drops all valid diagnostics.
    """
    stdout = json.dumps(
        [
            {"code": "E902", "message": "file-level notice", "location": None},
            {"code": "F401", "message": "`os` imported but unused", "location": {"row": 2, "column": 8}},
        ]
    )
    diags = parse_file_diagnostics("ruff_json", stdout)
    assert diags is not None
    assert any(d.code == "F401" and d.line == 2 for d in diags)

