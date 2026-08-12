"""Filesystem layout for an Orin installation.

Nothing here depends on the current working directory. That is the whole point:
``cd C:\\ && orin`` has to reach exactly the same data as running from the repo,
and a frozen ``orin.exe`` has to reach it without a repository existing at all.

Two layouts exist.

*Development* — a repository checkout is detected around the package. Data stays
in ``<repo>/data`` and logs in ``<repo>/.logs`` so the existing scripts, the
existing ``.gitignore`` entries and the existing local state keep working exactly
as they did before the launcher existed.

*Installed* — no checkout. State goes to the per-user OS locations, outside the
installation directory, so replacing the installation never touches user data.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _environment_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def find_repository_root(start: Path | None = None) -> Path | None:
    """Return the checkout that contains this package, if there is one.

    An editable install still points at ``<repo>/src/agentos``, so this correctly
    reports development for ``pip install -e .`` and reports nothing once the
    package is copied into site-packages or frozen into an executable.
    """
    if getattr(sys, "frozen", False):
        return None
    origin = (start or Path(__file__).resolve().parent).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "agentos").is_dir():
            return candidate
    return None


def _user_state_root() -> Path:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / "Orin"
        return Path.home() / "AppData" / "Local" / "Orin"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "orin"


def _user_config_root() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if base:
            return Path(base) / "Orin"
        return Path.home() / "AppData" / "Roaming" / "Orin"
    return Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "orin"


@dataclass(frozen=True, slots=True)
class OrinPaths:
    """Absolute roots for everything Orin writes."""

    config: Path
    data: Path
    logs: Path
    cache: Path
    run: Path

    @property
    def workspaces(self) -> Path:
        """Agent workspaces. One directory per conversation lives under here."""
        return self.data / "workspaces"

    @property
    def instance_state(self) -> Path:
        return self.run / "instance.json"

    @property
    def instance_lock(self) -> Path:
        return self.run / "instance.lock"

    @property
    def stop_request(self) -> Path:
        return self.run / "stop.request"

    def service_log(self, name: str) -> Path:
        return self.logs / f"{name}.log"

    def ensure(self) -> "OrinPaths":
        for directory in (self.config, self.data, self.logs, self.cache, self.run):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def as_environment(self) -> dict[str, str]:
        """The overrides a child process needs to resolve this exact layout.

        Children are spawned with these set, so a worker never has to guess from
        its own working directory which installation it belongs to.
        """
        return {
            "ORIN_CONFIG_DIR": str(self.config),
            "ORIN_DATA_DIR": str(self.data),
            "ORIN_LOGS_DIR": str(self.logs),
            "ORIN_CACHE_DIR": str(self.cache),
            "ORIN_RUN_DIR": str(self.run),
        }

    @classmethod
    def resolve(cls) -> "OrinPaths":
        repository = find_repository_root()
        home = _environment_path("ORIN_HOME")
        if home is not None:
            defaults = cls(home / "config", home / "data", home / "logs", home / "cache", home / "run")
        elif repository is not None:
            # Match what the repository already uses and already ignores in git.
            defaults = cls(repository, repository / "data", repository / ".logs", repository / ".cache", repository / "data" / "run")
        else:
            state, config = _user_state_root(), _user_config_root()
            defaults = cls(config / "config", state / "data", state / "logs", state / "cache", state / "run")
        return cls(
            config=_environment_path("ORIN_CONFIG_DIR") or defaults.config,
            data=_environment_path("ORIN_DATA_DIR") or defaults.data,
            logs=_environment_path("ORIN_LOGS_DIR") or defaults.logs,
            cache=_environment_path("ORIN_CACHE_DIR") or defaults.cache,
            run=_environment_path("ORIN_RUN_DIR") or defaults.run,
        )


_cached: OrinPaths | None = None


def orin_paths() -> OrinPaths:
    """The layout for this process, resolved once.

    Callers that only need a default path must not create directories as a side
    effect; ``ensure()`` is called explicitly by the launcher and by the stores
    that write.
    """
    global _cached
    if _cached is None:
        _cached = OrinPaths.resolve()
    return _cached


def reset_cached_paths() -> None:
    """Drop the memoized layout. Tests use this after changing the environment."""
    global _cached
    _cached = None


__all__ = ["OrinPaths", "find_repository_root", "orin_paths", "reset_cached_paths"]
