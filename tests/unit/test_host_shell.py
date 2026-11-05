from pathlib import Path

import pytest

from agentos import host_shell
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.permissions.shell import default_shell


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_discovery_prefers_the_explicit_setting(tmp_path: Path) -> None:
    bash = _file(tmp_path / "custom" / "bash.exe")
    assert host_shell._discover_git_bash({"ORIN_GIT_BASH_PATH": str(bash)}, which=lambda _n: None) == bash


def test_discovery_derives_bash_from_git_on_path(tmp_path: Path) -> None:
    git = _file(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _file(tmp_path / "Git" / "bin" / "bash.exe")
    assert host_shell._discover_git_bash({}, which=lambda n: str(git) if n == "git" else None) == bash


def test_discovery_falls_back_to_program_files(tmp_path: Path) -> None:
    bash = _file(tmp_path / "PF" / "Git" / "bin" / "bash.exe")
    assert host_shell._discover_git_bash({"ProgramFiles": str(tmp_path / "PF")}, which=lambda _n: None) == bash


def test_discovery_never_uses_the_wsl_launcher(tmp_path: Path) -> None:
    assert host_shell._discover_git_bash({}, which=lambda n: r"C:\Windows\System32\bash.exe" if n == "bash" else None) is None


def test_discovery_can_be_turned_off(tmp_path: Path) -> None:
    _file(tmp_path / "PF" / "Git" / "bin" / "bash.exe")
    environ = {"ORIN_GIT_BASH_PATH": "none", "ProgramFiles": str(tmp_path / "PF")}
    assert host_shell._discover_git_bash(environ, which=lambda _n: None) is None


def test_posix_and_windows_paths_convert() -> None:
    assert host_shell.posix_to_windows_path("/c/Users/Some Name/proj") == r"C:\Users\Some Name\proj"
    assert host_shell.posix_to_windows_path("/d") == "D:\\"
    assert host_shell.posix_to_windows_path("sub/dir") == "sub/dir"
    assert host_shell.windows_to_posix_path(r"C:\Users\Some Name\proj") == "/c/Users/Some Name/proj"


def test_without_git_bash_windows_falls_back_to_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_shell, "_is_windows", lambda: True)
    monkeypatch.setattr(host_shell, "find_git_bash", lambda: None)
    shell = host_shell.current_host_shell()
    assert (shell.kind, shell.dialect) == ("cmd", "cmd") and "cmd.exe" in shell.label
    assert host_shell.shell_argv("echo hi", shell) is None
    assert default_shell() == "cmd"


def test_with_git_bash_the_argv_is_bash_dash_c(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = Path(r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setattr(host_shell, "_is_windows", lambda: True)
    monkeypatch.setattr(host_shell, "find_git_bash", lambda: exe)
    shell = host_shell.current_host_shell()
    assert shell.dialect == "posix" and "Git Bash" in shell.label
    assert host_shell.shell_argv("echo hi", shell) == [str(exe), "-c", "echo hi"]
    assert default_shell() == "posix"


def test_command_separators_follow_the_dialect(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    sub = toolset.workspace.root / "sub"
    sub.mkdir()
    root = toolset.workspace.root
    assert toolset._resolve_cwd_after_command("echo x & cd sub", root, precheck=False, dialect="cmd") == sub.resolve()
    assert toolset._resolve_cwd_after_command("true || cd sub", root, precheck=False, dialect="posix") == sub.resolve()


@pytest.mark.skipif(not host_shell._is_windows(), reason="POSIX drive paths exist only on Windows")
def test_posix_cd_with_spaces_is_understood(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    target = toolset.workspace.root / "Some Name"
    target.mkdir()
    command = f'cd "{host_shell.windows_to_posix_path(target)}"'
    assert toolset._resolve_cwd_after_command(command, toolset.workspace.root, precheck=True, dialect="posix") == target.resolve()


@pytest.mark.skipif(host_shell.find_git_bash() is None, reason="needs Git Bash")
def test_run_command_runs_through_git_bash(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"))
    target = toolset.workspace.root / "sub dir"
    target.mkdir()
    outcome = toolset.invoke("run_command", {"command": 'echo "$((2+3))" && printf "%s\\n" "a b"'})
    assert outcome.status == "succeeded" and "5" in outcome.content and "a b" in outcome.content
    moved = toolset.invoke("run_command", {"command": f'cd "{host_shell.windows_to_posix_path(target)}" && pwd'})
    assert moved.status == "succeeded"
    assert toolset._current_cwd == target.resolve()
