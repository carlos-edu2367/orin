"""Git exclude maintenance for Orin internal directories and state."""
from __future__ import annotations

from pathlib import Path

_EXCLUDE_COMMENT = "# Orin: local agent state and attachments"
_EXCLUDE_ENTRY = ".orin/"
_EXISTING_ORIN_PATTERNS = frozenset({".orin/", ".orin", "/.orin/", "/.orin"})


def find_git_dir(start: Path) -> Path | None:
    """Find the .git directory or worktree gitdir for a path by traversing upwards."""
    try:
        current = start.resolve()
        if current.is_file():
            current = current.parent
        for directory in (current, *current.parents):
            git_path = directory / ".git"
            if git_path.is_dir():
                return git_path
            if git_path.is_file():
                try:
                    content = git_path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if content.startswith("gitdir:"):
                    raw = content[len("gitdir:"):].strip()
                    target = Path(raw)
                    if not target.is_absolute():
                        target = (git_path.parent / target).resolve()
                    return target
        return None
    except OSError:
        return None


def git_common_dir(git_dir: Path) -> Path:
    """Return the common git directory for a worktree or standard repo git_dir."""
    try:
        commondir_file = git_dir / "commondir"
        if commondir_file.is_file():
            rel = commondir_file.read_text(encoding="utf-8").strip()
            target = Path(rel)
            if not target.is_absolute():
                target = (git_dir / target).resolve()
            return target
    except OSError:
        pass
    return git_dir


def ensure_orin_excluded(root: Path) -> bool:
    """Ensure .orin/ is excluded in the git repository's info/exclude.

    Returns True only when the file was written, False otherwise. Never raises.
    """
    try:
        git_dir = find_git_dir(root)
        if git_dir is None:
            return False
        common_dir = git_common_dir(git_dir)
        info_dir = common_dir / "info"
        exclude_file = info_dir / "exclude"

        if exclude_file.exists():
            content = exclude_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip() in _EXISTING_ORIN_PATTERNS:
                    return False
            prefix = "" if (not content or content.endswith(("\n", "\r\n"))) else "\n"
            addition = f"{prefix}{_EXCLUDE_COMMENT}\n{_EXCLUDE_ENTRY}\n"
            exclude_file.write_text(content + addition, encoding="utf-8")
            return True
        else:
            info_dir.mkdir(parents=True, exist_ok=True)
            exclude_file.write_text(f"{_EXCLUDE_COMMENT}\n{_EXCLUDE_ENTRY}\n", encoding="utf-8")
            return True
    except OSError:
        return False
    except Exception:
        return False


__all__ = ["ensure_orin_excluded", "find_git_dir", "git_common_dir"]
