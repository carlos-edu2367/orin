from __future__ import annotations

from pathlib import Path
import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.code_mode.prompt import build_code_mode_system_prompt
from agentos.code_mode.system_prompt import render_code_turn_context


def test_workspace_unrestricted_flag_controls_external_paths(tmp_path: Path) -> None:
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    # Place external dir under a parent named "target" (which is in DENIED_SEGMENTS)
    # to adversarially verify ancestor segments outside the listed root do not hide entries.
    external_dir = tmp_path / "target" / "external_proj"
    external_dir.mkdir(parents=True)
    ext_file = external_dir / "hello.txt"
    ext_file.write_text("external content\n", encoding="utf-8")

    workspace = ConversationWorkspace.at_root(ws_root)
    assert workspace.unrestricted is False

    with pytest.raises(WorkspaceError):
        workspace.resolve(str(ext_file))
    with pytest.raises(WorkspaceError):
        workspace.resolve("../target/external_proj/hello.txt")
    with pytest.raises(WorkspaceError):
        workspace.key(ext_file)

    workspace.unrestricted = True
    assert workspace.resolve(str(ext_file)) == ext_file.resolve()
    assert workspace.resolve("../target/external_proj/hello.txt") == ext_file.resolve()
    assert workspace.key(ext_file) == ext_file.resolve().as_posix()
    assert workspace.key(ws_root) == "."

    entries = workspace.list_entries(str(external_dir))
    assert any(item["path"].endswith("hello.txt") for item in entries)


@pytest.mark.parametrize("code_mode_active", [True, False])
def test_agent_toolset_full_autonomy_allows_files_search_and_shell_outside_workspace(
    tmp_path: Path,
    code_mode_active: bool,
) -> None:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    # External folder under "build" (a segment in DENIED_SEGMENTS / _GLOB_IGNORED_DIRS)
    ext_dir = tmp_path / "build" / "outside_app"
    ext_dir.mkdir(parents=True)

    restricted_ws = ConversationWorkspace.at_root(ws_root)
    restricted_tools = AgentToolset(
        restricted_ws,
        code_mode_active=code_mode_active,
        permission_mode="accept_edits",
    )
    ext_file_path = str(ext_dir / "notes.py")

    # Restricted mode blocks outside workspace
    denied_write = restricted_tools.invoke("write_file", {"path": ext_file_path, "content": "x = 1\n"})
    assert denied_write.status == "failed"

    denied_cd = restricted_tools.invoke("run_command", {"command": f'cd "{ext_dir}"'})
    assert denied_cd.status == "failed"
    assert "outside workspace" in denied_cd.content.lower()

    # Full autonomy removes workspace confinement in both Code and Chat modes
    unrestricted_ws = ConversationWorkspace.at_root(ws_root)
    full_tools = AgentToolset(
        unrestricted_ws,
        code_mode_active=code_mode_active,
        permission_mode="full_autonomy",
    )
    assert unrestricted_ws.unrestricted is True

    # 1. write_file outside workspace (including nested subdirectory creation)
    nested_ext_file = ext_dir / "sub" / "module.py"
    write_res = full_tools.invoke(
        "write_file",
        {"path": str(nested_ext_file), "content": "def greet():\n    return 'hi'\n"},
    )
    assert write_res.status == "succeeded", write_res.content
    assert nested_ext_file.read_text(encoding="utf-8") == "def greet():\n    return 'hi'\n"

    # 2. read_file outside workspace
    read_res = full_tools.invoke("read_file", {"path": str(nested_ext_file)})
    assert read_res.status == "succeeded", read_res.content
    assert "def greet():" in read_res.content

    # 3. edit_file outside workspace
    edit_res = full_tools.invoke(
        "edit_file",
        {
            "path": str(nested_ext_file),
            "old_string": "return 'hi'",
            "new_string": "return 'hello unrestricted'",
        },
    )
    assert edit_res.status == "succeeded", edit_res.content
    assert "hello unrestricted" in nested_ext_file.read_text(encoding="utf-8")

    # 4. list_files outside workspace
    list_res = full_tools.invoke("list_files", {"path": str(ext_dir), "depth": 2})
    assert list_res.status == "succeeded", list_res.content
    assert "module.py" in list_res.content

    # 5. glob_files outside workspace
    glob_res = full_tools.invoke("glob_files", {"pattern": "**/*.py", "path": str(ext_dir)})
    assert glob_res.status == "succeeded", glob_res.content
    assert "module.py" in glob_res.content

    # 6. search_files outside workspace
    grep_res = full_tools.invoke(
        "search_files",
        {"pattern": "hello unrestricted", "path": str(ext_dir)},
    )
    assert grep_res.status == "succeeded", grep_res.content
    assert "hello unrestricted" in grep_res.content

    # 7. run_command with cd and cwd outside workspace
    cd_res = full_tools.invoke(
        "run_command",
        {"command": f'cd "{ext_dir}" && python -c "import os; print(os.getcwd())"'},
    )
    assert cd_res.status == "succeeded", cd_res.content
    assert ext_dir.resolve().as_posix() in cd_res.content

    cwd_res = full_tools.invoke(
        "run_command",
        {"command": 'python -c "import pathlib; print(pathlib.Path(\'sub/module.py\').exists())"', "cwd": str(ext_dir)},
    )
    assert cwd_res.status == "succeeded", cwd_res.content
    assert "True" in cwd_res.content


def test_plan_mode_and_readonly_subagents_preserve_or_toggle_unrestricted_safely(tmp_path: Path) -> None:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    ext_file = ext_dir / "info.txt"
    ext_file.write_text("secret info\n", encoding="utf-8")

    workspace = ConversationWorkspace.at_root(ws_root)
    parent_tools = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy")
    assert workspace.unrestricted is True

    # A read-only subagent sharing the workspace with permission_mode="plan_mode"
    # must NOT clobber workspace.unrestricted to False!
    subagent_tools = AgentToolset(
        workspace,
        code_mode_active=True,
        permission_mode="plan_mode",
        read_only_mode=True,
    )
    assert workspace.unrestricted is True
    sub_read = subagent_tools.invoke("read_file", {"path": str(ext_file)})
    assert sub_read.status == "succeeded", sub_read.content

    # Entering plan mode on the main toolset switches permission_mode to plan_mode
    # and confines the workspace until exit_plan_mode restores full_autonomy
    parent_tools.invoke("run_command", {"command": f'cd "{ext_dir}"'})
    assert parent_tools._current_cwd == ext_dir.resolve()

    enter_res = parent_tools.invoke("enter_plan_mode", {"reason": "planning architecture"})
    assert enter_res.status == "succeeded"
    assert workspace.unrestricted is False
    assert parent_tools._current_cwd == ws_root.resolve()

    exit_res = parent_tools.invoke("exit_plan_mode", {"summary": "ready"})
    assert exit_res.status == "succeeded"
    assert workspace.unrestricted is True


def test_prompts_describe_full_autonomy_as_unrestricted_outside_workspace() -> None:
    code_prompt = build_code_mode_system_prompt(permission_mode="full_autonomy")
    assert "outside the workspace" in code_prompt.lower()

    turn_ctx = render_code_turn_context(
        permission_mode="full_autonomy",
        plan_path=None,
        permits_push=False,
        environment={"os": "Windows", "shell": "cmd.exe", "python": "3.13", "available": "git"},
        workspace_root="C:/work",
        workspace_is_local=True,
        git_status="",
        public_names=True,
    )
    assert "outside the workspace" in turn_ctx.lower()
