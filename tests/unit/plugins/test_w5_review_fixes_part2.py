"""W5 review fixes, part 2 (docs/superpowers/reviews/2026-10-19-w5-review.md): plugin hook commands (W5-R14) and the
plugin fetcher's links (W5-R15)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentos.plugins.fetcher import FetchRejected, PluginFetcher
from agentos.plugins.hook_executor import HookRejected, resolve_argv
from agentos.plugins.sources import resolve_source


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


# W5-R15: a junction in a local plugin folder ----------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows link (no admin rights needed)")
def test_a_junction_in_a_local_package_refuses_the_package(tmp_path: Path) -> None:
    import _winapi

    outside = tmp_path / "outside-secrets"
    outside.mkdir()
    (outside / "id_rsa").write_text("PRIVATE KEY (stand-in)", encoding="utf-8")
    package = tmp_path / "pkg"
    (package / ".claude-plugin").mkdir(parents=True)
    (package / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kit", "version": "1.0.0"}), encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(package / "linked"))
    with pytest.raises(FetchRejected, match="unsafe path or symlink"):
        PluginFetcher(tmp_path / "plugins").fetch(resolve_source(str(package)))
    plugins = tmp_path / "plugins"
    assert not plugins.exists() or not list(plugins.rglob("id_rsa"))  # nothing was installed
