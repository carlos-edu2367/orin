"""W4: PowerShell commands are analysed conservatively, and rules match them per simple command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.permissions.engine import PermissionEngine
from agentos.permissions.powershell import analyze_powershell, canonical_cmdlet
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir

SIMPLE = [
    ("Get-ChildItem src", ("Get-ChildItem src",)),
    ("Get-Content a.txt | Select-String foo", ("Get-Content a.txt", "Select-String foo")),
    ("Get-Date; Remove-Item x", ("Get-Date", "Remove-Item x")),
    ("Write-Output 'a;b'", ("Write-Output 'a;b'",)),
    ("Write-Output 'it''s'", ("Write-Output 'it''s'",)),
    ("Get-Date # note; Remove-Item x", ("Get-Date",)),
    ("git status 2>&1", ("git status 2>&1",)),
    ("Get-Item x 2>$null", ("Get-Item x 2>$null",)),
]
COMPLEX = [
    "$x = 1",
    'Write-Output "$env:SECRET"',
    "Get-ChildItem | Where-Object { $_.Length -gt 1 }",
    "& calc",
    ". ./profile.ps1",
    "iex 'Remove-Item x'",
    "Invoke-Expression 'Remove-Item x'",
    "Write-Output `\nRemove-Item x",
    "Get-Date $(Remove-Item x)",
    "@'\nx\n'@",
    "powershell -EncodedCommand AAAA",
    "cmd /c del x",
    "echo x > out.txt",
    "Start-Process notepad",
    "Write-Output ‘a; Remove-Item x’",
    "Write-Output 'unterminated",
]


@pytest.mark.parametrize(("command", "subcommands"), SIMPLE)
def test_simple_commands(command: str, subcommands: tuple[str, ...]) -> None:
    analysis = analyze_powershell(command)
    assert not analysis.complex, analysis.reasons
    assert analysis.subcommands == subcommands


@pytest.mark.parametrize("command", COMPLEX)
def test_anything_unclear_is_complex(command: str) -> None:
    assert analyze_powershell(command).complex


def test_redirect_targets_are_recorded() -> None:
    assert analyze_powershell("Write-Output x > .git/hooks/pre-commit").redirect_targets == (".git/hooks/pre-commit",)
    assert canonical_cmdlet("RM") == "remove-item" and canonical_cmdlet("Get-Date") == "get-date"


def _engine(root: Path, rules: dict) -> PermissionEngine:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": rules}), encoding="utf-8")
    return PermissionEngine(root, load_effective_settings(root, trusted=False), shell="cmd", home=root.parent / "home")


def test_an_allow_rule_approves_only_simple_matching_commands(tmp_path: Path) -> None:
    engine = _engine(tmp_path, {"allow": ["PowerShell(Get-ChildItem:*)"]})
    decide = lambda command: engine.decide("powershell", {"command": command}, "ask_permissions").behavior  # noqa: E731
    assert decide("Get-ChildItem src") == "allow"
    assert decide("get-childitem src") == "allow"
    assert decide("Get-ChildItem src; Remove-Item x") == "ask"
    assert decide("Get-ChildItem $(Remove-Item x)") == "ask"
    assert decide("ls src") == "ask"  # an alias never widens an allow


def test_a_deny_rule_catches_aliases_case_and_compounds(tmp_path: Path) -> None:
    engine = _engine(tmp_path, {"deny": ["PowerShell(Remove-Item:*)"]})
    for command in ("Remove-Item x", "remove-item x", "rm x", "del x", "Get-Date; ri x"):
        assert engine.decide("powershell", {"command": command}, "full_autonomy").behavior == "deny", command


def test_bash_rules_do_not_apply_to_powershell(tmp_path: Path) -> None:
    engine = _engine(tmp_path, {"allow": ["Bash(git status:*)"]})
    assert engine.decide("powershell", {"command": "git status"}, "ask_permissions").behavior == "ask"


def test_a_redirect_into_a_protected_path_needs_a_person(tmp_path: Path) -> None:
    decision = _engine(tmp_path, {}).decide("powershell", {"command": "Write-Output x > .git/hooks/pre-commit"}, "ask_permissions")
    assert decision.behavior == "ask" and decision.requires_human


def test_bash_deny_rules_also_stop_powershell(tmp_path: Path) -> None:
    engine = _engine(tmp_path, {"deny": ["Bash(rm:*)"]})
    for command in ("rm x", "del x", "Remove-Item x", "Get-Date; ri x"):
        assert engine.decide("powershell", {"command": command}, "full_autonomy").behavior == "deny", command
    assert engine.decide("run_command", {"command": "rm x"}, "full_autonomy").behavior == "deny"


def test_bash_ask_rules_also_ask_for_powershell(tmp_path: Path) -> None:
    decision = _engine(tmp_path, {"ask": ["Bash(git push:*)"]}).decide("powershell", {"command": "git push origin"}, "full_autonomy")
    assert decision.behavior == "ask" and decision.requires_human


def test_bash_allow_rules_still_do_not_cross_over(tmp_path: Path) -> None:
    engine = _engine(tmp_path, {"allow": ["Bash(git status:*)"]})
    assert engine.decide("powershell", {"command": "git status"}, "ask_permissions").behavior == "ask"
