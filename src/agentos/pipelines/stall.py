"""Stall detection for one child and "no progress" fingerprints for loops (WS-C6)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .hashing import hash_text
from .ports import AgentProgress

DEFAULT_STALL_MINUTES = 10
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")
_FINGERPRINT_LINES = 12


class StallWatch:
    """Judges a child stalled when its last activity is older than ``stall_minutes`` (read on the engine clock).

    A child that has not reported any activity yet counts from the moment the watch started, so a child that
    never says anything is eventually caught too.
    """

    def __init__(self, stall_minutes: float, started_at: datetime) -> None:
        self.stall_seconds = max(1.0, float(stall_minutes) * 60.0)
        self._started_at = started_at
        self.last_progress = AgentProgress()

    def observe(self, progress: AgentProgress) -> None:
        self.last_progress = progress

    def idle_seconds(self, now: datetime) -> float:
        last = self.last_progress.last_activity_at or self._started_at
        if self._started_at > last:
            last = self._started_at  # activity from before this attempt does not count
        return max(0.0, (now - last).total_seconds())

    def stalled(self, now: datetime) -> bool:
        return self.idle_seconds(now) >= self.stall_seconds

    def replan_note(self) -> str:
        tool = self.last_progress.current_tool or "nenhuma ferramenta"
        calls = self.last_progress.tool_calls
        return (
            f"The previous attempt stopped at `{tool}` after {calls} calls; "
            "change your approach and do not repeat the same tool with the same arguments."
        )


def _normalize_line(line: str) -> str:
    return _DIGITS.sub("#", _SPACES.sub(" ", line.strip()).lower())


def command_fingerprint(exit_code: int | None, output_tail: str) -> str:
    """Hash of the exit code and the last normalized lines (digits folded so timings and counters do not matter)."""
    lines = [_normalize_line(item) for item in output_tail.splitlines() if item.strip()]
    return hash_text(f"cmd|{exit_code}|" + "\n".join(lines[-_FINGERPRINT_LINES:]))


def failure_fingerprint(step_id: str, code: str | None, cause: str | None, *texts: str | None) -> str:
    """Fingerprint of an agent step failure: step, error code, cause and the last normalized lines of the texts.

    The texts are the error message, the end of the child's answer and the schema errors, so two failures with the
    same code but different messages or output are different failures (digits folded, as for commands).
    """
    lines = [_normalize_line(line) for text in texts for line in (text or "").splitlines() if line.strip()]
    return hash_text(f"agent|{step_id}|{code}|{cause}|" + "\n".join(lines[-_FINGERPRINT_LINES:]))


def findings_fingerprint(findings: Sequence[Mapping[str, Any]]) -> str:
    pairs = sorted((str(item.get("id", "")), _normalize_line(str(item.get("claim", "")))) for item in findings)
    return hash_text("find|" + "|".join(f"{key}:{claim}" for key, claim in pairs))


def combine_fingerprints(parts: Sequence[str]) -> str:
    return hash_text("all|" + "|".join(parts)) if parts else ""


class LoopProgress:
    """Remembers the failure fingerprint of the last visit of each gate that loops back."""

    def __init__(self) -> None:
        self.last: dict[str, str] = {}

    def repeats(self, step_id: str, fingerprint: str) -> bool:
        """Record ``fingerprint``; ``True`` when the previous failure of this step had the same one."""
        previous = self.last.get(step_id)
        self.last[step_id] = fingerprint
        return bool(fingerprint) and previous == fingerprint

    def forget(self, step_id: str) -> None:
        """The step passed: a later failure is a new problem, not a repeat of the one before it passed."""
        self.last.pop(step_id, None)


__all__ = [
    "DEFAULT_STALL_MINUTES",
    "LoopProgress",
    "StallWatch",
    "combine_fingerprints",
    "command_fingerprint",
    "failure_fingerprint",
    "findings_fingerprint",
]
