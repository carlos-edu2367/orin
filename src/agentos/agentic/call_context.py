"""The tool invocation a thread is running, visible to code that has no toolset in hand (W6-R1).

The permission broker is shared by the main agent and its subagents, so the request it creates cannot name the call
it is for from its arguments. The toolset records the invocation it is running in a thread-local here, and the
broker reads it in the same thread.
"""

from __future__ import annotations

import threading

_LOCAL = threading.local()


def current_invocation_id() -> str | None:
    """The invocation id this thread's toolset last recorded, or None (another thread, or no call)."""
    return getattr(_LOCAL, "invocation_id", None)


class ActiveInvocation:
    """Mixin for a toolset: ``_active_invocation_id`` per thread, mirrored into the module thread-local.

    The toolset must set ``self._tls = threading.local()`` before the first access.
    """

    @property
    def _active_invocation_id(self) -> str | None:
        return getattr(self._tls, "active_invocation_id", None)

    @_active_invocation_id.setter
    def _active_invocation_id(self, value: str | None) -> None:
        self._tls.active_invocation_id = value
        _LOCAL.invocation_id = value


__all__ = ["ActiveInvocation", "current_invocation_id"]
