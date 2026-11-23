"""Step runners: one agent attempt loop (retries, stall, budget, resume) and the ``map`` fan-out (WS-C6)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from agentos.agentic.structured_output import ExtractError, extract_json, validate_instance

from . import events as ev
from .budgets import PipelineBudget
from .context import RunContext, make_resolver
from .expressions import render_template
from .gates import GateEvaluator
from .hashing import canonical_json, hash_text, step_input_hash
from .model import AgentStep, Defaults, PipelineDefinition
from .ports import (
    AgentCall,
    AgentHandle,
    AgentLauncher,
    AgentResult,
    Clock,
    EngineOptions,
    EventSink,
    JournalStore,
)
from .render import RenderError, over_is_untrusted, render_prompt, resolve_over
from .resume import RESUME_CONFLICT, ResumePlanner, wrap_envelope
from .retries import (
    CAUSE_SCHEMA_INVALID,
    SCHEMA_INVALID_CODE,
    cause_of,
    effective_policy,
    is_retryable,
    wait_before_retry,
)
from .stall import DEFAULT_STALL_MINUTES, StallWatch

logger = logging.getLogger("agentos.pipelines.steps")

GRACE_POLLS = 20  # polls to wait for a cancelled child before giving up on its result
ABORT_CANCELLED = "cancelled"
ABORT_BUDGET = "budget_exceeded"
ABORT_PAUSED = "paused"
ABORT_AGENT_CAP = "agent_cap"
ABORT_CONFLICT = "resume_conflict"


class EventBus:
    """One lock in front of the sink: events from any thread are recorded one at a time, in a stable order."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink
        self._lock = threading.Lock()

    def emit(self, event: ev.PipelineEvent, *, agent_id: str | None = None) -> None:
        with self._lock:
            try:
                self._sink.emit(event, agent_id=agent_id)
            except Exception:  # noqa: BLE001 - observability never breaks the run
                logger.exception("event sink failed")


class InFlight:
    """Handles of the children running now. ``stop_all`` cancels each once and refuses new ones afterwards."""

    def __init__(self, launcher: AgentLauncher) -> None:
        self._launcher = launcher
        self._lock = threading.Lock()
        self._handles: dict[str, AgentHandle] = {}
        self._cancelled: set[str] = set()
        self._stopping: str | None = None

    def add(self, handle: AgentHandle) -> bool:
        with self._lock:
            self._handles[handle.agent_id] = handle
            return self._stopping is None

    def remove(self, handle: AgentHandle) -> None:
        with self._lock:
            self._handles.pop(handle.agent_id, None)

    def cancel_one(self, handle: AgentHandle, reason: str) -> None:
        with self._lock:
            if handle.agent_id in self._cancelled:
                return
            self._cancelled.add(handle.agent_id)
        try:
            self._launcher.cancel(handle, reason)
        except Exception:  # noqa: BLE001
            logger.exception("could not cancel %s", handle.agent_id)

    def stop_all(self, reason: str) -> None:
        with self._lock:
            self._stopping = self._stopping or reason
            handles = list(self._handles.values())
        for handle in handles:
            self.cancel_one(handle, reason)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._handles)


@dataclass(slots=True)
class RunEnv:
    """Everything the runners share for one ``execute`` call."""

    definition: PipelineDefinition
    run_id: str
    store: JournalStore
    launcher: AgentLauncher
    clock: Clock
    bus: EventBus
    budget: PipelineBudget
    context: RunContext
    options: EngineOptions
    planner: ResumePlanner
    inflight: InFlight
    gates: GateEvaluator
    drain: threading.Event = field(default_factory=threading.Event)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    dirty: bool = False
    agents_launched: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def stop(self) -> threading.Event:
        return self.budget.cancel

    def abort_reason(self) -> str | None:
        """Why running work must stop now (``None`` while everything may go on)."""
        if self.cancel_requested.is_set():
            return ABORT_CANCELLED
        if self.stop.is_set() or self.budget.tripped is not None:
            return ABORT_BUDGET
        if self.budget.exceeded() is not None:
            self.trip_budget()
            return ABORT_BUDGET
        return None

    def should_start(self) -> str | None:
        """Why no new work may start (an abort reason, or a requested pause)."""
        reason = self.abort_reason()
        if reason:
            return reason
        return ABORT_PAUSED if self.drain.is_set() else None

    def trip_budget(self) -> None:
        tripped = self.budget.tripped or self.budget.exceeded()
        if tripped is not None and self.budget.claim_announcement():
            self.bus.emit(
                ev.budget_exceeded(
                    run_id=self.run_id, scope="pipeline", limit_kind=tripped.kind, limit=tripped.limit, used=tripped.used
                )
            )
        self.inflight.stop_all(ABORT_BUDGET)

    def request_cancel(self) -> None:
        self.cancel_requested.set()
        self.stop.set()
        self.inflight.stop_all(ABORT_CANCELLED)

    def reserve_agent(self) -> bool:
        with self._lock:
            if self.agents_launched >= self.options.max_agents:
                return False
            self.agents_launched += 1
            return True

    def mark_dirty(self) -> None:
        with self._lock:
            self.dirty = True

    def count_step(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.steps_succeeded += 1
            else:
                self.steps_failed += 1

    def charge(self, tokens: int, cost: float | None) -> None:
        if self.launcher_live():
            return
        if self.budget.charge(tokens, cost):
            self.trip_budget()
        elif self.budget.tripped is not None:
            self.trip_budget()

    def launcher_live(self) -> bool:
        return bool(getattr(self.launcher, "live_metering", False))


@dataclass(slots=True)
class UnitResult:
    """Outcome of one step visit (or one ``map`` item)."""

    state: str  # succeeded | failed | aborted
    output: Any = None
    error_code: str | None = None
    error: str | None = None
    reused: bool = False
    abort: str | None = None
    worktree: Mapping[str, Any] | None = None
    fingerprint: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.state == "succeeded"


class _Meter:
    """Charges the pipeline budget with a child's spending as it grows (launchers without live metering)."""

    def __init__(self, env: RunEnv) -> None:
        self._env = env
        self.tokens = 0
        self.cost = 0.0
        self.cost_missing = False

    def sync(self, tokens: int, cost: float | None) -> None:
        delta = max(0, tokens - self.tokens)
        if delta == 0 and cost is None:
            return
        cost_delta: float | None = None
        if cost is not None:
            cost_delta = max(0.0, cost - self.cost)
            self.cost = max(self.cost, cost)
        elif delta > 0:
            self.cost_missing = True
        self.tokens = max(self.tokens, tokens)
        if delta or cost_delta:
            self._env.charge(delta, cost_delta if cost is not None else None)

    def finish(self, result: AgentResult) -> None:
        self.sync(result.tokens, result.cost_usd if result.cost_known else None)


@dataclass(slots=True)
class _Waited:
    result: AgentResult | None
    stalled: bool
    abort: str | None
    agent_id: str
    replan: str | None


def _hash8(item: Any) -> str:
    return hash_text(canonical_json(item))[:8]


def item_key_for(index: int, item: Any) -> str:
    return f"{index:03d}-{_hash8(item)}"


class AgentStepRunner:
    def __init__(self, env: RunEnv) -> None:
        self.env = env

    # ------------------------------------------------------------- rendering
    def _field(self, template: str | None, view: Mapping[str, Any]) -> str | None:
        if template is None:
            return None
        return render_template(template, make_resolver(view)) if "${{" in template else template

    def _defaults(self) -> Defaults:
        return self.env.definition.defaults

    def run_unit(
        self,
        step: AgentStep,
        *,
        item_key: str = "",
        item: Any = None,
        loop_count: int = 0,
        var: str | None = None,
        untrusted: bool = False,
    ) -> UnitResult:
        env = self.env
        view = env.context.snapshot({var: item} if var else None)
        defaults, options = self._defaults(), env.options
        try:
            prompt = render_prompt(step.prompt, view, untrusted_roots=(var,) if var and untrusted else ())
            agent = self._field(step.agent, view) or ""
            provider = self._field(step.provider, view) or defaults.provider or options.default_provider
            model = self._field(step.model, view) or defaults.model or options.default_model
            effort = self._field(step.effort, view) or defaults.effort or options.default_effort
        except (RenderError, ValueError) as exc:
            return UnitResult("failed", error_code="E_RENDER", error=str(exc)[:200])
        budget_hash = (
            {"tokens": step.budget.max_tokens, "cost": step.budget.max_cost_usd, "minutes": step.budget.max_minutes}
            if step.budget
            else None
        )
        input_hash = step_input_hash(
            prompt=prompt,
            agent=agent,
            agent_digest=options.agent_digests.get(agent),
            provider=provider,
            model=model,
            effort=effort,
            item=item,
            output_schema=step.output_schema,
            isolation=step.isolation,
            budget=budget_hash,
        )
        worktree = step.isolation == "worktree"
        decision = env.planner.decide_agent(step.id, item_key, loop_count, input_hash, worktree=worktree)
        if decision.action == "conflict":
            return UnitResult(
                "aborted",
                error_code=RESUME_CONFLICT,
                error=f"a etapa {step.id} já integrou arquivos ao projeto e seria refeita com outras entradas",
                abort=ABORT_CONFLICT,
            )
        if decision.action == "reuse":
            env.bus.emit(
                ev.step_reused(
                    run_id=env.run_id, step_id=step.id, item_key=item_key, loop_count=loop_count,
                    from_attempt=decision.record.attempt, input_hash=input_hash,
                )
            )
            return UnitResult("succeeded", output=decision.value, reused=True, worktree=decision.worktree)
        if decision.discard_agent_id is not None:
            try:
                env.launcher.discard_worktree(AgentHandle(decision.discard_agent_id, step.id, item_key))
            except Exception:  # noqa: BLE001
                logger.warning("could not discard the worktree of %s", decision.discard_agent_id, exc_info=True)
        env.mark_dirty()
        return self._attempts(step, item_key, item, loop_count, prompt, agent, provider, model, effort, input_hash)

    # --------------------------------------------------------------- attempts
    def _attempts(
        self,
        step: AgentStep,
        item_key: str,
        item: Any,
        loop_count: int,
        prompt: str,
        agent: str,
        provider: str | None,
        model: str | None,
        effort: str | None,
        input_hash: str,
    ) -> UnitResult:
        env = self.env
        policy = effective_policy(step.retries, self._defaults().retries)
        minutes = step.stall_minutes or self._defaults().stall_minutes or DEFAULT_STALL_MINUTES
        retries_used = 0
        replan: str | None = None
        attempts = 0
        while True:
            reason = env.should_start()
            if reason:
                return UnitResult("aborted", abort=reason, attempts=attempts)
            if not env.reserve_agent():
                return UnitResult(
                    "aborted", abort=ABORT_AGENT_CAP, error_code="AGENT_CAP",
                    error=f"limite de {env.options.max_agents} agentes por execução atingido", attempts=attempts,
                )
            row = env.store.start_step(
                run_id=env.run_id, step_id=step.id, input_hash=input_hash, item_key=item_key, loop_count=loop_count
            )
            attempts += 1
            started = env.clock.monotonic()
            env.bus.emit(
                ev.step_started(
                    run_id=env.run_id, step_id=step.id, item_key=item_key, attempt=row.attempt, agent=agent,
                    provider=provider, model=model, effort=effort, loop_count=loop_count,
                )
            )
            call = AgentCall(
                step_id=step.id, item_key=item_key, attempt=row.attempt, agent=agent, prompt=prompt, provider=provider,
                model=model, effort=effort, isolation=step.isolation, output_schema=step.output_schema,
                budget=env.budget.agent_budget(step.budget), pipeline_run_id=env.run_id, replan_note=replan,
                loop_count=loop_count, item=item,
            )
            waited = self._launch_and_wait(step, call, minutes)
            result, stalled, abort = waited.result, waited.stalled, waited.abort
            value: Any = None
            errors: list[str] = []
            if result is not None and result.status == "succeeded":
                value, errors = self._validated(step, result)
            state, code, message = self._classify(result, stalled, abort, errors, env)
            tokens_in = result.input_tokens if result is not None else 0
            tokens_out = result.output_tokens if result is not None else 0
            cost = result.cost_usd if result is not None and result.cost_known else None
            wt = result.worktree if result is not None else None
            stored_state = {"succeeded": "succeeded", "failed": "failed", "stalled": "stalled"}.get(state, "interrupted")
            done = env.store.finish_step(
                env.run_id, step.id, item_key, row.attempt, state=stored_state,
                output=wrap_envelope(value, wt) if (state == "succeeded" or wt) else None,
                error=message, input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
                agent_id=waited.agent_id,
            )
            if tokens_in or tokens_out or cost is not None:
                env.store.add_usage(env.run_id, tokens=tokens_in + tokens_out, cost_usd=cost)
            env.bus.emit(
                ev.step_finished(
                    run_id=env.run_id, step_id=step.id, item_key=item_key, state=stored_state if state != "aborted" else "interrupted",
                    input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
                    cost_known=bool(result and result.cost_known), duration_ms=int((env.clock.monotonic() - started) * 1000),
                    output_ref=_output_ref(done.output), error_code=code,
                ),
                agent_id=done.agent_id,
            )
            if state == "succeeded":
                return UnitResult("succeeded", output=value, worktree=wt, attempts=attempts)
            if state == "aborted":
                return UnitResult("aborted", error_code=code, error=message, abort=abort or env.abort_reason(), attempts=attempts)
            cause = CAUSE_SCHEMA_INVALID if errors else (cause_of(_as_result(result, stalled)))
            fingerprint = hash_text(f"agent|{step.id}|{code}|{cause}")
            if env.abort_reason() or not is_retryable(policy, cause, code, retries_used):
                return UnitResult(
                    "failed", error_code=code, error=message, fingerprint=fingerprint, worktree=wt, attempts=attempts
                )
            retries_used += 1
            if stalled:
                replan = waited.replan
            wait = wait_before_retry(
                env.clock, env.stop, retries_used,
                base=env.options.backoff_base_seconds, cap=env.options.backoff_cap_seconds,
            )
            env.bus.emit(
                ev.step_retrying(
                    run_id=env.run_id, step_id=step.id, item_key=item_key, attempt=row.attempt,
                    next_attempt=row.attempt + 1, reason=cause or "failed", delay_ms=int(wait.delay * 1000),
                    error_code=code,
                )
            )
            if wait.interrupted or env.abort_reason():
                return UnitResult("aborted", abort=env.abort_reason() or ABORT_CANCELLED, attempts=attempts)

    def _launch_and_wait(self, step: AgentStep, call: AgentCall, minutes: float) -> _Waited:
        env = self.env
        handle = env.launcher.launch(call)
        replan: str | None = None
        if not env.inflight.add(handle):
            env.inflight.cancel_one(handle, env.abort_reason() or ABORT_CANCELLED)
        meter = _Meter(env)
        watch = StallWatch(minutes, env.clock.now())
        stalled = False
        abort: str | None = None
        grace = 0
        result: AgentResult | None = None
        try:
            while True:
                result = env.launcher.wait(handle, env.options.poll_seconds)
                if result is not None:
                    break
                if abort is not None or stalled:
                    grace += 1
                    if grace > GRACE_POLLS:
                        break
                    continue
                abort = env.abort_reason()
                if abort:
                    env.inflight.cancel_one(handle, abort)
                    continue
                progress = env.launcher.progress(handle)
                if not env.launcher_live():
                    meter.sync(progress.input_tokens + progress.output_tokens, progress.cost_usd)
                watch.observe(progress)
                if env.abort_reason():
                    continue
                if watch.stalled(env.clock.now()):
                    late = env.launcher.wait(handle, 0.0)
                    if late is not None:
                        result = late
                        break
                    env.bus.emit(
                        ev.step_stalled(
                            run_id=env.run_id, step_id=step.id, item_key=call.item_key, agent_id=handle.agent_id,
                            idle_seconds=int(watch.idle_seconds(env.clock.now())), action="cancelled",
                        ),
                        agent_id=handle.agent_id,
                    )
                    stalled = True
                    replan = watch.replan_note()
                    env.inflight.cancel_one(handle, "stalled")
        finally:
            env.inflight.remove(handle)
        if result is not None and not env.launcher_live():
            meter.finish(result)
        return _Waited(result, stalled, abort, handle.agent_id, replan)

    # ---------------------------------------------------------------- helpers
    def _validated(self, step: AgentStep, result: AgentResult) -> tuple[Any, list[str]]:
        if step.output_schema is None:
            return result.text, []
        value = result.structured_output
        if value is None:
            try:
                value = extract_json(result.text)
            except ExtractError as exc:
                return None, [exc.message]
        errors = validate_instance(step.output_schema, value)
        return (None, errors) if errors else (value, [])

    def _classify(
        self, result: AgentResult | None, stalled: bool, abort: str | None, errors: list[str], env: RunEnv
    ) -> tuple[str, str | None, str | None]:
        """``(state, error_code, message)`` with state in succeeded | failed | stalled | aborted."""
        if result is not None and result.status == "succeeded" and not errors:
            return "succeeded", None, None
        if errors:
            return "failed", SCHEMA_INVALID_CODE, ("saída fora do esquema: " + "; ".join(errors[:3]))[:500]
        if stalled:
            return "stalled", "STEP_STALLED", "o agente ficou sem atividade além do limite e foi cancelado"
        aborted = env.abort_reason()
        if aborted and (result is None or result.status in {"cancelled", "budget_exceeded"}):
            return "aborted", aborted.upper(), aborted
        if result is None:
            return "failed", "NO_RESULT", "o agente não devolveu resultado depois de cancelado"
        code = result.error_code or ("SUBAGENT_BUDGET_EXCEEDED" if result.status == "budget_exceeded" else None)
        return "failed", code or "AGENT_FAILED", (result.error_message or f"o agente terminou com status {result.status}")[:500]


def _as_result(result: AgentResult | None, stalled: bool) -> AgentResult:
    if stalled:
        return AgentResult(status="stalled")
    return result if result is not None else AgentResult(status="failed")


def _output_ref(output: Any) -> str | None:
    if isinstance(output, dict) and isinstance(output.get("ref"), str):
        return output["ref"]
    return None


@dataclass(slots=True)
class MapResult:
    outputs: list[Any]
    units: list[UnitResult | None]
    items: list[Any]

    @property
    def ok(self) -> bool:
        return all(unit is not None and unit.ok for unit in self.units)

    def first_abort(self) -> str | None:
        for unit in self.units:
            if unit is not None and unit.state == "aborted" and unit.abort:
                return unit.abort
        return None

    def failures(self) -> list[UnitResult]:
        return [unit for unit in self.units if unit is not None and unit.state == "failed"]

    @property
    def incomplete(self) -> bool:
        return any(unit is None for unit in self.units)


class MapStepRunner:
    def __init__(self, env: RunEnv, runner: AgentStepRunner) -> None:
        self.env = env
        self.runner = runner

    def run(self, step: AgentStep, loop_count: int) -> MapResult | UnitResult:
        assert step.map is not None
        env = self.env
        view = env.context.snapshot()
        try:
            items = resolve_over(step.map.over, step.map.literal, view, 50)
        except RenderError as exc:
            return UnitResult("failed", error_code=getattr(exc, "code", "E_MAP_RANGE"), error=str(exc))
        untrusted = over_is_untrusted(step.map.over, step.map.literal)
        units: list[UnitResult | None] = [None] * len(items)
        halt = threading.Event()

        def work(index: int) -> None:
            if halt.is_set() or env.should_start():
                return
            unit = self.runner.run_unit(
                step, item_key=item_key_for(index, items[index]), item=items[index], loop_count=loop_count,
                var=step.map.var, untrusted=untrusted,  # type: ignore[union-attr]
            )
            units[index] = unit
            if unit.state != "succeeded" and (step.map.fail_fast or unit.state == "aborted"):  # type: ignore[union-attr]
                halt.set()

        with ThreadPoolExecutor(max_workers=max(1, step.map.max_parallel), thread_name_prefix=f"map-{step.id}") as pool:
            futures = [pool.submit(work, index) for index in range(len(items))]
            for future in futures:
                future.result()  # re-raises a crash (BaseException) from a worker
        outputs = [unit.output if unit is not None and unit.ok else None for unit in units]
        return MapResult(outputs, units, items)


__all__ = [
    "ABORT_AGENT_CAP",
    "ABORT_BUDGET",
    "ABORT_CANCELLED",
    "ABORT_CONFLICT",
    "ABORT_PAUSED",
    "AgentStepRunner",
    "EventBus",
    "InFlight",
    "MapResult",
    "MapStepRunner",
    "RunEnv",
    "UnitResult",
    "item_key_for",
]
