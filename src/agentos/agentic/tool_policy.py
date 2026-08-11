"""Declarative authorization for the agent-facing tool set.

``agentos.tool_runtime`` already models policy for the projected action path.
This is the same idea placed on the path the chat agent actually uses, kept
deliberately small: a decision function over a tool's name and its tags. A
denied tool is never published, because a tool the model can see and cannot use
is a wasted round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Protocol


class ToolPolicy(Protocol):
    def allows(self, name: str, tags: tuple[str, ...]) -> bool: ...


@dataclass(frozen=True, slots=True)
class AllowList:
    """``allowed=None`` means everything that is not explicitly denied.

    An entry of the form ``tag:<value>`` matches a tool carrying that policy
    tag, which is how a whole family is authorized or refused at once.
    """

    allowed: Collection[str] | None = None
    denied: Collection[str] = ()

    def allows(self, name: str, tags: tuple[str, ...]) -> bool:
        tagged = {f"tag:{tag}" for tag in tags}
        denied = set(self.denied)
        if name in denied or tagged & denied:
            return False
        if self.allowed is None:
            return True
        allowed = set(self.allowed)
        return name in allowed or bool(tagged & allowed)


__all__ = ["AllowList", "ToolPolicy"]
