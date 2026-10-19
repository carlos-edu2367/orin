"""Additional working directories (W4): folders besides the workspace that this conversation's tools may use.

They come from ``permissions.additionalDirectories`` (the user, CLI and managed layers; a repository's list
only once the workspace is trusted, which the settings loader enforces) and from the directories a person
added to the conversation (``/add-dir``). Each one is resolved and checked before use: it must exist and be
a directory, and it may not be a filesystem or drive root, a network path, the home folder or a folder that
contains it, or one of Orin's (or Claude Code's) own configuration and data folders. A rejected entry is
skipped, never widened to something else.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ADDITIONAL_DIRECTORIES = 20
MAX_DIRECTORY_CHARS = 1024
# Credential folders under the home folder: never a working directory, nor a folder that contains one.
SECRET_HOME_FOLDERS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config/gcloud", ".password-store")


class DirectoryError(ValueError):
    """The folder cannot be a working directory; the message says why."""


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _guarded_folders() -> list[Path]:
    from agentos.settings.paths import claude_user_dir, orin_managed_dir, orin_user_dir

    folders = [orin_user_dir(), claude_user_dir(), orin_managed_dir()]
    try:
        from agentos.installation.paths import orin_paths

        layout = orin_paths()
        folders += [layout.config, layout.data, layout.logs, layout.cache, layout.run]
    except Exception:  # noqa: BLE001 - an unknown layout only means fewer folders to compare against
        logger.debug("Orin's folder layout is unavailable", exc_info=True)
    out: list[Path] = []
    for folder in folders:
        try:
            out.append(Path(folder).resolve())
        except OSError:
            continue
    return out


def check_directory(raw: str, *, workspace_root: Path) -> Path:
    """The resolved folder ``raw`` names, or ``DirectoryError`` saying why it cannot be a working directory."""
    text = str(raw or "").strip()
    if not text or len(text) > MAX_DIRECTORY_CHARS or "\x00" in text:
        raise DirectoryError("a directory path is required")
    if text.startswith(("\\\\", "//")):
        raise DirectoryError("network paths cannot be working directories")
    path = Path(os.path.expanduser(text)) if text.startswith("~") else Path(text)
    if not path.is_absolute():
        path = Path(workspace_root) / path
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise DirectoryError(f"'{text[:200]}' does not exist") from None
    if not resolved.is_dir():
        raise DirectoryError(f"'{text[:200]}' is not a directory")
    if resolved.parent == resolved:
        raise DirectoryError("a drive or filesystem root cannot be a working directory")
    home = Path.home().resolve()
    if _inside(home, resolved):
        raise DirectoryError("the home folder, or a folder that contains it, cannot be a working directory; add the project folder itself")
    for guarded in _guarded_folders():
        if _inside(resolved, guarded) or _inside(guarded, resolved):
            raise DirectoryError("Orin's and Claude Code's own configuration and data folders cannot be working directories")
    for name in SECRET_HOME_FOLDERS:
        secret = home / name
        if _inside(resolved, secret) or _inside(secret, resolved):
            raise DirectoryError("credential folders (.ssh, .aws, …) cannot be working directories")
    root = Path(workspace_root).resolve()
    if _inside(resolved, root):
        raise DirectoryError("that folder is already inside the workspace")
    return resolved


def working_directories(candidates: Iterable[object], *, workspace_root: Path) -> tuple[Path, ...]:
    """Every usable additional directory among ``candidates``, deduplicated, at most ``MAX_ADDITIONAL_DIRECTORIES``."""
    out: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            continue
        try:
            resolved = check_directory(item, workspace_root=workspace_root)
        except DirectoryError as error:
            logger.info("additional directory %r skipped: %s", str(item)[:200], error)
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        # A folder inside another additional directory adds nothing.
        if any(_inside(resolved, other) for other in out):
            continue
        out = [other for other in out if not _inside(other, resolved)]
        seen = {os.path.normcase(str(other)) for other in out}
        out.append(resolved)
        seen.add(key)
        if len(out) >= MAX_ADDITIONAL_DIRECTORIES:
            break
    return tuple(out)


def settings_directories(settings: object) -> list[str]:
    """``permissions.additionalDirectories`` from the effective settings (already trust-filtered by the loader)."""
    getter = getattr(settings, "get", None)
    value = getter("permissions.additionalDirectories") if callable(getter) else None
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


__all__ = ["DirectoryError", "MAX_ADDITIONAL_DIRECTORIES", "check_directory", "settings_directories", "working_directories"]
