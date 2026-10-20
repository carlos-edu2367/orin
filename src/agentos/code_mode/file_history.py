"""Content-addressed file history: each file as it was before Orin first changed it, per user message (W4).

Layout under ``<root>/<conversation>/``:

* ``blobs/<sha256>`` — the exact bytes a file held, written atomically and verified before any restore;
* ``messages/<message>.json`` — the files one user message's turn changed and what each held before.

A rewind to message M puts every file that M or any later message changed back the way it was just
before M. It first saves the current state as an undo snapshot, and it refuses, file by file, to write
through anything that is not what was recorded: a file that became a link or a directory, a directory
that was replaced (a different file id, or a link or junction now), a backup whose checksum no longer
matches, or a path outside the working directories it is given. Changes made by shell commands are not
recorded (as in Claude Code).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_BLOB_BYTES = 10 * 1024 * 1024
# The one blocker the person may waive for a file (W7 force_unbacked): its current content cannot be backed up.
TOO_LARGE_NOW = "its current content is too large to keep an undo copy"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions on Windows
_LOCK = threading.RLock()
# A sharing violation (another thread or process holds a manifest) is retried this long before it is an error (W4-R20).
_RETRY_SECONDS = 1.0


class ManifestUnavailable(OSError):
    """A manifest exists but cannot be read or parsed. It is never overwritten (W4-R20)."""


def _retry_sharing(operation: Callable[[], Any]) -> Any:
    deadline = time.monotonic() + _RETRY_SECONDS
    while True:
        try:
            return operation()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _read_json(path: Path) -> dict[str, Any] | None:
    """A manifest as a dict, or None when the file does not exist. Raises ManifestUnavailable when it is damaged."""
    try:
        text = _retry_sharing(lambda: path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise ManifestUnavailable(str(path)) from error
    try:
        loaded = json.loads(text)
    except ValueError as error:
        raise ManifestUnavailable(str(path)) from error
    if not isinstance(loaded, dict) or not isinstance(loaded.get("files"), dict):
        raise ManifestUnavailable(str(path))
    return loaded


def _safe_name(value: str) -> str:
    """A directory/file name for an id; a changed id gets a hash suffix, so two ids never share one."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("an id is required")
    cleaned = _UNSAFE.sub("_", text)[:80]
    if cleaned != text or cleaned in {".", ".."}:
        cleaned = f"{cleaned}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
    return cleaned


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".orin-tmp-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)  # our own temporary file, never the user's
        except OSError:
            pass
        raise


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (a junction included)."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _identity(path: Path) -> list[int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return [int(info.st_dev), int(info.st_ino)]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(target: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = target.parent.resolve() / target.name
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _working_root(target: Path, roots: Sequence[Path]) -> Path | None:
    """The root that holds ``target`` by its spelling, or None."""
    absolute = Path(os.path.abspath(target))
    for root in roots:
        candidate = Path(os.path.abspath(root))
        try:
            absolute.relative_to(candidate)
        except ValueError:
            continue
        return candidate
    return None


def _intact_root(root: Path) -> bool:
    """A working directory is intact when it is not a link and resolves to the path it was given (W4-R12)."""
    if _is_link(root):
        return False
    try:
        resolved = os.path.realpath(root)
    except OSError:
        return False
    return os.path.normcase(resolved) == os.path.normcase(os.path.abspath(root))


def _link_below(root: Path, directory: Path) -> bool:
    """Whether a link or junction stands between ``root`` and ``directory``, both included (W4-R12)."""
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        return True
    current = root
    for part in parts:
        current = current / part
        if _is_link(current):
            return True
    return False


class FileHistory:
    def __init__(self, root: Path | str, conversation_id: str) -> None:
        self.base = Path(root) / _safe_name(conversation_id)

    # -- storage -----------------------------------------------------------

    def _manifest_path(self, message_id: str) -> Path:
        return self.base / "messages" / f"{_safe_name(message_id)}.json"

    def _blob_path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(str(digest or "")):
            raise ValueError("not a sha256 digest")
        return self.base / "blobs" / digest

    def _load(self, message_id: str) -> dict[str, Any] | None:
        """The message's manifest, or None when it has none. Raises ManifestUnavailable when the file is damaged."""
        return _read_json(self._manifest_path(message_id))

    def _save(self, message_id: str, manifest: dict[str, Any]) -> None:
        self._load(message_id)  # a damaged manifest raises here and is never overwritten (W4-R20)
        data = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        _retry_sharing(lambda: _atomic_write(self._manifest_path(message_id), data))

    def _record_sidecar(self, message_id: str, target: Path, *, turn_id: str, kind: str) -> dict[str, Any]:
        """Record ``target`` in a sidecar manifest, because the message's own manifest is damaged (W4-R20)."""
        key = os.path.normcase(str(Path(target)))
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        path = self.base / "messages" / f"{_safe_name(message_id)}.recovered-{digest}.json"
        sidecar = _read_json(path) or {
            "message_id": str(message_id), "turn_id": str(turn_id or ""), "kind": kind,
            "created_at": _now(), "rewound_at": None, "files": {},
        }
        if key in sidecar["files"]:
            return dict(sidecar["files"][key])
        entry = self._record(Path(target))
        sidecar["files"][key] = entry
        data = json.dumps(sidecar, ensure_ascii=False, indent=1).encode("utf-8")
        _retry_sharing(lambda: _atomic_write(path, data))
        return dict(entry)

    def _manifests(self) -> list[dict[str, Any]]:
        """Every recorded message, oldest first. A message's sidecar manifests are merged into its own (W4-R20)."""
        folder = self.base / "messages"
        if not folder.is_dir():
            return []
        merged: dict[str, dict[str, Any]] = {}
        for path in sorted(folder.glob("*.json")):  # "<id>.json" sorts before its "<id>.recovered-…" sidecars
            try:
                loaded = _read_json(path)
            except ManifestUnavailable:
                continue
            if loaded is None or not loaded.get("message_id"):
                continue
            key = str(loaded["message_id"])
            if key not in merged:
                merged[key] = {**loaded, "files": dict(loaded["files"])}
                continue
            for file_key, entry in loaded["files"].items():
                merged[key]["files"].setdefault(file_key, entry)
        out = list(merged.values())
        out.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("message_id"))))
        return out

    def has_snapshot(self, message_id: str, target: Path) -> bool:
        """Whether ``message_id`` already recorded ``target``. A damaged manifest answers True: its entries are kept."""
        try:
            with _LOCK:
                manifest = self._load(message_id)
        except ManifestUnavailable:
            return True
        return manifest is not None and os.path.normcase(str(Path(target))) in manifest["files"]

    def forget_snapshot(self, message_id: str, target: Path) -> None:
        """Drop the entry ``message_id`` recorded for ``target`` because that write never happened. Never raises."""
        try:
            with _LOCK:
                manifest = self._load(message_id)
                if manifest is not None and manifest["files"].pop(os.path.normcase(str(Path(target))), None) is not None:
                    self._save(message_id, manifest)
        except Exception:  # noqa: BLE001 - history is best-effort
            logger.warning("file history could not drop the entry for %s", target, exc_info=True)

    # -- recording ---------------------------------------------------------

    def snapshot_before_write(self, message_id: str, target: Path, *, turn_id: str = "", kind: str = "message") -> dict[str, Any] | None:
        """Record ``target`` as it is now, unless ``message_id`` already did. Never raises: history is best-effort."""
        try:
            with _LOCK:
                try:
                    current = self._load(message_id)
                except ManifestUnavailable:
                    return self._record_sidecar(message_id, Path(target), turn_id=turn_id, kind=kind)
                manifest = current or {
                    "message_id": str(message_id), "turn_id": str(turn_id or ""), "kind": kind,
                    "created_at": _now(), "rewound_at": None, "files": {},
                }
                key = os.path.normcase(str(Path(target)))
                if key in manifest["files"]:
                    return dict(manifest["files"][key])
                entry = self._record(Path(target))
                manifest["files"][key] = entry
                self._save(message_id, manifest)
                return dict(entry)
        except Exception:  # noqa: BLE001 - a failed snapshot must never fail the edit
            logger.warning("file history snapshot failed for %s", target, exc_info=True)
            return None

    def _record(self, target: Path) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "path": str(target), "existed": False, "parent": str(target.parent),
            "parent_identity": _identity(target.parent), "captured_at": _now(),
        }
        if _is_link(target) or (target.exists() and not target.is_file()):
            entry.update(existed=True, unsupported=True)
            return entry
        if not target.exists():
            return entry
        size = target.stat().st_size
        if size > MAX_BLOB_BYTES:
            entry.update(existed=True, too_large=True, size=size)
            return entry
        data = target.read_bytes()
        digest = _digest(data)
        blob = self._blob_path(digest)
        if not blob.exists():
            _atomic_write(blob, data)
        entry.update(existed=True, sha256=digest, size=len(data))
        return entry

    def messages(self) -> list[dict[str, Any]]:
        """Each recorded message, oldest first, with the files its turn changed (no file contents)."""
        return [
            {
                "message_id": item["message_id"], "turn_id": item.get("turn_id") or "", "kind": item.get("kind") or "message",
                "created_at": item.get("created_at"), "rewound_at": item.get("rewound_at"),
                "files": sorted(str(entry.get("path")) for entry in item["files"].values() if isinstance(entry, dict)),
            }
            for item in self._manifests()
        ]

    # -- rewind --------------------------------------------------------------

    def rewind(self, message_id: str, *, roots: Iterable[Path], force_unbacked: Iterable[str] = ()) -> dict[str, Any]:
        """Restore every file ``message_id`` or a later message changed to its state just before ``message_id``.

        Two phases (W4-R10). First every file is checked, and its current content is copied into a new undo
        snapshot. Only when every file passes is anything written. A blocked rewind writes nothing: ``blocked`` is
        true, and ``skipped`` names each file that blocked it.

        ``force_unbacked`` (W7) names files whose current content is too large for an undo copy and that the person
        chose to rewind anyway: only that one reason is waived, only for those paths; they are listed as
        ``unbacked`` (the rewind cannot be undone for them). Every other reason still blocks.
        """
        allowed = [Path(root) for root in roots]
        forced = {os.path.normcase(str(item)) for item in force_unbacked if str(item)}
        unbacked: list[str] = []
        with _LOCK:
            manifests = self._manifests()
            index = next((i for i, item in enumerate(manifests) if item.get("message_id") == str(message_id)), None)
            if index is None:
                raise LookupError(f"no file history for message '{message_id}'")
            chosen = manifests[index]
            plan: dict[str, dict[str, Any]] = {}
            if chosen.get("kind") == "undo":
                plan = {key: entry for key, entry in chosen["files"].items() if isinstance(entry, dict)}
                affected = [chosen]
            else:
                affected = [item for item in manifests[index:] if item.get("kind") != "undo"]
                for item in affected:
                    for key, entry in item["files"].items():
                        if isinstance(entry, dict):
                            plan.setdefault(key, entry)
            undo_id = f"undo-{_safe_name(message_id)}-{int(datetime.now(UTC).timestamp() * 1000)}"
            # Phase 1: check every file and copy its current content. Nothing in the workspace is written here.
            blockers: list[dict[str, str]] = []
            undo_files: dict[str, dict[str, Any]] = {}
            for key, entry in plan.items():
                path = str(entry.get("path") or "")
                reason = self._blocker(entry, allowed)
                if reason == TOO_LARGE_NOW and os.path.normcase(path) in forced:
                    # Only the size is waived: every check after it (the backup, its checksum) still runs.
                    reason = self._blocker(entry, allowed, waive_size=True)
                    if reason is None:
                        unbacked.append(path)  # the person accepted losing its current content: no undo copy
                        continue
                if reason is None and Path(path).exists():
                    try:
                        undo_files[key] = self._record(Path(path))
                    except Exception as error:  # noqa: BLE001 - the copy decides whether the rewind may run
                        reason = f"its current content could not be copied for undo ({type(error).__name__})"
                if reason is not None:
                    blockers.append({"path": path, "reason": reason})
            if blockers:
                return _rewind_result(message_id, skipped=blockers, blocked=True)
            undo_manifest = {
                "message_id": undo_id, "turn_id": "", "kind": "undo", "created_at": _now(), "rewound_at": None, "files": undo_files,
            }
            try:
                self._save(undo_id, undo_manifest)
            except Exception as error:  # noqa: BLE001 - no undo copy, no rewind
                reason = f"the undo snapshot could not be saved ({type(error).__name__})"
                return _rewind_result(message_id, skipped=[{"path": str(self._manifest_path(undo_id)), "reason": reason}], blocked=True)
            # Phase 2: apply. Every file is checked again just before it is written.
            restored: list[str] = []
            deleted: list[str] = []
            unchanged: list[str] = []
            skipped: list[dict[str, str]] = []
            for entry in plan.values():
                path = str(entry.get("path") or "")
                try:
                    outcome = self._restore(entry, allowed, waive_size=path in unbacked)
                except Exception as error:  # noqa: BLE001 - one file never stops the others
                    outcome = f"skip: {type(error).__name__}"
                if outcome == "restored":
                    restored.append(path)
                elif outcome == "deleted":
                    deleted.append(path)
                elif outcome == "unchanged":
                    unchanged.append(path)
                else:
                    skipped.append({"path": path, "reason": outcome.removeprefix("skip: ")})
            stamp = _now()
            for item in affected:
                item["rewound_at"] = stamp
                try:
                    self._save(str(item["message_id"]), item)
                except ManifestUnavailable:
                    logger.warning("the rewind of message %s was not recorded: its manifest is damaged", item.get("message_id"))
        return _rewind_result(message_id, restored=restored, deleted=deleted, unchanged=unchanged, skipped=skipped, undo_id=undo_id,
                              unbacked=unbacked)

    def _blocker(self, entry: dict[str, Any], roots: Sequence[Path], *, waive_size: bool = False) -> str | None:
        """Why ``entry`` cannot be rewound, or None. Reads the workspace and the history; writes nothing."""
        target = Path(str(entry.get("path") or ""))
        if not str(target) or not target.is_absolute():
            return "not an absolute path"
        if entry.get("unsupported"):
            return "it was not a regular file when it was recorded"
        if entry.get("too_large"):
            return "it was too large to keep a copy"
        container = _working_root(target, roots)
        if container is None or not _inside(target, roots):
            return "it is outside the working directories"
        # A working directory that is a junction resolves into another folder, so every path below it passes the
        # resolved check: the root itself, and each folder between it and the file, must be plain (W4-R12).
        if not _intact_root(container):
            return "its working directory was replaced after the snapshot"
        if _link_below(container, target.parent):
            return "its directory was replaced after the snapshot"
        parent = target.parent
        recorded_parent = entry.get("parent_identity")
        current_parent = _identity(parent)
        if _is_link(parent) or (recorded_parent and current_parent is not None and current_parent != recorded_parent):
            return "its directory was replaced after the snapshot"
        if _is_link(target):
            return "it is now a link"
        if target.exists() and not target.is_file():
            return "it is no longer a regular file"
        if target.exists() and target.stat().st_size > MAX_BLOB_BYTES and not waive_size:
            # Restoring or deleting it would destroy content that no undo snapshot can hold (W4-R10).
            return TOO_LARGE_NOW
        if not entry.get("existed"):
            return None
        digest = str(entry.get("sha256") or "")
        try:
            data = self._blob_path(digest).read_bytes()
        except (OSError, ValueError):
            return "its backup is missing"
        if _digest(data) != digest:
            return "its backup does not match its checksum"
        return None

    def _restore(self, entry: dict[str, Any], roots: Sequence[Path], *, waive_size: bool = False) -> str:
        reason = self._blocker(entry, roots, waive_size=waive_size)
        if reason is not None:
            return f"skip: {reason}"
        target = Path(str(entry.get("path") or ""))
        if not entry.get("existed"):
            if not target.exists():
                return "unchanged"
            target.unlink()  # the turn created it; the rewind removes it again
            return "deleted"
        digest = str(entry.get("sha256") or "")
        data = self._blob_path(digest).read_bytes()
        if target.exists() and _digest(target.read_bytes()) == digest:
            return "unchanged"
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Checked once more just before the swap; os.replace never follows a link at the target.
        if _is_link(parent) or _is_link(target):
            return "skip: it became a link during the rewind"
        _atomic_write(target, data)
        return "restored"


def _rewind_result(
    message_id: str, *, restored: Sequence[str] = (), deleted: Sequence[str] = (), unchanged: Sequence[str] = (),
    skipped: Sequence[dict[str, str]] = (), blocked: bool = False, undo_id: str | None = None, unbacked: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "message_id": str(message_id), "restored": sorted(restored), "deleted": sorted(deleted),
        "unchanged": sorted(unchanged), "skipped": list(skipped), "blocked": blocked, "undo_id": undo_id,
        "unbacked": sorted(unbacked),
    }


__all__ = ["FileHistory", "MAX_BLOB_BYTES", "TOO_LARGE_NOW"]
