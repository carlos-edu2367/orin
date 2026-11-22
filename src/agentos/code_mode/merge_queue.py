"""Serialized per-workspace merge queue for isolated subagent worktrees (WS-B2, B-RF2)."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator, Literal

from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore

from .agent_worktree import (
    AgentWorktree,
    AgentWorktreeManager,
    _is_escaped_or_linked_path,
    _is_junction,
    _sha256_file,
)
from .worktree import GIT_WORKTREE_TIMEOUT_SECONDS

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - POSIX branch
    import fcntl


MergeState = Literal["applied", "conflict", "check_failed", "empty"]
CommandAuthorizer = Callable[[str], tuple[bool, str]]

_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_LOCKFILE_POLL_SECONDS = 0.02
_CHECK_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _ParentFileSnapshot:
    exists: bool
    content: bytes | None = None
    mode: int | None = None
    symlink_target: str | None = None


@dataclass(frozen=True, slots=True)
class _ParentIndexSnapshot:
    entries: tuple[str, ...]
    raw_index_path: Path | None = None
    raw_index_bytes: bytes | None = None


def _workspace_lock_key(workspace_root: Path) -> str:
    resolved = str(workspace_root.resolve())
    return resolved.lower() if os.name == "nt" else resolved


def _process_mutex_for(workspace_root: Path) -> threading.Lock:
    key = _workspace_lock_key(workspace_root)
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


@contextmanager
def _cross_process_lockfile(lock_path: Path) -> Iterator[None]:
    """Acquire an advisory lockfile under ``.orin/locks/merge-queue.lock`` (never workspace_lock.exclusive_folder)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+b")
    try:
        if os.name == "nt":
            fh.seek(0)
            if fh.tell() == 0 and lock_path.stat().st_size == 0:
                fh.write(b"0")
                fh.flush()
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(_LOCKFILE_POLL_SECONDS)
            try:
                yield
            finally:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:  # pragma: no cover - POSIX branch
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        try:
            fh.close()
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Outcome of integrating one subagent worktree into the parent working tree."""

    state: MergeState
    files: tuple[str, ...] = ()
    conflict_detail: dict[str, Any] | None = None
    check_output: str | None = None
    worktree_path: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Structured ``ToolOutcome.payload['worktree']`` contract (K19)."""
        detail: dict[str, Any]
        if isinstance(self.conflict_detail, dict):
            detail = dict(self.conflict_detail)
        else:
            detail = {}
        if self.files and "files" not in detail:
            detail["files"] = list(self.files)
        if self.check_output is not None and "check_output" not in detail:
            detail["check_output"] = self.check_output[:4000]
        return {
            "merge_state": self.state,
            "merge_detail": detail,
            "worktree_path": self.worktree_path,
        }

    as_dict = to_payload


def _chunks(items: list[str], size: int = 100) -> Iterator[list[str]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


class MergeQueue:
    """Serialized per-workspace integration queue applying subagent diffs without commits."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        manager: AgentWorktreeManager | None = None,
        store: AgentWorktreeStore | None = None,
        merge_check_command: str | None = None,
        command_authorizer: CommandAuthorizer | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.manager = manager or AgentWorktreeManager(self.workspace_root, store=store)
        self.store = store or getattr(self.manager, "_store", None)
        self.merge_check_command = (
            str(merge_check_command).strip()
            if isinstance(merge_check_command, str) and merge_check_command.strip()
            else None
        )
        self.command_authorizer = command_authorizer
        self._mutex = _process_mutex_for(self.workspace_root)
        self._lockfile = self.workspace_root / ".orin" / "locks" / "merge-queue.lock"

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        with self._mutex:
            with _cross_process_lockfile(self._lockfile):
                yield

    def enqueue(
        self,
        agent_id: str,
        *,
        merge_check_command: str | None = None,
        command_authorizer: CommandAuthorizer | None = None,
        cancelled: Callable[[], bool] | None = None,
        before_apply: Callable[[Path, list[str]], None] | None = None,
        on_rollback: Callable[[Path, list[str]], None] | None = None,
    ) -> MergeResult:
        """Integrate ``agent_id``'s worktree changes into the parent workspace, one at a time."""
        aid = str(agent_id or "").strip()
        if not aid:
            return MergeResult(state="empty")

        effective_cmd = (
            str(merge_check_command).strip()
            if isinstance(merge_check_command, str) and merge_check_command.strip()
            else self.merge_check_command
        )
        effective_auth = command_authorizer or self.command_authorizer
        is_cancelled = cancelled or (lambda: False)

        now_queued = datetime.now(UTC)
        if self.store is not None:
            self.store.update_merge_state(aid, "pending", queued_at=now_queued)

        with self._serialized():
            wt = self.manager.find_for_agent(aid)
            if wt is None:
                return MergeResult(state="empty")

            patch_bytes, changed_files = self._build_worktree_patch(wt)
            if not patch_bytes or not changed_files:
                self.manager.cleanup_if_unchanged(aid, store=self.store)
                return MergeResult(state="empty")

            parent = wt.parent_root
            disk_snapshot = self._snapshot_parent_files(parent, changed_files)
            index_snapshot = self._snapshot_parent_index(parent, changed_files)
            if before_apply is not None:
                before_apply(parent, list(changed_files))

            def _rollback_with_callback() -> None:
                self._rollback_parent(parent, changed_files, disk_snapshot, index_snapshot)
                if on_rollback is not None:
                    on_rollback(parent, list(changed_files))

            apply_proc = self._apply_patch_3way(parent, patch_bytes, changed_files=changed_files)
            if apply_proc.returncode != 0:
                conflict_files, snippets = self._extract_conflict_info(parent, changed_files, apply_proc.stderr)
                _rollback_with_callback()
                detail: dict[str, Any] = {
                    "files": conflict_files or list(changed_files),
                    "hunks": snippets,
                    "stderr": (apply_proc.stderr or "")[:2000],
                    "worktree_path": str(wt.path),
                    "branch": wt.branch,
                }
                if self.store is not None:
                    self.store.update_merge_state(aid, "conflict", merge_detail=detail)
                return MergeResult(
                    state="conflict",
                    files=tuple(conflict_files or changed_files),
                    conflict_detail=detail,
                    worktree_path=str(wt.path),
                )

            if is_cancelled():
                _rollback_with_callback()
                detail = {
                    "files": list(changed_files),
                    "reason": "cancelled",
                    "worktree_path": str(wt.path),
                    "branch": wt.branch,
                }
                if self.store is not None:
                    self.store.update_merge_state(aid, "check_failed", merge_detail=detail)
                return MergeResult(
                    state="check_failed",
                    files=tuple(changed_files),
                    conflict_detail=detail,
                    worktree_path=str(wt.path),
                )

            check_output: str | None = None
            if effective_cmd:
                if effective_auth is not None:
                    allowed, reason = effective_auth(effective_cmd)
                else:
                    allowed, reason = (False, "no permission authorizer configured")
                if not allowed:
                    _rollback_with_callback()
                    detail = {
                        "files": list(changed_files),
                        "reason": "permission_denied",
                        "message": reason,
                        "command": effective_cmd,
                        "worktree_path": str(wt.path),
                        "branch": wt.branch,
                    }
                    if self.store is not None:
                        self.store.update_merge_state(aid, "check_failed", merge_detail=detail)
                    return MergeResult(
                        state="check_failed",
                        files=tuple(changed_files),
                        conflict_detail=detail,
                        check_output=reason,
                        worktree_path=str(wt.path),
                    )

                check_rc, check_output, was_cancelled = self._run_check_command(
                    parent, effective_cmd, cancelled=is_cancelled
                )
                if was_cancelled or check_rc != 0:
                    _rollback_with_callback()
                    detail = {
                        "files": list(changed_files),
                        "reason": "cancelled" if was_cancelled else "check_failed",
                        "exit_code": check_rc,
                        "command": effective_cmd,
                        "check_output": (check_output or "")[:4000],
                        "worktree_path": str(wt.path),
                        "branch": wt.branch,
                    }
                    if self.store is not None:
                        self.store.update_merge_state(aid, "check_failed", merge_detail=detail)
                    return MergeResult(
                        state="check_failed",
                        files=tuple(changed_files),
                        conflict_detail=detail,
                        check_output=check_output,
                        worktree_path=str(wt.path),
                    )

            # Success: remove worktree & branch, record applied state.
            self.manager.remove_applied_worktree(aid)
            if self.store is not None:
                self.store.update_merge_state(
                    aid,
                    "applied",
                    merge_detail={"files": list(changed_files)},
                    merged_at=datetime.now(UTC),
                )
            return MergeResult(
                state="applied",
                files=tuple(changed_files),
                conflict_detail=None,
                check_output=check_output,
                worktree_path=None,
            )

    def _build_worktree_patch(self, wt: AgentWorktree) -> tuple[bytes, list[str]]:
        """Generate a complete binary-safe patch from ``base_commit`` to the child's current edits."""
        meta = AgentWorktreeManager.read_agent_meta(wt.path)
        base_commit = str(meta.get("base_commit") or wt.base_commit).strip()
        linked_dirs = {
            str(d).replace("\\", "/").strip("/")
            for d in (meta.get("linked_dirs") or wt.linked_dirs or ())
            if str(d).strip()
        }
        copied_hashes: dict[str, str] = {
            str(k).replace("\\", "/"): str(v)
            for k, v in (meta.get("copied_files_sha256") or {}).items()
        }
        copied_blobs: dict[str, str] = {
            str(k).replace("\\", "/"): str(v).strip()
            for k, v in (meta.get("copied_files_blobs") or {}).items()
            if str(v or "").strip()
        }

        to_add: set[str] = set()
        to_remove: set[str] = set()

        def _is_ignored_rel(rel: str) -> bool:
            if not rel or rel == ".orin" or rel.startswith(".orin/") or rel == ".git" or rel.startswith(".git/"):
                return True
            return any(rel == ld or rel.startswith(f"{ld}/") for ld in linked_dirs)

        # 1. Include committed changes inside the worktree branch if child committed anything.
        head_commit = self.manager._head_commit(wt.path)
        if base_commit and head_commit and head_commit != base_commit:
            committed = self._run_git_text(
                ["-c", "core.quotepath=false", "diff", "--raw", "-z", base_commit, head_commit],
                cwd=wt.path,
            )
            if committed.returncode == 0 and committed.stdout:
                parts = committed.stdout.split("\0")
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
                    if status_code.startswith(("R", "C")):
                        old_rel = parts[i].replace("\\", "/")
                        i += 1
                        if i >= len(parts):
                            break
                        new_rel = parts[i].replace("\\", "/")
                        i += 1
                        if new_mode in {"120000", "160000"}:
                            continue
                        if not _is_ignored_rel(old_rel) and old_mode not in {"120000", "160000"}:
                            to_remove.add(old_rel)
                        if not _is_ignored_rel(new_rel) and not _is_escaped_or_linked_path(wt.path, new_rel):
                            new_full = wt.path / new_rel
                            if new_full.exists() and not new_full.is_symlink() and not _is_junction(new_full):
                                to_add.add(new_rel)
                            else:
                                to_remove.add(new_rel)
                    else:
                        rel = parts[i].replace("\\", "/")
                        i += 1
                        if _is_ignored_rel(rel):
                            continue
                        if new_mode in {"120000", "160000"} or (new_mode == "000000" and old_mode in {"120000", "160000"}):
                            continue
                        if _is_escaped_or_linked_path(wt.path, rel):
                            continue
                        full_rel = wt.path / rel
                        if status_code.startswith("D") or not full_rel.exists():
                            to_remove.add(rel)
                        elif not full_rel.is_symlink() and not _is_junction(full_rel) and full_rel.is_file():
                            to_add.add(rel)

        # 2. Inspect working tree & untracked files in worktree.
        status_proc = self._run_git_text(
            ["-c", "core.quotepath=false", "status", "--porcelain", "-uall", "-z"],
            cwd=wt.path,
        )
        if status_proc.returncode == 0 and status_proc.stdout:
            tokens = status_proc.stdout.split("\0")
            i = 0
            while i < len(tokens):
                entry = tokens[i]
                i += 1
                if len(entry) < 4:
                    continue
                xy = entry[:2]
                rel = entry[3:].replace("\\", "/")
                if xy[0] in {"R", "C"} or xy[1] in {"R", "C"}:
                    orig_rel = tokens[i].replace("\\", "/") if i < len(tokens) else ""
                    i += 1
                    if orig_rel and not _is_ignored_rel(orig_rel) and not _is_escaped_or_linked_path(wt.path, orig_rel):
                        to_remove.add(orig_rel)
                if _is_ignored_rel(rel):
                    continue
                if _is_escaped_or_linked_path(wt.path, rel):
                    to_add.discard(rel)
                    continue
                full = wt.path / rel
                if xy == "??":
                    if _is_junction(full) or full.is_symlink():
                        continue
                    expected_hash = copied_hashes.get(rel)
                    if expected_hash is not None and full.is_file():
                        try:
                            if _sha256_file(full) == expected_hash:
                                continue
                        except OSError:
                            pass
                    if full.is_file():
                        to_add.add(rel)
                    continue
                if "D" in xy or not full.exists():
                    to_remove.add(rel)
                    to_add.discard(rel)
                elif not full.is_symlink() and not _is_junction(full) and full.is_file():
                    to_add.add(rel)
                    to_remove.discard(rel)

        if not to_add and not to_remove:
            return b"", []

        tmp_dir = Path(tempfile.mkdtemp(prefix="orin-merge-idx-"))
        tmp_index = tmp_dir / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        try:
            read_tree = self._run_git_text(["read-tree", base_commit], cwd=wt.path, env=env)
            if read_tree.returncode != 0:
                return b"", []

            base_tree = base_commit
            if copied_blobs:
                added_any = False
                for rel, blob_oid in sorted(copied_blobs.items()):
                    if _is_ignored_rel(rel):
                        continue
                    upd = self._run_git_text(
                        ["update-index", "--add", "--cacheinfo", f"100644,{blob_oid},{rel}"],
                        cwd=wt.path,
                        env=env,
                    )
                    if upd.returncode == 0:
                        added_any = True
                if added_any:
                    wt_tree = self._run_git_text(["write-tree"], cwd=wt.path, env=env)
                    if wt_tree.returncode == 0 and wt_tree.stdout.strip():
                        base_tree = wt_tree.stdout.strip()

            if to_add:
                for batch in _chunks(sorted(to_add)):
                    self._run_git_text(
                        ["-c", "core.longpaths=true", "add", "-f", "--", *batch],
                        cwd=wt.path,
                        env=env,
                    )
            if to_remove:
                for batch in _chunks(sorted(to_remove)):
                    self._run_git_text(
                        ["rm", "--cached", "--ignore-unmatch", "-f", "--", *batch],
                        cwd=wt.path,
                        env=env,
                    )

            # Security guard (RT-B-01): strip any newly added/modified symlink (120000) or gitlink (160000)
            # from the temporary index so they can never be emitted or applied to the parent workspace.
            raw_diff = self._run_git_text(
                ["-c", "core.quotepath=false", "diff", "--cached", "--raw", "-z", base_tree],
                cwd=wt.path,
                env=env,
            )
            forbidden_paths: list[str] = []
            if raw_diff.returncode == 0 and raw_diff.stdout:
                dparts = raw_diff.stdout.split("\0")
                di = 0
                while di < len(dparts):
                    dhdr = dparts[di]
                    di += 1
                    if not dhdr.startswith(":") or di >= len(dparts):
                        continue
                    dbits = dhdr[1:].split()
                    d_old_mode = dbits[0] if len(dbits) > 0 else ""
                    d_new_mode = dbits[1] if len(dbits) > 1 else ""
                    d_status = dbits[4] if len(dbits) > 4 else ""
                    d_rel = dparts[di].replace("\\", "/")
                    di += 1
                    if d_status.startswith(("R", "C")) and di < len(dparts):
                        d_rel = dparts[di].replace("\\", "/")
                        di += 1
                    if d_new_mode in {"120000", "160000"} or d_old_mode in {"120000", "160000"}:
                        forbidden_paths.append(d_rel)
            if forbidden_paths:
                for batch in _chunks(sorted(set(forbidden_paths))):
                    # Restore forbidden path back to base_tree state (or remove if absent in base_tree).
                    self._run_git_text(["rm", "--cached", "--ignore-unmatch", "-f", "--", *batch], cwd=wt.path, env=env)
                    self._run_git_text(["reset", "-q", base_tree, "--", *batch], cwd=wt.path, env=env)

            diff_proc = self._run_git_bytes(
                ["-c", "core.quotepath=false", "diff", "--cached", "--binary", base_tree],
                cwd=wt.path,
                env=env,
            )
            names_proc = self._run_git_text(
                ["-c", "core.quotepath=false", "diff", "--cached", "--name-only", "-z", base_tree],
                cwd=wt.path,
                env=env,
            )
            files = [
                p.replace("\\", "/")
                for p in (names_proc.stdout.split("\0") if names_proc.returncode == 0 else [])
                if p and not _is_ignored_rel(p.replace("\\", "/"))
            ]
            return diff_proc.stdout if diff_proc.returncode == 0 else b"", files
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _snapshot_parent_files(parent: Path, rel_paths: list[str]) -> dict[str, _ParentFileSnapshot]:
        snap: dict[str, _ParentFileSnapshot] = {}
        for rel in rel_paths:
            full = parent / rel
            try:
                st = os.lstat(full)
            except OSError:
                snap[rel] = _ParentFileSnapshot(exists=False)
                continue
            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(full)
                except OSError:
                    target = ""
                snap[rel] = _ParentFileSnapshot(
                    exists=True,
                    mode=stat.S_IMODE(st.st_mode),
                    symlink_target=target,
                )
            elif stat.S_ISREG(st.st_mode):
                snap[rel] = _ParentFileSnapshot(
                    exists=True,
                    content=full.read_bytes(),
                    mode=stat.S_IMODE(st.st_mode),
                )
            else:
                snap[rel] = _ParentFileSnapshot(exists=False)
        return snap

    def _snapshot_parent_index(self, parent: Path, rel_paths: list[str]) -> _ParentIndexSnapshot:
        entries: list[str] = []
        if rel_paths:
            for batch in _chunks(rel_paths):
                proc = self._run_git_text(["ls-files", "--stage", "-z", "--", *batch], cwd=parent)
                if proc.returncode == 0 and proc.stdout:
                    for raw in proc.stdout.split("\0"):
                        if raw:
                            entries.append(raw)
        raw_index_path: Path | None = None
        raw_index_bytes: bytes | None = None
        idx_proc = self._run_git_text(["rev-parse", "--git-path", "index"], cwd=parent)
        if idx_proc.returncode == 0 and idx_proc.stdout.strip():
            candidate = Path(idx_proc.stdout.strip())
            if not candidate.is_absolute():
                candidate = (parent / candidate).resolve()
            if candidate.is_file():
                try:
                    raw_index_bytes = candidate.read_bytes()
                    raw_index_path = candidate
                except OSError:
                    raw_index_path = None
                    raw_index_bytes = None
        return _ParentIndexSnapshot(
            entries=tuple(entries),
            raw_index_path=raw_index_path,
            raw_index_bytes=raw_index_bytes,
        )

    def _rollback_parent(
        self,
        parent: Path,
        rel_paths: list[str],
        disk_snapshot: dict[str, _ParentFileSnapshot | bytes | None],
        index_snapshot: _ParentIndexSnapshot | list[str],
    ) -> None:
        # 1. Restore exact bytes, permissions (st_mode), and symlinks on disk.
        for rel, entry_snap in disk_snapshot.items():
            target = parent / rel
            if isinstance(entry_snap, _ParentFileSnapshot):
                exists = entry_snap.exists
                content = entry_snap.content
                mode = entry_snap.mode
                symlink_target = entry_snap.symlink_target
            else:
                exists = entry_snap is not None
                content = entry_snap
                mode = None
                symlink_target = None

            if not exists:
                if target.exists() or target.is_symlink():
                    try:
                        if target.is_file() and not target.is_symlink():
                            try:
                                os.chmod(target, 0o644)
                            except OSError:
                                pass
                        target.unlink()
                    except OSError:
                        pass
                cur = target.parent
                while cur != parent and parent in cur.parents:
                    try:
                        cur.rmdir()
                    except OSError:
                        break
                    cur = cur.parent
            elif symlink_target is not None:
                if target.exists() or target.is_symlink():
                    try:
                        target.unlink()
                    except OSError:
                        pass
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(symlink_target, target)
            elif content is not None:
                if target.is_symlink():
                    try:
                        target.unlink()
                    except OSError:
                        pass
                elif target.exists():
                    try:
                        os.chmod(target, 0o644)
                    except OSError:
                        pass
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if mode is not None:
                    try:
                        os.chmod(target, mode)
                    except OSError:
                        pass

        # 2. Restore parent Git index atomically (prefer binary backup of .git/index; fallback to update-index --index-info).
        if isinstance(index_snapshot, _ParentIndexSnapshot):
            if index_snapshot.raw_index_path is not None and index_snapshot.raw_index_bytes is not None:
                tmp_idx = index_snapshot.raw_index_path.parent / f".orin-index-restore-{os.getpid()}-{threading.get_ident()}.tmp"
                try:
                    tmp_idx.write_bytes(index_snapshot.raw_index_bytes)
                    os.replace(tmp_idx, index_snapshot.raw_index_path)
                    return
                except OSError:
                    try:
                        tmp_idx.unlink(missing_ok=True)
                    except OSError:
                        pass
            entries = list(index_snapshot.entries)
        else:
            entries = list(index_snapshot)

        for batch in _chunks(rel_paths):
            self._run_git_text(["update-index", "--force-remove", "--", *batch], cwd=parent)
        if entries:
            lines: list[str] = []
            for entry in entries:
                # Format from `ls-files --stage`: "<mode> <sha> <stage>\t<path>"
                if "\t" not in entry:
                    continue
                meta_part, path_part = entry.split("\t", 1)
                bits = meta_part.split()
                if len(bits) >= 3:
                    lines.append(f"{bits[0]} {bits[1]} {bits[2]}\t{path_part}")
            if lines:
                self._run_git_text(
                    ["update-index", "--index-info"],
                    cwd=parent,
                    input_text="\n".join(lines) + "\n",
                )

    def _apply_patch_3way(
        self,
        parent: Path,
        patch_bytes: bytes,
        changed_files: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="orin-apply-"))
        patch_file = tmp_dir / "worktree.patch"
        tmp_index = tmp_dir / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        try:
            patch_file.write_bytes(patch_bytes)
            # Initialize temporary index from parent HEAD plus parent's current on-disk state
            # for changed_files so git apply --3way merges cleanly against unstaged/untracked
            # parent files without ever mutating the parent's real .git/index.
            self._run_git_text(["read-tree", "HEAD"], cwd=parent, env=env)
            if changed_files:
                existing: list[str] = []
                missing: list[str] = []
                for rel in changed_files:
                    full = parent / rel
                    if full.is_file() and not full.is_symlink() and not _is_junction(full):
                        existing.append(rel)
                    elif not full.exists() and not full.is_symlink():
                        missing.append(rel)
                if existing:
                    for batch in _chunks(existing):
                        self._run_git_text(
                            ["-c", "core.longpaths=true", "add", "-f", "--", *batch],
                            cwd=parent,
                            env=env,
                        )
                if missing:
                    for batch in _chunks(missing):
                        self._run_git_text(
                            ["rm", "--cached", "--ignore-unmatch", "-f", "--", *batch],
                            cwd=parent,
                            env=env,
                        )
            return self._run_git_text(
                [
                    "-c",
                    "core.longpaths=true",
                    "apply",
                    "--3way",
                    "--whitespace=nowarn",
                    str(patch_file),
                ],
                cwd=parent,
                env=env,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _extract_conflict_info(
        parent: Path,
        changed_files: list[str],
        stderr: str,
    ) -> tuple[list[str], dict[str, str]]:
        conflict_files: list[str] = []
        snippets: dict[str, str] = {}
        for rel in changed_files:
            full = parent / rel
            if not full.is_file():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "<<<<<<<" in text and ">>>>>>>" in text:
                conflict_files.append(rel)
                lines = text.splitlines()
                excerpt_lines: list[str] = []
                in_conflict = False
                for ln in lines:
                    if ln.startswith("<<<<<<<"):
                        in_conflict = True
                    if in_conflict:
                        excerpt_lines.append(ln)
                        if ln.startswith(">>>>>>>"):
                            in_conflict = False
                            if len(excerpt_lines) >= 40:
                                break
                snippets[rel] = "\n".join(excerpt_lines[:40])[:2000]
        if not conflict_files and stderr:
            for rel in changed_files:
                if rel in stderr:
                    conflict_files.append(rel)
        return conflict_files, snippets

    @staticmethod
    def _run_check_command(
        parent: Path,
        command: str,
        *,
        cancelled: Callable[[], bool],
        timeout_seconds: float = _CHECK_TIMEOUT_SECONDS,
    ) -> tuple[int, str, bool]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                if cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    out = proc.stdout.read() if proc.stdout is not None else ""
                    return -1, out, True
                rc = proc.poll()
                if rc is not None:
                    out = proc.stdout.read() if proc.stdout is not None else ""
                    return rc, out, False
                if time.monotonic() >= deadline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    out = proc.stdout.read() if proc.stdout is not None else ""
                    return 124, f"{out}\n[mergeCheckCommand timed out after {int(timeout_seconds)}s]", False
                time.sleep(0.05)
        finally:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

    @staticmethod
    def _run_git_text(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=env,
                input=input_text,
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

    @staticmethod
    def _run_git_bytes(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=False,
                timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=1, stdout=b"", stderr=str(error).encode("utf-8", errors="replace")
            )


__all__ = [
    "CommandAuthorizer",
    "MergeQueue",
    "MergeResult",
    "MergeState",
]
