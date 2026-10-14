"""Git Worktree isolation manager for Orin Code sessions and subagents."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from uuid import uuid4

from agentos.pathsafety import resolve_contained


GIT_WORKTREE_TIMEOUT_SECONDS = 30
_SLUG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WORKTREEINCLUDE_CANDIDATES = (
    ".worktreeinclude",
    ".orin/worktreeinclude",
    ".claude/worktreeinclude",
)


class WorktreeError(RuntimeError):
    """Raised when a Git worktree operation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    """Metadata describing an active Git worktree."""

    name: str
    path: Path
    branch: str
    head_commit: str = ""
    is_main: bool = False
    copied_files: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "branch": self.branch,
            "head_commit": self.head_commit,
            "is_main": self.is_main,
            "copied_files": list(self.copied_files),
        }

    to_dict = as_dict


class WorktreeManager:
    """Creates, inspects, and removes isolated Git worktrees under ``.orin/worktrees/<slug>``."""

    def __init__(self, workspace_root: Any) -> None:
        if isinstance(workspace_root, (Path, str)):
            root = workspace_root
        else:
            root = getattr(workspace_root, "root", workspace_root)
        self.workspace_root = Path(root).resolve()

    def is_git_repo(self) -> bool:
        """Return True when ``workspace_root`` is inside a Git working tree."""
        if shutil.which("git") is None or not self.workspace_root.exists():
            return False
        if (self.workspace_root / ".git").exists():
            return True
        completed = self._run_git(["rev-parse", "--is-inside-work-tree"], cwd=self.workspace_root)
        return completed.returncode == 0 and completed.stdout.strip().lower() == "true"

    def sanitize_slug(self, slug: str | None = None) -> str:
        """Normalize a user- or agent-supplied worktree name into a safe slug."""
        raw = (slug or "").strip()
        if not raw:
            return f"wt-{uuid4().hex[:8]}"
        cleaned = _SLUG_SANITIZE_RE.sub("-", raw).strip("-._")[:64]
        if not cleaned or cleaned in {".", ".."}:
            raise WorktreeError(f"invalid worktree name: {slug!r}")
        return cleaned

    def main_worktree_root(self) -> Path:
        """Return the root directory of the primary Git repository."""
        worktrees = self.list_worktrees()
        if worktrees:
            return worktrees[0].path
        completed = self._run_git(["rev-parse", "--show-toplevel"], cwd=self.workspace_root)
        if completed.returncode == 0 and completed.stdout.strip():
            return Path(completed.stdout.strip()).resolve()
        return self.workspace_root

    def load_worktreeinclude_patterns(self, source_root: Path | None = None) -> list[str]:
        """Read glob patterns from ``.worktreeinclude`` and ``.orin/worktreeinclude``."""
        root = (source_root or self.main_worktree_root()).resolve()
        patterns: list[str] = []
        seen: set[str] = set()
        for rel_candidate in _WORKTREEINCLUDE_CANDIDATES:
            candidate = root / rel_candidate
            if not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                normalized = line.replace("\\", "/").lstrip("/")
                if ".." in normalized.split("/"):
                    continue
                if normalized not in seen:
                    seen.add(normalized)
                    patterns.append(normalized)
        return patterns

    def copy_worktreeinclude_files(self, source_root: Path, target_root: Path) -> list[str]:
        """Copy untracked/ignored files matching ``.worktreeinclude`` from ``source_root`` to ``target_root``."""
        source = source_root.resolve()
        target = target_root.resolve()
        patterns = self.load_worktreeinclude_patterns(source)
        if not patterns:
            return []
        copied: list[str] = []
        seen_rel: set[str] = set()
        for pattern in patterns:
            try:
                matches = sorted(source.glob(pattern))
            except (OSError, ValueError):
                continue
            for item in matches:
                resolved = resolve_contained(item, source)
                if resolved is None:
                    continue
                try:
                    rel = resolved.relative_to(source).as_posix()
                except ValueError:
                    continue
                if rel == ".git" or rel.startswith(".git/") or rel.startswith(".orin/worktrees/"):
                    continue
                if resolved.is_file():
                    self._copy_single_file(resolved, target, rel, copied, seen_rel)
                elif resolved.is_dir():
                    for child in sorted(resolved.rglob("*")):
                        child_resolved = resolve_contained(child, source)
                        if child_resolved is None or not child_resolved.is_file():
                            continue
                        try:
                            child_rel = child_resolved.relative_to(source).as_posix()
                        except ValueError:
                            continue
                        if child_rel.startswith(".git/") or child_rel.startswith(".orin/worktrees/"):
                            continue
                        self._copy_single_file(child_resolved, target, child_rel, copied, seen_rel)
        return copied

    @staticmethod
    def _copy_single_file(
        source_file: Path,
        target_root: Path,
        rel_posix: str,
        copied: list[str],
        seen_rel: set[str],
    ) -> None:
        if rel_posix in seen_rel:
            return
        dest = target_root / rel_posix
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, dest)
        except OSError:
            return
        seen_rel.add(rel_posix)
        copied.append(rel_posix)

    def create_worktree(
        self,
        slug: str | None = None,
        base_branch: str | None = None,
        *,
        name: str | None = None,
    ) -> WorktreeInfo:
        """Create (or reuse) an isolated worktree at ``.orin/worktrees/<slug>`` on branch ``orin/<slug>``."""
        if not self.is_git_repo():
            raise WorktreeError("workspace is not a Git repository")
        clean_slug = self.sanitize_slug(slug or name)
        main_root = self.main_worktree_root()
        worktree_path = (main_root / ".orin" / "worktrees" / clean_slug).resolve()
        branch_name = f"orin/{clean_slug}"

        existing = {wt.path.resolve(): wt for wt in self.list_worktrees()}
        if worktree_path in existing:
            copied = self.copy_worktreeinclude_files(main_root, worktree_path)
            current = existing[worktree_path]
            self._write_worktree_metadata(main_root, worktree_path, clean_slug, current.branch or branch_name, copied)
            return WorktreeInfo(
                name=clean_slug,
                path=worktree_path,
                branch=current.branch or branch_name,
                head_commit=current.head_commit or self._head_commit(worktree_path),
                is_main=False,
                copied_files=tuple(copied),
            )

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = (
            self._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                cwd=main_root,
            ).returncode
            == 0
        )
        if branch_exists:
            cmd = ["worktree", "add", str(worktree_path), branch_name]
        else:
            cmd = ["worktree", "add", "-b", branch_name, str(worktree_path)]
            if base_branch and base_branch.strip():
                cmd.append(base_branch.strip())

        completed = self._run_git(cmd, cwd=main_root)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown git error").strip()
            raise WorktreeError(f"failed to create git worktree '{clean_slug}': {detail}")

        copied = self.copy_worktreeinclude_files(main_root, worktree_path)
        head = self._head_commit(worktree_path)
        self._write_worktree_metadata(main_root, worktree_path, clean_slug, branch_name, copied)
        return WorktreeInfo(
            name=clean_slug,
            path=worktree_path,
            branch=branch_name,
            head_commit=head,
            is_main=False,
            copied_files=tuple(copied),
        )

    def has_uncommitted_changes(self, worktree_path: Path | str) -> bool:
        """Return True when ``worktree_path`` has modified or untracked files (excluding ``.orin``)."""
        target = Path(worktree_path).resolve()
        if not target.is_dir():
            return False
        completed = self._run_git(["status", "--porcelain", "-uall"], cwd=target)
        if completed.returncode != 0:
            return False
        for line in completed.stdout.splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip().strip('"').replace("\\", "/")
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1].strip('"')
            if rel == ".orin" or rel.startswith(".orin/"):
                continue
            return True
        return False

    def remove_worktree(
        self,
        worktree_path: Path | str,
        *,
        force: bool = False,
        delete_branch: bool = False,
    ) -> dict[str, Any]:
        """Remove an isolated worktree after verifying it has no uncommitted changes (unless ``force=True``)."""
        if not self.is_git_repo():
            raise WorktreeError("workspace is not a Git repository")
        main_root = self.main_worktree_root()
        target = self._resolve_worktree_target(main_root, worktree_path)
        if not target.exists():
            raise WorktreeError(f"worktree '{worktree_path}' does not exist")
        if target.resolve() == main_root.resolve():
            raise WorktreeError("cannot remove the main repository worktree")

        if not force and self.has_uncommitted_changes(target):
            raise WorktreeError(
                f"worktree '{target.name}' has uncommitted changes; commit/stash them or pass force=True"
            )

        branch_name = ""
        for wt in self.list_worktrees():
            if wt.path.resolve() == target.resolve():
                branch_name = wt.branch
                break

        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(target))
        completed = self._run_git(cmd, cwd=main_root)
        if completed.returncode != 0:
            if force and target.exists():
                shutil.rmtree(target, ignore_errors=True)
                self._run_git(["worktree", "prune"], cwd=main_root)
            else:
                detail = (completed.stderr or completed.stdout or "unknown git error").strip()
                raise WorktreeError(f"failed to remove git worktree '{target.name}': {detail}")

        if delete_branch and branch_name.startswith("orin/"):
            self._run_git(["branch", "-D" if force else "-d", branch_name], cwd=main_root)

        self._clear_active_worktree_if_matches(main_root, target)
        return {
            "removed": True,
            "name": target.name,
            "path": str(target),
            "branch": branch_name,
        }

    def list_worktrees(self) -> list[WorktreeInfo]:
        """Return all Git worktrees registered for this repository."""
        if not self.is_git_repo():
            return []
        completed = self._run_git(["worktree", "list", "--porcelain"], cwd=self.workspace_root)
        if completed.returncode != 0:
            return []
        entries: list[WorktreeInfo] = []
        raw_blocks = completed.stdout.strip().split("\n\n")
        for idx, block in enumerate(raw_blocks):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            wt_path: Path | None = None
            head_commit = ""
            branch = ""
            for line in lines:
                if line.startswith("worktree "):
                    wt_path = Path(line[len("worktree "):].strip()).resolve()
                elif line.startswith("HEAD "):
                    head_commit = line[len("HEAD "):].strip()
                elif line.startswith("branch "):
                    ref = line[len("branch "):].strip()
                    branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
                elif line == "detached":
                    branch = "HEAD"
            if wt_path is None:
                continue
            entries.append(
                WorktreeInfo(
                    name=wt_path.name,
                    path=wt_path,
                    branch=branch,
                    head_commit=head_commit,
                    is_main=(idx == 0),
                )
            )
        return entries

    def active_worktree(self) -> WorktreeInfo | None:
        """Return the currently active worktree if ``workspace_root`` is inside one or recorded in metadata."""
        if not self.is_git_repo():
            return None
        worktrees = self.list_worktrees()
        for wt in worktrees:
            if not wt.is_main and (
                self.workspace_root == wt.path or wt.path in self.workspace_root.parents
            ):
                return wt
        main_root = worktrees[0].path if worktrees else self.workspace_root
        meta_path = main_root / ".orin" / "active_worktree.json"
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                wt_path = Path(str(data.get("path") or "")).resolve()
                for wt in worktrees:
                    if not wt.is_main and wt.path == wt_path:
                        return wt
            except Exception:
                return None
        return None

    def _resolve_worktree_target(self, main_root: Path, worktree_path: Path | str) -> Path:
        if isinstance(worktree_path, Path):
            return worktree_path.resolve()
        raw = str(worktree_path).strip()
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        rel_candidate = (main_root / raw).resolve()
        if rel_candidate.exists():
            return rel_candidate
        slug = self.sanitize_slug(raw)
        return (main_root / ".orin" / "worktrees" / slug).resolve()

    def _head_commit(self, cwd: Path) -> str:
        completed = self._run_git(["rev-parse", "HEAD"], cwd=cwd)
        return completed.stdout.strip() if completed.returncode == 0 else ""

    @staticmethod
    def _write_worktree_metadata(
        main_root: Path,
        worktree_path: Path,
        name: str,
        branch: str,
        copied_files: list[str],
    ) -> None:
        payload = {
            "name": name,
            "path": str(worktree_path),
            "branch": branch,
            "main_root": str(main_root),
            "copied_files": copied_files,
        }
        for target_dir in (main_root / ".orin", worktree_path / ".orin"):
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "active_worktree.json").write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                continue

    @staticmethod
    def _clear_active_worktree_if_matches(main_root: Path, removed_path: Path) -> None:
        meta_path = main_root / ".orin" / "active_worktree.json"
        if not meta_path.is_file():
            return
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if Path(str(data.get("path") or "")).resolve() == removed_path.resolve():
                meta_path.unlink(missing_ok=True)
        except Exception:
            return

    @staticmethod
    def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr=str(error))


__all__ = [
    "WorktreeError",
    "WorktreeInfo",
    "WorktreeManager",
]
