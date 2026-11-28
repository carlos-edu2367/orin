"""Where each settings layer lives, for Orin and (read-only) for Claude Code and standard skill harnesses."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _is_running_under_pytest() -> bool:
    return (
        "pytest" in sys.modules
        or "_pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def _pytest_fallback_root() -> Path:
    return Path(tempfile.gettempdir()) / f"orin-pytest-fallback-{os.getpid()}"


def orin_user_dir() -> Path:
    override = _env_path("ORIN_USER_DIR")
    if override is not None:
        return override
    if _is_running_under_pytest():
        return _pytest_fallback_root() / ".orin"
    return Path.home() / ".orin"


def claude_user_dir() -> Path:
    override = _env_path("ORIN_CLAUDE_HOME")
    if override is not None:
        return override
    if _is_running_under_pytest():
        return _pytest_fallback_root() / ".claude"
    return Path.home() / ".claude"


def agents_user_dir() -> Path:
    """Canonical cross-harness user skills directory (``~/.agents``, used by ``npx skills add -g``)."""
    override = _env_path("ORIN_AGENTS_HOME")
    if override is not None:
        return override
    if _is_running_under_pytest():
        return _pytest_fallback_root() / ".agents"
    return Path.home() / ".agents"


def _skills_home_dir() -> Path:
    """Home root for discovering external harness skill folders (Codex, Cursor, OpenCode, etc.)."""
    override = _env_path("ORIN_SKILLS_HOME")
    if override is not None:
        return override
    agents_override = _env_path("ORIN_AGENTS_HOME")
    if agents_override is not None:
        return agents_override.parent
    if _is_running_under_pytest():
        return _pytest_fallback_root()
    return Path.home()


def _dedupe_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return tuple(result)


def user_skill_dirs() -> tuple[Path, ...]:
    """Global/user skill directories across Orin and standard harnesses (``npx skills add -g``), precedence order."""
    home = _skills_home_dir()
    return _dedupe_paths((
        orin_user_dir() / "skills",
        agents_user_dir() / "skills",
        home / ".config" / "agents" / "skills",
        claude_user_dir() / "skills",
        home / ".codex" / "skills",
        home / ".cursor" / "skills",
        home / ".gemini" / "antigravity" / "skills",
        home / ".agent" / "skills",
        home / ".config" / "opencode" / "skills",
        home / ".copilot" / "skills",
        home / ".codeium" / "windsurf" / "skills",
    ))


def project_skill_dirs(workspace_root: Path) -> tuple[Path, ...]:
    """Workspace/project skill directories across Orin and standard harnesses (``npx skills add``), precedence order."""
    root = Path(workspace_root)
    return _dedupe_paths((
        root / ".orin" / "skills",
        root / ".agents" / "skills",
        root / ".claude" / "skills",
        root / ".codex" / "skills",
        root / ".cursor" / "skills",
        root / ".agent" / "skills",
        root / ".opencode" / "skills",
        root / ".github" / "skills",
        root / ".windsurf" / "skills",
    ))


def claude_user_config_file() -> Path:
    """Claude Code's ``~/.claude.json`` (its MCP servers and per-folder choices), next to its ``~/.claude`` folder."""
    return _env_path("ORIN_CLAUDE_CONFIG") or claude_user_dir().parent / ".claude.json"


def trust_store_file() -> Path:
    """User-owned record of trusted workspaces, inside the user's Orin directory."""
    return orin_user_dir() / "trusted-workspaces.json"


def orin_managed_dir() -> Path:
    override = _env_path("ORIN_MANAGED_DIR")
    if override is not None:
        return override
    if _is_running_under_pytest():
        return _pytest_fallback_root() / "orin-managed"
    if os.name == "nt":
        return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Orin"
    return Path("/etc/orin")


def claude_managed_dir() -> Path:
    override = _env_path("ORIN_CLAUDE_MANAGED_DIR")
    if override is not None:
        return override
    if _is_running_under_pytest():
        return _pytest_fallback_root() / "claude-managed"
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
    project_root: Path | None = None
    user_files: tuple[Path, ...] | None = None

    def files_for(self, layer: str) -> tuple[Path, ...]:
        """Files of one layer, lowest precedence first (Claude before Orin)."""
        if layer == "user":
            if self.user_files is not None:
                return tuple(Path(item) for item in self.user_files)
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
