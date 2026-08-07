from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import *


class BrowserJobSink(Protocol):
    def emit(self, item: BrowserStreamItem) -> str: ...


class BrowserJobPort(Protocol):
    def submit(self, request: BrowserJobRequest) -> BrowserJobAccepted | BrowserRejected: ...
    def inspect(self, query: AuthorizedBrowserJobQuery) -> BrowserJobSnapshot | BrowserRejected: ...
    def stream(self, request: BrowserJobStreamRequest, sink: BrowserJobSink) -> StreamResult | BrowserRejected: ...
    def request_cancel(self, request: CancelBrowserJob) -> CancelBrowserResult | BrowserRejected: ...


class BrowserAdapter(Protocol):
    executed_operations: list[BrowserOperationKind]

    def execute(self, job: BrowserJob) -> BrowserResult | BrowserJobFailed: ...
    def cleanup(self, session_id: str) -> bool: ...


class BrowserWorkerPort(Protocol):
    def execute(self, job: BrowserJob) -> BrowserJobSucceeded | BrowserJobFailed | BrowserJobCancelled: ...


class BoundedByteSource(Protocol):
    def read(self, maximum_bytes: int) -> bytes: ...


class BrowserInputResolver(Protocol):
    def open(self, reference: str, grant: BrowserWorkerGrant) -> BoundedByteSource: ...


class ArtifactWriteSink(Protocol):
    def write(self, data: bytes) -> int: ...


@dataclass(frozen=True, slots=True)
class GrantedArtifactWrite:
    kind: str
    context: BrowserOperationContext
    grant: BrowserWorkerGrant
    maximum_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        if self.maximum_bytes < 0:
            raise ValueError("maximum_bytes must be non-negative")


class BrowserArtifactOutput(Protocol):
    def begin(self, kind: str, context: BrowserOperationContext, grant: BrowserWorkerGrant, maximum_bytes: int) -> ArtifactWriteSink: ...
    def commit(self, sink: ArtifactWriteSink, media_type: str) -> BrowserArtifactRef: ...
    def abort(self, sink: ArtifactWriteSink) -> None: ...


class SecretReferencePort(Protocol):
    def resolve(self, reference: str, grant: BrowserWorkerGrant) -> bytes: ...


__all__ = [name for name in globals() if not name.startswith("_")]
