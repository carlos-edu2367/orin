"""``/goal``: keep working in successive turns until a small evaluator says the objective is met (WS-C9, C-RF6, D8).

A goal is one row of ``pipeline_runs`` with ``pipeline_id = "goal"`` (no table of its own). The conversation runs
ordinary turns; after each one ``GoalController.after_turn`` (called from the single post-turn hook,
``pipelines/after_turn.py``) measures what the turn spent, asks the evaluator and either stops or sends the next
message. Only a typed command starts a goal (P4): there is no tool that starts one and nothing here is reachable
from model text.

Journal layout (``pipeline_step_runs``, ``item_key = ""``, ``attempt`` is the running number of the step):

* ``turno``  one per finished turn: tokens the conversation spent since the last reading, plus ``turn_id``,
  ``tool_calls``, ``files_changed`` and the cumulative ``usage_total`` that was read;
* ``avaliar`` one per evaluation (``failed`` when the evaluator gave no verdict): ``done``, ``reason``, ``model``;
* ``retomar``  one per ``/goal resume``: opens a new window (a fresh token allowance, turn count and failure count).

``run.used_tokens`` is the sum of those rows, so the journal stays consistent with the run total. The state of the
goal is derived from the rows, never cached, which is what makes ``after_turn`` safe to deliver twice.

Stop rule (a stopped goal keeps its journal; ``resume`` reopens it unless it was cleared or succeeded):

========================  =================  ==============================================
cause                      run state          ``stop_reason``
========================  =================  ==============================================
evaluator says ``done``    ``succeeded``      ``done``
token cap reached          ``budget_exceeded``  ``budget`` (no evaluator call, no new turn)
``GOAL_MAX_TURNS`` turns   ``failed``         ``max_turns``
3 turns without a tool     ``failed``         ``no_progress``
2 evaluator failures       ``failed``         ``evaluator_failed`` (never pretends ``done``)
turn failed or cancelled   ``interrupted``    ``turn_failed`` / ``turn_cancelled`` / ``waiting_user`` / ``reconciliation``
``/goal clear``            ``cancelled``      ``cleared``
========================  =================  ==============================================
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import uuid4

from sqlalchemy import func, select

from . import events as pipeline_events
from .events import PipelineEvent
from .goal_evaluator import (
    Evaluation,
    EvaluatorFailed,
    EvaluatorModelPort,
    ToolUse,
    TransportEvaluatorModel,
    choose_evaluator_model,
    evaluate_goal,
)
from .hashing import hash_text
from .store import PipelineStore, PipelineStoreError, RunRecord, StepRecord

if TYPE_CHECKING:  # pragma: no cover - the import cycle (after_turn imports this module) is avoided at run time
    from .after_turn import AfterTurnContext

logger = logging.getLogger("agentos.pipelines.goal")

GOAL_PIPELINE_ID: Final = "goal"
GOAL_MAX_TURNS: Final = 50
NO_PROGRESS_TURNS: Final = 3
EVALUATOR_FAILURES_TO_STOP: Final = 2
MIN_MAX_TOKENS: Final = 1_000
MAX_MAX_TOKENS: Final = 5_000_000
MAX_OBJECTIVE_CHARS: Final = 4_000
STEP_TURN: Final = "turno"
STEP_EVALUATE: Final = "avaliar"
STEP_RESUME: Final = "retomar"
SKIPPED_TURN_KINDS: Final = frozenset({"compact", "shell", "pipeline"})
COMPLETED_STATES: Final = frozenset({"completed", "completed_with_caveats"})
_LIVE_TURN_STATES: Final = ("queued", "starting", "running")
_PARKED_REASONS: Final = {
    "failed": "turn_failed",
    "cancelled": "turn_cancelled",
    "waiting_user": "waiting_user",
    "reconciliation_required": "reconciliation",
}
_RESUMABLE_STOPS: Final = frozenset({"budget", "max_turns", "no_progress", "evaluator_failed", "send_failed"})
_START_LOCK = threading.Lock()


class GoalError(Exception):
    """A refusal the API maps to an HTTP status: ``code`` is stable, ``message`` is pt-BR and fit for the user."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ----------------------------------------------------------------------------------------------- evidence
@dataclass(frozen=True, slots=True)
class TurnEvidence:
    final_text: str
    tools: tuple[ToolUse, ...]
    tool_calls: int
    files_changed: int

    @property
    def made_progress(self) -> bool:
        return self.tool_calls > 0 or self.files_changed > 0


class GoalEvidence(Protocol):
    def turn_evidence(self, turn: Mapping[str, Any]) -> TurnEvidence: ...

    def usage_total(self, conversation_id: str) -> tuple[int, int]:
        """Cumulative ``(input_tokens, output_tokens)`` of every agent of the conversation."""
        ...

    def has_pending_work(self, turn: Mapping[str, Any]) -> bool:
        """The person (or the Friend) has queued text, or another turn of the conversation is already live."""
        ...


class StoreGoalEvidence:
    """``GoalEvidence`` over the chat store's tables (messages, activity events, agent usage, turns)."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @property
    def _engine(self) -> Any:
        return self._store._engine

    def turn_evidence(self, turn: Mapping[str, Any]) -> TurnEvidence:
        from agentos.persistence.postgres.schema import conversation_activity_events, conversation_messages

        with self._engine.connect() as connection:
            text = connection.execute(
                select(conversation_messages.c.content).where(conversation_messages.c.message_id == str(turn.get("assistant_message_id") or ""))
            ).scalar()
            rows = connection.execute(
                select(
                    conversation_activity_events.c.event_type,
                    conversation_activity_events.c.summary,
                    conversation_activity_events.c.payload,
                )
                .where(
                    conversation_activity_events.c.turn_id == str(turn["turn_id"]),
                    conversation_activity_events.c.event_type.in_(("tool.finished", "filesystem.write")),
                )
                .order_by(conversation_activity_events.c.id)
            ).all()
        tools: list[ToolUse] = []
        files = 0
        for event_type, summary, payload in rows:
            if event_type == "filesystem.write":
                files += 1
                continue
            body = payload if isinstance(payload, Mapping) else {}
            tools.append(ToolUse(str(body.get("tool_name") or "tool"), str(body.get("status") or ""), str(summary or "")))
        return TurnEvidence(str(text or ""), tuple(tools), len(tools), files)

    def usage_total(self, conversation_id: str) -> tuple[int, int]:
        from agentos.persistence.postgres.schema import conversation_agent_usage

        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    func.coalesce(func.sum(conversation_agent_usage.c.input_tokens), 0),
                    func.coalesce(func.sum(conversation_agent_usage.c.output_tokens), 0),
                ).where(conversation_agent_usage.c.conversation_id == conversation_id)
            ).one()
        return int(row[0] or 0), int(row[1] or 0)

    def has_pending_work(self, turn: Mapping[str, Any]) -> bool:
        from agentos.persistence.postgres.schema import conversation_turns

        queued = getattr(self._store, "has_queued_inputs", None)
        if callable(queued) and queued(turn):
            return True
        with self._engine.connect() as connection:
            live = connection.execute(
                select(func.count()).select_from(conversation_turns).where(
                    conversation_turns.c.conversation_id == str(turn["conversation_id"]),
                    conversation_turns.c.turn_id != str(turn["turn_id"]),
                    conversation_turns.c.state.in_(_LIVE_TURN_STATES),
                )
            ).scalar()
        return int(live or 0) > 0

    def turn_row(self, turn_id: str) -> Mapping[str, Any] | None:
        """The turn's row (enough for ``store._activity``): used to hang ``goal.*`` events on a turn that just started."""
        from agentos.persistence.postgres.schema import conversation_turns

        with self._engine.connect() as connection:
            row = connection.execute(select(conversation_turns).where(conversation_turns.c.turn_id == turn_id)).mappings().first()
        return dict(row) if row is not None else None

    def live_turn(self, conversation_id: str) -> bool:
        from agentos.persistence.postgres.schema import conversation_turns

        with self._engine.connect() as connection:
            live = connection.execute(
                select(func.count()).select_from(conversation_turns).where(
                    conversation_turns.c.conversation_id == conversation_id,
                    conversation_turns.c.state.in_(_LIVE_TURN_STATES),
                )
            ).scalar()
        return int(live or 0) > 0


# ------------------------------------------------------------------------------------------- dependencies
@dataclass(slots=True)
class GoalDeps:
    runs: PipelineStore
    evidence: GoalEvidence
    send: Callable[..., Any]  # send(user_id, conversation_id, message, idempotency_key, *, effort=None) -> receipt (``turn_id``)
    emit: Callable[[Mapping[str, Any], PipelineEvent], None]
    evaluator: EvaluatorModelPort | None = None
    candidates: Callable[[Mapping[str, Any]], Sequence[str]] = lambda turn: ()
    tiers_for: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] = lambda turn: None
    default_max_tokens: int = 300_000
    live_turn: Callable[[str], bool] = lambda conversation_id: False
    load_turn: Callable[[str], Mapping[str, Any] | None] = lambda turn_id: None


@dataclass(frozen=True, slots=True)
class GoalStart:
    run_id: str
    turn_id: str
    max_tokens: int


@dataclass(frozen=True, slots=True)
class GoalStatus:
    run_id: str
    state: str
    stop_reason: str | None
    objective: str
    max_tokens: int
    used_tokens: int
    window_used_tokens: int
    turns: int
    window_turns: int
    max_turns: int
    last_evaluation: Mapping[str, Any] | None
    evaluator_model: str | None
    resumable: bool
    started_at: str
    finished_at: str | None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.window_used_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "state": self.state, "stop_reason": self.stop_reason, "objective": self.objective,
            "max_tokens": self.max_tokens, "used_tokens": self.used_tokens, "window_used_tokens": self.window_used_tokens,
            "remaining_tokens": self.remaining_tokens, "turns": self.turns, "window_turns": self.window_turns,
            "max_turns": self.max_turns, "last_evaluation": dict(self.last_evaluation) if self.last_evaluation else None,
            "evaluator_model": self.evaluator_model, "resumable": self.resumable, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True, slots=True)
class _View:
    run: RunRecord
    turns: tuple[StepRecord, ...]
    evals: tuple[StepRecord, ...]
    resumes: tuple[StepRecord, ...]

    @property
    def objective(self) -> str:
        return str(self.run.inputs.get("objetivo") or "")

    @property
    def _window(self) -> Mapping[str, Any]:
        out = self.resumes[-1].output if self.resumes else None
        return out if isinstance(out, Mapping) else {}

    @property
    def cap(self) -> int:
        return int(self._window.get("max_tokens") or self.run.inputs.get("max_tokens") or MIN_MAX_TOKENS)

    @property
    def spent(self) -> int:
        return max(0, self.run.used_tokens - int(self._window.get("spent_before") or 0))

    @property
    def window_turns(self) -> tuple[StepRecord, ...]:
        return self.turns[int(self._window.get("turns_base") or 0):]

    @property
    def window_evals(self) -> tuple[StepRecord, ...]:
        return self.evals[int(self._window.get("evals_base") or 0):]


def _out(step: StepRecord) -> Mapping[str, Any]:
    return step.output if isinstance(step.output, Mapping) else {}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ----------------------------------------------------------------------------------------------- controller
class GoalController:
    def __init__(self, deps: GoalDeps) -> None:
        self.deps = deps

    # ------------------------------------------------------------------------------------------ helpers
    @property
    def _runs(self) -> PipelineStore:
        return self.deps.runs

    def _goal_runs(self, conversation_id: str, user_id: str | None = None) -> list[RunRecord]:
        return [
            run for run in self._runs.list_runs(conversation_id, limit=200)
            if run.pipeline_id == GOAL_PIPELINE_ID and (user_id is None or run.user_id == user_id)
        ]

    def _active(self, conversation_id: str, user_id: str | None = None) -> RunRecord | None:
        return next((run for run in self._goal_runs(conversation_id, user_id) if run.state == "running"), None)

    def _view(self, run: RunRecord) -> _View:
        fresh = self._runs.get_run(run.run_id) or run
        steps = self._runs.steps_of(run.run_id)

        def of(step_id: str) -> tuple[StepRecord, ...]:
            return tuple(sorted((s for s in steps if s.step_id == step_id), key=lambda s: s.attempt))

        return _View(fresh, of(STEP_TURN), of(STEP_EVALUATE), of(STEP_RESUME))

    def _emit(self, turn: Mapping[str, Any] | None, event: PipelineEvent) -> None:
        if turn is None:
            return
        try:
            self.deps.emit(turn, event)
        except Exception:  # noqa: BLE001 - an event is advice, never a reason to lose the goal's state
            logger.exception("could not record %s", getattr(event.type, "value", event.type))

    def _write_step(
        self, run_id: str, step_id: str, key: str, *, state: str, output: Mapping[str, Any], input_tokens: int = 0,
        output_tokens: int = 0, cost_usd: Decimal | None = None, error: str | None = None,
    ) -> StepRecord:
        step = self._runs.start_step(run_id=run_id, step_id=step_id, input_hash=hash_text(key))
        done = self._runs.finish_step(
            run_id, step_id, "", step.attempt, state=state, output=dict(output), error=error,
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
        )
        tokens = max(0, input_tokens) + max(0, output_tokens)
        if tokens or cost_usd is not None:
            self._runs.add_usage(run_id, tokens=tokens, cost_usd=cost_usd)
        return done

    # --------------------------------------------------------------------------------------- the commands
    def start(
        self,
        *,
        user_id: str,
        conversation_id: str,
        objective: str,
        max_tokens: int | None = None,
        workspace_root: str = "",
        effort: str | None = None,
    ) -> GoalStart:
        """``/goal <objective> [--max-tokens N]``: create the run and send the objective as an ordinary first message."""
        text = str(objective or "").strip()
        if not text or len(text) > MAX_OBJECTIVE_CHARS:
            raise GoalError("GOAL_OBJECTIVE_INVALID", f"O objetivo deve ter de 1 a {MAX_OBJECTIVE_CHARS} caracteres.", 422)
        cap = self.deps.default_max_tokens if max_tokens is None else max_tokens
        if isinstance(cap, bool) or not isinstance(cap, int) or not MIN_MAX_TOKENS <= cap <= MAX_MAX_TOKENS:
            raise GoalError(
                "GOAL_MAX_TOKENS_INVALID",
                f"O teto de tokens deve estar entre {MIN_MAX_TOKENS:,} e {MAX_MAX_TOKENS:,}.".replace(",", "."),
                422,
            )
        with _START_LOCK:
            if self._active(conversation_id, user_id) is not None:
                raise GoalError("GOAL_ACTIVE", "Já existe um objetivo ativo nesta conversa. Use /goal clear para encerrá-lo.", 409)
            for old in self._goal_runs(conversation_id, user_id):
                if old.state == "interrupted":  # a parked goal is replaced by the new one
                    self._runs.set_run_state(old.run_id, "cancelled", stop_reason="superseded")
            used_in, used_out = self.deps.evidence.usage_total(conversation_id)
            run_id = f"goal-{uuid4().hex[:16]}"
            self._runs.create_run(
                run_id=run_id, user_id=user_id, conversation_id=conversation_id, workspace_root=workspace_root,
                pipeline_id=GOAL_PIPELINE_ID, definition_hash=hash_text("goal:v1"), source="builtin",
                inputs={"objetivo": text, "max_tokens": cap, "usage_baseline": {"input": used_in, "output": used_out}},
            )
        try:
            receipt = self.deps.send(user_id, conversation_id, text, f"goal:{run_id}:0", effort=effort)
        except Exception:
            self._runs.set_run_state(run_id, "cancelled", stop_reason="send_failed")
            raise
        turn_id = str(getattr(receipt, "turn_id", "") or "")
        if turn_id:
            self._runs.bind_turn(run_id, turn_id)
            first = self.deps.load_turn(turn_id)
            self._emit(first, pipeline_events.goal_started(run_id=run_id, condition=text, max_tokens=cap, max_turns=GOAL_MAX_TURNS))
        return GoalStart(run_id, turn_id, cap)

    def status(self, conversation_id: str, user_id: str | None = None) -> GoalStatus | None:
        runs = self._goal_runs(conversation_id, user_id)
        return self._status(self._view(runs[0])) if runs else None

    def clear(self, conversation_id: str, user_id: str | None = None) -> GoalStatus | None:
        """``/goal clear``: end the goal for good. A turn that is running keeps running; its end will find no goal."""
        run = next((r for r in self._goal_runs(conversation_id, user_id) if r.state in {"running", "interrupted"}), None)
        if run is None:
            return None
        self._runs.set_run_state(run.run_id, "cancelled", stop_reason="cleared")
        view = self._view(run)
        self._emit(self.deps.load_turn(run.turn_id) if run.turn_id else None, self._finished_event(view))
        return self._status(view)

    def resume(
        self, *, user_id: str, conversation_id: str, max_tokens: int | None = None, effort: str | None = None
    ) -> GoalStatus:
        """``/goal resume``: reopen a parked or stopped goal with a fresh window (allowance, turn and failure counts)."""
        if self._active(conversation_id, user_id) is not None:
            raise GoalError("GOAL_ACTIVE", "O objetivo desta conversa já está ativo.", 409)
        runs = self._goal_runs(conversation_id, user_id)
        run = runs[0] if runs else None
        if run is None:
            raise GoalError("GOAL_NOT_FOUND", "Não há objetivo nesta conversa.", 404)
        if not (run.state == "interrupted" or (run.state in {"failed", "budget_exceeded"} and run.stop_reason in _RESUMABLE_STOPS)):
            raise GoalError("GOAL_NOT_RESUMABLE", "Este objetivo foi encerrado e não pode ser retomado. Use /goal para criar outro.", 409)
        view = self._view(run)
        cap = view.cap if max_tokens is None else max_tokens
        if isinstance(cap, bool) or not isinstance(cap, int) or not MIN_MAX_TOKENS <= cap <= MAX_MAX_TOKENS:
            raise GoalError("GOAL_MAX_TOKENS_INVALID", "O teto de tokens está fora da faixa permitida.", 422)
        self._runs.set_run_state(run.run_id, "running")
        opened = len(view.resumes) + 1
        self._write_step(
            run.run_id, STEP_RESUME, f"{run.run_id}:resume:{opened}", state="succeeded",
            output={"max_tokens": cap, "spent_before": view.run.used_tokens, "turns_base": len(view.turns), "evals_base": len(view.evals)},
        )
        if not self.deps.live_turn(conversation_id):  # a live turn will reach after_turn and continue by itself
            self._send_continuation(
                run, view.objective, "the person resumed the goal; check what is still missing and finish it",
                key=f"goal:{run.run_id}:r{opened}", user_id=user_id, conversation_id=conversation_id,
                effort=effort, turn=None, index=len(view.turns) + 1,
            )
        fresh = self._runs.get_run(run.run_id)
        return self._status(self._view(fresh or run))

    # ------------------------------------------------------------------------------------------ the hook
    def after_turn(self, turn: Mapping[str, Any], result_state: str) -> str:
        """Called once per ended turn by the worker; returns a short label of what it did (for logs and tests)."""
        if turn.get("turn_kind") in SKIPPED_TURN_KINDS:
            return "ignored"
        run = self._active(str(turn["conversation_id"]))
        if run is None:
            return "no_goal"
        if result_state not in COMPLETED_STATES:
            return self._park(run, turn, result_state)
        if self.deps.evidence.has_pending_work(turn):
            return "deferred"  # the person has priority: the next turn that ends is evaluated instead
        evidence = self.deps.evidence.turn_evidence(turn)
        view = self._record_turn(self._view(run), turn, evidence)
        if view.spent >= view.cap:
            return self._stop(view, turn, "budget_exceeded", "budget")
        evaluation = self._evaluation_for(view, turn, evidence)
        view = self._view(run)
        if evaluation is not None and evaluation.state == "succeeded":
            body = _out(evaluation)
            self._emit(turn, pipeline_events.goal_evaluated(
                run_id=run.run_id, turn_index=len(view.turns), met=bool(body.get("done")), reason=str(body.get("reason") or ""),
                evaluator_model=str(body.get("model") or "") or None,
            ))
            if body.get("done"):
                return self._stop(view, turn, "succeeded", "done")
        else:
            failed = 0
            for row in reversed(view.window_evals):
                if row.state != "failed":
                    break
                failed += 1
            if failed >= EVALUATOR_FAILURES_TO_STOP:
                return self._stop(view, turn, "failed", "evaluator_failed")
        recent = view.window_turns[-NO_PROGRESS_TURNS:]
        if len(recent) == NO_PROGRESS_TURNS and not any(_out(row).get("tool_calls") or _out(row).get("files_changed") for row in recent):
            return self._stop(view, turn, "failed", "no_progress")
        if len(view.window_turns) >= GOAL_MAX_TURNS:
            return self._stop(view, turn, "failed", "max_turns")
        if view.spent >= view.cap:
            return self._stop(view, turn, "budget_exceeded", "budget")
        reason = str(_out(evaluation).get("reason") or "") if evaluation is not None and evaluation.state == "succeeded" else ""
        return self._send_continuation(
            run, view.objective, reason, key=f"goal:{run.run_id}:{len(view.turns)}", user_id=str(turn["user_id"]),
            conversation_id=str(turn["conversation_id"]), effort=_effort_of(turn), turn=turn, index=len(view.turns) + 1,
        )

    # ------------------------------------------------------------------------------------------- pieces
    def _record_turn(self, view: _View, turn: Mapping[str, Any], evidence: TurnEvidence) -> _View:
        turn_id = str(turn["turn_id"])
        if view.turns and _out(view.turns[-1]).get("turn_id") == turn_id:
            return view  # the same hook delivered twice
        now_in, now_out = self.deps.evidence.usage_total(str(turn["conversation_id"]))
        if view.turns:
            before = _out(view.turns[-1]).get("usage_total") or {}
        else:
            before = view.run.inputs.get("usage_baseline") or {}
        delta_in = max(0, now_in - int(before.get("input") or 0))
        delta_out = max(0, now_out - int(before.get("output") or 0))
        self._write_step(
            view.run.run_id, STEP_TURN, f"{view.run.run_id}:turn:{turn_id}", state="succeeded", input_tokens=delta_in,
            output_tokens=delta_out,
            output={
                "turn_id": turn_id, "turn_index": len(view.turns) + 1, "tool_calls": evidence.tool_calls,
                "files_changed": evidence.files_changed, "usage_total": {"input": now_in, "output": now_out},
            },
        )
        return self._view(view.run)

    def _evaluation_for(self, view: _View, turn: Mapping[str, Any], evidence: TurnEvidence) -> StepRecord | None:
        turn_id = str(turn["turn_id"])
        if view.evals and _out(view.evals[-1]).get("turn_id") == turn_id:
            return view.evals[-1]  # already judged: a redelivery reuses the verdict instead of paying for another call
        choice = choose_evaluator_model(
            str(turn.get("provider") or ""), str(turn.get("model_id") or ""), self.deps.candidates(turn),
            tiers=self.deps.tiers_for(turn),
        )
        key = f"{view.run.run_id}:eval:{turn_id}"
        base = {"turn_id": turn_id, "turn_index": len(view.turns), "model": str(choice), "fallback": choice.fallback}
        try:
            if self.deps.evaluator is None:
                raise EvaluatorFailed("EVALUATOR_UNAVAILABLE", "Nenhum modelo avaliador está disponível.")
            result: Evaluation = evaluate_goal(self.deps.evaluator, choice, view.objective, evidence.final_text, evidence.tools)
        except EvaluatorFailed as failure:
            logger.warning("goal %s: evaluator failed (%s)", view.run.run_id, failure.code)
            return self._write_step(
                view.run.run_id, STEP_EVALUATE, key, state="failed", error=failure.code,
                output={**base, "error": failure.code}, input_tokens=failure.input_tokens,
                output_tokens=failure.output_tokens, cost_usd=failure.cost_usd,
            )
        return self._write_step(
            view.run.run_id, STEP_EVALUATE, key, state="succeeded", input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, cost_usd=result.cost_usd,
            output={**base, "done": result.done, "reason": result.reason, "attempts": result.attempts},
        )

    def _send_continuation(
        self, run: RunRecord, objective: str, reason: str, *, key: str, user_id: str, conversation_id: str,
        effort: str | None, turn: Mapping[str, Any] | None, index: int,
    ) -> str:
        last = reason.strip() or "the last evaluation could not be completed; check the objective against your own work"
        message = f"Continue working toward the goal: {objective}. Last evaluation: {last}."
        try:
            receipt = self.deps.send(user_id, conversation_id, message[:16000], key, effort=effort)
        except Exception:  # noqa: BLE001 - park the goal so the person can resume it; never lose it silently
            logger.exception("goal %s: could not send the continuation", run.run_id)
            try:
                self._runs.set_run_state(run.run_id, "interrupted", stop_reason="send_failed")
            except PipelineStoreError:
                pass
            return "send_failed"
        turn_id = str(getattr(receipt, "turn_id", "") or "")
        if turn_id:
            self._runs.bind_turn(run.run_id, turn_id)
        fresh = self._runs.get_run(run.run_id)
        self._emit(turn, pipeline_events.goal_continued(
            run_id=run.run_id, turn_index=index, used_tokens=fresh.used_tokens if fresh else 0,
        ))
        return "continued"

    def _park(self, run: RunRecord, turn: Mapping[str, Any], result_state: str) -> str:
        reason = _PARKED_REASONS.get(result_state, "turn_failed")
        self._runs.set_run_state(run.run_id, "interrupted", stop_reason=reason)
        self._emit(turn, self._finished_event(self._view(run)))
        return "parked"

    def _stop(self, view: _View, turn: Mapping[str, Any], state: str, reason: str) -> str:
        self._runs.set_run_state(view.run.run_id, state, stop_reason=reason)
        self._emit(turn, self._finished_event(self._view(view.run)))
        return reason

    def _finished_event(self, view: _View) -> PipelineEvent:
        return pipeline_events.goal_finished(
            run_id=view.run.run_id, state=view.run.state, stop_reason=view.run.stop_reason, turns=len(view.turns),
            used_tokens=view.run.used_tokens,
        )

    def _status(self, view: _View) -> GoalStatus:
        last = next((row for row in reversed(view.evals) if row.state == "succeeded"), None)
        body = _out(last) if last is not None else {}
        run = view.run
        return GoalStatus(
            run_id=run.run_id, state=run.state, stop_reason=run.stop_reason, objective=view.objective, max_tokens=view.cap,
            used_tokens=run.used_tokens, window_used_tokens=view.spent, turns=len(view.turns),
            window_turns=len(view.window_turns), max_turns=GOAL_MAX_TURNS,
            last_evaluation=(
                {"turn_index": body.get("turn_index"), "done": body.get("done"), "reason": body.get("reason"),
                 "model": body.get("model"), "fallback": body.get("fallback")}
                if last is not None else None
            ),
            evaluator_model=str(body.get("model") or "") or None,
            resumable=run.state == "interrupted" or (run.state in {"failed", "budget_exceeded"} and run.stop_reason in _RESUMABLE_STOPS),
            started_at=run.started_at.isoformat(), finished_at=_iso(run.finished_at),
        )

    # ------------------------------------------------------------------------------------------ factory
    @classmethod
    def from_store(
        cls, store: Any, *, context: AfterTurnContext | None = None, user_id: str | None = None
    ) -> GoalController:
        """Wire the controller to a chat store (API commands) and, when a worker context is given, to its models."""
        from agentos.settings.keys_pipelines import DEFAULT_GOAL_MAX_TOKENS

        evidence = StoreGoalEvidence(store)

        def send(uid: str, conversation_id: str, message: str, key: str, *, effort: str | None = None) -> Any:
            from agentos.conversations.chat import ChatApplication

            return ChatApplication(store).send(uid, conversation_id, message, key, effort=effort)

        def emit(turn: Mapping[str, Any], event: PipelineEvent) -> None:
            store._activity(dict(turn), event.type, event.summary, dict(event.payload))

        evaluator: EvaluatorModelPort | None = None
        if context is not None and context.provider_transport is not None and user_id:
            evaluator = TransportEvaluatorModel(context.provider_transport, user_id=user_id)
        favorites = context.favorite_models if context is not None else None

        def candidates(turn: Mapping[str, Any]) -> Sequence[str]:
            """Favorite models of the turn's own provider (the same subscription); none means the turn's model judges."""
            if favorites is None:
                return ()
            try:
                return tuple(favorites(turn).get(str(turn.get("provider") or ""), ()))
            except Exception:  # noqa: BLE001 - no catalog means the turn's own model is the evaluator
                logger.warning("could not read the favorite models for the goal evaluator", exc_info=True)
                return ()

        return cls(GoalDeps(
            runs=PipelineStore(store._engine), evidence=evidence, send=send, emit=emit, evaluator=evaluator,
            candidates=candidates, tiers_for=_tiers_from_settings, default_max_tokens=DEFAULT_GOAL_MAX_TOKENS,
            live_turn=evidence.live_turn, load_turn=evidence.turn_row,
        ))


def _effort_of(turn: Mapping[str, Any]) -> str | None:
    value = turn.get("effort")
    return value if isinstance(value, str) and value else None


def _tiers_from_settings(turn: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """``orchestration.modelTiers`` from the user's settings; the default table when nothing can be read."""
    try:
        from agentos.agentic.verification_gate import read_gate_setting
        from agentos.settings.loader import load_effective_settings

        root = turn.get("workspace_root_path")
        settings = load_effective_settings(root if isinstance(root, str) and root else None)
        value = read_gate_setting(settings, "orchestration.modelTiers", None)
        return value if isinstance(value, Mapping) else None
    except Exception:  # noqa: BLE001 - tiers are advice
        logger.debug("could not read orchestration.modelTiers", exc_info=True)
        return None


def after_turn_handler(store: Any, turn: Mapping[str, Any], result_state: str, context: AfterTurnContext) -> None:
    """The handler ``after_turn.py`` registers: ``/goal`` continuation. A store without an engine has no goals."""
    if getattr(store, "_engine", None) is None:
        return
    user_id = str(turn.get("user_id") or "") or None
    GoalController.from_store(store, context=context, user_id=user_id).after_turn(turn, result_state)


__all__ = [
    "GOAL_MAX_TURNS",
    "GOAL_PIPELINE_ID",
    "GoalController",
    "GoalDeps",
    "GoalError",
    "GoalEvidence",
    "GoalStart",
    "GoalStatus",
    "StoreGoalEvidence",
    "TurnEvidence",
    "after_turn_handler",
]
