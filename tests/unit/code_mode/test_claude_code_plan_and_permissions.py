from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine, select

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import code_mode_runs, metadata
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker


class _DummyStore:
    def __init__(self) -> None:
        self.events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []

    def main_agent_id(self, turn: Mapping[str, Any]) -> str:
        return f"agent:{turn.get('conversation_id', 'conv')}:main"

    def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Implemente a função soma em calc.py"}]

    def record(
        self,
        turn: Mapping[str, Any],
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        self.events.append((event_type, summary, dict(payload or {})))


class _DummyAgents:
    def list(self) -> list[dict[str, Any]]:
        return []


def test_accept_edits_turn_does_not_create_dummy_plan_file_and_settles_only_existing_plan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    receipt = store.create(
        user_id="user-1",
        message="Crie um módulo utilitário simples",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="code-no-dummy-plan",
        code_mode="code",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None
    assert claimed["permission_mode"] == "accept_edits"

    worker = ChatWorker(store, workspace_root=str(tmp_path))
    hydrated = worker._hydrate_code_mode(claimed)

    assert hydrated["permission_mode"] == "accept_edits"
    assert hydrated["code_mode_autonomy"] == "code_autonomy"
    assert hydrated.get("code_mode_plan_path") is None

    with engine.connect() as conn:
        run_row = conn.execute(
            select(code_mode_runs).where(code_mode_runs.c.execution_id == claimed["execution_id"])
        ).mappings().one()
    assert run_row["plan_path"] is None
    assert run_row["autonomy"] == "code_autonomy"

    dummy_store = _DummyStore()
    session = TurnSession(
        turn=hydrated,
        store=dummy_store,
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
    )
    runtime = session.build_runtime()
    assert runtime is not None

    plan_file = session.workspace.root / ".orin" / "plans" / f"{receipt.turn_id}-plan.md"
    assert not plan_file.exists()
    assert not any(
        evt_type is AgentActivityEventType.CODE_MODE_PLAN_READY for evt_type, _, _ in dummy_store.events
    )

    # _settle_code_mode should not emit plan settlement activity when plan is None
    settled_events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        store,
        "_activity",
        lambda turn, event, summary, payload=None: settled_events.append((event, summary, dict(payload or {}))),
    )
    worker._settle_code_mode(hydrated, "completed", error_code=None)
    assert settled_events == []

    # When an active plan exists, _settle_code_mode settles it and emits the event
    hydrated_with_plan = {**hydrated, "code_mode_plan_path": ".orin/plans/active-plan.md"}
    worker._settle_code_mode(hydrated_with_plan, "completed", error_code=None)
    assert len(settled_events) == 1
    assert settled_events[0][0] is AgentActivityEventType.CODE_MODE_COMPLETED


def test_turn_session_reads_permission_mode_and_passes_to_broker(tmp_path: Path) -> None:
    store = _DummyStore()

    # Default for code_mode == "code" is "accept_edits"
    session_code_default = TurnSession(
        turn={
            "turn_id": "turn-default-code",
            "conversation_id": "conv-default-code",
            "user_id": "user-1",
            "execution_id": "exec-1",
            "code_mode": "code",
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
        store=store,
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
    )
    rt_code = session_code_default.build_runtime()
    assert session_code_default.permission_mode == "accept_edits"
    assert session_code_default._in_plan_mode is False
    assert getattr(rt_code.toolset._permission_broker, "mode", None) == "accept_edits"
    assert rt_code.toolset._permission_mode.value == "accept_edits"

    # Default for code_mode != "code" is "ask_permissions"
    session_chat_default = TurnSession(
        turn={
            "turn_id": "turn-default-chat",
            "conversation_id": "conv-default-chat",
            "user_id": "user-1",
            "execution_id": "exec-2",
            "code_mode": None,
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
        store=store,
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
    )
    rt_chat = session_chat_default.build_runtime()
    assert session_chat_default.permission_mode == "ask_permissions"
    assert session_chat_default._in_plan_mode is False
    assert getattr(rt_chat.toolset._permission_broker, "mode", None) == "ask_permissions"

    # Explicit permission_mode on turn is honored
    for mode in ("accept_edits", "plan_mode", "ask_permissions", "full_autonomy"):
        s = TurnSession(
            turn={
                "turn_id": f"turn-{mode}",
                "conversation_id": f"conv-{mode}",
                "user_id": "user-1",
                "execution_id": f"exec-{mode}",
                "code_mode": "code",
                "permission_mode": mode,
                "provider": "anthropic",
                "model_id": "claude-sonnet-4-6",
            },
            store=store,
            agents_store=_DummyAgents(),
            memory_store=None,
            provider_factory=lambda: None,
            workspace_root=tmp_path,
        )
        rt = s.build_runtime()
        assert s.permission_mode == mode
        assert s._in_plan_mode is (mode == "plan_mode")
        assert getattr(rt.toolset._permission_broker, "mode", None) == mode
        assert rt.toolset._permission_mode.value == mode


def test_accept_edits_with_bound_workspace_allows_file_mutations_and_safe_commands(tmp_path: Path) -> None:
    bound_repo = tmp_path / "bound_repo"
    bound_repo.mkdir(parents=True, exist_ok=True)
    existing_file = bound_repo / "calc.py"
    existing_file.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    session = TurnSession(
        turn={
            "turn_id": "turn-bound-accept",
            "conversation_id": "conv-bound-accept",
            "user_id": "user-1",
            "execution_id": "exec-bound-1",
            "code_mode": "code",
            "permission_mode": "accept_edits",
            "workspace_root_path": str(bound_repo),
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
        store=_DummyStore(),
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path / "managed",
    )
    runtime = session.build_runtime()
    toolset = runtime.toolset

    # 1. Creating a new file in the bound workspace succeeds without blocking on plan approval / ask_user
    write_res = toolset.invoke("write_file", {"path": "new_module.py", "content": "VALUE = 42\n"})
    assert write_res.status == "succeeded", write_res.content
    assert (bound_repo / "new_module.py").read_text(encoding="utf-8") == "VALUE = 42\n"

    # 2. Reading and editing an existing file in the bound workspace succeeds without blocking
    read_res = toolset.invoke("read_file", {"path": "calc.py"})
    assert read_res.status == "succeeded"
    edit_res = toolset.invoke(
        "edit_file",
        {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"},
    )
    assert edit_res.status == "succeeded", edit_res.content
    assert "return a + b" in existing_file.read_text(encoding="utf-8")

    # 3. Running a normal command succeeds without blocking on plan approval
    cmd_res = toolset.invoke(
        "run_command",
        {"command": f'"{sys.executable}" -c "import calc; print(calc.add(2, 3))"'},
    )
    assert cmd_res.status == "succeeded", cmd_res.content
    assert "5" in cmd_res.content

    # 4. Destructive / external commands (like unauthorized git push) remain blocked
    push_res = toolset.invoke("run_command", {"command": "git push origin main"})
    assert push_res.status == "failed"
    assert push_res.error_code == "CODE_PUSH_CONFIRMATION_REQUIRED"


def test_plan_mode_starts_active_and_blocks_mutations_outside_orin_plans(tmp_path: Path) -> None:
    bound_repo = tmp_path / "plan_repo"
    bound_repo.mkdir(parents=True, exist_ok=True)
    (bound_repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    session = TurnSession(
        turn={
            "turn_id": "turn-plan-mode",
            "conversation_id": "conv-plan-mode",
            "user_id": "user-1",
            "execution_id": "exec-plan-1",
            "code_mode": "code",
            "permission_mode": "plan_mode",
            "workspace_root_path": str(bound_repo),
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
        },
        store=_DummyStore(),
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path / "managed",
    )
    runtime = session.build_runtime()
    toolset = runtime.toolset

    assert session._in_plan_mode is True
    assert toolset._plan_mode_active is True

    # Mutating production files is blocked in plan_mode
    blocked_write = toolset.invoke("write_file", {"path": "src/main.py", "content": "print(1)\n"})
    assert blocked_write.status == "failed"
    assert blocked_write.error_code == "CODE_PLAN_MODE_READONLY"

    toolset.invoke("read_file", {"path": "app.py"})
    blocked_edit = toolset.invoke("edit_file", {"path": "app.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert blocked_edit.status == "failed"
    assert blocked_edit.error_code == "CODE_PLAN_MODE_READONLY"

    blocked_cmd = toolset.invoke("run_command", {"command": "echo hello"})
    assert blocked_cmd.status == "failed"
    assert blocked_cmd.error_code == "CODE_PLAN_MODE_READONLY"

    # Writing inside .orin/plans/ is allowed in plan_mode
    plan_write = toolset.invoke(
        "write_file",
        {"path": ".orin/plans/design.md", "content": "# Architecture Plan\n- Step 1\n"},
    )
    assert plan_write.status == "succeeded", plan_write.content


def test_build_code_mode_system_prompt_documents_permission_modes_and_claude_code_style() -> None:
    from agentos.code_mode.prompt import build_code_mode_system_prompt

    accept_prompt = build_code_mode_system_prompt(permission_mode="accept_edits")
    assert "accept_edits" in accept_prompt
    assert "plan_mode" in accept_prompt
    assert "ask_permissions" in accept_prompt
    assert "full_autonomy" in accept_prompt
    assert "todo_write" in accept_prompt
    assert "task_create" in accept_prompt
    assert "task_update" in accept_prompt
    assert "enter_plan_mode" in accept_prompt
    assert "exit_plan_mode" in accept_prompt

    plan_prompt = build_code_mode_system_prompt(
        permission_mode="plan_mode",
        plan_path=".orin/plans/turn-1-plan.md",
    )
    assert "plan_mode" in plan_prompt
    assert ".orin/plans/" in plan_prompt
    assert "exit_plan_mode" in plan_prompt
