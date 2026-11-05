from __future__ import annotations

from agentos.agentic.provider_stream import ProviderKeyRejected


class ProviderTransportError(ProviderKeyRejected):
    """Raised when `claude -p` fails auth, security, protocol, or execution."""


class NativeLoopUnavailable(ProviderTransportError):
    """Raised before the first user-visible delta when native MCP loop cannot start."""
