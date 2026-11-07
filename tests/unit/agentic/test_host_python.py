"""The prompt never advertises the Microsoft Store `python` stub, and Git Bash path rewriting is explained."""
from __future__ import annotations

from pathlib import Path

from agentos.host_python import (
    GIT_BASH_PATH_HINT, environment_notes, is_store_stub, posix_path_hint, resolve_python,
)

STUB = r"C:\Users\carlos\AppData\Local\Microsoft\WindowsApps\python.exe"


def _which(**found):
    return lambda name: found.get(name)


def test_store_alias_is_detected_by_path_or_zero_size(tmp_path: Path) -> None:
    assert is_store_stub(STUB) and is_store_stub("/c/Users/x/AppData/Local/Microsoft/WindowsApps/python")
    empty = tmp_path / "python.exe"
    empty.write_bytes(b"")
    real = tmp_path / "real.exe"
    real.write_bytes(b"MZ")
    assert is_store_stub(str(empty)) and not is_store_stub(str(real)) and not is_store_stub(None)


def test_a_working_python_is_reported_with_its_own_version() -> None:
    text, note = resolve_python(which=_which(python=r"C:\Python313\python.exe"), version_of=lambda argv: "3.13.1")
    assert text.startswith("3.13.1") and note == ""


def test_the_stub_is_replaced_by_the_py_launcher_first() -> None:
    seen: list[list[str]] = []

    def version_of(argv):
        seen.append(argv)
        return "3.12.4"

    text, note = resolve_python(which=_which(python=STUB, py="C:/Windows/py.exe", uv="uv"), version_of=version_of)
    assert seen == [["py"]] and "3.12.4" in text and "via `py`" in text
    assert "Microsoft Store alias (does not run)" in note and "`py" in note and "13.17" not in text


def test_uv_then_the_project_venv_are_the_fallbacks(tmp_path: Path) -> None:
    text, note = resolve_python(which=_which(python=STUB, uv="uv"), version_of=lambda argv: "3.13.5" if argv[0] == "uv" else "")
    assert "uv run python" in text and "uv run python" in note
    scripts = tmp_path / ".venv" / ("Scripts" if __import__("os").name == "nt" else "bin")
    scripts.mkdir(parents=True)
    (scripts / ("python.exe" if __import__("os").name == "nt" else "python")).write_bytes(b"x")
    text, note = resolve_python(which=_which(python=STUB), version_of=lambda argv: "3.11.9", project_root=tmp_path)
    assert ".venv" in text and "3.11.9" in text and "Microsoft Store" in note


def test_a_stub_with_nothing_else_says_so_instead_of_inventing_a_version() -> None:
    text, note = resolve_python(which=_which(python=STUB), version_of=lambda argv: "", project_root=Path("Z:/nope"))
    assert text == "none runnable" and "Microsoft Store" in note


def test_environment_notes_include_the_git_bash_line_only_for_git_bash() -> None:
    assert environment_notes({"python_note": "n", "shell_kind": "git_bash"})[1].startswith("Git Bash rewrites POSIX paths")
    assert environment_notes({"python_note": "", "shell_kind": "cmd"}) == []


def test_hint_for_posix_paths_given_to_native_programs() -> None:
    assert posix_path_hint("node script.js /c/tmp/q.sql", windows=True) == GIT_BASH_PATH_HINT
    assert posix_path_hint("cd x && python /tmp/a.py", windows=True) == GIT_BASH_PATH_HINT
    assert posix_path_hint("FOO=1 py /d/data/x.csv", windows=True)
    assert "C:/tmp/q.sql" in GIT_BASH_PATH_HINT


def test_no_hint_for_msys_programs_windows_style_paths_or_other_systems() -> None:
    assert posix_path_hint("cat /c/tmp/q.sql", windows=True) == ""
    assert posix_path_hint("node C:/tmp/q.sql", windows=True) == ""
    assert posix_path_hint("node ./src/index.js --flag /v", windows=True) == ""
    assert posix_path_hint("node /c/tmp/q.sql", windows=False) == ""


def test_a_failed_bash_command_gets_the_hint_appended(monkeypatch, tmp_path: Path) -> None:
    import agentos.agentic.tools.shell.bash as bash

    monkeypatch.setattr(bash, "posix_path_hint", lambda command: GIT_BASH_PATH_HINT)
    from agentos.agentic.agent_tools import AgentToolset
    from agentos.agentic.workspace import ConversationWorkspace

    toolset = AgentToolset(ConversationWorkspace(tmp_path), enable_terminal=True)
    result = toolset.run_command("exit 3")
    assert result["exit_code"] == 3 and result["content"].rstrip().endswith(GIT_BASH_PATH_HINT)
    ok = toolset.run_command("exit 0")
    assert GIT_BASH_PATH_HINT not in ok["content"]
