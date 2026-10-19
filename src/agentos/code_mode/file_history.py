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
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_BLOB_BYTES = 10 * 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions on Windows
_LOCK = threading.RLock()


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
        try:
            loaded = json.loads(self._manifest_path(message_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) and isinstance(loaded.get("files"), dict) else None

    def _save(self, message_id: str, manifest: dict[str, Any]) -> None:
        _atomic_write(self._manifest_path(message_id), json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))

    def _manifests(self) -> list[dict[str, Any]]:
        folder = self.base / "messages"
        if not folder.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in folder.glob("*.json"):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(loaded, dict) and isinstance(loaded.get("files"), dict) and loaded.get("message_id"):
                out.append(loaded)
        out.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("message_id"))))
        return out

    # -- recording ---------------------------------------------------------

    def snapshot_before_write(self, message_id: str, target: Path, *, turn_id: str = "", kind: str = "message") -> dict[str, Any] | None:
        """Record ``target`` as it is now, unless ``message_id`` already did. Never raises: history is best-effort."""
        try:
            with _LOCK:
                manifest = self._load(message_id) or {
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

    def rewind(self, message_id: str, *, roots: Iterable[Path]) -> dict[str, Any]:
        """Restore every file ``message_id`` or a later message changed to its state just before ``message_id``.

        Two phases (W4-R10). First every file is checked, and its current content is copied into a new undo
        snapshot. Only when every file passes is anything written. A blocked rewind writes nothing: ``blocked`` is
        true, and ``skipped`` names each file that blocked it.
        """
        allowed = [Path(root) for root in roots]
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
                    outcome = self._restore(entry, allowed)
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
                self._save(str(item["message_id"]), item)
        return _rewind_result(message_id, restored=restored, deleted=deleted, unchanged=unchanged, skipped=skipped, undo_id=undo_id)

    def _blocker(self, entry: dict[str, Any], roots: Sequence[Path]) -> str | None:
        """Why ``entry`` cannot be rewound, or None. Reads the workspace and the history; writes nothing."""
        target = Path(str(entry.get("path") or ""))
        if not str(target) or not target.is_absolute():
            return "not an absolute path"
        if entry.get("unsupported"):
            return "it was not a regular file when it was recorded"
        if entry.get("too_large"):
            return "it was too large to keep a copy"
        if not _inside(target, roots):
            return "it is outside the working directories"
        parent = target.parent
        recorded_parent = entry.get("parent_identity")
        current_parent = _identity(parent)
        if _is_link(parent) or (recorded_parent and current_parent is not None and current_parent != recorded_parent):
            return "its directory was replaced after the snapshot"
        if _is_link(target):
            return "it is now a link"
        if target.exists() and not target.is_file():
            return "it is no longer a regular file"
        if target.exists() and target.stat().st_size > MAX_BLOB_BYTES:
            # Restoring or deleting it would destroy content that no undo snapshot can hold (W4-R10).
            return "its current content is too large to keep an undo copy"
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

    def _restore(self, entry: dict[str, Any], roots: Sequence[Path]) -> str:
        reason = self._blocker(entry, roots)
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
    skipped: Sequence[dict[str, str]] = (), blocked: bool = False, undo_id: str | None = None,
) -> dict[str, Any]:
    return {
        "message_id": str(message_id), "restored": sorted(restored), "deleted": sorted(deleted),
        "unchanged": sorted(unchanged), "skipped": list(skipped), "blocked": blocked, "undo_id": undo_id,
    }


__all__ = ["FileHistory", "MAX_BLOB_BYTES"]
