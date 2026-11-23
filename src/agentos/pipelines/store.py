"""Durable journal of pipeline runs and steps (WS-C3, spec 7.6).

``PipelineStore`` is the only writer of ``pipeline_runs`` and ``pipeline_step_runs``. It is deliberately dumb:
it records what the engine tells it, refuses impossible state transitions and never decides policy (budgets,
retries, resume rules live in the engine).

Reuse key (decision D7/G11): ``(step_id, item_key, loop_count)`` plus ``input_hash``. ``attempt`` is a monotonic
counter per ``(run_id, step_id, item_key)``, so a step repeated inside a loop never collides.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from sqlalchemy import and_, delete, func, insert, literal, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.types import DateTime

from agentos.persistence.postgres.schema import agent_runs, pipeline_runs, pipeline_step_runs

from .hashing import hash_text

logger = logging.getLogger("agentos.pipelines.store")

PIPELINE_RETENTION_DAYS: Final = 90
ARTIFACT_THRESHOLD_BYTES: Final = 32 * 1024
ERROR_MAX_CHARS: Final = 2000
ARTIFACT_DIR: Final = (".orin", "pipeline-runs")
_PRUNE_BATCH: Final = 200
_ATTEMPT_RETRIES: Final = 8

RUN_SOURCES: Final = frozenset({"builtin", "user", "project"})
RUN_STATES: Final = frozenset({"running", "succeeded", "failed", "cancelled", "interrupted", "budget_exceeded"})
RUN_TERMINAL_STATES: Final = frozenset({"succeeded", "failed", "cancelled", "budget_exceeded"})
STEP_STATES: Final = frozenset(
    {"pending", "running", "succeeded", "failed", "stalled", "skipped", "interrupted", "gate_failed"}
)

_STEP_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "pending": frozenset({"running", "skipped", "interrupted"}),
    "running": frozenset({"succeeded", "failed", "stalled", "interrupted", "gate_failed"}),
}
_RUN_RESUMABLE: Final = frozenset({"failed", "cancelled", "budget_exceeded"})
_USAGE_GROUPS: Final = frozenset({"step", "agent", "provider"})
_NO_COST_REQUIRED_STATES: Final = frozenset({"pending", "skipped"})


class PipelineStoreError(RuntimeError):
    """The journal refused an operation (unknown run or step, invalid value, impossible transition)."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    user_id: str
    conversation_id: str
    workspace_root: str
    pipeline_id: str
    definition_hash: str
    source: str
    inputs: Mapping[str, Any]
    state: str
    used_tokens: int
    used_cost_usd: Decimal | None
    used_seconds: int
    started_at: datetime
    finished_at: datetime | None
    definition_snapshot: str
    turn_id: str | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class StepRecord:
    run_id: str
    step_id: str
    item_key: str
    attempt: int
    input_hash: str
    state: str
    agent_id: str | None
    output: Any
    error: str | None
    loop_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    started_at: datetime | None
    finished_at: datetime | None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: float | int | str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _safe_component(text: str) -> str:
    """Make ``text`` safe as one file-name component on every OS; keep a hash when it had to change."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text).lstrip(".")
    if cleaned != text or not cleaned:
        cleaned = f"{cleaned or '_'}-{hash_text(text)[:8]}"
    return cleaned[:96]


def artifact_dir(workspace_root: str | Path, run_id: str) -> Path:
    return Path(workspace_root).joinpath(*ARTIFACT_DIR, _safe_component(run_id))


def _run_from_row(row: Any) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        user_id=row["user_id"],
        conversation_id=row["conversation_id"],
        workspace_root=row["workspace_root"],
        pipeline_id=row["pipeline_id"],
        definition_hash=row["definition_hash"],
        source=row["source"],
        inputs=row["inputs"] if isinstance(row["inputs"], dict) else {},
        state=row["state"],
        used_tokens=int(row["used_tokens"] or 0),
        used_cost_usd=_decimal(row["used_cost_usd"]),
        used_seconds=int(row["used_seconds"] or 0),
        started_at=_utc(row["started_at"]),  # type: ignore[arg-type]
        finished_at=_utc(row["finished_at"]),
        definition_snapshot=row["definition_snapshot"] or "",
        turn_id=row["turn_id"],
        stop_reason=row["stop_reason"],
    )


def _step_from_row(row: Any) -> StepRecord:
    return StepRecord(
        run_id=row["run_id"],
        step_id=row["step_id"],
        item_key=row["item_key"] or "",
        attempt=int(row["attempt"]),
        input_hash=row["input_hash"],
        state=row["state"],
        agent_id=row["agent_id"],
        output=row["output"],
        error=row["error"],
        loop_count=int(row["loop_count"] or 0),
        input_tokens=int(row["input_tokens"] or 0),
        output_tokens=int(row["output_tokens"] or 0),
        cost_usd=_decimal(row["cost_usd"]),
        started_at=_utc(row["started_at"]),
        finished_at=_utc(row["finished_at"]),
    )


class PipelineStore:
    """SQLAlchemy Core access to the pipeline journal. Safe to share between threads."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._attempt_lock = threading.Lock()

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        *,
        run_id: str,
        user_id: str,
        conversation_id: str,
        workspace_root: str,
        pipeline_id: str,
        definition_hash: str,
        source: str,
        inputs: Mapping[str, Any] | None = None,
        definition_snapshot: str = "",
        turn_id: str | None = None,
        state: str = "running",
        started_at: datetime | None = None,
    ) -> RunRecord:
        if not run_id or len(run_id) > 64:
            raise PipelineStoreError("run_id deve ter de 1 a 64 caracteres")
        if source not in RUN_SOURCES:
            raise PipelineStoreError(f"origem de pipeline invalida: {source!r}")
        if state not in RUN_STATES:
            raise PipelineStoreError(f"estado de execucao invalido: {state!r}")
        moment = _utc(started_at) or _now()
        values = dict(
            run_id=run_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workspace_root=workspace_root,
            pipeline_id=pipeline_id,
            definition_hash=definition_hash,
            source=source,
            inputs=dict(inputs or {}),
            state=state,
            used_tokens=0,
            used_cost_usd=None,
            used_seconds=0,
            started_at=moment,
            finished_at=None if state == "running" else moment,
            definition_snapshot=definition_snapshot,
            turn_id=turn_id,
            stop_reason=None,
        )
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(pipeline_runs).values(**values))
        except IntegrityError as exc:
            raise PipelineStoreError(f"a execucao {run_id!r} ja existe") from exc
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(pipeline_runs).where(pipeline_runs.c.run_id == run_id)).mappings().first()
        return _run_from_row(row) if row is not None else None

    def list_runs(self, conversation_id: str, *, limit: int = 100) -> list[RunRecord]:
        statement = (
            select(pipeline_runs)
            .where(pipeline_runs.c.conversation_id == conversation_id)
            .order_by(pipeline_runs.c.started_at.desc(), pipeline_runs.c.run_id.desc())
            .limit(max(1, int(limit)))
        )
        with self._engine.connect() as connection:
            return [_run_from_row(row) for row in connection.execute(statement).mappings()]

    def set_run_state(
        self,
        run_id: str,
        state: str,
        *,
        stop_reason: str | None = None,
        turn_id: str | None = None,
    ) -> RunRecord:
        """Move a run to ``state``. ``stop_reason`` replaces the stored one (``None`` clears it)."""
        if state not in RUN_STATES:
            raise PipelineStoreError(f"estado de execucao invalido: {state!r}")
        if stop_reason is not None and len(stop_reason) > 32:
            raise PipelineStoreError("stop_reason excede 32 caracteres")
        with self._engine.begin() as connection:
            current = connection.execute(
                select(pipeline_runs.c.state).where(pipeline_runs.c.run_id == run_id)
            ).scalar()
            if current is None:
                raise PipelineStoreError(f"execucao desconhecida: {run_id!r}")
            if not self._run_transition_allowed(current, state):
                raise PipelineStoreError(f"transicao invalida de execucao: {current!r} para {state!r}")
            values: dict[str, Any] = {
                "state": state,
                "stop_reason": None if state == "running" else stop_reason,
                "finished_at": None if state == "running" else _now(),
            }
            if turn_id is not None:
                values["turn_id"] = turn_id
            connection.execute(
                update(pipeline_runs)
                .where(and_(pipeline_runs.c.run_id == run_id, pipeline_runs.c.state == current))
                .values(**values)
            )
        run = self.get_run(run_id)
        assert run is not None
        return run

    @staticmethod
    def _run_transition_allowed(current: str, target: str) -> bool:
        if current == "succeeded":
            return False
        if current in _RUN_RESUMABLE:
            return target == "running"
        return True  # running and interrupted can go anywhere, including to themselves

    def add_usage(
        self,
        run_id: str,
        *,
        tokens: int = 0,
        cost_usd: float | int | str | Decimal | None = None,
        seconds: int = 0,
    ) -> None:
        """Accumulate usage on the run. ``cost_usd=None`` (unknown cost) leaves the cost total untouched."""
        values: dict[str, Any] = {
            "used_tokens": pipeline_runs.c.used_tokens + int(tokens),
            "used_seconds": pipeline_runs.c.used_seconds + int(seconds),
        }
        cost = _decimal(cost_usd)
        if cost is not None:
            values["used_cost_usd"] = func.coalesce(pipeline_runs.c.used_cost_usd, 0) + cost
        with self._engine.begin() as connection:
            result = connection.execute(update(pipeline_runs).where(pipeline_runs.c.run_id == run_id).values(**values))
            if result.rowcount == 0:
                raise PipelineStoreError(f"execucao desconhecida: {run_id!r}")

    def recompute_usage(self, run_id: str) -> RunRecord:
        """Set the run's token and cost totals to the sum of its step rows (heals drift after a crash)."""
        totals = self.usage_by(run_id, "step")
        tokens = sum(row["input_tokens"] + row["output_tokens"] for row in totals)
        known = [row["cost_usd"] for row in totals if row["cost_usd"] is not None and row["cost_known"]]
        cost = sum(known, Decimal("0")) if known else None
        with self._engine.begin() as connection:
            connection.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == run_id)
                .values(used_tokens=tokens, used_cost_usd=cost)
            )
        run = self.get_run(run_id)
        if run is None:
            raise PipelineStoreError(f"execucao desconhecida: {run_id!r}")
        return run

    # ----------------------------------------------------------------- steps

    def start_step(
        self,
        *,
        run_id: str,
        step_id: str,
        input_hash: str,
        item_key: str = "",
        loop_count: int = 0,
        agent_id: str | None = None,
        state: str = "running",
    ) -> StepRecord:
        """Insert the next attempt of a step before it runs, with its ``input_hash`` (spec C-RF2)."""
        if state not in {"pending", "running", "skipped"}:
            raise PipelineStoreError(f"estado inicial de etapa invalido: {state!r}")
        if not step_id or len(step_id) > 64 or len(item_key) > 255:
            raise PipelineStoreError("step_id (1 a 64) ou item_key (ate 255) fora do limite")
        moment = _now()
        started = moment if state in {"running", "skipped"} else None
        finished = moment if state == "skipped" else None
        with self._attempt_lock:
            for attempt_number in range(_ATTEMPT_RETRIES):
                try:
                    attempt = self._insert_next_attempt(
                        run_id=run_id,
                        step_id=step_id,
                        item_key=item_key,
                        input_hash=input_hash,
                        state=state,
                        agent_id=agent_id,
                        loop_count=int(loop_count),
                        started_at=started,
                        finished_at=finished,
                    )
                    break
                except (IntegrityError, OperationalError) as exc:
                    # Another process took the same attempt (or the SQLite writer lock): try the next number.
                    if attempt_number == _ATTEMPT_RETRIES - 1:
                        raise PipelineStoreError("nao foi possivel gravar a etapa no diario") from exc
        step = self._get_step(run_id, step_id, item_key, attempt)
        assert step is not None
        return step

    def _insert_next_attempt(
        self,
        *,
        run_id: str,
        step_id: str,
        item_key: str,
        input_hash: str,
        state: str,
        agent_id: str | None,
        loop_count: int,
        started_at: datetime | None,
        finished_at: datetime | None,
    ) -> int:
        steps = pipeline_step_runs
        with self._engine.begin() as connection:
            if connection.execute(select(pipeline_runs.c.run_id).where(pipeline_runs.c.run_id == run_id)).first() is None:
                raise PipelineStoreError(f"execucao desconhecida: {run_id!r}")
            next_attempt = (
                select(
                    literal(run_id),
                    literal(step_id),
                    literal(item_key),
                    func.coalesce(func.max(steps.c.attempt), 0) + 1,
                    literal(input_hash),
                    literal(state),
                    literal(agent_id),
                    literal(loop_count),
                    literal(started_at, DateTime(timezone=True)),
                    literal(finished_at, DateTime(timezone=True)),
                )
                .where(and_(steps.c.run_id == run_id, steps.c.step_id == step_id, steps.c.item_key == item_key))
            )
            connection.execute(
                insert(steps).from_select(
                    [
                        "run_id", "step_id", "item_key", "attempt", "input_hash", "state", "agent_id",
                        "loop_count", "started_at", "finished_at",
                    ],
                    next_attempt,
                )
            )
            attempt = connection.execute(
                select(func.max(steps.c.attempt)).where(
                    and_(steps.c.run_id == run_id, steps.c.step_id == step_id, steps.c.item_key == item_key)
                )
            ).scalar()
        return int(attempt)

    def finish_step(
        self,
        run_id: str,
        step_id: str,
        item_key: str,
        attempt: int,
        *,
        state: str,
        output: Any = None,
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | int | str | Decimal | None = None,
        agent_id: str | None = None,
    ) -> StepRecord:
        """Close an attempt. Large outputs go to an artifact file and the column keeps ``{"ref": ...}``."""
        if state not in STEP_STATES:
            raise PipelineStoreError(f"estado de etapa invalido: {state!r}")
        current = self._get_step(run_id, step_id, item_key, attempt)
        if current is None:
            raise PipelineStoreError(f"etapa desconhecida: {run_id}/{step_id}/{item_key}#{attempt}")
        if state not in _STEP_TRANSITIONS.get(current.state, frozenset()):
            raise PipelineStoreError(f"transicao invalida de etapa: {current.state!r} para {state!r}")
        stored_output = self._externalize_output(current, output) if output is not None else None
        values: dict[str, Any] = {
            "state": state,
            "output": stored_output,
            "error": error[:ERROR_MAX_CHARS] if error else None,
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "cost_usd": _decimal(cost_usd),
            "finished_at": _now(),
        }
        if current.state == "pending":
            values["started_at"] = _now()
        if agent_id is not None:
            values["agent_id"] = agent_id
        steps = pipeline_step_runs
        with self._engine.begin() as connection:
            result = connection.execute(
                update(steps)
                .where(
                    and_(
                        steps.c.run_id == run_id,
                        steps.c.step_id == step_id,
                        steps.c.item_key == item_key,
                        steps.c.attempt == attempt,
                        steps.c.state == current.state,
                    )
                )
                .values(**values)
            )
            if result.rowcount == 0:
                raise PipelineStoreError("a etapa mudou de estado durante a gravacao")
        done = self._get_step(run_id, step_id, item_key, attempt)
        assert done is not None
        return done

    def find_reusable(self, run_id: str, step_id: str, item_key: str, loop_count: int) -> StepRecord | None:
        """Latest attempt of the step for this loop iteration (the caller compares ``state`` and ``input_hash``)."""
        steps = pipeline_step_runs
        statement = (
            select(steps)
            .where(
                and_(
                    steps.c.run_id == run_id,
                    steps.c.step_id == step_id,
                    steps.c.item_key == item_key,
                    steps.c.loop_count == int(loop_count),
                )
            )
            .order_by(steps.c.attempt.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _step_from_row(row) if row is not None else None

    def steps_of(self, run_id: str) -> list[StepRecord]:
        steps = pipeline_step_runs
        statement = select(steps).where(steps.c.run_id == run_id).order_by(
            steps.c.started_at, steps.c.step_id, steps.c.item_key, steps.c.attempt
        )
        with self._engine.connect() as connection:
            return [_step_from_row(row) for row in connection.execute(statement).mappings()]

    def _get_step(self, run_id: str, step_id: str, item_key: str, attempt: int) -> StepRecord | None:
        steps = pipeline_step_runs
        statement = select(steps).where(
            and_(
                steps.c.run_id == run_id,
                steps.c.step_id == step_id,
                steps.c.item_key == item_key,
                steps.c.attempt == attempt,
            )
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _step_from_row(row) if row is not None else None

    def interrupt_orphans(self, run_id: str | None = None) -> int:
        """Turn ``running`` runs (all, or just ``run_id``) and their open steps into ``interrupted``."""
        moment = _now()
        runs, steps = pipeline_runs, pipeline_step_runs
        condition = runs.c.state == "running"
        if run_id is not None:
            condition = and_(condition, runs.c.run_id == run_id)
        with self._engine.begin() as connection:
            orphan_ids = [row[0] for row in connection.execute(select(runs.c.run_id).where(condition))]
            if not orphan_ids:
                return 0
            for start in range(0, len(orphan_ids), _PRUNE_BATCH):
                batch = orphan_ids[start : start + _PRUNE_BATCH]
                connection.execute(
                    update(steps)
                    .where(and_(steps.c.run_id.in_(batch), steps.c.state.in_(("running", "pending"))))
                    .values(state="interrupted", finished_at=moment)
                )
                connection.execute(
                    update(runs)
                    .where(and_(runs.c.run_id.in_(batch), runs.c.state == "running"))
                    .values(state="interrupted", finished_at=moment)
                )
        return len(orphan_ids)

    # ------------------------------------------------------------- telemetry

    def usage_by(self, run_id: str, group: str) -> list[dict[str, Any]]:
        """Token and cost totals grouped by ``step``, ``agent`` or ``provider``.

        Rows without an agent (command gates) are left out of the ``agent`` and ``provider`` groups. ``provider``
        comes from ``agent_runs`` and is ``"unknown"`` when that table has no row for the agent. ``cost_usd`` sums
        only the known costs and is ``None`` when none is known; ``cost_known`` is false while any row that ran an
        agent has no cost (so the number is a lower bound, never a silent zero).
        """
        if group not in _USAGE_GROUPS:
            raise PipelineStoreError(f"agrupamento desconhecido: {group!r}")
        steps = pipeline_step_runs
        statement = (
            select(
                steps.c.step_id,
                steps.c.agent_id,
                steps.c.state,
                steps.c.input_tokens,
                steps.c.output_tokens,
                steps.c.cost_usd,
                agent_runs.c.provider,
            )
            .select_from(steps.outerjoin(agent_runs, agent_runs.c.agent_id == steps.c.agent_id))
            .where(steps.c.run_id == run_id)
        )
        buckets: dict[str, dict[str, Any]] = {}
        with self._engine.connect() as connection:
            rows = list(connection.execute(statement).mappings())
        for row in rows:
            if group == "step":
                key: str | None = row["step_id"]
            elif group == "agent":
                key = row["agent_id"]
            else:
                key = (row["provider"] or "unknown") if row["agent_id"] else None
            if key is None:
                continue
            bucket = buckets.setdefault(
                key,
                {"key": key, "rows": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None, "cost_known": True},
            )
            bucket["rows"] += 1
            bucket["input_tokens"] += int(row["input_tokens"] or 0)
            bucket["output_tokens"] += int(row["output_tokens"] or 0)
            cost = _decimal(row["cost_usd"])
            if cost is not None:
                bucket["cost_usd"] = (bucket["cost_usd"] or Decimal("0")) + cost
            elif row["agent_id"] and row["state"] not in _NO_COST_REQUIRED_STATES:
                bucket["cost_known"] = False
        for bucket in buckets.values():
            if bucket["cost_usd"] is None and bucket["cost_known"]:
                bucket["cost_usd"] = Decimal("0")  # nothing here needed a cost (for example only command gates)
        return sorted(buckets.values(), key=lambda item: item["key"])

    # ------------------------------------------------------------- artifacts

    def _externalize_output(self, step: StepRecord, output: Any) -> Any:
        text = json.dumps(output, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text.encode("utf-8")) <= ARTIFACT_THRESHOLD_BYTES:
            return json.loads(text)
        run = self.get_run(step.run_id)
        if run is None:
            raise PipelineStoreError(f"execucao desconhecida: {step.run_id!r}")
        name = (
            f"{_safe_component(step.step_id)}-{_safe_component(step.item_key) if step.item_key else '_'}"
            f"-l{step.loop_count}-a{step.attempt}.json"
        )
        directory = artifact_dir(run.workspace_root, step.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        temporary = directory / f"{name}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"ref": "/".join((*ARTIFACT_DIR, directory.name, name))}

    def load_output(self, run_id: str, output: Any) -> Any:
        """Return the real output: the artifact content for a ``{"ref": ...}`` column, else the value itself."""
        if not (isinstance(output, Mapping) and set(output) == {"ref"} and isinstance(output["ref"], str)):
            return output
        run = self.get_run(run_id)
        if run is None:
            raise PipelineStoreError(f"execucao desconhecida: {run_id!r}")
        base = artifact_dir(run.workspace_root, run_id).resolve()
        target = (Path(run.workspace_root) / output["ref"]).resolve()
        if not target.is_relative_to(base):
            raise PipelineStoreError("a referencia de artefato aponta para fora da pasta da execucao")
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PipelineStoreError(f"artefato ilegivel: {output['ref']}") from exc

    # ----------------------------------------------------------------- prune

    def prune(self, older_than: datetime) -> int:
        """Delete finished runs (never ``running`` or ``interrupted``) that ended before ``older_than``."""
        cutoff = _utc(older_than)
        runs, steps = pipeline_runs, pipeline_step_runs
        condition = and_(runs.c.state.in_(tuple(RUN_TERMINAL_STATES)), runs.c.finished_at < cutoff)
        removed: list[tuple[str, str]] = []
        with self._engine.begin() as connection:
            candidates = [
                (row["run_id"], row["workspace_root"])
                for row in connection.execute(select(runs.c.run_id, runs.c.workspace_root).where(condition)).mappings()
            ]
            for start in range(0, len(candidates), _PRUNE_BATCH):
                batch = candidates[start : start + _PRUNE_BATCH]
                ids = [run_id for run_id, _root in batch]
                connection.execute(delete(steps).where(steps.c.run_id.in_(ids)))
                connection.execute(delete(runs).where(and_(runs.c.run_id.in_(ids), condition)))
                removed.extend(batch)
        for run_id, root in removed:
            try:
                shutil.rmtree(artifact_dir(root, run_id), ignore_errors=True)
            except OSError:  # pragma: no cover - ignore_errors already covers the usual cases
                logger.warning("could not remove pipeline artifacts of %s", run_id)
        return len(removed)


__all__ = [
    "ARTIFACT_THRESHOLD_BYTES",
    "PIPELINE_RETENTION_DAYS",
    "PipelineStore",
    "PipelineStoreError",
    "RUN_STATES",
    "RUN_TERMINAL_STATES",
    "RunRecord",
    "STEP_STATES",
    "StepRecord",
    "artifact_dir",
]
