"""``PipelineDriver``: one ``turn_kind="pipeline"`` turn executes (or resumes) one journal run (WS-C7, decision D1).

The driver is pure with respect to Orin's runtime: the host (``session_adapter``) hands it the ports of the
engine, the permission mode of the turn and a ``cancelled`` probe. It checks that the turn may run the run
(owner, state, plan mode, definition re-validated against the live system), resumes from the journal when the run
already started (a turn that the worker re-queued after a crash resumes the same run: AC3 with no button), runs
the engine while a watcher turns the turn's cancellation into ``engine.cancel()``, and writes the summary the
conversation keeps (a markdown table, no step output). It never raises a permission mode: ``plan_mode`` refuses to
start and ``ask_permissions`` only adds the engine's ``pipeline.degraded`` warning.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentos.agentic.usage_budget import BudgetLimits

from .engine import PipelineEngine, RunOutcome
from .model import AgentStep, PipelineDefinition
from .ports import (
    AgentLauncher,
    ChangeScopePort,
    Clock,
    EngineOptions,
    EventSink,
    GateRunner,
    QuotaPort,
    VerifierPort,
)
from .resume import DefinitionChanged, ResumeRefused
from .settings import cap_budget_to_ceiling
from .store import PipelineStore, RunRecord
from .telemetry import UsageRow, run_summary
from .validator import ValidationContext, validate_context, validate_structure

logger = logging.getLogger("agentos.pipelines.driver")

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RESUMABLE = frozenset({"failed", "cancelled", "budget_exceeded"})

_STATE_LABELS = {
    "succeeded": "concluída", "failed": "falhou", "gate_failed": "gate reprovado", "skipped": "pulada",
    "interrupted": "interrompida", "stalled": "travada", "running": "em execução", "pending": "pendente",
    "cancelled": "cancelada",
}
_OUTCOME_LABELS = {
    "succeeded": "concluído", "failed": "falhou", "cancelled": "cancelado", "interrupted": "pausado",
    "budget_exceeded": "parou no orçamento",
}


# -------------------------------------------------------------------------- request
@dataclass(frozen=True, slots=True)
class PipelineRequest:
    """What a ``/pipeline`` turn asks for. The text of the turn is the whole contract (the worker has nothing else)."""

    run_id: str
    resume: bool = False
    rerun_gates: bool = False
    budget_override: bool = False


def parse_pipeline_request(task: str) -> PipelineRequest | None:
    """``/pipeline <run_id>`` or ``/pipeline resume <run_id> [--rerun-gates] [--budget-override]``; else ``None``."""
    parts = str(task or "").split()
    if len(parts) < 2 or parts[0] != "/pipeline":
        return None
    rest = parts[1:]
    resume = rest[0] == "resume"
    if resume:
        rest = rest[1:]
    if not rest or not _RUN_ID.match(rest[0]):
        return None
    rerun = override = False
    for flag in rest[1:]:
        if resume and flag == "--rerun-gates":
            rerun = True
        elif resume and flag == "--budget-override":
            override = True
        else:
            return None
    return PipelineRequest(rest[0], resume, rerun, override)


def pipeline_task(run_id: str, *, resume: bool = False, rerun_gates: bool = False, budget_override: bool = False) -> str:
    """The text a ``turn_kind="pipeline"`` turn carries (the inverse of :func:`parse_pipeline_request`)."""
    head = f"/pipeline resume {run_id}" if resume else f"/pipeline {run_id}"
    flags = (" --rerun-gates" if rerun_gates and resume else "") + (" --budget-override" if budget_override and resume else "")
    return head + flags


# ----------------------------------------------------------------------------- ports
@dataclass(slots=True)
class DriverPorts:
    """Everything the host supplies; only the three engine ports are required."""

    launcher: AgentLauncher
    gate_runner: GateRunner
    sink: EventSink
    verifier: VerifierPort | None = None
    change_scope: ChangeScopePort | None = None
    quota: QuotaPort | None = None
    clock: Clock | None = None
    permission_mode: str | None = None
    cancelled: Callable[[], bool] = field(default=lambda: False)
    validation_context: Callable[[], ValidationContext] | None = None
    agent_digest: Callable[[str], str | None] | None = None
    shell_dialect: str = "posix"
    default_provider: str | None = None
    default_model: str | None = None
    default_effort: str | None = None
    # Called with the engine once it exists; returns a function that undoes it (the session registers the run's
    # ``UsageBudget`` here, so children spend against it live).
    on_engine: Callable[[Any], Callable[[], None]] | None = None
    # WS-C10: the user's subagent budget ceiling a *project* pipeline is lowered to (G6), and the cross-process pause
    # request (the API runs in another process than the worker, so it cannot call ``pause_run`` directly).
    budget_ceiling: BudgetLimits | None = None
    pause_requested: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class DriverResult:
    state: str  # completed | completed_with_caveats | failed | cancelled
    error_code: str | None
    markdown: str
    outcome: RunOutcome | None = None


# ------------------------------------------------------------------------- registry
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: dict[str, PipelineEngine | None] = {}


def _reserve(run_id: str) -> bool:
    with _ACTIVE_LOCK:
        if run_id in _ACTIVE:
            return False
        _ACTIVE[run_id] = None
        return True


def _bind(run_id: str, engine: PipelineEngine) -> None:
    with _ACTIVE_LOCK:
        if run_id in _ACTIVE:
            _ACTIVE[run_id] = engine


def _release(run_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.pop(run_id, None)


PAUSE_REQUESTED = "pause_requested"  # ``pipeline_runs.stop_reason`` of a ``running`` run a process other than the worker asked to pause


def pause_flag_set(store: PipelineStore, run_id: str) -> bool:
    """The API (another process) asked this running run to drain: the driver's watcher polls this (WS-C10)."""
    run = store.get_run(run_id)
    return run is not None and run.state == "running" and run.stop_reason == PAUSE_REQUESTED


def active_run_ids() -> tuple[str, ...]:
    with _ACTIVE_LOCK:
        return tuple(sorted(_ACTIVE))


def pause_run(run_id: str) -> bool:
    """Drain a run executing in this process (the API's pause button); ``False`` when it is not live here."""
    with _ACTIVE_LOCK:
        engine = _ACTIVE.get(run_id)
    if engine is None:
        return False
    engine.pause()
    return True


def cancel_run(run_id: str) -> bool:
    with _ACTIVE_LOCK:
        engine = _ACTIVE.get(run_id)
    if engine is None:
        return False
    engine.cancel()
    return True


# ------------------------------------------------------------------------ summary
def _money(cost: float | None, known: bool) -> str:
    if cost is None:
        return "desconhecido"
    text = f"{cost:.4f}".replace(".", ",")
    return text if known else f"{text}+"


def _states_text(counts: Mapping[str, int]) -> str:
    total = sum(counts.values())
    if total <= 1:
        return _STATE_LABELS.get(next(iter(counts), ""), "pendente")
    return ", ".join(f"{count} {_STATE_LABELS.get(state, state)}" for state, count in sorted(counts.items()))


def _row_line(row: UsageRow) -> str:
    states = _states_text(row.latest_states) if row.latest_states else "pendente"
    return f"| {row.key} | {states} | {row.total_tokens} | {_money(row.cost_usd, row.cost_known)} |"


def render_summary(store: PipelineStore, run_id: str, headline: str, *, notes: tuple[str, ...] = (), caveats: tuple[str, ...] = ()) -> str:
    """The turn's answer: a headline, the step table with tokens and cost, the artifacts and the caveats."""
    lines = [headline, ""]
    try:
        summary = run_summary(store, run_id)
    except LookupError:
        return headline
    if summary.by_step:
        lines += ["| Etapa | Estado | Tokens | Custo (US$) |", "|---|---|---|---|", *(_row_line(row) for row in summary.by_step)]
        total = summary.total
        lines += ["", f"Total: {total.total_tokens} tokens, US$ {_money(total.cost_usd, total.cost_known)}."]
    refs = sorted({row.output["ref"] for row in store.steps_of(run_id) if isinstance(row.output, dict) and isinstance(row.output.get("ref"), str)})
    if refs:
        lines += ["", "Saídas completas gravadas em:", *(f"- `{ref}`" for ref in refs)]
    if caveats:
        lines += ["", "Ressalvas:", *(f"- {item}" for item in caveats)]
    if notes:
        lines += ["", *notes]
    return "\n".join(lines).strip() + "\n"


def _code_for(outcome: RunOutcome) -> str:
    return "PIPELINE_" + (outcome.stop_reason or "failed").upper()


# ------------------------------------------------------------------------- driver
class PipelineDriver:
    def __init__(
        self,
        store: PipelineStore,
        request: PipelineRequest,
        turn: Mapping[str, Any],
        ports: DriverPorts,
        *,
        engine_factory: Callable[..., Any] = PipelineEngine,
        poll_seconds: float = 0.5,
        engine_poll_seconds: float = 1.0,
        engine_monitor_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._request = request
        self._turn = turn
        self._ports = ports
        self._engine_factory = engine_factory
        self._poll = poll_seconds
        self._engine_poll = engine_poll_seconds
        self._engine_monitor = engine_monitor_seconds

    # -------------------------------------------------------------------- helpers
    def _refuse(self, code: str, text: str) -> DriverResult:
        return DriverResult("failed", code, text.rstrip() + "\n")

    def _resume_hint(self) -> str:
        return f"Para continuar de onde parou: `/pipeline resume {self._request.run_id}`."

    # ------------------------------------------------------------------------ run
    def run(self) -> DriverResult:
        request, store = self._request, self._store
        run = store.get_run(request.run_id)
        if run is None:
            return self._refuse("PIPELINE_RUN_NOT_FOUND", f"A execução `{request.run_id}` não existe.")
        if run.user_id != str(self._turn.get("user_id") or "") or run.conversation_id != str(self._turn.get("conversation_id") or ""):
            return self._refuse("PIPELINE_RUN_FORBIDDEN", f"A execução `{request.run_id}` pertence a outra conversa e não pode ser executada aqui.")
        if self._ports.permission_mode == "plan_mode":
            if run.state == "running":
                store.set_run_state(run.run_id, "interrupted", stop_reason="plan_mode")
            return self._refuse(
                "PIPELINE_PLAN_MODE",
                "Este pipeline não roda no modo de planejamento (somente leitura): nada foi executado. "
                f"Saia do modo de planejamento e use `/pipeline resume {run.run_id}`.",
            )
        refusal = self._state_refusal(run)
        if refusal is not None:
            return refusal
        if not _reserve(run.run_id):
            return self._refuse("PIPELINE_ALREADY_ACTIVE", f"A execução `{run.run_id}` já está rodando neste processo.")
        try:
            return self._execute(run)
        finally:
            _release(run.run_id)

    def _state_refusal(self, run: RunRecord) -> DriverResult | None:
        request = self._request
        turn_id = str(self._turn.get("turn_id") or "")
        if run.state == "succeeded" and turn_id and run.turn_id == turn_id:
            # The worker died after the run finished and before this very turn did: the requeued turn reports the result.
            headline = f"**Pipeline `{run.pipeline_id}` concluído** (execução `{run.run_id}`)"
            return DriverResult("completed", None, render_summary(self._store, run.run_id, headline))
        if run.state == "succeeded":
            return self._refuse("PIPELINE_ALREADY_DONE", f"A execução `{run.run_id}` já foi concluída; inicie uma nova execução se quiser repetir.")
        if run.state in _RESUMABLE and not request.resume:
            return self._refuse("PIPELINE_NOT_RUNNING", f"A execução `{run.run_id}` terminou como `{run.state}`. {self._resume_hint()}")
        if run.state == "budget_exceeded" and not request.budget_override:
            return self._refuse(
                "PIPELINE_BUDGET_OVERRIDE_REQUIRED",
                f"A execução `{run.run_id}` parou porque o orçamento acabou. Para continuar mesmo assim, use "
                f"`/pipeline resume {run.run_id} --budget-override` (o gasto recomeça do zero).",
            )
        return None

    def _definition(self, run: RunRecord) -> tuple[PipelineDefinition | None, DriverResult | None]:
        if not run.definition_snapshot.strip():
            return None, self._refuse("PIPELINE_SNAPSHOT_MISSING", f"A execução `{run.run_id}` não guardou a definição do pipeline e não pode ser retomada.")
        definition, report = validate_structure(run.definition_snapshot, run.source)
        errors = list(report.errors)
        if definition is not None and self._ports.validation_context is not None:
            errors += list(validate_context(definition, self._ports.validation_context()).errors)
        if definition is None or errors:
            if run.state in {"running", "interrupted"}:
                self._store.set_run_state(run.run_id, "failed", stop_reason="invalid")
            listed = "\n".join(f"- {item.format()}" for item in errors)
            return None, self._refuse("PIPELINE_INVALID", f"O pipeline `{run.pipeline_id}` não passou na validação:\n{listed}")
        if self._ports.budget_ceiling is not None:
            definition = cap_budget_to_ceiling(definition, self._ports.budget_ceiling)  # project files only go down (G6)
        return definition, None

    def _options(self, definition: PipelineDefinition) -> EngineOptions:
        ports, request = self._ports, self._request
        names = sorted({step.agent for step in definition.steps if isinstance(step, AgentStep) and step.agent and "${{" not in step.agent})
        digests: dict[str, str] = {}
        if ports.agent_digest is not None:
            for name in names:
                digest = ports.agent_digest(name)
                if digest:
                    digests[name] = digest
        return EngineOptions(
            poll_seconds=self._engine_poll, monitor_seconds=self._engine_monitor, rerun_gates=request.rerun_gates,
            budget_override=request.budget_override, default_provider=ports.default_provider, default_model=ports.default_model,
            default_effort=ports.default_effort, agent_digests=digests, shell_dialect=ports.shell_dialect,
            permission_mode=ports.permission_mode,
        )

    def _execute(self, run: RunRecord) -> DriverResult:
        store, request, ports = self._store, self._request, self._ports
        definition, refusal = self._definition(run)
        if refusal is not None:
            return refusal
        assert definition is not None
        resuming = request.resume or run.state == "interrupted" or (run.state == "running" and bool(store.steps_of(run.run_id)))
        if resuming and run.state == "running":
            store.interrupt_orphans(run.run_id)  # steps the dead worker left open: the engine redoes them
        store.bind_turn(run.run_id, str(self._turn.get("turn_id") or ""))
        release: Callable[[], None] | None = None
        done = threading.Event()
        watcher: threading.Thread | None = None
        try:
            engine = self._engine_factory(
                definition, run.run_id, store=store, launcher=ports.launcher, gate_runner=ports.gate_runner, sink=ports.sink,
                clock=ports.clock, inputs=run.inputs, verifier=ports.verifier, change_scope=ports.change_scope, quota=ports.quota,
                options=self._options(definition), resume=resuming,
            )
            _bind(run.run_id, engine)
            if ports.on_engine is not None:
                release = ports.on_engine(engine)
            watcher = threading.Thread(target=self._watch, args=(engine, done), name=f"pipeline-cancel-{run.run_id}", daemon=True)
            watcher.start()
            outcome = engine.execute()
        except DefinitionChanged:
            return self._refuse(
                "PIPELINE_DEFINITION_CHANGED",
                f"A definição do pipeline mudou desde o início da execução `{run.run_id}`; ela não foi retomada. "
                "Inicie uma nova execução ou retome com a definição original.",
            )
        except ResumeRefused as exc:
            return self._refuse(f"PIPELINE_{exc.code}", str(exc))
        except Exception as exc:  # noqa: BLE001 - a driver failure ends the turn, never the worker
            logger.exception("pipeline driver failed for run %s", run.run_id)
            if store.get_run(run.run_id) is not None and store.get_run(run.run_id).state == "running":  # type: ignore[union-attr]
                store.set_run_state(run.run_id, "failed", stop_reason="engine_error")
            return self._refuse("PIPELINE_ENGINE_ERROR", f"Erro interno ao executar o pipeline: {str(exc)[:200]}")
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=5.0)
            if release is not None:
                try:
                    release()
                except Exception:  # noqa: BLE001
                    logger.warning("could not release the run budget of %s", run.run_id, exc_info=True)
        return self._result(run, definition, outcome)

    def _watch(self, engine: Any, done: threading.Event) -> None:
        paused = False
        while not done.wait(self._poll):
            try:
                cancelled = bool(self._ports.cancelled())
            except Exception:  # noqa: BLE001 - a broken probe must not stop the run
                cancelled = False
            if cancelled:
                engine.cancel()
                return
            probe = self._ports.pause_requested
            if probe is not None and not paused:
                try:
                    paused = bool(probe())
                except Exception:  # noqa: BLE001 - same: a broken probe never stops the run
                    paused = False
                if paused:
                    engine.pause()

    # --------------------------------------------------------------------- result
    def _result(self, run: RunRecord, definition: PipelineDefinition, outcome: RunOutcome) -> DriverResult:
        label = _OUTCOME_LABELS.get(outcome.state, outcome.state)
        headline = f"**Pipeline `{definition.id}` {label}** (execução `{run.run_id}`)"
        caveats = outcome.caveats
        notes: list[str] = []
        if outcome.state == "succeeded":
            state, code = ("completed_with_caveats", None) if caveats else ("completed", None)
        elif outcome.state == "cancelled":
            state, code = "cancelled", "TURN_CANCELLED"
        elif outcome.state == "interrupted":
            state, code = "completed_with_caveats", "PIPELINE_PAUSED"
            notes.append(f"Execução pausada. {self._resume_hint()}")
        elif outcome.state == "budget_exceeded":
            state, code = "failed", "PIPELINE_BUDGET"
            notes.append(f"{outcome.message}. Para continuar mesmo assim: `/pipeline resume {run.run_id} --budget-override`.")
        else:
            state, code = "failed", _code_for(outcome)
            if outcome.failed_step:
                notes.append(f"Etapa que falhou: `{outcome.failed_step}`.")
            notes.append(f"{outcome.message}" if outcome.message else "")
            notes.append(self._resume_hint())
        if outcome.state == "succeeded" and not outcome.cost_known:
            notes.append("O custo de alguns agentes é desconhecido (assinatura sem preço por token); o total é um mínimo.")
        text = render_summary(self._store, run.run_id, headline, notes=tuple(item for item in notes if item), caveats=caveats)
        return DriverResult(state, code, text, outcome)


__all__ = [
    "DriverPorts",
    "DriverResult",
    "PAUSE_REQUESTED",
    "PipelineDriver",
    "PipelineRequest",
    "active_run_ids",
    "pause_flag_set",
    "cancel_run",
    "parse_pipeline_request",
    "pause_run",
    "pipeline_task",
    "render_summary",
]
