"""What one turn taught, derived mechanically from what the turn actually did.

Sibling of ``quality.py`` and held to the same rule: this observes a turn, it
never influences one. A counter that cannot make sense of an argument still
counts the call, and a ledger that cannot make sense of a command simply
learns nothing from it.

Only ``run_command`` is mined here, and only for one shape: a command that
failed, followed by a *different* command that did the same job and worked.
That shape is worth storing because the argument is structured -- the command
string is the fact. Free-form evidence (a verification's findings, a user's
correction) needs a model to shape it into a sentence, which is a later phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# Two commands serve the same purpose when a meaningful share of their words
# agree. "npm install" vs "pnpm install" share only "install" out of three
# distinct words (1/3 overlap) -- the runner name itself is expected to
# differ, that is the whole lesson -- while "npm install" and "git status"
# share nothing at any threshold below this.
_SAME_PURPOSE_OVERLAP = 0.3


@dataclass(frozen=True, slots=True)
class LearnedMemory:
    """One fact a turn produced, ready to be committed to the memory store."""

    fact: str
    kind: str
    scope: str
    confidence: float
    source: str
    tags: tuple[str, ...] = ()


def _command_of(arguments: object) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    value = arguments.get("command")
    return " ".join(value.split()) if isinstance(value, str) else ""


def _words(command: str) -> frozenset[str]:
    return frozenset(part.lower() for part in command.split() if part)


def _same_purpose(failed: str, succeeded: str) -> bool:
    """Whether two commands are two attempts at one job, not two different jobs."""
    if failed == succeeded:
        return False
    left, right = _words(failed), _words(succeeded)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= _SAME_PURPOSE_OVERLAP


@dataclass(slots=True)
class TurnLearningLedger:
    """Mutable tally for one turn. Never raises, never blocks the turn."""

    _failed_commands: list[str] = field(default_factory=list, repr=False)
    _resolutions: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def note_tool_outcome(self, name: str, arguments: Mapping[str, object], status: str) -> None:
        if name != "run_command":
            return
        command = _command_of(arguments)
        if not command:
            return
        if status == "failed":
            if command not in self._failed_commands:
                self._failed_commands.append(command)
            return
        if status != "succeeded":
            return
        for failed in self._failed_commands:
            if _same_purpose(failed, command) and (failed, command) not in self._resolutions:
                self._resolutions.append((failed, command))

    def mechanical_memories(self, scope: str) -> tuple[LearnedMemory, ...]:
        return tuple(
            LearnedMemory(
                fact=f"Neste workspace, `{succeeded}` funciona onde `{failed}` falha.",
                kind="operational",
                scope=scope,
                confidence=0.7,
                source="mechanical",
                tags=("comando",),
            )
            for failed, succeeded in self._resolutions
        )


__all__ = ["LearnedMemory", "TurnLearningLedger"]
