"""Resume decisions: reuse what the journal already finished with the same input hash (WS-C6, AC3).

The engine replays the same control flow on a resumed run; at every step visit the planner answers with
``reuse`` (restore the journaled result and emit ``pipeline.step_reused``), ``run`` (do the work again) or
``conflict`` (a worktree was already merged into the project and the step would run again with other inputs:
``RESUME_CONFLICT``). Reuse needs the same ``(step_id, item_key, loop_count)`` and the same ``input_hash``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from .ports import JournalStore

logger = logging.getLogger("agentos.pipelines.resume")

RESUME_CONFLICT = "RESUME_CONFLICT"
_REUSABLE_AGENT_STATES = frozenset({"succeeded"})
_REUSABLE_GATE_STATES = frozenset({"succeeded", "gate_failed"})


class DefinitionChanged(RuntimeError):
    """The pipeline file changed since the run started (the API answers 409 with the two ways out)."""

    def __init__(self, stored_hash: str, current_hash: str) -> None:
        super().__init__("a definição do pipeline mudou desde o início da execução")
        self.stored_hash = stored_hash
        self.current_hash = current_hash


class ResumeRefused(RuntimeError):
    """The run cannot be resumed as asked (for example a budget-exceeded run without ``budget_override``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReuseDecision:
    action: Literal["reuse", "run", "conflict"]
    record: Any = None
    value: Any = None
    worktree: Any = None
    discard_agent_id: str | None = None
    reason: str = ""


def unwrap_envelope(raw: Any) -> tuple[Any, Any]:
    """``(value, worktree)`` of a journaled output (``{"value": ..., "worktree": ...}``)."""
    if isinstance(raw, dict) and "value" in raw and set(raw) <= {"value", "worktree"}:
        return raw["value"], raw.get("worktree")
    return raw, None


def wrap_envelope(value: Any, worktree: Any = None) -> Any:
    if worktree:
        return {"value": value, "worktree": dict(worktree)}
    return {"value": value}


class ResumePlanner:
    def __init__(self, store: JournalStore, run_id: str, *, enabled: bool, rerun_gates: bool = False) -> None:
        self._store = store
        self._run_id = run_id
        self.enabled = enabled
        self.rerun_gates = rerun_gates
        self.reused = 0

    def _latest(self, step_id: str, item_key: str, loop_count: int) -> Any:
        if not self.enabled:
            return None
        return self._store.find_reusable(self._run_id, step_id, item_key, loop_count)

    def _read(self, record: Any) -> tuple[bool, Any, Any]:
        """``(readable, value, worktree)``: an unreadable artifact must not be reused as ``None`` (F6)."""
        try:
            raw = self._store.load_output(self._run_id, record.output)
        except Exception:  # noqa: BLE001 - an unreadable artifact only means "do it again"
            logger.warning("could not read the journaled output of %s", record.step_id, exc_info=True)
            return False, None, None
        value, tree = unwrap_envelope(raw)
        return True, value, tree

    def _moved_past(self, record: Any) -> bool:
        """``True`` when the journal has work that started after ``record``: the run went on after that visit."""
        began = getattr(record, "started_at", None)
        if began is None:
            return False
        return any(row.started_at is not None and row.started_at > began for row in self._store.steps_of(self._run_id))

    def decide_agent(
        self, step_id: str, item_key: str, loop_count: int, input_hash: str, *, worktree: bool
    ) -> ReuseDecision:
        record = self._latest(step_id, item_key, loop_count)
        if record is None:
            return ReuseDecision("run", reason="no_record")
        readable, value, tree = self._read(record)
        merged = isinstance(tree, dict) and tree.get("merge_state") == "applied"
        if record.state in _REUSABLE_AGENT_STATES and record.input_hash == input_hash:
            if not readable:
                return ReuseDecision("run", record, reason="artifact_unreadable")
            self.reused += 1
            return ReuseDecision("reuse", record, value, tree)
        if merged:
            return ReuseDecision("conflict", record, reason="worktree_applied")
        # K19: a worktree left by an attempt that did not finish is dropped before the step is redone. The row of an
        # attempt that died before ``finish_step`` has no agent id yet (``""``): the adapter finds it by step and item.
        never_launched = record.state == "failed" and not record.agent_id  # failed before a child existed (S1)
        discard = (record.agent_id or "") if worktree and record.state != "succeeded" and not never_launched else None
        return ReuseDecision("run", record, discard_agent_id=discard, reason="hash_or_state")

    def decide_gate(
        self, step_id: str, loop_count: int, input_hash: str, *, dirty: bool
    ) -> ReuseDecision:
        record = self._latest(step_id, "", loop_count)
        if record is None:
            return ReuseDecision("run", reason="no_record")
        if self.rerun_gates or dirty or record.input_hash != input_hash or record.state not in _REUSABLE_GATE_STATES:
            return ReuseDecision("run", record, reason="gate_rerun")
        if record.state == "gate_failed" and not self._moved_past(record):
            # F5: the failure that ended the run is not a result to reuse: resuming means trying that gate again
            return ReuseDecision("run", record, reason="gate_failed_terminal")
        readable, value, _tree = self._read(record)
        if not readable:
            return ReuseDecision("run", record, reason="artifact_unreadable")
        self.reused += 1
        return ReuseDecision("reuse", record, value)

    def had_skip(self, step_id: str, loop_count: int) -> bool:
        record = self._latest(step_id, "", loop_count)
        return record is not None and record.state == "skipped"


__all__ = [
    "DefinitionChanged",
    "RESUME_CONFLICT",
    "ResumePlanner",
    "ResumeRefused",
    "ReuseDecision",
    "unwrap_envelope",
    "wrap_envelope",
]
