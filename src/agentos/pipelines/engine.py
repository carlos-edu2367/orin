"""The pipeline engine: a program counter over the steps of a ``PipelineDefinition`` (WS-C6).

Pure Python. It talks to Orin only through the ports of ``ports.py``, never imports the session, the runtime or
the workers, and keeps every durable fact in the journal (``JournalStore``), so a crash can be resumed.

Control flow, per step visit: ``when`` (false -> ``skipped``), run (agent, ``map`` or gate), then on success
``next`` or the following step; on failure ``on_fail``: ``stop`` (default), ``continue`` (recorded as a caveat) or
``goto`` (at most ``max_loops`` times, and never when two failures in a row look the same: ``no_progress``).
Final run states: ``succeeded``, ``failed``, ``cancelled``, ``interrupted`` (paused) and ``budget_exceeded``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import events as ev
from .budgets import PipelineBudget
from .context import RunContext, StepResult
from .gates import GateEvaluator, GateResult, gate_input_hash_parts, summarize_failure
from .hashing import canonical_json, hash_text
from .model import AgentStep, GateStep, PipelineDefinition, Step
from .ports import (
    NON_RETRYABLE_CODES,
    AgentLauncher,
    ChangeScopePort,
    Clock,
    EngineOptions,
    EventSink,
    GateRunner,
    JournalStore,
    QuotaPort,
    SystemClock,
    VerifierPort,
)
from .render import RenderError, evaluate_when
from .resume import DefinitionChanged, ResumePlanner, ResumeRefused, wrap_envelope
from .retries import CAUSE_GATE_FAILED, effective_policy, is_retryable, wait_before_retry
from .stall import LoopProgress
from .steps import (
    ABORT_AGENT_CAP,
    ABORT_BUDGET,
    ABORT_CANCELLED,
    ABORT_CONFLICT,
    ABORT_PAUSED,
    AgentStepRunner,
    EventBus,
    InFlight,
    MapResult,
    MapStepRunner,
    RunEnv,
    UnitResult,
)

logger = logging.getLogger("agentos.pipelines.engine")

MAX_STEP_EXECUTIONS = 500
MAX_AGENTS_PER_RUN = 200


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: str
    state: str  # succeeded | failed | cancelled | interrupted | budget_exceeded
    stop_reason: str | None
    message: str
    error_code: str | None = None
    failed_step: str | None = None
    caveats: tuple[str, ...] = ()
    steps_succeeded: int = 0
    steps_failed: int = 0
    executions: int = 0
    used_tokens: int = 0
    used_cost_usd: float | None = None
    cost_known: bool = True
    loops: int = 0


@dataclass(slots=True)
class _StepOutcome:
    ok: bool
    abort: str | None = None
    error_code: str | None = None
    message: str = ""
    fingerprint: str = ""
    output: Any = None


@dataclass(slots=True)
class _Stop:
    state: str
    stop_reason: str | None
    message: str
    error_code: str | None = None
    failed_step: str | None = None


class PipelineEngine:
    def __init__(
        self,
        definition: PipelineDefinition,
        run_id: str,
        *,
        store: JournalStore,
        launcher: AgentLauncher,
        gate_runner: GateRunner,
        sink: EventSink,
        clock: Clock | None = None,
        inputs: Mapping[str, Any] | None = None,
        verifier: VerifierPort | None = None,
        change_scope: ChangeScopePort | None = None,
        quota: QuotaPort | None = None,
        options: EngineOptions | None = None,
        resume: bool = False,
    ) -> None:
        self.definition = definition
        self.run_id = run_id
        self.resume = resume
        self.options = options or EngineOptions(max_agents=MAX_AGENTS_PER_RUN, max_step_executions=MAX_STEP_EXECUTIONS)
        self._clock = clock or SystemClock()
        self._quota = quota
        run = store.get_run(run_id)
        if run is None:
            raise LookupError(f"execução desconhecida: {run_id}")
        self._run = run
        seed = resume and not self.options.budget_override
        self.budget = PipelineBudget(
            definition.budget,
            self._clock,
            run_id,
            used_tokens=run.used_tokens if seed else 0,
            used_cost_usd=run.used_cost_usd if seed else None,
            used_seconds=float(run.used_seconds) if seed else 0.0,
        )
        bus = EventBus(sink)
        context = RunContext(inputs if inputs is not None else run.inputs)
        self.env = RunEnv(
            definition=definition,
            run_id=run_id,
            store=store,
            launcher=launcher,
            clock=self._clock,
            bus=bus,
            budget=self.budget,
            context=context,
            options=self.options,
            planner=ResumePlanner(store, run_id, enabled=resume, rerun_gates=self.options.rerun_gates),
            inflight=InFlight(launcher),
            gates=GateEvaluator(
                gate_runner, run_id=run_id, dialect=self.options.shell_dialect, verifier=verifier, change_scope=change_scope
            ),
        )
        self._agents = AgentStepRunner(self.env)
        self._maps = MapStepRunner(self.env, self._agents)
        self._monitor_stop = threading.Event()
        self._loop_progress = LoopProgress()
        self._executions = 0

    # --------------------------------------------------------------- controls
    @property
    def usage_budget(self):  # type: ignore[no-untyped-def]
        """The run's ``UsageBudget``: the session adapter registers it so children spend against it live."""
        return self.budget.usage

    @property
    def context(self) -> RunContext:
        return self.env.context

    def cancel(self) -> None:
        """Cancel every child in flight and end the run as ``cancelled``."""
        self.env.request_cancel()

    def pause(self) -> None:
        """Drain: start nothing new, let running children finish, end as ``interrupted`` (``paused``)."""
        self.env.drain.set()

    # ---------------------------------------------------------------- execute
    def execute(self) -> RunOutcome:
        env, store = self.env, self.env.store
        run = self._run
        if self.resume:
            if run.definition_hash and self.definition.definition_hash and run.definition_hash != self.definition.definition_hash:
                raise DefinitionChanged(run.definition_hash, self.definition.definition_hash)
            if run.state == "budget_exceeded" and not self.options.budget_override:
                raise ResumeRefused("BUDGET_OVERRIDE_REQUIRED", "a execução parou por orçamento; retome com budget_override")
            if run.state != "running":
                store.set_run_state(self.run_id, "running")
            prior = [row for row in store.steps_of(self.run_id) if row.agent_id]
            env.agents_launched = len(prior)
        started = self._clock.monotonic()
        monitor = threading.Thread(target=self._watch, name=f"pipeline-monitor-{self.run_id}", daemon=True)
        monitor.start()
        stop: _Stop
        try:
            self._announce()
            stop = self._loop()
        except Exception as exc:  # noqa: BLE001 - a bug must not leave the run "running" forever
            logger.exception("pipeline engine failed")
            stop = _Stop("failed", "engine_error", f"erro interno do motor: {str(exc)[:200]}", "ENGINE_ERROR")
            env.inflight.stop_all("engine_error")
        finally:
            self._monitor_stop.set()
            monitor.join(timeout=5.0)
            env.inflight.stop_all("engine_exit")
        return self._finish(stop, started)

    def _watch(self) -> None:
        env = self.env
        while not self._monitor_stop.wait(self.options.monitor_seconds):
            if env.stop.is_set():
                if env.cancel_requested.is_set():
                    env.inflight.stop_all(ABORT_CANCELLED)
                else:
                    env.trip_budget()
            elif env.budget.exceeded() is not None:
                env.trip_budget()

    def _announce(self) -> None:
        env, definition = self.env, self.definition
        if self.resume:
            rows = env.store.steps_of(self.run_id)
            done = {row.step_id for row in rows if row.state in {"succeeded", "gate_failed", "skipped"}}
            env.bus.emit(
                ev.pipeline_resumed(
                    run_id=self.run_id, reused_steps=len(done), pending_steps=max(0, len(definition.steps) - len(done)),
                    definition_changed=False,
                )
            )
        else:
            env.bus.emit(
                ev.pipeline_started(
                    run_id=self.run_id, pipeline_id=definition.id, source=definition.source,
                    definition_hash=definition.definition_hash, steps_total=len(definition.steps),
                    input_names=[spec.name for spec in definition.inputs],
                )
            )
        if self.budget.is_default:
            env.bus.emit(
                ev.degraded(
                    run_id=self.run_id, step_id=None, reason="budget_default",
                    detail="o pipeline não declara budget", effect="vale o teto padrão: 2.000.000 de tokens, US$ 15 e 120 minutos",
                )
            )
        if self.options.permission_mode == "ask_permissions":
            env.bus.emit(
                ev.degraded(
                    run_id=self.run_id, step_id=None, reason="ask_permissions",
                    detail="o modo de permissão da conversa é Pedir permissões",
                    effect="o pipeline pode pedir aprovações durante a execução; considere o modo Aceitar edições",
                )
            )

    # ------------------------------------------------------------------ loop
    def _loop(self) -> _Stop:
        env, steps = self.env, self.definition.steps
        index_of = {step.id: position for position, step in enumerate(steps)}
        pc = 0
        executions = 0
        while pc < len(steps):
            reason = env.should_start()
            if reason:
                return self._stop_for(reason)
            step = steps[pc]
            executions += 1
            self._executions = executions
            if executions > self.options.max_step_executions:
                return _Stop("failed", "step_cap", f"limite de {self.options.max_step_executions} execuções de etapa atingido", "STEP_CAP")
            loop_count = env.context.enter(step.id)
            if step.when is not None and not self._when_true(step, loop_count):
                pc += 1
                continue
            outcome = self._run_step(step, loop_count)
            if outcome.abort:
                return self._stop_for(outcome.abort, outcome)
            if outcome.ok:
                env.count_step(True)
                pc = index_of[step.next] if step.next else pc + 1
                continue
            env.count_step(False)
            decision = self._on_failure(step, outcome)
            if isinstance(decision, _Stop):
                return decision
            pc = index_of[decision] if decision is not None else pc + 1
        if self.budget.tripped is not None:  # R5: the last child went over in the call that finished the work
            env.context.add_caveat("o orçamento do pipeline foi ultrapassado na última etapa; o resultado foi mantido")
        return _Stop("succeeded", None, "pipeline concluído")

    def _when_true(self, step: Step, loop_count: int) -> bool:
        env = self.env
        try:
            truthy = evaluate_when(step.when or "", env.context.snapshot())
        except RenderError:
            truthy = False
        if truthy:
            return True
        env.context.set_result(step.id, StepResult(output=None, failed=False, findings=[], state="skipped"))
        if not env.planner.had_skip(step.id, loop_count):
            env.store.start_step(
                run_id=self.run_id, step_id=step.id, input_hash=hash_text(step.when or ""), loop_count=loop_count,
                state="skipped",
            )
            env.bus.emit(
                ev.step_finished(
                    run_id=self.run_id, step_id=step.id, item_key="", state="skipped", input_tokens=0, output_tokens=0,
                    cost_usd=None, cost_known=True, duration_ms=0, output_ref=None, error_code=None,
                )
            )
        return False

    def _on_failure(self, step: Step, outcome: _StepOutcome) -> _Stop | str | None:
        """``None`` = go on with the next step, a step id = jump there, ``_Stop`` = end the run."""
        env = self.env
        action = step.on_fail
        message = f"A etapa {step.id} falhou: {outcome.message}".strip()
        if action.action == "continue":
            env.context.add_caveat(f"a etapa {step.id} falhou e foi ignorada (on_fail: continue): {outcome.message}"[:300])
            return None
        if action.action != "goto" or action.goto is None or action.max_loops is None:
            return _Stop("failed", "step_failed", message[:300], outcome.error_code, step.id)
        if outcome.error_code in NON_RETRYABLE_CODES:
            return _Stop("failed", "step_failed", message[:300], outcome.error_code, step.id)
        if self._loop_progress.repeats(step.id, outcome.fingerprint):
            env.bus.emit(
                ev.step_stalled(
                    run_id=self.run_id, step_id=step.id, item_key="", agent_id=None, idle_seconds=0, action="no_progress"
                )
            )
            return _Stop(
                "failed", "no_progress",
                f"A etapa {step.id} falhou duas vezes seguidas do mesmo jeito; o laço parou. {outcome.message}"[:300],
                outcome.error_code, step.id,
            )
        if env.context.goto_count(step.id) >= action.max_loops:
            return _Stop(
                "failed", "loop_limit", f"A etapa {step.id} ainda falha depois de {action.max_loops} voltas. {outcome.message}"[:300],
                outcome.error_code, step.id,
            )
        count = env.context.bump_goto(step.id)
        env.bus.emit(
            ev.loop_iteration(
                run_id=self.run_id, step_id=step.id, loop_count=count, max_loops=action.max_loops, reason=outcome.error_code or "step_failed"
            )
        )
        return action.goto

    # ------------------------------------------------------------ step kinds
    def _run_step(self, step: Step, loop_count: int) -> _StepOutcome:
        if isinstance(step, GateStep):
            return self._run_gate(step, loop_count)
        assert isinstance(step, AgentStep)
        blocked = self._quota_check(step)
        if blocked:
            self.env.context.set_result(step.id, StepResult(output=None, failed=True, state="failed"))
            return _StepOutcome(False, error_code="CHILD_QUOTA_BLOCKED", message=blocked, fingerprint="quota")
        if step.map is None:
            unit = self._agents.run_unit(step, loop_count=loop_count)
            return self._from_unit(step, unit)
        mapped = self._maps.run(step, loop_count)
        if isinstance(mapped, UnitResult):
            self.env.context.set_result(step.id, StepResult(output=None, failed=True, state="failed"))
            return _StepOutcome(False, error_code=mapped.error_code, message=mapped.error or "", fingerprint=mapped.error_code or "")
        return self._from_map(step, mapped)

    def _quota_check(self, step: AgentStep) -> str | None:
        if self._quota is None:
            return None
        defaults, options = self.definition.defaults, self.options
        provider = step.provider or defaults.provider or options.default_provider
        if not provider or "${{" in provider:
            return None
        if step.map is None and provider == options.default_provider:
            return None
        assessment = self._quota.assess([provider])
        if provider in assessment.blocked:
            return f"a cota do provedor {provider} está acima do limite para etapas em segundo plano"
        if provider in assessment.warn:
            self.env.bus.emit(
                ev.degraded(
                    run_id=self.run_id, step_id=step.id, reason="quota_warn",
                    detail=f"a cota de {provider} está perto do limite", effect="a etapa segue; o custo pode esgotar a assinatura",
                )
            )
        return None

    def _from_unit(self, step: AgentStep, unit: UnitResult) -> _StepOutcome:
        env = self.env
        if unit.state == "aborted":
            return _StepOutcome(False, abort=unit.abort or ABORT_CANCELLED, error_code=unit.error_code, message=unit.error or "")
        ok = unit.state == "succeeded"
        env.context.set_result(
            step.id, StepResult(output=unit.output if ok else None, failed=not ok, findings=[], state="succeeded" if ok else "failed")
        )
        if ok:
            return _StepOutcome(True, output=unit.output)
        return _StepOutcome(False, error_code=unit.error_code, message=unit.error or "", fingerprint=unit.fingerprint)

    def _from_map(self, step: AgentStep, mapped: MapResult) -> _StepOutcome:
        env = self.env
        failures = mapped.failures()
        abort = mapped.first_abort()
        if failures:
            env.context.set_result(step.id, StepResult(output=mapped.outputs, failed=True, state="failed"))
            first = failures[0]
            codes = sorted({unit.error_code or "AGENT_FAILED" for unit in failures})
            return _StepOutcome(
                False, error_code=first.error_code,
                message=f"{len(failures)} de {len(mapped.units)} itens falharam ({', '.join(codes)})",
                fingerprint=hash_text("map|" + "|".join(codes) + f"|{len(failures)}"),
            )
        if abort or mapped.incomplete:
            return _StepOutcome(False, abort=abort or env.should_start() or ABORT_PAUSED)
        env.context.set_result(step.id, StepResult(output=mapped.outputs, failed=False, state="succeeded"))
        return _StepOutcome(True, output=mapped.outputs)

    # ------------------------------------------------------------------ gates
    def _run_gate(self, step: GateStep, loop_count: int) -> _StepOutcome:
        env = self.env
        parts = gate_input_hash_parts(step, env.context.inputs, self.options.shell_dialect)
        for item in step.gate.items:
            target = getattr(item, "step", None)
            if target:
                found = env.context.result(target)
                parts.append(["target", target, found.output if found else None])
        input_hash = hash_text(canonical_json(parts))
        decision = env.planner.decide_gate(step.id, loop_count, input_hash, dirty=env.dirty)
        if decision.action == "reuse":
            env.bus.emit(
                ev.step_reused(
                    run_id=self.run_id, step_id=step.id, item_key="", loop_count=loop_count,
                    from_attempt=decision.record.attempt, input_hash=input_hash,
                )
            )
            value = decision.value if isinstance(decision.value, dict) else {}
            passed = bool(value.get("passed", decision.record.state == "succeeded"))
            findings = list(value.get("findings") or [])
            env.context.set_result(
                step.id,
                StepResult(output={"passed": passed, "findings": findings}, failed=not passed, findings=findings,
                           state="succeeded" if passed else "gate_failed"),
            )
            return _StepOutcome(passed, message="" if passed else "gate reprovado", fingerprint=str(value.get("fingerprint", "")),
                                error_code=value.get("error_code"))
        policy = effective_policy(step.retries, self.definition.defaults.retries)
        retries_used = 0
        while True:
            reason = env.should_start()
            if reason:
                return _StepOutcome(False, abort=reason)
            row = env.store.start_step(run_id=self.run_id, step_id=step.id, input_hash=input_hash, loop_count=loop_count)
            started = self._clock.monotonic()
            env.bus.emit(
                ev.step_started(
                    run_id=self.run_id, step_id=step.id, item_key="", attempt=row.attempt, agent=None, provider=None,
                    model=None, effort=None, loop_count=loop_count,
                )
            )
            result: GateResult = env.gates.evaluate(step, env.context, env.stop)
            cost = result.cost_usd if result.cost_known else None
            tokens = result.input_tokens + result.output_tokens
            if tokens or cost is not None:
                env.charge(tokens, cost)
                env.store.add_usage(self.run_id, tokens=tokens, cost_usd=cost)
            aborted = env.abort_reason() if result.cancelled else None
            if result.cancelled and not aborted:
                aborted = ABORT_CANCELLED
            state = "interrupted" if aborted else ("succeeded" if result.passed else "gate_failed")
            message = aborted or (None if result.passed else summarize_failure(result))
            done = env.store.finish_step(
                self.run_id, step.id, "", row.attempt, state=state,
                output=None if aborted else wrap_envelope(result.as_output()), error=message,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens, cost_usd=cost,
            )
            if not aborted:
                env.bus.emit(
                    ev.gate_evaluated(
                        run_id=self.run_id, step_id=step.id, item_key="", gate_kind=result.gate_kind, passed=result.passed,
                        exit_code=result.exit_code, verdict=result.verdict, findings_count=len(result.findings),
                        findings=[item for item in result.findings if "id" in item],
                    )
                )
            env.bus.emit(
                ev.step_finished(
                    run_id=self.run_id, step_id=step.id, item_key="", state=state, input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens, cost_usd=cost, cost_known=result.cost_known,
                    duration_ms=int((self._clock.monotonic() - started) * 1000), output_ref=_ref(done.output),
                    error_code=result.error_code,
                )
            )
            if aborted:
                return _StepOutcome(False, abort=aborted)
            env.context.set_result(
                step.id,
                StepResult(output={"passed": result.passed, "findings": result.findings}, failed=not result.passed,
                           findings=list(result.findings), state="succeeded" if result.passed else "gate_failed"),
            )
            if result.passed:
                return _StepOutcome(True)
            if not is_retryable(policy, CAUSE_GATE_FAILED, result.error_code, retries_used):
                return _StepOutcome(False, error_code=result.error_code, message=message or "", fingerprint=result.fingerprint)
            retries_used += 1
            wait = wait_before_retry(
                self._clock, env.stop, retries_used, base=self.options.backoff_base_seconds, cap=self.options.backoff_cap_seconds
            )
            env.bus.emit(
                ev.step_retrying(
                    run_id=self.run_id, step_id=step.id, item_key="", attempt=row.attempt, next_attempt=row.attempt + 1,
                    reason=CAUSE_GATE_FAILED, delay_ms=int(wait.delay * 1000), error_code=result.error_code,
                )
            )
            if wait.interrupted or env.abort_reason():
                return _StepOutcome(False, abort=env.abort_reason() or ABORT_CANCELLED)

    # ------------------------------------------------------------- finishing
    def _stop_for(self, reason: str, outcome: _StepOutcome | None = None) -> _Stop:
        code = outcome.error_code if outcome else None
        text = outcome.message if outcome else ""
        if reason == ABORT_CANCELLED:
            return _Stop("cancelled", "cancelled", "execução cancelada", code)
        if reason == ABORT_BUDGET:
            return _Stop("budget_exceeded", "budget", "o orçamento do pipeline acabou", "PIPELINE_BUDGET_EXCEEDED")
        if reason == ABORT_PAUSED:
            return _Stop("interrupted", "paused", "execução pausada; retome para continuar")
        if reason == ABORT_AGENT_CAP:
            return _Stop("failed", "agent_cap", text or "limite de agentes por execução atingido", "AGENT_CAP")
        if reason == ABORT_CONFLICT:
            return _Stop("failed", "resume_conflict", text or "conflito ao retomar", "RESUME_CONFLICT")
        return _Stop("failed", "step_failed", text or reason, code)

    def _finish(self, stop: _Stop, started: float) -> RunOutcome:
        env, store = self.env, self.env.store
        seconds = int(max(0.0, self._clock.monotonic() - started))
        try:
            store.set_run_state(self.run_id, stop.state, stop_reason=stop.stop_reason)
            store.add_usage(self.run_id, seconds=seconds)
        except Exception:  # noqa: BLE001
            logger.exception("could not close the run journal")
        run = store.get_run(self.run_id)
        used_tokens = int(getattr(run, "used_tokens", 0) or 0)
        used_cost = getattr(run, "used_cost_usd", None)
        outcome = RunOutcome(
            run_id=self.run_id,
            state=stop.state,
            stop_reason=stop.stop_reason,
            message=stop.message,
            error_code=stop.error_code,
            failed_step=stop.failed_step,
            caveats=tuple(env.context.caveats),
            steps_succeeded=env.steps_succeeded,
            steps_failed=env.steps_failed,
            executions=getattr(self, "_executions", 0),
            used_tokens=used_tokens,
            used_cost_usd=None if used_cost is None else float(used_cost),
            cost_known=self.budget.cost_known,
            loops=env.context.total_loops(),
        )
        env.bus.emit(
            ev.pipeline_finished(
                run_id=self.run_id, state=outcome.state, stop_reason=outcome.stop_reason, used_tokens=used_tokens,
                used_cost_usd=outcome.used_cost_usd, cost_known=outcome.cost_known, duration_ms=seconds * 1000,
                steps_succeeded=outcome.steps_succeeded, steps_failed=outcome.steps_failed,
            )
        )
        return outcome


def _ref(output: Any) -> str | None:
    if isinstance(output, dict) and isinstance(output.get("ref"), str):
        return output["ref"]
    return None


__all__ = [
    "DefinitionChanged",
    "MAX_AGENTS_PER_RUN",
    "MAX_STEP_EXECUTIONS",
    "PipelineEngine",
    "ResumeRefused",
    "RunOutcome",
]
