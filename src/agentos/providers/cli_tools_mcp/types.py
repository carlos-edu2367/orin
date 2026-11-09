from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    name: str
    arguments: dict[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)
    # Set when the client gives up on this call (MCP ``notifications/cancelled``, sent
    # on its tool timeout): a queued call is then skipped and a running one stopped.
    cancelled: threading.Event | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    text: str
    is_error: bool = False
    images: tuple[Mapping[str, str], ...] = ()
    tools_changed: bool = False


class ToolExecutor(Protocol):
    def list_tools(self) -> Sequence[McpToolSpec]: ...

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult: ...
