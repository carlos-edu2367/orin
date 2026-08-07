from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from .models import CleanupResult, EffectState, ResourceDescriptor, ResourceError, ResourceErrorCode
from .ports import AdapterResourceHandle


class _ReferenceAdapter:
    def __init__(self, adapter_ref: str) -> None:
        self.adapter_ref = adapter_ref
        self._lock = RLock()
        self._counter = 0
        self._states: dict[str, str] = {}
        self.fail_cleanup = False
        self.cancelled: list[str] = []

    def allocate(self, *, lease_id, descriptor: ResourceDescriptor, context, isolation_key):
        with self._lock:
            self._counter += 1
            value = f"adapter:{self._counter}"
            self._states[value] = "READY"
            return AdapterResourceHandle(value, self.adapter_ref, lease_id)

    def inspect(self, handle: AdapterResourceHandle) -> str:
        return self._states.get(handle._value, "MISSING")

    def signal_cancel(self, handle: AdapterResourceHandle, reason: str) -> None:
        with self._lock:
            if handle._value in self._states:
                self._states[handle._value] = "CANCELLED"
                self.cancelled.append(handle._value)

    def cleanup(self, handle: AdapterResourceHandle, *, deadline) -> CleanupResult:
        with self._lock:
            if self.fail_cleanup:
                self.fail_cleanup = False
                return CleanupResult(handle.lease_id, "UNCERTAIN", EffectState.UNKNOWN, "cleanup:retry", "ADAPTER_CLEANUP_FAILED")
            self._states[handle._value] = "CLEANED"
            return CleanupResult(handle.lease_id, "CLEANED", EffectState.APPLIED)


class FilesystemResourceAdapter(_ReferenceAdapter):
    def __init__(self) -> None:
        super().__init__("filesystem.reference")
        self.filesystem_port = None

    def bind_filesystem_port(self, filesystem_port) -> None:
        self.filesystem_port = filesystem_port

    def cleanup(self, handle: AdapterResourceHandle, *, deadline):
        if self.filesystem_port is not None and not self.filesystem_port.cleanup_lease(handle.lease_id):
            return CleanupResult(handle.lease_id, "UNCERTAIN", EffectState.UNKNOWN, "cleanup:filesystem", "FILESYSTEM_CLEANUP_FAILED")
        return super().cleanup(handle, deadline=deadline)


class TerminalResourceAdapter(_ReferenceAdapter):
    def __init__(self) -> None:
        super().__init__("terminal.reference")


class BrowserResourceAdapter(_ReferenceAdapter):
    def __init__(self) -> None:
        super().__init__("browser.reference")


__all__ = ["BrowserResourceAdapter", "FilesystemResourceAdapter", "TerminalResourceAdapter"]
