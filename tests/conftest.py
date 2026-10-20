from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the developer's real ~/.orin and ~/.claude.

    The settings loader reads user and managed layers from the home directory
    and from machine-wide folders. A test that picked those up would pass or
    fail depending on whose machine runs it.
    """
    root: Path = tmp_path_factory.mktemp("settings-home")
    for name, sub in (
        ("ORIN_USER_DIR", "orin-user"),
        ("ORIN_CLAUDE_HOME", "claude-user"),
        ("ORIN_MANAGED_DIR", "orin-managed"),
        ("ORIN_CLAUDE_MANAGED_DIR", "claude-managed"),
    ):
        target = root / sub
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    # The run directory holds Orin's runtime markers (the workspace locks among them): never the developer's own.
    from agentos.installation.paths import reset_cached_paths

    monkeypatch.setenv("ORIN_RUN_DIR", str(root / "orin-run"))
    # And the data folder (W6): in a checkout it is the repository's data/, the developer's own Orin data.
    monkeypatch.setenv("ORIN_DATA_DIR", str(root / "orin-data"))
    # Claude Code's ~/.claude.json is found next to ORIN_CLAUDE_HOME (inside this temporary folder) unless overridden.
    monkeypatch.delenv("ORIN_CLAUDE_CONFIG", raising=False)
    reset_cached_paths()
    # Grep must not depend on a ripgrep the developer configured; the ripgrep tests set it themselves.
    monkeypatch.delenv("ORIN_RIPGREP_PATH", raising=False)
    # WebFetch's page cache is process-wide: a page cached by one test must not answer another.
    from agentos.agentic.tools.web.fetch import clear_fetch_cache

    clear_fetch_cache()
