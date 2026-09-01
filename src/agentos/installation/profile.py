"""What kind of Orin installation this process belongs to.

The launcher never asks "am I in the repository?" directly. It asks the profile
for a web bundle, a migrations directory, a command that starts another Orin
service, and gets an answer that is correct for a checkout, for a plain
``pip install``, and for a frozen ``orin.exe`` alike. Freezing later is then a
packaging change rather than a launcher change.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import find_repository_root


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _version() -> str:
    # Frozen builds can contain stale ``agentos-*.dist-info`` copied from the
    # build environment. The package constant is therefore the runtime source
    # of truth for both source and packaged launchers.
    from agentos.version import __version__

    return __version__


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """The read-only side of an installation."""

    kind: str
    """``development`` for a checkout, ``installed`` otherwise."""

    root: Path
    """Installation root. Treated as read-only; never written to at runtime."""

    version: str
    repository: Path | None

    @property
    def is_development(self) -> bool:
        return self.kind == "development"

    @property
    def migrations(self) -> Path:
        """Alembic scripts, always resolved from the package, never from the cwd."""
        return _package_root() / "persistence" / "postgres" / "migrations"

    @property
    def web_dist(self) -> Path | None:
        """The built SPA the backend serves.

        There is no frontend dev server in the runtime. A development checkout
        uses ``frontend/dist``; an installation ships the same build as a static
        asset directory. Either way the backend serves files, not a bundler.
        """
        candidates = (
            [self.repository / "frontend" / "dist"] if self.repository is not None else []
        ) + [self.root / "web", self.root / "_internal" / "web", _package_root() / "web"]
        for candidate in candidates:
            if (candidate / "index.html").is_file():
                return candidate
        return candidates[0] if candidates else None

    def environment_files(self, config_dir: Path) -> tuple[Path, ...]:
        """Configuration files to load, lowest precedence first."""
        candidates = [config_dir / "orin.env", config_dir / ".env.local"]
        if self.repository is not None:
            candidates.append(self.repository / ".env.local")
        seen: dict[Path, None] = {}
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen and candidate.is_file():
                seen[resolved] = None
        return tuple(seen)

    def service_command(self, service: str) -> tuple[str, ...]:
        """The command that starts one Orin service as a child process.

        Orin re-executes *itself* with a hidden verb rather than invoking
        ``python -m ...`` against a source tree. A frozen build changes only the
        first element of this tuple.
        """
        if getattr(sys, "frozen", False):
            return (sys.executable, "internal-service", service)
        return (sys.executable, "-m", "agentos.launcher", "internal-service", service)

    def launcher_command(self) -> tuple[str, ...]:
        """The public launcher command, used by the desktop retry action."""
        if getattr(sys, "frozen", False):
            return (sys.executable,)
        return (sys.executable, "-m", "agentos.launcher")

    @property
    def installer(self) -> Path:
        """The verified release installer shipped beside a frozen runtime."""
        name = "install.ps1" if os.name == "nt" else "install.sh"
        candidates = [self.root / name, self.root / "_internal" / name]
        if self.repository is not None:
            candidates.append(self.repository / name)
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])

    @classmethod
    def detect(cls) -> "RuntimeProfile":
        repository = find_repository_root()
        if repository is not None:
            return cls("development", repository, _version(), repository)
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _package_root()
        return cls("installed", root, _version(), None)


_cached: RuntimeProfile | None = None


def runtime_profile() -> RuntimeProfile:
    global _cached
    if _cached is None:
        _cached = RuntimeProfile.detect()
    return _cached


def reset_cached_profile() -> None:
    """Drop the memoized profile. Tests use this after changing the environment."""
    global _cached
    _cached = None


__all__ = ["RuntimeProfile", "reset_cached_profile", "runtime_profile"]
