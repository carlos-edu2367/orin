from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agentos.settings.loader import load_effective_settings
from agentos.settings.paths import (
    SettingsPaths,
    claude_managed_dir,
    claude_user_config_file,
    claude_user_dir,
    orin_managed_dir,
    orin_user_dir,
    trust_store_file,
)


def test_pytest_fallback_never_returns_real_home_dirs_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: even if ORIN_USER_DIR and ORIN_CLAUDE_HOME are unset or blank,
    running under pytest must never resolve to ~/.orin, ~/.claude, or system managed dirs.
    """
    for env_var in (
        "ORIN_USER_DIR",
        "ORIN_CLAUDE_HOME",
        "ORIN_CLAUDE_CONFIG",
        "ORIN_MANAGED_DIR",
        "ORIN_CLAUDE_MANAGED_DIR",
    ):
        monkeypatch.delenv(env_var, raising=False)

    real_orin = (Path.home() / ".orin").resolve()
    real_claude = (Path.home() / ".claude").resolve()
    expected_root = (
        Path(tempfile.gettempdir()) / f"orin-pytest-fallback-{os.getpid()}"
    ).resolve()

    resolved_orin = orin_user_dir().resolve()
    resolved_claude = claude_user_dir().resolve()
    resolved_claude_cfg = claude_user_config_file().resolve()
    resolved_orin_managed = orin_managed_dir().resolve()
    resolved_claude_managed = claude_managed_dir().resolve()
    resolved_trust = trust_store_file().resolve()

    assert resolved_orin != real_orin
    assert resolved_claude != real_claude
    assert resolved_orin == expected_root / ".orin"
    assert resolved_claude == expected_root / ".claude"
    assert resolved_claude_cfg == expected_root / ".claude.json"
    assert resolved_orin_managed == expected_root / "orin-managed"
    assert resolved_claude_managed == expected_root / "claude-managed"
    assert resolved_trust == expected_root / ".orin" / "trusted-workspaces.json"

    # Adversarial check: whitespace-only env vars must also fall back to the safe pytest temp dir
    monkeypatch.setenv("ORIN_USER_DIR", "   ")
    monkeypatch.setenv("ORIN_CLAUDE_HOME", " \t ")
    assert orin_user_dir().resolve() == expected_root / ".orin"
    assert claude_user_dir().resolve() == expected_root / ".claude"


def test_conftest_isolates_all_orin_installation_env_vars() -> None:
    """tests/conftest.py must isolate ORIN_HOME, ORIN_CONFIG_DIR, ORIN_LOGS_DIR, and ORIN_CACHE_DIR."""
    for env_var in (
        "ORIN_HOME",
        "ORIN_CONFIG_DIR",
        "ORIN_DATA_DIR",
        "ORIN_LOGS_DIR",
        "ORIN_CACHE_DIR",
        "ORIN_RUN_DIR",
    ):
        value = os.environ.get(env_var, "")
        assert value, f"{env_var} should be set by _isolated_settings_dirs fixture"


def test_real_user_home_directories_have_zero_settings_errors_and_zero_pytest_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The developer's real ~/.orin and ~/.claude must be clean of pytest pollution
    and malformed JSON settings files.
    """
    real_orin_dir = Path.home() / ".orin"
    real_claude_dir = Path.home() / ".claude"

    paths = SettingsPaths(
        project_root=None,
        user_files=(
            real_claude_dir / "settings.json",
            real_orin_dir / "settings.json",
        ),
    )
    assert paths.files_for("user") == (
        real_claude_dir / "settings.json",
        real_orin_dir / "settings.json",
    )

    monkeypatch.setenv("ORIN_USER_DIR", str(real_orin_dir))
    monkeypatch.setenv("ORIN_CLAUDE_HOME", str(real_claude_dir))

    effective = load_effective_settings(None)
    assert effective.errors == (), f"Unexpected settings load errors in real user dirs: {effective.errors}"

    hooks_dump = json.dumps(effective.data.get("hooks", {}))
    assert "pytest-" not in hooks_dump, f"Polluted pytest hook still present in real user settings: {hooks_dump}"
    assert "unguarded-hook-ran.txt" not in hooks_dump

    orin_md = real_orin_dir / "ORIN.md"
    if orin_md.exists():
        assert "Always run the tests before saying a task is done." not in orin_md.read_text(encoding="utf-8")

    claude_md = real_claude_dir / "CLAUDE.md"
    if claude_md.exists():
        assert "Never run git push --force. Ask before deleting files." not in claude_md.read_text(encoding="utf-8")

    trusted_file = real_orin_dir / "trusted-workspaces.json"
    if trusted_file.exists():
        assert "pytest-" not in trusted_file.read_text(encoding="utf-8")

    polluted_local = real_orin_dir / "workspaces" / "4a64e69624209f9a" / "settings.local.json"
    assert not polluted_local.exists()


def test_standalone_pytest_outside_tests_dir_loads_conftest_and_isolates_dirs(
    tmp_path: Path,
) -> None:
    """Adversarial check: running pytest on a script outside `tests/` still loads
    `tests.conftest` via pyproject.toml `addopts = ["-p", "tests.conftest"]`.
    """
    repo_root = Path(__file__).resolve().parents[3]
    standalone = tmp_path / "test_standalone_outside_tests.py"
    standalone.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from agentos.settings.paths import orin_user_dir, claude_user_dir\n"
        "def test_isolated():\n"
        "    assert orin_user_dir().resolve() != (Path.home() / '.orin').resolve()\n"
        "    assert claude_user_dir().resolve() != (Path.home() / '.claude').resolve()\n"
        "    assert os.environ.get('ORIN_HOME')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in (
        "ORIN_USER_DIR",
        "ORIN_CLAUDE_HOME",
        "ORIN_HOME",
        "ORIN_CONFIG_DIR",
        "ORIN_DATA_DIR",
        "ORIN_LOGS_DIR",
        "ORIN_CACHE_DIR",
        "ORIN_RUN_DIR",
    ):
        env.pop(key, None)

    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(repo_root)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(repo_root / "pyproject.toml"),
            f"--rootdir={tmp_path}",
            str(standalone),
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Standalone pytest failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
