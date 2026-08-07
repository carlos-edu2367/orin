from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
from threading import RLock

from agentos.events.models import DataClassification
from agentos.workspaces.models import FilesystemObjectIdentity

from .models import FilesystemEntryKind, FilesystemError, FilesystemErrorCode, FilesystemOperationContext, WorkspacePath
from .ports import CanonicalWorkspaceRoot


@dataclass
class _Node:
    kind: FilesystemEntryKind
    data: bytes = b""
    version: int = 1
    modified_at: datetime | None = None
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        self.modified_at = self.modified_at or datetime.now(timezone.utc)


class InMemoryWorkspaceRootResolver:
    """Logical root resolver; no physical path exists in this adapter."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._roots: dict[str, CanonicalWorkspaceRoot] = {}
        self._identities: dict[str, FilesystemObjectIdentity] = {}
        self._counter = 0
        self._swapped: set[str] = set()

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    def resolve(self, context: FilesystemOperationContext) -> CanonicalWorkspaceRoot | FilesystemError:
        with self._lock:
            root = self._roots.get(context.workspace_id)
            if root is None:
                return FilesystemError(FilesystemErrorCode.NOT_FOUND)
            if context.workspace_id in self._swapped:
                return FilesystemError(FilesystemErrorCode.UNSAFE_ROOT, "root identity changed")
            return root

    def provision(self, context: FilesystemOperationContext) -> CanonicalWorkspaceRoot:
        with self._lock:
            root = self._roots.get(context.workspace_id)
            if root is not None:
                return root
            identity = FilesystemObjectIdentity(self._next("identity"))
            root = CanonicalWorkspaceRoot(self._next("root"), context.workspace_id, identity, 1)
            self._roots[context.workspace_id] = root
            self._identities[context.workspace_id] = identity
            return root

    def revalidate(self, root: CanonicalWorkspaceRoot, context: FilesystemOperationContext) -> bool:
        with self._lock:
            current = self._roots.get(context.workspace_id)
            return current is not None and current.root_ref == root.root_ref and current.identity == root.identity and context.workspace_id not in self._swapped

    def swap_identity(self, workspace_id: str) -> None:
        with self._lock:
            self._swapped.add(workspace_id)


class WorkspaceBackedRootResolver:
    """Composes the public Workspace manager root/identity authority with logical storage."""

    def __init__(self, workspace_manager) -> None:
        self.workspace_manager = workspace_manager
        self._roots: dict[str, CanonicalWorkspaceRoot] = {}

    @staticmethod
    def _workspace_context(context: FilesystemOperationContext):
        from agentos.workspaces.models import WorkspaceOperationContext
        return WorkspaceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, "workspace.resource", context.actor)

    def resolve(self, context: FilesystemOperationContext):
        from agentos.workspaces.models import InspectWorkspace
        snapshot = self.workspace_manager.inspect(InspectWorkspace(self._workspace_context(context)))
        if hasattr(snapshot, "code") or snapshot.root_descriptor is None:
            return FilesystemError(FilesystemErrorCode.NOT_FOUND)
        if snapshot.state.value not in ("ACTIVE", "ARCHIVED"):
            return FilesystemError(FilesystemErrorCode.UNSAFE_ROOT, "workspace state does not permit filesystem")
        root = CanonicalWorkspaceRoot(f"workspace-root:{context.workspace_id}", context.workspace_id, snapshot.root_descriptor.root_identity, snapshot.policy_version)
        self._roots[context.workspace_id] = root
        return root

    def revalidate(self, root: CanonicalWorkspaceRoot, context: FilesystemOperationContext) -> bool:
        current = self.resolve(context)
        return not isinstance(current, FilesystemError) and current.root_ref == root.root_ref and current.identity == root.identity and current.policy_version == root.policy_version


class InMemoryFilesystemAdapter:
    """Deterministic logical adapter for all supported Filesystem operations."""

    supports_atomic = True

    def __init__(self) -> None:
        self._lock = RLock()
        self._trees: dict[str, dict[tuple[str, ...], _Node]] = {}

    @staticmethod
    def _error(code: FilesystemErrorCode, reason: str = "filesystem operation failed", *, effect_state: str = "NOT_APPLIED") -> FilesystemError:
        return FilesystemError(code, reason, effect_state)

    def _tree(self, root: CanonicalWorkspaceRoot) -> dict[tuple[str, ...], _Node]:
        tree = self._trees.setdefault(root.root_ref, {})
        tree.setdefault((), _Node(FilesystemEntryKind.DIRECTORY))
        return tree

    @staticmethod
    def _entry(path: WorkspacePath, node: _Node):
        from .models import FilesystemEntry
        return FilesystemEntry(path, node.kind, len(node.data), node.version, node.classification, node.modified_at)

    @staticmethod
    def _children(tree: dict[tuple[str, ...], _Node], path: tuple[str, ...], recursive: bool):
        for candidate in sorted(tree):
            if candidate == path or not candidate[: len(path)] == path:
                continue
            rest = candidate[len(path):]
            if not rest:
                continue
            if recursive or len(rest) == 1:
                yield candidate

    def stat(self, root, path, *, expected_version=None):
        with self._lock:
            node = self._tree(root).get(path.segments)
            if node is None:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if expected_version is not None and node.version != expected_version:
                return self._error(FilesystemErrorCode.CONFLICT)
            return self._entry(path, node)

    def list(self, root, path, *, recursive, maximum_entries):
        with self._lock:
            tree = self._tree(root)
            parent = tree.get(path.segments)
            if parent is None:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if parent.kind is not FilesystemEntryKind.DIRECTORY:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            candidates = list(self._children(tree, path.segments, recursive))
            if len(candidates) > maximum_entries:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            from .models import FilesystemPage
            return FilesystemPage(tuple(self._entry(WorkspacePath(candidate), tree[candidate]) for candidate in candidates))

    def read(self, root, path, sink, *, offset_bytes, maximum_bytes, expected_version):
        with self._lock:
            node = self._tree(root).get(path.segments)
            if node is None:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if node.kind is not FilesystemEntryKind.FILE:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            if expected_version is not None and node.version != expected_version:
                return self._error(FilesystemErrorCode.CONFLICT)
            chunk = node.data[offset_bytes: offset_bytes + maximum_bytes]
            written = sink.write(chunk)
            if written is not None and written != len(chunk):
                return self._error(FilesystemErrorCode.UNKNOWN_EFFECT, effect_state="APPLIED")
            from .models import FilesystemReadResult
            next_offset = offset_bytes + len(chunk) if offset_bytes + len(chunk) < len(node.data) else None
            return FilesystemReadResult(len(chunk), next_offset)

    def create_directory(self, root, path, *, create_parents, maximum_depth):
        with self._lock:
            tree = self._tree(root)
            if len(path.segments) > maximum_depth:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            if path.segments in tree:
                return self._error(FilesystemErrorCode.CONFLICT)
            missing = []
            for index in range(1, len(path.segments) + 1):
                candidate = path.segments[:index]
                if candidate not in tree:
                    missing.append(candidate)
            if missing and not create_parents and len(missing) > 1:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            for candidate in missing:
                tree[candidate] = _Node(FilesystemEntryKind.DIRECTORY)
            return self._entry(path, tree[path.segments])

    def write(self, root, path, source, *, mode, atomicity, maximum_bytes, maximum_entries, expected_version):
        with self._lock:
            if atomicity == "REQUIRE_ATOMIC" and not self.supports_atomic:
                return self._error(FilesystemErrorCode.ATOMICITY_UNSUPPORTED)
            tree = self._tree(root)
            parent = tree.get(path.segments[:-1])
            if parent is None or parent.kind is not FilesystemEntryKind.DIRECTORY:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            data = source.read(maximum_bytes + 1)
            data = bytes(data)
            if len(data) > maximum_bytes:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            current = tree.get(path.segments)
            if mode == "CREATE_NEW" and current is not None:
                return self._error(FilesystemErrorCode.CONFLICT)
            if current is not None and current.kind is not FilesystemEntryKind.FILE:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            if expected_version is not None and (current is None or current.version != expected_version):
                return self._error(FilesystemErrorCode.CONFLICT)
            if current is None and len(tree) >= maximum_entries + 1:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            old = current.data if current is not None and mode == "APPEND" else b""
            new_data = old + data
            if len(new_data) > maximum_bytes:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            node = _Node(FilesystemEntryKind.FILE, new_data, (current.version + 1 if current else 1))
            tree[path.segments] = node
            from .models import FilesystemWriteResult
            return FilesystemWriteResult(self._entry(path, node), len(data))

    def copy(self, root, source, destination, *, expected_source_version, overwrite, maximum_bytes, maximum_entries):
        with self._lock:
            tree = self._tree(root)
            source_node = tree.get(source.segments)
            if source_node is None:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if source_node.version != expected_source_version:
                return self._error(FilesystemErrorCode.CONFLICT)
            if source_node.kind is not FilesystemEntryKind.FILE:
                return self._error(FilesystemErrorCode.TYPE_MISMATCH)
            if len(source_node.data) > maximum_bytes:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            if destination.segments in tree and overwrite == "NEVER":
                return self._error(FilesystemErrorCode.CONFLICT)
            if destination.segments[:-1] not in tree:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            node = copy.copy(source_node)
            node.version = 1 if destination.segments not in tree else tree[destination.segments].version + 1
            tree[destination.segments] = node
            from .models import FilesystemMutationResult
            return FilesystemMutationResult(self._entry(destination, node), 1)

    def move(self, root, source, destination, *, expected_source_version, overwrite):
        with self._lock:
            tree = self._tree(root)
            result = self.copy(root, source, destination, expected_source_version=expected_source_version, overwrite=overwrite, maximum_bytes=10**18, maximum_entries=10**9)
            if isinstance(result, FilesystemError):
                return result
            del tree[source.segments]
            return result

    def remove(self, root, path, *, expected_version, recursive, maximum_entries):
        with self._lock:
            tree = self._tree(root)
            node = tree.get(path.segments)
            if node is None:
                return self._error(FilesystemErrorCode.NOT_FOUND)
            if expected_version is not None and node.version != expected_version:
                return self._error(FilesystemErrorCode.CONFLICT)
            descendants = list(self._children(tree, path.segments, True))
            if descendants and not recursive:
                return self._error(FilesystemErrorCode.CONFLICT)
            targets = descendants + [path.segments]
            if len(targets) > maximum_entries:
                return self._error(FilesystemErrorCode.QUOTA_EXCEEDED)
            for target in targets:
                tree.pop(target, None)
            from .models import FilesystemMutationResult
            return FilesystemMutationResult(None, len(targets))


__all__ = ["InMemoryFilesystemAdapter", "InMemoryWorkspaceRootResolver", "WorkspaceBackedRootResolver"]
