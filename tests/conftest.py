from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Prevent duplicate conftest registration when loaded early via `-p tests.conftest`."""
    pm = early_config.pluginmanager
    mod = pm.get_plugin("tests.conftest")
    if mod is None:
        return
    this_conftest = os.path.normcase(os.path.abspath(__file__))
    pm._name2plugin.setdefault(str(Path(__file__).resolve()), mod)
    orig_importconftest = pm._importconftest

    def _safe_importconftest(conftestpath: Path, *args: object, **kwargs: object) -> object:
        if os.path.normcase(os.path.abspath(conftestpath)) == this_conftest:
            pm._name2plugin.setdefault(str(conftestpath), mod)
            return mod
        return orig_importconftest(conftestpath, *args, **kwargs)

    pm._importconftest = _safe_importconftest  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _isolated_settings_dirs(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep every test away from the developer's real ~/.orin and ~/.claude.

    The settings loader reads user and managed layers from the home directory
    and from machine-wide folders. A test that picked those up would pass or
    fail depending on whose machine runs it.
    """
    if request.node.name == "test_user_dir_env_override_is_respected":
        from agentos.settings.paths import _pytest_fallback_root

        fallback_home = _pytest_fallback_root()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fallback_home))
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

    orin_home = root / "orin-home"
    orin_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORIN_HOME", str(orin_home))
    monkeypatch.setenv("ORIN_CONFIG_DIR", str(root / "orin-config"))
    monkeypatch.setenv("ORIN_LOGS_DIR", str(root / "orin-logs"))
    monkeypatch.setenv("ORIN_CACHE_DIR", str(root / "orin-cache"))
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
    try:
        yield
    finally:
        reset_cached_paths()
        clear_fetch_cache()


def _real_claude_binaries() -> frozenset[str]:
    import shutil

    found: set[str] = set()
    for name in ("claude", "claude.exe", "claude.cmd"):
        hit = shutil.which(name)
        if hit:
            found.add(os.path.normcase(os.path.realpath(hit)))
    return frozenset(found)


@pytest.fixture(autouse=True, scope="session")
def _never_spawn_the_real_claude_cli() -> Iterator[None]:
    """Tests never run the real `claude` CLI: a spawn of the installed binary fails the test instead.

    Fakes (scripts in tmp_path, `C:/fake/claude.exe`, monkeypatched resolvers) stay allowed;
    only the binary found on PATH when the session starts is refused.
    """
    import shlex
    import shutil
    import subprocess

    real = _real_claude_binaries()
    if not real:
        yield
        return
    original = subprocess.Popen

    def _first_arg(args: object) -> str:
        if isinstance(args, (str, bytes, os.PathLike)):
            text = os.fsdecode(args)
            try:
                parts = shlex.split(text, posix=os.name != "nt")
            except ValueError:
                parts = text.split()
            return parts[0] if parts else ""
        try:
            seq = list(args)  # type: ignore[call-overload]
        except TypeError:
            return ""
        return os.fsdecode(seq[0]) if seq else ""

    class _GuardedPopen(original):  # type: ignore[misc, valid-type]
        def __init__(self, args: object, *a: object, **kw: object) -> None:
            first = _first_arg(kw.get("executable") or args)
            if first:
                located = first if os.path.isabs(first) else (shutil.which(first) or first)
                if os.path.normcase(os.path.realpath(located)) in real:
                    raise RuntimeError(f"tests must never run the real Claude CLI: {first!r}")
            super().__init__(args, *a, **kw)  # type: ignore[arg-type]

    subprocess.Popen = _GuardedPopen  # type: ignore[misc]
    try:
        yield
    finally:
        subprocess.Popen = original  # type: ignore[misc]
