from __future__ import annotations

import pytest

from agentos.permissions.shell import analyze_command, default_shell


def subs(command: str, shell: str = "posix") -> tuple[str, ...]:
    return analyze_command(command, shell=shell).subcommands


def is_complex(command: str, shell: str = "posix") -> bool:
    return analyze_command(command, shell=shell).complex


def test_default_shell_matches_platform() -> None:
    import os

    assert default_shell() == ("cmd" if os.name == "nt" else "posix")


@pytest.mark.parametrize("shell", ["posix", "cmd"])
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", ("git status",)),
        ("  git   status  ", ("git status",)),
        ("git status && rm -rf build", ("git status", "rm -rf build")),
        ("git status || rm x", ("git status", "rm x")),
        ("git status ; rm x", ("git status", "rm x")),
        ("git log | head", ("git log", "head")),
        ("npm test & calc", ("npm test", "calc")),
        ("git status\nrm -rf /", ("git status", "rm -rf /")),
        ("git status\r\nrm -rf /", ("git status", "rm -rf /")),
        ("", ()),
    ],
)
def test_operators_split_subcommands(shell: str, command: str, expected: tuple[str, ...]) -> None:
    assert subs(command, shell) == expected


@pytest.mark.parametrize(
    "command",
    [
        "git log $(evil)",
        "git log `evil`",
        'git log "$(evil)"',
        'echo "$HOME"',
        "echo $HOME",
        "cat <(evil)",
        "diff >(evil) x",
        "echo hi > out.txt",
        "echo hi >> out.txt",
        "cat < secret",
        "cat <<EOF",
        "(cd x && rm y)",
        "{ rm y; }",
        "FOO=bar git status",
        "git status \\; rm x",
        "echo 'unterminated",
        'echo "unterminated',
        "echo a\x00b",
        "x" * 20001,
    ],
)
def test_posix_complex_constructs(command: str) -> None:
    assert is_complex(command, "posix"), command


@pytest.mark.parametrize(
    "command",
    [
        "echo %PATH%",
        "echo !VAR!",
        "dir ^& calc",
        "(dir)",
        "echo hi > out.txt",
        'echo "unterminated',
        'echo "%USERPROFILE%"',
    ],
)
def test_cmd_complex_constructs(command: str) -> None:
    assert is_complex(command, "cmd"), command


@pytest.mark.parametrize(
    ("command", "shell", "expected"),
    [
        ("pytest 2>&1", "posix", ("pytest",)),
        ("npm test > /dev/null 2>&1", "posix", ("npm test",)),
        ("npm test &>/dev/null", "posix", ("npm test",)),
        ("npm test 2>nul", "cmd", ("npm test",)),
        ("npm test >nul 2>&1", "cmd", ("npm test",)),
    ],
)
def test_harmless_redirects_are_not_complex(command: str, shell: str, expected: tuple[str, ...]) -> None:
    analysis = analyze_command(command, shell=shell)
    assert not analysis.complex, analysis.reasons
    assert analysis.subcommands == expected


def test_quotes_protect_operators_in_posix_but_not_single_quotes_in_cmd() -> None:
    posix = analyze_command("echo 'a && b' \"c | d\"", shell="posix")
    assert posix.subcommands == ("echo 'a && b' \"c | d\"",) and not posix.complex
    cmd = analyze_command("echo 'a & calc'", shell="cmd")
    assert cmd.subcommands == ("echo 'a", "calc'")  # cmd.exe has no single quotes
    assert analyze_command('echo "a & b"', shell="cmd").subcommands == ('echo "a & b"',)


def test_posix_comment_is_dropped_and_background_amp_splits() -> None:
    assert subs("ls # list files") == ("ls",)
    assert subs("sleep 1 & echo done") == ("sleep 1", "echo done")


def test_non_string_is_complex() -> None:
    assert analyze_command(None).complex  # type: ignore[arg-type]


@pytest.mark.parametrize("sep", [" ", "\t"])
def test_escaped_blank_does_not_open_a_comment(sep: str) -> None:
    # W1-R1: after "\<space>" the "#" is still part of the word for /bin/sh, so the
    # appended command runs. The analyzer must keep it and mark the line complex.
    analysis = analyze_command(f"git status \\{sep}#; rm -rf ~", shell="posix")
    assert analysis.complex is True
    assert "rm -rf ~" in analysis.subcommands


def test_hash_after_unescaped_blank_is_still_a_comment() -> None:
    analysis = analyze_command("git status # note; rm -rf ~", shell="posix")
    assert analysis.subcommands == ("git status",)
    assert analysis.complex is False
