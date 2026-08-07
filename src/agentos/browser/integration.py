from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from threading import RLock
from uuid import uuid4

from .models import AuthorizedFileReference, BrowserArtifactRef, BrowserOperationContext, BrowserWorkerGrant
from .ports import ArtifactWriteSink, BoundedByteSource, GrantedArtifactWrite


class _Source:
    def __init__(self, data: bytes) -> None:
        self._data = bytes(data)
        self._offset = 0

    def read(self, maximum_bytes: int) -> bytes:
        if maximum_bytes < 0:
            raise ValueError("maximum_bytes must be non-negative")
        chunk = self._data[self._offset:self._offset + maximum_bytes]
        self._offset += len(chunk)
        return chunk


class InMemoryBrowserInputResolver:
    def __init__(self, references: dict[str, tuple[bytes, BrowserOperationContext]]) -> None:
        self._references = {key: (bytes(value), context) for key, (value, context) in references.items()}

    def open(self, reference: str, grant: BrowserWorkerGrant) -> BoundedByteSource:
        if isinstance(reference, AuthorizedFileReference):
            reference = reference.reference_id
        if any(marker in reference for marker in ("\\", "/", ":", "..")):
            raise ValueError("physical path is not an input reference")
        item = self._references.get(reference)
        if item is None or item[1].scope_key() != grant.context.scope_key():
            raise ValueError("input reference is unauthorized")
        return _Source(item[0])


class _ArtifactSink:
    def __init__(self, owner: "InMemoryBrowserArtifactOutput", maximum: int) -> None:
        self.owner = owner
        self.maximum = maximum
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.closed or len(self.data) + len(data) > self.maximum:
            raise ValueError("artifact limit exceeded")
        self.data.extend(data)
        return len(data)


class InMemoryBrowserArtifactOutput:
    def __init__(self, *, maximum_bytes: int = 16 * 1024 * 1024) -> None:
        self.maximum_bytes = maximum_bytes
        self._objects: dict[str, bytes] = {}
        self._lock = RLock()

    def begin(self, kind: str | GrantedArtifactWrite, context: BrowserOperationContext | None = None, grant: BrowserWorkerGrant | None = None, maximum_bytes: int | None = None) -> ArtifactWriteSink:
        if isinstance(kind, GrantedArtifactWrite):
            context, grant, maximum_bytes = kind.context, kind.grant, kind.maximum_bytes
            kind = kind.kind
        if context is None or grant is None or maximum_bytes is None:
            raise ValueError("artifact grant is required")
        if maximum_bytes < 0 or maximum_bytes > self.maximum_bytes:
            raise ValueError("artifact limit exceeds output policy")
        return _ArtifactSink(self, maximum_bytes)

    def commit(self, sink: ArtifactWriteSink, media_type: str) -> BrowserArtifactRef:
        if not isinstance(sink, _ArtifactSink) or sink.closed:
            raise ValueError("artifact sink is invalid")
        with self._lock:
            artifact_id = "artifact-" + uuid4().hex
            self._objects[artifact_id] = bytes(sink.data)
            sink.closed = True
            return BrowserArtifactRef(artifact_id, 1, len(sink.data), media_type, "INTERNAL")

    def abort(self, sink: ArtifactWriteSink) -> None:
        if isinstance(sink, _ArtifactSink):
            sink.closed = True
            sink.data.clear()

    def read(self, reference: BrowserArtifactRef) -> bytes:
        return self._objects[reference.artifact_id]


__all__ = ["InMemoryBrowserArtifactOutput", "InMemoryBrowserInputResolver"]
