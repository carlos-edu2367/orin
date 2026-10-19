"""W5 review fixes, part 2 (docs/superpowers/reviews/2026-10-19-w5-review.md): plugin hook commands (W5-R14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.plugins.hook_executor import HookRejected, resolve_argv


def _package_in(tmp_path: Path) -> Path:
    # Installed under a folder with a space, as under a Windows profile such as C:\Users\Ana Souza.
    root = tmp_path / "Ana Souza" / "AppData" / "Local" / "orin" / "plugins" / "kit" / "1.0.0"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "check.py").write_text("print(1)", encoding="utf-8")
    return root


@pytest.mark.parametrize("command", [
    "python ${CLAUDE_PLUGIN_ROOT}/hooks/check.py",
    "python ${ORIN_PLUGIN_ROOT}/hooks/check.py",
    'python "${CLAUDE_PLUGIN_ROOT}/hooks/check.py"',
    '"${CLAUDE_PLUGIN_ROOT}/hooks/check.py"',
])
def test_a_plugin_folder_with_a_space_still_runs_its_script(tmp_path: Path, command: str) -> None:
    root = _package_in(tmp_path)
    argv = resolve_argv(command, install_path=root)
    assert argv[-1] == str((root / "hooks" / "check.py").resolve())
    assert len(argv) == (1 if command.startswith('"') else 2)


def test_the_folder_substitution_keeps_the_confinement(tmp_path: Path) -> None:
    root = _package_in(tmp_path)
    (root.parent / "outside.py").write_text("print(1)", encoding="utf-8")  # next to the package, not inside it
    for command in ("python ${CLAUDE_PLUGIN_ROOT}/../outside.py", '"${CLAUDE_PLUGIN_ROOT}/../outside.py"'):
        with pytest.raises(HookRejected):
            resolve_argv(command, install_path=root)
