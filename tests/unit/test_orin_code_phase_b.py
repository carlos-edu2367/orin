from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import LoopbackSecurityService
from agentos.code_mode import (
    CodeAutonomy,
    CodeModeSettings,
    CodePermissionMode,
    FileCheckpointStore,
    ToolPermissionBroker,
    load_workspace_permission_rules,
    matches_permission_rule,
    normalize_permission_mode,
    parse_permission_rule,
    suggest_permission_rule,
)


def test_four_permission_modes_and_backward_compatible_aliases() -> None:
    assert normalize_permission_mode("approval_required") is CodePermissionMode.ASK_PERMISSIONS
    assert normalize_permission_mode("ask_permissions") is CodePermissionMode.ASK_PERMISSIONS
    assert normalize_permission_mode("code_autonomy") is CodePermissionMode.ACCEPT_EDITS
    assert normalize_permission_mode("accept_edits") is CodePermissionMode.ACCEPT_EDITS
    assert normalize_permission_mode("plan_mode") is CodePermissionMode.PLAN_MODE
    assert normalize_permission_mode("full_autonomy") is CodePermissionMode.FULL_AUTONOMY

    assert CodeAutonomy("ask_permissions") is CodeAutonomy.APPROVAL_REQUIRED
    assert CodeAutonomy("accept_edits") is CodeAutonomy.CODE_AUTONOMY
    assert CodeAutonomy("plan_mode") is CodeAutonomy.PLAN_MODE
    assert CodeAutonomy("full_autonomy") is CodeAutonomy.FULL_AUTONOMY

    assert CodeModeSettings(autonomy=CodeAutonomy.APPROVAL_REQUIRED).permission_mode is CodePermissionMode.ASK_PERMISSIONS
    assert CodeModeSettings(autonomy=CodeAutonomy.CODE_AUTONOMY).permission_mode is CodePermissionMode.ACCEPT_EDITS
    assert CodeModeSettings(autonomy=CodeAutonomy.PLAN_MODE).permission_mode is CodePermissionMode.PLAN_MODE
    assert CodeModeSettings(autonomy=CodeAutonomy.FULL_AUTONOMY).permission_mode is CodePermissionMode.FULL_AUTONOMY


def test_permission_rules_parsing_and_matching(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-rules")
    broker = ToolPermissionBroker(workspace)

    assert parse_permission_rule("run_command(npm test:*)") == ("run_command", "npm test:*")
    assert parse_permission_rule("write_file") == ("write_file", None)
    assert suggest_permission_rule("run_command", {"command": "npm test"}) == "run_command(npm test:*)"
    assert suggest_permission_rule("run_command", {"command": "pytest tests/unit"}) == "run_command(pytest tests/unit:*)"
    assert suggest_permission_rule("edit_file", {"path": "src/app.py"}) == "edit_file(src/**)"

    assert matches_permission_rule("run_command(npm test:*)", "run_command", {"command": "npm test -- --watch"}) is True
    assert matches_permission_rule("run_command(npm test:*)", "run_command", {"command": "rm -rf build"}) is False
    assert matches_permission_rule("write_file(*)", "write_file", {"path": "src/main.py"}) is True

    # Evaluate modes without saved rules
    assert broker.evaluate("write_file", {"path": "src/a.py"}, mode="ask_permissions") == "ask"
    assert broker.evaluate("write_file", {"path": "src/a.py"}, mode="accept_edits") == "allow"
    assert broker.evaluate("run_command", {"command": "pytest"}, mode="accept_edits") == "ask"
    assert broker.evaluate("run_command", {"command": "pytest"}, mode="full_autonomy") == "allow"

    # Plan mode blocks mutations except .orin/plans/*.md
    assert broker.evaluate("write_file", {"path": "src/a.py"}, mode="plan_mode") == "deny"
    assert broker.evaluate("write_file", {"path": ".orin/plans/feature.md"}, mode="plan_mode") == "allow"
    assert broker.evaluate("run_command", {"command": "ls"}, mode="plan_mode") == "deny"


def test_permission_broker_ipc_and_allow_always_persistence(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-ipc")
    broker = ToolPermissionBroker(workspace)

    events: list[dict[str, object]] = []

    def resolve_shortly() -> None:
        deadline = time.monotonic() + 3.0
        while not events and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(events) == 1
        req_id = str(events[0]["request_id"])
        # Simulate API gateway process resolving the request via a fresh broker on the same workspace
        api_broker = ToolPermissionBroker(workspace)
        api_broker.submit_decision(req_id, decision="allow_always")

    worker = threading.Thread(target=resolve_shortly, daemon=True)
    worker.start()

    decision = broker.request_and_wait(
        turn_id="turn-1",
        tool_name="run_command",
        arguments={"command": "npm test"},
        emit_request=lambda req: events.append(req),
        timeout_seconds=5.0,
        poll_interval_seconds=0.02,
    )
    worker.join(timeout=2.0)

    assert decision["allowed"] is True
    assert decision["decision"] == "allow_always"
    saved_rules = load_workspace_permission_rules(workspace)
    assert "run_command(npm test:*)" in saved_rules["allow"]
    assert broker.evaluate("run_command", {"command": "npm test -- --run"}, mode="ask_permissions") == "allow"


def test_agent_toolset_interactive_permission_denial_and_approval(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-toolset-perm")
    broker = ToolPermissionBroker(workspace)

    # 1. Denied permission returns ToolOutcome with CODE_PERMISSION_DENIED
    def deny_immediately(req: dict[str, object]) -> None:
        broker.submit_decision(str(req["request_id"]), decision="deny", reason="User rejected")

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        permission_mode="ask_permissions",
        permission_broker=broker,
        interactive_permissions=True,
        on_permission_request=deny_immediately,
        turn_id="turn-perm-1",
    )
    denied = toolset.invoke("write_file", {"path": "hello.txt", "content": "world"})
    assert denied.status == "failed"
    assert denied.error_code == "CODE_PERMISSION_DENIED"
    assert not (workspace.root / "hello.txt").exists()

    # 2. Approved permission executes tool and includes pre-computed structured_patch preview
    def allow_immediately(req: dict[str, object]) -> None:
        assert req["structured_patch"]
        broker.submit_decision(str(req["request_id"]), decision="allow_once")

    toolset_allow = AgentToolset(
        workspace,
        code_mode_active=True,
        permission_mode="ask_permissions",
        permission_broker=broker,
        interactive_permissions=True,
        on_permission_request=allow_immediately,
        turn_id="turn-perm-2",
    )
    outcome = toolset_allow.invoke("write_file", {"path": "hello.txt", "content": "world\n"})
    assert outcome.status == "succeeded"
    assert (workspace.root / "hello.txt").read_text(encoding="utf-8") == "world\n"


def test_file_checkpoint_store_snapshots_once_per_turn_and_rewinds(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-checkpoints")
    existing = workspace.root / "src" / "service.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("v1 = 1\n", encoding="utf-8")

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        permission_mode="full_autonomy",
        turn_id="turn-cp-1",
    )
    # Read existing file then edit twice in the same turn
    toolset.invoke("read_file", {"path": "src/service.py"})
    toolset.invoke("edit_file", {"path": "src/service.py", "old_text": "v1 = 1", "new_text": "v1 = 2"})
    toolset.invoke("edit_file", {"path": "src/service.py", "old_text": "v1 = 2", "new_text": "v1 = 3"})
    # Create a new file in the same turn
    toolset.invoke("write_file", {"path": "src/new_helper.py", "content": "helper = True\n"})

    assert existing.read_text(encoding="utf-8") == "v1 = 3\n"
    assert (workspace.root / "src" / "new_helper.py").exists()

    store = FileCheckpointStore(workspace)
    checkpoint = store.get_checkpoint("turn-cp-1")
    assert checkpoint is not None
    files_by_path = checkpoint["files_by_path"]
    assert files_by_path["src/service.py"]["existed"] is True
    backup_file = store.turn_dir("turn-cp-1") / files_by_path["src/service.py"]["backup_rel_path"]
    assert backup_file.read_text(encoding="utf-8") == "v1 = 1\n"
    assert files_by_path["src/new_helper.py"]["existed"] is False

    rewind_result = store.rewind_turn("turn-cp-1")
    assert rewind_result["rewound"] is True
    assert "src/service.py" in rewind_result["restored_files"]
    assert "src/new_helper.py" in rewind_result["deleted_files"]
    assert rewind_result["undo_backup_id"] is not None
    assert existing.read_text(encoding="utf-8") == "v1 = 1\n"
    assert not (workspace.root / "src" / "new_helper.py").exists()


def test_enter_and_exit_plan_mode_blocks_mutations_except_plan_files(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-plan-mode")
    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        permission_mode="accept_edits",
        turn_id="turn-plan-1",
    )

    entered = toolset.invoke("enter_plan_mode", {"reason": "Design architecture first"})
    assert entered.status == "succeeded"
    assert entered.payload["plan_mode"] is True

    # Writing regular code file is blocked in Plan Mode
    blocked_write = toolset.invoke("write_file", {"path": "src/feature.py", "content": "x = 1\n"})
    assert blocked_write.status == "failed"
    assert blocked_write.error_code == "CODE_PLAN_MODE_READONLY"

    # Running shell command is blocked in Plan Mode
    blocked_cmd = toolset.invoke("run_command", {"command": "echo test"})
    assert blocked_cmd.status == "failed"
    assert blocked_cmd.error_code == "CODE_PLAN_MODE_READONLY"

    # Writing to .orin/plans/*.md is allowed in Plan Mode
    plan_write = toolset.invoke(
        "write_file",
        {"path": ".orin/plans/auth-plan.md", "content": "# Auth Plan\n1. Add middleware\n"},
    )
    assert plan_write.status == "succeeded"

    # Exit Plan Mode restores previous permission mode and includes plan content
    exited = toolset.invoke(
        "exit_plan_mode",
        {"plan_path": ".orin/plans/auth-plan.md", "summary": "Add auth middleware"},
    )
    assert exited.status == "succeeded"
    assert exited.payload["plan_mode"] is False
    assert exited.payload["plan_ready"] is True
    assert "# Auth Plan" in exited.content

    # Now writing to src/feature.py succeeds because accept_edits was restored
    code_write = toolset.invoke("write_file", {"path": "src/feature.py", "content": "x = 1\n"})
    assert code_write.status == "succeeded"


def test_fluid_agentic_loop_disables_phase_controller_in_code_mode(tmp_path: Path) -> None:
    class DummyStore:
        def main_agent_id(self, turn):
            return f"agent:{turn['conversation_id']}:main"

        def history_for_turn(self, turn):
            return [{"role": "user", "content": "implement feature"}]

        def record(self, *args, **kwargs):
            return None

    class DummyAgents:
        def list(self):
            return []

    code_session = TurnSession(
        turn={
            "turn_id": "turn-code",
            "conversation_id": "conv-fluid",
            "user_id": "local-user",
            "execution_id": "exe-1",
            "code_mode": "code",
            "provider": "openrouter",
            "model_id": "gpt-4o",
        },
        store=DummyStore(),
        agents_store=DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
        cancelled=lambda _turn: False,
    )
    runtime_code = code_session.build_runtime()
    assert runtime_code.phase_controller is None
    tool_names = {defn.name for defn in runtime_code.toolset.definitions()}
    assert "enter_plan_mode" in tool_names
    assert "exit_plan_mode" in tool_names

    chat_session = TurnSession(
        turn={
            "turn_id": "turn-chat",
            "conversation_id": "conv-fluid",
            "user_id": "local-user",
            "execution_id": "exe-2",
            "code_mode": None,
            "provider": "openrouter",
            "model_id": "gpt-4o",
        },
        store=DummyStore(),
        agents_store=DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=tmp_path,
        cancelled=lambda _turn: False,
    )
    runtime_chat = chat_session.build_runtime()
    assert runtime_chat.phase_controller is not None


def test_gateway_tool_permission_rewind_and_four_mode_settings_endpoints(tmp_path: Path) -> None:
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService

    workspace_root = tmp_path / "workspaces"
    runtime_settings = AgentRuntimeSettingsStore(tmp_path / "runtime.json")

    class StubConversationApp:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {
                "conversation_id": conversation_id,
                "title": "Test",
                "state": "running",
                "provider": "openai",
                "model_id": "gpt-4o",
                "messages": [],
                "turns": [],
                "activities": [],
                "activity_cursor": "0",
            }

    security = InMemorySecurityService()
    security.add_pat(
        "test-pat",
        AuthenticatedPrincipal(
            user_id="local-user",
            credential_ref="pat-1",
            scopes=frozenset({"api"}),
        ),
    )
    auth_headers = {"Authorization": "Bearer test-pat"}

    services = ApiServices(
        security=security,
        conversation_application=StubConversationApp(),  # type: ignore[arg-type]
        agentic_runtime=runtime_settings,
        workspace_root=workspace_root,
    )
    client = TestClient(create_app(services))

    # 1. Test 4 permission modes in /v1/code-mode/settings
    for mode_val in ("ask_permissions", "accept_edits", "plan_mode", "full_autonomy"):
        resp = client.put(
            "/v1/code-mode/settings",
            json={"autonomy": mode_val, "system_notifications": False, "monitoring_enabled": True},
            headers={**auth_headers, "Idempotency-Key": f"idem-{mode_val}"},
        )
        assert resp.status_code == 200

    # 2. Test /v1/conversations/{id}/turns/{turn_id}/tool-permission
    workspace = ConversationWorkspace(workspace_root, "conv-api")
    broker = ToolPermissionBroker(workspace)
    req = broker.create_request(turn_id="turn-api-1", tool_name="run_command", arguments={"command": "pytest"})

    perm_resp = client.post(
        "/v1/conversations/conv-api/turns/turn-api-1/tool-permission",
        json={"request_id": req["request_id"], "decision": "allow_always"},
        headers=auth_headers,
    )
    assert perm_resp.status_code == 200
    assert perm_resp.json()["allowed"] is True
    assert "run_command(pytest:*)" in load_workspace_permission_rules(workspace)["allow"]

    # 3. Test /v1/conversations/{id}/turns/{turn_id}/rewind
    store = FileCheckpointStore(workspace)
    target_file = workspace.root / "app.py"
    target_file.write_text("original = True\n", encoding="utf-8")
    store.snapshot_before_write("turn-api-1", "app.py")
    target_file.write_text("modified = True\n", encoding="utf-8")

    rewind_resp = client.post(
        "/v1/conversations/conv-api/turns/turn-api-1/rewind",
        headers=auth_headers,
    )
    assert rewind_resp.status_code == 200
    data = rewind_resp.json()
    assert data["rewound"] is True
    assert "app.py" in data["restored_files"]
    assert target_file.read_text(encoding="utf-8") == "original = True\n"
