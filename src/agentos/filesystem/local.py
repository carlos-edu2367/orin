from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
from uuid import uuid4

from agentos.workspaces.models import FilesystemObjectIdentity

from .in_memory import InMemoryFilesystemAdapter
from .models import FilesystemEntryKind, FilesystemError, FilesystemErrorCode, FilesystemOperationContext, WorkspacePath
from .ports import CanonicalWorkspaceRoot


class LocalWorkspaceRootResolver:
    """Owns the private mapping from Workspace root references to local directories."""

    def __init__(self, base_directory: Path) -> None:
        self._base = Path(base_directory).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._roots: dict[str, tuple[CanonicalWorkspaceRoot, Path]] = {}
        self._swapped: set[str] = set()

    @staticmethod
    def _identity(location: Path) -> FilesystemObjectIdentity:
        stat = location.stat()
        return FilesystemObjectIdentity(f"device:{stat.st_dev}:object:{stat.st_ino}")

    def root_for(self, context: FilesystemOperationContext) -> CanonicalWorkspaceRoot:
        with self._lock:
            existing = self._roots.get(context.workspace_id)
            if existing is not None:
                return existing[0]
            location = self._base / f"workspace-{uuid4().hex}"
            location.mkdir(mode=0o700)
            root = CanonicalWorkspaceRoot(f"local-root:{uuid4().hex}", context.workspace_id, self._identity(location), 1)
            self._roots[context.workspace_id] = (root, location)
            return root

    def resolve(self, context: FilesystemOperationContext):
        with self._lock:
            item = self._roots.get(context.workspace_id)
            return item[0] if item is not None else FilesystemError(FilesystemErrorCode.NOT_FOUND)

    def revalidate(self, root: CanonicalWorkspaceRoot, context: FilesystemOperationContext) -> bool:
        with self._lock:
            item = self._roots.get(context.workspace_id)
            if item is None or context.workspace_id in self._swapped:
                return False
            current, location = item
            try:
                return current.root_ref == root.root_ref and current.identity == root.identity and self._identity(location) == current.identity and location.resolve() == location
            except (OSError, RuntimeError):
                return False

    def location_for(self, workspace_id: str) -> Path:
        with self._lock:
            return self._roots[workspace_id][1]

    def swap_identity(self, workspace_id: str) -> None:
        with self._lock:
            self._swapped.add(workspace_id)


class LocalFilesystemAdapter(InMemoryFilesystemAdapter):
    """Operational adapter using only locations supplied by LocalWorkspaceRootResolver."""

    supports_atomic = True

    def __init__(self, resolver: LocalWorkspaceRootResolver) -> None:
        super().__init__()
        self.resolver = resolver
        self._lock = RLock()
        self.before_effect = None

    def _location(self, root: CanonicalWorkspaceRoot) -> Path | FilesystemError:
        try:
            location = self.resolver.location_for(root.workspace_id)
            if not self.resolver.revalidate(root, FilesystemOperationContext("system", root.workspace_id, "filesystem", "filesystem", "filesystem", "filesystem", "system:filesystem")):
                return self._error(FilesystemErrorCode.UNSAFE_ROOT, "root identity changed")
            resolved = location.resolve(strict=True)
            if resolved != location or location.is_symlink() or not location.is_dir():
                return self._error(FilesystemErrorCode.UNSAFE_ROOT, "root containment cannot be proven")
            return location
        except (KeyError, OSError, RuntimeError):
            return self._error(FilesystemErrorCode.UNSAFE_ROOT, "root unavailable")

    @staticmethod
    def _reparse(location: Path) -> bool:
        try:
            stat = location.lstat()
        except OSError:
            return False
        return location.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)

    def _safe(self, root: CanonicalWorkspaceRoot, path: WorkspacePath, *, allow_missing_leaf: bool = False) -> Path | FilesystemError:
        location = self._location(root)
        if isinstance(location, FilesystemError):
            return location
        current = location
        for index, segment in enumerate(path.segments):
            current = current / segment
            exists = current.exists() or current.is_symlink()
            if not exists:
                if allow_missing_leaf and index == len(path.segments) - 1:
                    return current
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if self._reparse(current):
                return self._error(FilesystemErrorCode.UNSAFE_ROOT, "link or reparse point rejected")
            try:
                if current.resolve(strict=True).parent != current.parent.resolve(strict=True) and index == 0:
                    return self._error(FilesystemErrorCode.UNSAFE_ROOT)
                current.resolve(strict=True).relative_to(location)
            except (OSError, RuntimeError, ValueError):
                return self._error(FilesystemErrorCode.UNSAFE_ROOT, "containment cannot be proven")
        return current

    def _pre_effect(self, root: CanonicalWorkspaceRoot) -> FilesystemError | None:
        if self.before_effect is not None:
            self.before_effect()
        if not self.resolver.revalidate(root, FilesystemOperationContext("system", root.workspace_id, "filesystem", "filesystem", "filesystem", "filesystem", "system:filesystem")):
            return self._error(FilesystemErrorCode.UNSAFE_ROOT, "root changed before effect")
        return None

    @staticmethod
    def _version(location: Path) -> int:
        stat = location.stat()
        return max(1, int(stat.st_mtime_ns))

    def _entry(self, location: Path, relative: WorkspacePath):
        from .models import FilesystemEntry
        if self._reparse(location):
            return self._error(FilesystemErrorCode.UNSAFE_ROOT, "link or reparse point rejected")
        if location.is_dir():
            kind, size = FilesystemEntryKind.DIRECTORY, 0
        elif location.is_file():
            kind, size = FilesystemEntryKind.FILE, location.stat().st_size
        else:
            return self._error(FilesystemErrorCode.TYPE_MISMATCH)
        return FilesystemEntry(relative, kind, size, self._version(location), modified_at=datetime.fromtimestamp(location.stat().st_mtime, timezone.utc))

    def stat(self, root, path, *, expected_version=None):
        with self._lock:
            location = self._safe(root, path)
            if isinstance(location, FilesystemError):
                return location
            entry = self._entry(location, path)
            if isinstance(entry, FilesystemError):
                return entry
            return self._error(FilesystemErrorCode.CONFLICT) if expected_version is not None and entry.version != expected_version else entry

    def list(self, root, path, *, recursive, maximum_entries):
        with self._lock:
            location = self._safe(root, path)
            if isinstance(location, FilesystemError):
                return location
            if not location.is_dir():
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            found = []
            iterator = location.rglob("*") if recursive else location.iterdir()
            for child in iterator:
                relative = WorkspacePath(tuple(child.relative_to(self.resolver.location_for(root.workspace_id)).parts))
                entry = self._entry(child, relative)
                if isinstance(entry, FilesystemError):
                    return entry
                found.append(entry)
                if len(found) > maximum_entries:
                    return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            return __import__("agentos.filesystem.models", fromlist=["FilesystemPage"]).FilesystemPage(tuple(sorted(found, key=lambda item: item.path.segments)))

    def read(self, root, path, sink, *, offset_bytes, maximum_bytes, expected_version):
        with self._lock:
            location = self._safe(root, path)
            if isinstance(location, FilesystemError):
                return location
            entry = self.stat(root, path, expected_version=expected_version)
            if isinstance(entry, FilesystemError):
                return entry
            if entry.kind is not FilesystemEntryKind.FILE:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            try:
                with location.open("rb") as stream:
                    stream.seek(offset_bytes)
                    data = stream.read(maximum_bytes)
                written = sink.write(data)
                if written is not None and written != len(data):
                    return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="APPLIED")
                from .models import FilesystemReadResult
                next_offset = offset_bytes + len(data) if offset_bytes + len(data) < entry.size_bytes else None
                return FilesystemReadResult(len(data), next_offset)
            except OSError:
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")

    def create_directory(self, root, path, *, create_parents, maximum_depth):
        with self._lock:
            if len(path.segments) > maximum_depth:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            target = self._safe(root, path, allow_missing_leaf=True)
            if isinstance(target, FilesystemError):
                return target
            if target.exists():
                return self._error(FilesystemErrorCode.CONFLICT)
            parent = target.parent
            if not parent.exists() and not create_parents:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            try:
                if self._pre_effect(root):
                    return self._pre_effect(root)
                target.mkdir(parents=create_parents, exist_ok=False)
                return self._entry(target, path)
            except FileExistsError:
                return self._error(FilesystemErrorCode.CONFLICT)
            except OSError:
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")

    def write(self, root, path, source, *, mode, atomicity, maximum_bytes, maximum_entries, expected_version):
        with self._lock:
            target = self._safe(root, path, allow_missing_leaf=True)
            if isinstance(target, FilesystemError):
                return target
            current = self.stat(root, path) if target.exists() else None
            if isinstance(current, FilesystemError) and current.code is not FilesystemErrorCode.NOT_FOUND:
                return current
            if mode == "CREATE_NEW" and current is not None:
                return self._error(FilesystemErrorCode.CONFLICT)
            if expected_version is not None and (current is None or current.version != expected_version):
                return self._error(FilesystemErrorCode.CONFLICT)
            if target.parent is None or not target.parent.exists():
                return self._error(FilesystemErrorCode.NOT_FOUND)
            data = bytes(source.read(maximum_bytes + 1))
            if len(data) > maximum_bytes:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            if mode == "APPEND" and current is not None:
                try:
                    data = target.read_bytes() + data
                except OSError:
                    return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")
            if len(data) > maximum_bytes:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            effect_check = self._pre_effect(root)
            if effect_check is not None:
                return effect_check
            try:
                if atomicity == "REQUIRE_ATOMIC":
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".agentos-", delete=False) as staged:
                        staged.write(data)
                        staged.flush()
                        os.fsync(staged.fileno())
                        staging = Path(staged.name)
                    os.replace(staging, target)
                else:
                    target.write_bytes(data)
                entry = self._entry(target, path)
                from .models import FilesystemWriteResult
                return FilesystemWriteResult(entry, len(data))
            except OSError:
                try:
                    if 'staging' in locals() and staging.exists():
                        staging.unlink()
                except OSError as cleanup_error:
                    _ = cleanup_error
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")

    def copy(self, root, source, destination, *, expected_source_version, overwrite, maximum_bytes, maximum_entries):
        with self._lock:
            source_location = self._safe(root, source)
            destination_location = self._safe(root, destination, allow_missing_leaf=True)
            if isinstance(source_location, FilesystemError):
                return source_location
            if isinstance(destination_location, FilesystemError):
                return destination_location
            source_entry = self.stat(root, source, expected_version=expected_source_version)
            if isinstance(source_entry, FilesystemError):
                return source_entry
            if source_entry.kind is not FilesystemEntryKind.FILE:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            if destination_location.exists() and overwrite == "NEVER":
                return self._error(FilesystemErrorCode.CONFLICT)
            if not destination_location.parent.exists():
                return self._error(FilesystemErrorCode.NOT_FOUND)
            effect_check = self._pre_effect(root)
            if effect_check is not None:
                return effect_check
            try:
                shutil.copyfile(source_location, destination_location)
                entry = self._entry(destination_location, destination)
                from .models import FilesystemMutationResult
                return FilesystemMutationResult(entry, 1)
            except OSError:
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")

    def move(self, root, source, destination, *, expected_source_version, overwrite):
        with self._lock:
            source_location = self._safe(root, source)
            destination_location = self._safe(root, destination, allow_missing_leaf=True)
            if isinstance(source_location, FilesystemError):
                return source_location
            if isinstance(destination_location, FilesystemError):
                return destination_location
            source_entry = self.stat(root, source, expected_version=expected_source_version)
            if isinstance(source_entry, FilesystemError):
                return source_entry
            if destination_location.exists() and overwrite == "NEVER":
                return self._error(FilesystemErrorCode.CONFLICT)
            if not destination_location.parent.exists():
                return self._error(FilesystemErrorCode.NOT_FOUND)
            effect_check = self._pre_effect(root)
            if effect_check is not None:
                return effect_check
            try:
                os.replace(source_location, destination_location)
                entry = self._entry(destination_location, destination)
                from .models import FilesystemMutationResult
                return FilesystemMutationResult(entry, 1)
            except OSError:
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")

    def remove(self, root, path, *, expected_version, recursive, maximum_entries):
        with self._lock:
            target = self._safe(root, path)
            if isinstance(target, FilesystemError):
                return target
            entry = self.stat(root, path, expected_version=expected_version)
            if isinstance(entry, FilesystemError):
                return entry
            descendants = list(target.rglob("*")) if target.is_dir() else []
            if descendants and not recursive:
                return self._error(FilesystemErrorCode.CONFLICT)
            if len(descendants) + 1 > maximum_entries:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            if any(self._reparse(item) for item in descendants):
                return self._error(FilesystemErrorCode.UNSAFE_ROOT)
            effect_check = self._pre_effect(root)
            if effect_check is not None:
                return effect_check
            try:
                if target.is_dir():
                    for item in sorted(descendants, key=lambda p: len(p.parts), reverse=True):
                        item.unlink() if item.is_file() else item.rmdir()
                    target.rmdir()
                else:
                    target.unlink()
                from .models import FilesystemMutationResult
                return FilesystemMutationResult(None, len(descendants) + 1)
            except OSError:
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="UNKNOWN")


__all__ = ["LocalFilesystemAdapter", "LocalWorkspaceRootResolver"]
