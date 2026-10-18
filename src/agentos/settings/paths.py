"""Where each settings layer lives, for Orin and (read-only) for Claude Code."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def orin_user_dir() -> Path:
    return _env_path("ORIN_USER_DIR") or Path.home() / ".orin"


def claude_user_dir() -> Path:
    return _env_path("ORIN_CLAUDE_HOME") or Path.home() / ".claude"


def orin_managed_dir() -> Path:
    override = _env_path("ORIN_MANAGED_DIR")
    if override is not None:
        return override
    if os.name == "nt":
        return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Orin"
    return Path("/etc/orin")


def claude_managed_dir() -> Path:
    override = _env_path("ORIN_CLAUDE_MANAGED_DIR")
    if override is not None:
        return override
    if os.name == "nt":
        return Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "ClaudeCode"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    return Path("/etc/claude-code")


def workspace_grants_file(project_root: Path) -> Path:
    """User-owned per-workspace grants ("always allow"), outside the repository."""
    canonical = os.path.normcase(str(Path(project_root).resolve()))
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return orin_user_dir() / "workspaces" / key / "settings.local.json"


def _managed_files(directory: Path) -> list[Path]:
    files = [directory / "managed-settings.json"]
    dropins = directory / "managed-settings.d"
    try:
        extra = sorted(
            (item for item in dropins.iterdir() if item.is_file() and item.suffix == ".json" and not item.name.startswith(".")),
            key=lambda item: item.name,
        )
    except OSError:
        extra = []
    files.extend(extra)
    return files


@dataclass(frozen=True, slots=True)
class SettingsPaths:
    project_root: Path | None

    def files_for(self, layer: str) -> tuple[Path, ...]:
        """Files of one layer, lowest precedence first (Claude before Orin)."""
        if layer == "user":
            return (claude_user_dir() / "settings.json", orin_user_dir() / "settings.json")
        if layer == "managed":
            return (*_managed_files(claude_managed_dir()), *_managed_files(orin_managed_dir()))
        if self.project_root is None:
            return ()
        root = Path(self.project_root)
        if layer == "project":
            return (root / ".claude" / "settings.json", root / ".orin" / "settings.json")
        if layer == "local":
            return (
                root / ".claude" / "settings.local.json",
                root / ".orin" / "settings.local.json",
                workspace_grants_file(root),
            )
        return ()

    def writable_file(self, layer: str) -> Path:
        if layer == "user":
            return orin_user_dir() / "settings.json"
        if self.project_root is None:
            raise ValueError("a project root is required for project and local settings")
        if layer == "project":
            return Path(self.project_root) / ".orin" / "settings.json"
        if layer == "local":
            return workspace_grants_file(Path(self.project_root))
        raise ValueError(f"settings layer '{layer}' is not writable")
