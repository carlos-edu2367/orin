"""Isolated Git worktree lifecycle manager for subagents (WS-B1, B-RF1)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from agentos.pathsafety import resolve_contained
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.settings.keys_worktree import DEFAULT_WORKTREE_LINK_DIRS

from .worktree import GIT_WORKTREE_TIMEOUT_SECONDS, WorktreeError, WorktreeManager

if os.name == "nt":
    import _winapi  # type: ignore[import-not-found]
else:  # pragma: no cover - POSIX branch
    _winapi = None


WORKTREE_META_REL = ".orin/worktree-meta.json"
WINDOWS_WORKTREE_MAX_PATH = 205
_SAFE_SHORT_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_REMOVE_RETRY_DELAYS: tuple[float, ...] = (0.15, 0.4)


class IsolationUnavailableError(WorktreeError):
    """Raised when worktree isolation cannot be enabled for a workspace."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = str(reason or "unavailable").strip() or "unavailable"
        super().__init__(message or f"worktree isolation unavailable: {self.reason}")


@dataclass(frozen=True, slots=True)
class AgentWorktree:
    """Descriptor for an isolated subagent Git worktree."""

    agent_id: str
    slug: str
    path: Path
    branch: str
    base_commit: str
    parent_root: Path
    linked_dirs: tuple[str, ...] = ()
    copied_untracked: tuple[str, ...] = ()
    copied_included: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "slug": self.slug,
            "path": str(self.path),
            "branch": self.branch,
            "base_commit": self.base_commit,
            "parent_root": str(self.parent_root),
            "linked_dirs": list(self.linked_dirs),
            "copied_untracked": list(self.copied_untracked),
            "copied_included": list(self.copied_included),
        }

    to_dict = as_dict


def agent_worktree_slug(agent_id: str) -> str:
    """Deterministic short filesystem/Git-safe slug (<= 32 chars) for an ``agent_id``."""
    raw = str(agent_id or "").strip()
    if not raw:
        raise WorktreeError("agent_id must be non-blank")
    if _SAFE_SHORT_SLUG_RE.fullmatch(raw) and raw not in {".", ".."}:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    tail_parts = _ALNUM_RE.findall(raw)
    tail = (tail_parts[-1] if tail_parts else "ag")[-12:].lower()
    if not tail:
        tail = "ag"
    return f"{tail}-{digest}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if callable(checker):
        try:
            return bool(checker(path))
        except OSError:
            return False
    return False


def _is_escaped_or_linked_path(wt_path: Path, rel_posix: str) -> bool:
    """Return True if ``rel_posix`` or any ancestor inside ``wt_path`` is a symlink/junction or escapes ``wt_path``."""
    cleaned = str(rel_posix or "").replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ":" in cleaned:
        return True
    parts = [part for part in cleaned.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return True
    root = wt_path.resolve()
    cur = wt_path
    for part in parts:
        cur = cur / part
        if _is_junction(cur) or cur.is_symlink():
            return True
    if cur.exists():
        try:
            resolve_contained(cur, root)
        except Exception:  # noqa: BLE001
            return True
    return False


def validate_link_dir(raw: str, parent_root: Path) -> tuple[str, Path] | None:
    """Validate one ``linkDirs`` entry against traversal, drives, and symlink escapes."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith(("/", "\\")) or ":" in text:
        return None
    normalized = text.replace("\\", "/").strip("/")
    if not normalized:
        return None
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    rel_posix = "/".join(parts)
    if rel_posix == ".git" or rel_posix.startswith(".git/") or rel_posix == ".orin" or rel_posix.startswith(".orin/"):
        return None
    candidate = parent_root / rel_posix
    if not candidate.exists():
        return None
    resolved = resolve_contained(candidate, parent_root)
    if resolved is None or not resolved.is_dir():
        return None
    return rel_posix, resolved


def unlink_junctions_and_symlinks(root: Path, known_rel_dirs: Sequence[str] = ()) -> int:
    """Safely remove all NTFS junctions and symlinks inside ``root`` without traversing them."""
    removed = 0
    if not root.exists():
        return 0

    for rel in known_rel_dirs:
        norm = str(rel or "").replace("\\", "/").strip("/")
        if not norm or ".." in norm.split("/"):
            continue
        candidate = root / norm
        if _is_junction(candidate):
            os.rmdir(candidate)
            removed += 1
        elif candidate.is_symlink():
            try:
                candidate.unlink()
            except OSError:
                os.rmdir(candidate)
            removed += 1

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(dirpath)
        keep_dirs: list[str] = []
        for dname in dirnames:
            full = base / dname
            if _is_junction(full):
                os.rmdir(full)
                removed += 1
                continue  # Prune: NEVER descend into a junction!
            if full.is_symlink():
                try:
                    full.unlink()
                except OSError:
                    os.rmdir(full)
                removed += 1
                continue  # Prune: NEVER descend into a symlinked dir!
            keep_dirs.append(dname)
        dirnames[:] = keep_dirs

        for fname in filenames:
            full_file = base / fname
            if full_file.is_symlink():
                try:
                    full_file.unlink()
                    removed += 1
                except OSError:
                    pass

    return removed


def _create_dir_link(target: Path, link_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        assert _winapi is not None
        _winapi.CreateJunction(str(target), str(link_path))
    else:
        os.symlink(str(target), str(link_path), target_is_directory=True)


def _is_locked_error(exc: BaseException | None, stderr: str = "", returncode: int = 0) -> bool:
    if exc is not None:
        winerr = getattr(exc, "winerror", None)
        if winerr in {5, 32, 33}:
            return True
        if isinstance(exc, PermissionError):
            return True
    low = (stderr or "").lower()
    if returncode == 255 or "invalid argument" in low or "permission denied" in low or "being used by another process" in low:
        return True
    return False


def _locked_error_message(worktree_path: Path) -> str:
    return (
        f"Não foi possível remover a worktree em '{worktree_path}' porque um arquivo está em uso "
        "por outro processo (ex.: servidor de desenvolvimento, terminal aberto ou antivírus). "
        "Feche os processos que usam essa pasta e tente remover novamente."
    )


class AgentWorktreeManager(WorktreeManager):
    """Creates, inspects, cleans up and discards isolated Git worktrees for subagents."""

    def __init__(self, workspace_root: Any, *, store: AgentWorktreeStore | None = None) -> None:
        super().__init__(workspace_root)
        self._store = store
        self._active: dict[str, AgentWorktree] = {}

    def _check_submodules(self, parent: Path) -> bool:
        if (parent / ".gitmodules").is_file():
            return True
        completed = self._run_git(["submodule", "status"], cwd=parent)
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def _check_lfs(self, parent: Path) -> bool:
        root_attrs = parent / ".gitattributes"
        candidates: list[Path] = []
        if root_attrs.is_file():
            candidates.append(root_attrs)
        listed = self._run_git(["ls-files", "-z", "*gitattributes"], cwd=parent)
        if listed.returncode == 0 and listed.stdout:
            for rel in listed.stdout.split("\0"):
                if not rel:
                    continue
                p = parent / rel
                if p.is_file() and p not in candidates:
                    candidates.append(p)
        for attrs_path in candidates:
            try:
                text = attrs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "filter=lfs" in stripped:
                    return True
        return False

    def _resolve_worktree_path(self, parent: Path, slug: str) -> Path:
        primary = (parent / ".orin" / "worktrees" / slug).resolve()
        if os.name != "nt" or len(str(primary)) < WINDOWS_WORKTREE_MAX_PATH:
            return primary
        fallback = (Path(tempfile.gettempdir()) / "orin-wt" / slug).resolve()
        if len(str(fallback)) < WINDOWS_WORKTREE_MAX_PATH:
            return fallback
        raise IsolationUnavailableError(
            "path_too_long",
            f"O caminho da worktree excede o limite seguro do Git no Windows ({WINDOWS_WORKTREE_MAX_PATH} caracteres).",
        )

    def _compute_base_commit(self, parent: Path) -> str:
        stashed = self._run_git(["stash", "create"], cwd=parent)
        if stashed.returncode == 0 and stashed.stdout.strip():
            return stashed.stdout.strip()
        head = self._run_git(["rev-parse", "--verify", "HEAD"], cwd=parent)
        if head.returncode == 0 and head.stdout.strip():
            return head.stdout.strip()
        raise IsolationUnavailableError(
            "no_head_commit",
            "O repositório Git ainda não possui um commit inicial (HEAD).",
        )

    def _copy_parent_untracked(self, parent: Path, worktree_path: Path, hashes: dict[str, str]) -> list[str]:
        listed = self._run_git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=parent)
        if listed.returncode != 0 or not listed.stdout:
            return []
        copied: list[str] = []
        for raw_rel in listed.stdout.split("\0"):
            if not raw_rel:
                continue
            rel = raw_rel.replace("\\", "/").lstrip("/")
            if rel == ".git" or rel.startswith(".git/") or rel == ".orin" or rel.startswith(".orin/"):
                continue
            src = parent / rel
            if src.is_symlink() or _is_junction(src):
                continue
            resolved = resolve_contained(src, parent)
            if resolved is None or not resolved.is_file():
                continue
            dest = worktree_path / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, dest)
                hashes[rel] = _sha256_file(dest)
                copied.append(rel)
            except OSError:
                continue
        return copied

    def _is_tracked_dir(self, parent: Path, rel_posix: str) -> bool:
        check = self._run_git(["ls-files", "--", rel_posix], cwd=parent)
        return check.returncode == 0 and bool(check.stdout.strip())

    def _link_dependency_dirs(
        self,
        parent: Path,
        worktree_path: Path,
        link_dirs: Sequence[str],
    ) -> list[str]:
        linked: list[str] = []
        seen: set[str] = set()
        for raw in link_dirs:
            validated = validate_link_dir(raw, parent)
            if validated is None:
                continue
            rel_posix, target = validated
            if rel_posix in seen:
                continue
            # Never shadow tracked directories (would pollute git status/diff).
            if self._is_tracked_dir(parent, rel_posix):
                continue
            dest = worktree_path / rel_posix
            if dest.exists() or dest.is_symlink() or _is_junction(dest):
                continue
            try:
                _create_dir_link(target, dest)
                seen.add(rel_posix)
                linked.append(rel_posix)
            except OSError:
                continue
        return linked

    def _write_agent_meta(
        self,
        wt: AgentWorktree,
        copied_hashes: dict[str, str],
    ) -> None:
        meta_file = wt.path / WORKTREE_META_REL
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent_id": wt.agent_id,
            "slug": wt.slug,
            "branch": wt.branch,
            "base_commit": wt.base_commit,
            "parent_root": str(wt.parent_root),
            "worktree_path": str(wt.path),
            "linked_dirs": list(wt.linked_dirs),
            "copied_files_sha256": copied_hashes,
        }
        meta_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def read_agent_meta(worktree_path: Path) -> dict[str, Any]:
        meta_file = Path(worktree_path) / WORKTREE_META_REL
        if not meta_file.is_file():
            return {}
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def create_for_agent(
        self,
        agent_id: str,
        parent_root: Path | str | None = None,
        *,
        link_dirs: Sequence[str] | None = None,
        store: AgentWorktreeStore | None = None,
    ) -> AgentWorktree:
        """Create an isolated worktree for ``agent_id`` rooted from the parent's current state."""
        if not self.is_git_repo():
            raise IsolationUnavailableError(
                "not_git_repo",
                "O workspace não é um repositório Git; executando subagente sem isolamento por worktree.",
            )
        parent = Path(parent_root).resolve() if parent_root is not None else self.main_worktree_root()
        if self._check_submodules(parent):
            raise IsolationUnavailableError(
                "submodules",
                "Repositórios com submódulos Git não suportam isolamento por worktree na 0.13.",
            )
        if self._check_lfs(parent):
            raise IsolationUnavailableError(
                "git_lfs",
                "Repositórios com Git LFS não suportam isolamento por worktree na 0.13.",
            )

        slug = agent_worktree_slug(agent_id)
        branch = f"orin/agent-{slug}"
        worktree_path = self._resolve_worktree_path(parent, slug)
        base_commit = self._compute_base_commit(parent)

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        # Clean up stale branch if no worktree is attached to it.
        branch_exists = (
            self._run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=parent).returncode == 0
        )
        if branch_exists and not worktree_path.exists():
            self._run_git(["branch", "-D", branch], cwd=parent)

        completed = self._run_git(
            ["-c", "core.longpaths=true", "worktree", "add", "-b", branch, str(worktree_path), base_commit],
            cwd=parent,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "erro desconhecido do git").strip()
            if "$GIT_DIR" in detail and "too big" in detail:
                raise IsolationUnavailableError("path_too_long", detail)
            raise IsolationUnavailableError("git_error", f"Falha ao criar worktree '{slug}': {detail}")

        self._run_git(["config", "core.longpaths", "true"], cwd=worktree_path)

        copied_hashes: dict[str, str] = {}
        copied_untracked = self._copy_parent_untracked(parent, worktree_path, copied_hashes)
        copied_included = self.copy_worktreeinclude_files(parent, worktree_path)
        for rel in copied_included:
            dest = worktree_path / rel
            if dest.is_file() and rel not in copied_hashes:
                try:
                    copied_hashes[rel] = _sha256_file(dest)
                except OSError:
                    pass

        effective_link_dirs = tuple(DEFAULT_WORKTREE_LINK_DIRS) if link_dirs is None else tuple(link_dirs)
        linked = self._link_dependency_dirs(parent, worktree_path, effective_link_dirs)

        wt = AgentWorktree(
            agent_id=str(agent_id),
            slug=slug,
            path=worktree_path,
            branch=branch,
            base_commit=base_commit,
            parent_root=parent,
            linked_dirs=tuple(linked),
            copied_untracked=tuple(copied_untracked),
            copied_included=tuple(copied_included),
        )
        self._write_agent_meta(wt, copied_hashes)
        self._active[str(agent_id)] = wt

        active_store = store or self._store
        if active_store is not None:
            active_store.upsert(
                agent_id=str(agent_id),
                workspace_root=str(parent),
                base_commit=base_commit,
                branch=branch,
                merge_state="pending",
                merge_detail={"worktree_path": str(worktree_path), "slug": slug},
            )
        return wt

    def find_for_agent(self, agent_id: str) -> AgentWorktree | None:
        """Locate an active worktree for ``agent_id`` (in memory, via git worktree list, or on disk)."""
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        cached = self._active.get(aid)
        if cached is not None and cached.path.is_dir():
            return cached
        slug = agent_worktree_slug(aid)
        expected_branch = f"orin/agent-{slug}"
        main_root = self.main_worktree_root()

        wt_path: Path | None = None
        for info in self.list_worktrees():
            if not info.is_main and info.branch == expected_branch:
                wt_path = info.path
                break
        if wt_path is None:
            for candidate in (
                (main_root / ".orin" / "worktrees" / slug).resolve(),
                (Path(tempfile.gettempdir()) / "orin-wt" / slug).resolve(),
            ):
                if candidate.is_dir() and (candidate / WORKTREE_META_REL).is_file():
                    wt_path = candidate
                    break
        if wt_path is None or not wt_path.is_dir():
            return None

        meta = self.read_agent_meta(wt_path)
        wt = AgentWorktree(
            agent_id=str(meta.get("agent_id") or aid),
            slug=str(meta.get("slug") or slug),
            path=wt_path,
            branch=str(meta.get("branch") or expected_branch),
            base_commit=str(meta.get("base_commit") or self._head_commit(wt_path)),
            parent_root=Path(str(meta.get("parent_root") or main_root)).resolve(),
            linked_dirs=tuple(str(d) for d in (meta.get("linked_dirs") or ())),
        )
        self._active[aid] = wt
        return wt

    def has_agent_changes(self, agent_id_or_path: str | Path) -> bool:
        """Return True if the subagent created, modified, deleted or committed any files."""
        if isinstance(agent_id_or_path, Path):
            wt_path = agent_id_or_path.resolve()
        else:
            found = self.find_for_agent(str(agent_id_or_path))
            if found is not None:
                wt_path = found.path
            else:
                candidate = Path(str(agent_id_or_path))
                wt_path = candidate.resolve() if candidate.is_dir() else None
        if wt_path is None or not wt_path.is_dir():
            return False

        meta = self.read_agent_meta(wt_path)
        base_commit = str(meta.get("base_commit") or "").strip()
        linked_dirs = {
            str(d).replace("\\", "/").strip("/")
            for d in (meta.get("linked_dirs") or ())
            if str(d).strip()
        }
        copied_hashes: dict[str, str] = {
            str(k).replace("\\", "/"): str(v)
            for k, v in (meta.get("copied_files_sha256") or {}).items()
        }

        if base_commit:
            current_head = self._head_commit(wt_path)
            if current_head and current_head != base_commit:
                diff_raw = self._run_git(
                    ["-c", "core.quotepath=false", "diff", "--raw", "-z", base_commit, current_head],
                    cwd=wt_path,
                )
                if diff_raw.returncode == 0 and diff_raw.stdout:
                    parts = diff_raw.stdout.split("\0")
                    i = 0
                    while i < len(parts):
                        hdr = parts[i]
                        i += 1
                        if not hdr.startswith(":") or i >= len(parts):
                            continue
                        hdr_bits = hdr[1:].split()
                        old_mode = hdr_bits[0] if len(hdr_bits) > 0 else ""
                        new_mode = hdr_bits[1] if len(hdr_bits) > 1 else ""
                        status_code = hdr_bits[4] if len(hdr_bits) > 4 else ""
                        rel_path = parts[i].replace("\\", "/")
                        i += 1
                        if status_code.startswith(("R", "C")) and i < len(parts):
                            rel_path = parts[i].replace("\\", "/")
                            i += 1
                        if rel_path == ".orin" or rel_path.startswith(".orin/"):
                            continue
                        if any(rel_path == ld or rel_path.startswith(f"{ld}/") for ld in linked_dirs):
                            continue
                        if new_mode in {"120000", "160000"} or (new_mode == "000000" and old_mode in {"120000", "160000"}):
                            continue
                        if _is_escaped_or_linked_path(wt_path, rel_path):
                            continue
                        return True
                else:
                    return True

        completed = self._run_git(
            ["-c", "core.quotepath=false", "status", "--porcelain", "-uall", "-z"],
            cwd=wt_path,
        )
        if completed.returncode != 0:
            return False

        tokens = completed.stdout.split("\0")
        idx = 0
        while idx < len(tokens):
            entry = tokens[idx]
            idx += 1
            if len(entry) < 4:
                continue
            xy = entry[:2]
            rel = entry[3:].replace("\\", "/")
            # Rename/copy entries consume a second NUL-delimited path.
            if xy[0] in {"R", "C"} or xy[1] in {"R", "C"}:
                idx += 1
            if rel == ".orin" or rel.startswith(".orin/"):
                continue
            if any(rel == ld or rel.startswith(f"{ld}/") for ld in linked_dirs):
                continue
            if _is_escaped_or_linked_path(wt_path, rel):
                continue
            if xy == "??":
                full = wt_path / rel
                expected_hash = copied_hashes.get(rel)
                if expected_hash is not None and full.is_file():
                    try:
                        if _sha256_file(full) == expected_hash:
                            continue
                    except OSError:
                        pass
                return True
            return True
        return False

    def _safe_remove_worktree(self, wt: AgentWorktree) -> None:
        parent = wt.parent_root
        target = wt.path
        last_error: str = ""
        last_exc: BaseException | None = None
        last_rc: int = 0

        for attempt in range(len(_REMOVE_RETRY_DELAYS) + 1):
            # CRITICAL: ALWAYS remove junctions & symlinks BEFORE git worktree remove / rmtree!
            try:
                unlink_junctions_and_symlinks(target, wt.linked_dirs)
            except OSError as exc:
                last_exc = exc
                if attempt < len(_REMOVE_RETRY_DELAYS):
                    time.sleep(_REMOVE_RETRY_DELAYS[attempt])
                    continue
                if _is_locked_error(exc):
                    raise WorktreeError(_locked_error_message(target)) from exc
                raise WorktreeError(f"Falha ao desvincular junções em '{target}': {exc}") from exc

            completed = self._run_git(
                ["-c", "core.longpaths=true", "worktree", "remove", "--force", str(target)],
                cwd=parent,
            )
            last_rc = completed.returncode
            last_error = (completed.stderr or completed.stdout or "").strip()

            if completed.returncode == 0 and not target.exists():
                break

            # Fallback removal ONLY after all junctions are unlinked.
            if target.exists():
                try:
                    unlink_junctions_and_symlinks(target, wt.linked_dirs)
                    shutil.rmtree(target)
                    self._run_git(["worktree", "prune"], cwd=parent)
                    if not target.exists():
                        break
                except OSError as exc:
                    last_exc = exc
                    if attempt < len(_REMOVE_RETRY_DELAYS):
                        time.sleep(_REMOVE_RETRY_DELAYS[attempt])
                        continue
                    if _is_locked_error(exc, last_error, last_rc):
                        raise WorktreeError(_locked_error_message(target)) from exc
                    raise WorktreeError(f"Falha ao remover worktree '{target}': {exc}") from exc
            else:
                self._run_git(["worktree", "prune"], cwd=parent)
                break

            if attempt < len(_REMOVE_RETRY_DELAYS):
                time.sleep(_REMOVE_RETRY_DELAYS[attempt])
        else:
            if target.exists():
                if _is_locked_error(last_exc, last_error, last_rc):
                    raise WorktreeError(_locked_error_message(target))
                raise WorktreeError(f"Falha ao remover worktree '{target}': {last_error}")

        if wt.branch.startswith("orin/"):
            self._run_git(["branch", "-D", wt.branch], cwd=parent)
        self._active.pop(wt.agent_id, None)

    def remove_applied_worktree(self, agent_id: str) -> bool:
        """Remove a subagent worktree & branch after its diff has been applied."""
        wt = self.find_for_agent(agent_id)
        if wt is None:
            return False
        self._safe_remove_worktree(wt)
        return True

    def cleanup_if_unchanged(
        self,
        agent_id: str,
        *,
        store: AgentWorktreeStore | None = None,
    ) -> bool:
        """Remove worktree and branch when the subagent made no changes; records ``merge_state='empty'``."""
        wt = self.find_for_agent(agent_id)
        if wt is None:
            return False
        if self.has_agent_changes(wt.path):
            return False
        self._safe_remove_worktree(wt)
        active_store = store or self._store
        if active_store is not None:
            active_store.update_merge_state(
                str(agent_id),
                "empty",
                merge_detail={"worktree_path": str(wt.path), "reason": "no_changes"},
            )
        return True

    def discard_worktree(
        self,
        agent_id: str,
        *,
        store: AgentWorktreeStore | None = None,
    ) -> bool:
        """Forcefully discard a subagent's worktree, junctions, and branch even with changes."""
        aid = str(agent_id or "").strip()
        if not aid:
            return False
        wt = self.find_for_agent(aid)
        active_store = store or self._store
        removed_any = False

        if wt is not None:
            self._safe_remove_worktree(wt)
            removed_any = True
        else:
            # Also prune orphan branch if present.
            if self.is_git_repo():
                parent = self.main_worktree_root()
                branch = f"orin/agent-{agent_worktree_slug(aid)}"
                if (
                    self._run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=parent).returncode
                    == 0
                ):
                    self._run_git(["branch", "-D", branch], cwd=parent)
                    removed_any = True

        if active_store is not None:
            existing = active_store.get(aid)
            if existing is not None:
                active_store.update_merge_state(
                    aid,
                    "discarded",
                    merge_detail={
                        **(existing.merge_detail or {}),
                        "discarded": True,
                    },
                )
                removed_any = True
        return removed_any

    @staticmethod
    def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr=str(error))


__all__ = [
    "AgentWorktree",
    "AgentWorktreeManager",
    "IsolationUnavailableError",
    "WINDOWS_WORKTREE_MAX_PATH",
    "WORKTREE_META_REL",
    "agent_worktree_slug",
    "unlink_junctions_and_symlinks",
    "validate_link_dir",
]
