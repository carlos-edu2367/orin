from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    text: str
    is_error: bool = False
    images: tuple[Mapping[str, str], ...] = ()


class ToolExecutor(Protocol):
    def list_tools(self) -> Sequence[McpToolSpec]: ...

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult: ...
