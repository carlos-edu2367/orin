"""The only module of the pipeline package that knows ``TurnSession`` (WS-C7, decision D2).

It builds the real ports of the engine on top of the session of the ``/pipeline`` turn:

* ``SessionAgentLauncher``: a step's child is a background subagent (D3). The id goes into the journal right after the
  launch (``set_step_agent``), the run's ``UsageBudget`` is fed live by the session (``live_metering``), progress comes
  from the ``ProgressTracker`` through ``TurnSession.child_progress``, and ``discard_worktree`` finds the worktree by
  the id the journal kept.
* ``SessionGateRunner``: a gate command is one ordinary Bash tool call (D4), so hooks, permissions, checkpoints and
  the tool card apply. The exit code is read from the tool result (spike S4).
* the verifier, quota and change-scope ports live in ``adapters/``.

``run_pipeline_turn`` is what ``AgenticTurnRuntime._run_pipeline`` calls.
"""

from __future__ import annotations

import itertools
import logging
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentos.agentic.background_agents import BACKGROUND_AGENTS

from .adapters.catalog import SessionChangeScope, SessionQuota
from .adapters.verifier import SessionVerifier
from .driver import DriverPorts, DriverResult, PipelineDriver, PipelineRequest, pause_flag_set
from .events import TurnSessionEventSink
from .ports import AgentCall, AgentHandle, AgentProgress, AgentResult, CancelToken, CommandResult
from .settings import ceiling_from_settings
from .store import PipelineStore
from .validator import ValidationContext

logger = logging.getLogger("agentos.pipelines.session_adapter")

MAX_GATE_TIMEOUT_SECONDS = 600  # the Bash tool's own ceiling (``MAX_COMMAND_TIMEOUT_SECONDS``)
_EXIT_LINE = re.compile(r"(?m)^exit=(-?\d+)$")
_OUTPUT_TAIL_CHARS = 4000
_CANCELLED_CODES = frozenset({"INTERRUPTED_BY_USER", "STOPPED_BY_USER", "TOOL_CALL_ABANDONED", "TURN_CANCELLED"})
_DENIED_CODES = frozenset(
    {
        "CODE_PERMISSION_DENIED", "CODE_PLAN_MODE_READONLY", "CODE_PLAN_APPROVAL_REQUIRED", "CODE_DEPLOY_CONFIRMATION_REQUIRED",
        "CODE_PUSH_CONFIRMATION_REQUIRED", "HOOK_DENIED", "HOOK_STOPPED", "READ_ONLY_SUBAGENT",
    }
)
_RUNNING_AGENT_STATES = ("queued", "running", "stalled", "interrupted")


# ============================================================================ launcher
class SessionAgentLauncher:
    """``AgentLauncher`` over ``TurnSession.run_pipeline_agent`` and the background-agent registry."""

    live_metering = True  # the session charges the run's ``UsageBudget`` as each child spends (record_usage)

    def __init__(
        self,
        session: Any,
        store: PipelineStore,
        *,
        run_id: str,
        discard: Callable[[str], object] | None = None,
        orphan_agents: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._run_id = run_id
        self._discard = discard
        self._orphan_agents = orphan_agents
        self._conversation = str(session.turn.get("conversation_id") or "")
        self._lock = threading.Lock()
        self._failed: dict[str, AgentResult] = {}
        self._results: dict[str, AgentResult] = {}

    # ----------------------------------------------------------------- launch
    def launch(self, call: AgentCall) -> AgentHandle:
        options = self._options(call)
        task = call.prompt if not call.replan_note else f"{call.prompt}\n\nNote about the previous attempt: {call.replan_note}"
        try:
            outcome = self._session.run_pipeline_agent(call.agent, task, **options)
        except Exception as exc:  # noqa: BLE001 - a launch that raises is a failed step, not a dead run
            logger.exception("could not launch the agent of step %s", call.step_id)
            return self._failed_handle(call, "AGENT_START_FAILED", f"{type(exc).__name__}: {exc}"[:300])
        agent_id = str((outcome.payload or {}).get("agent_id") or "")
        if outcome.status != "succeeded" or not agent_id:
            return self._failed_handle(call, outcome.error_code or "AGENT_START_FAILED", str(outcome.content or outcome.summary)[:500])
        # Risk 1 (C6): the journal must know the child before anything can kill it, so a resume finds its worktree.
        try:
            self._store.set_step_agent(call.pipeline_run_id, call.step_id, call.item_key, call.attempt, agent_id)
        except Exception:  # noqa: BLE001 - the journal row also gets the id when the step finishes
            logger.warning("could not journal the agent of step %s", call.step_id, exc_info=True)
        # The engine reads this child's result itself: the end-of-turn barrier must not re-inject it as a message.
        BACKGROUND_AGENTS.mark_notified(self._conversation, agent_id)
        return AgentHandle(agent_id, call.step_id, call.item_key)

    def _options(self, call: AgentCall) -> dict[str, Any]:
        turn = self._session.turn
        provider, model = call.provider, call.model
        if (str(provider or "").lower() == str(turn.get("provider") or "").lower() or not provider) and (
            not model or model == str(turn.get("model_id") or "")
        ):
            provider = model = None  # the turn's own model: the child needs no routing
        options: dict[str, Any] = {
            "description": f"Etapa {call.step_id}" + (f" ({call.item_key})" if call.item_key else ""),
            "provider": provider, "model_id": model, "effort": call.effort,
            "isolation": call.isolation if call.isolation and call.isolation != "none" else None,
            "output_schema": dict(call.output_schema) if call.output_schema else None,
            "budget": dict(call.budget) if call.budget else None,
            "pipeline_run_id": call.pipeline_run_id,
        }
        return {key: value for key, value in options.items() if value is not None}

    def _failed_handle(self, call: AgentCall, code: str, message: str) -> AgentHandle:
        handle = AgentHandle(f"pipeline-unstarted:{uuid.uuid4().hex[:12]}", call.step_id, call.item_key)
        with self._lock:
            self._failed[handle.agent_id] = AgentResult(status="failed", error_code=code, error_message=message)
        return handle

    # ------------------------------------------------------------------- wait
    def wait(self, handle: AgentHandle, timeout: float) -> AgentResult | None:
        with self._lock:
            known = self._failed.get(handle.agent_id) or self._results.get(handle.agent_id)
        if known is not None:
            return known
        entry = BACKGROUND_AGENTS.get(self._conversation, handle.agent_id)
        if entry is None or entry.future is None:
            return AgentResult(status="failed", error_code="AGENT_NOT_FOUND", error_message="o agente não está registrado")
        try:
            outcome = entry.future.result(timeout=max(0.0, float(timeout)))
        except TimeoutError:
            return None
        except CancelledError:
            return self._remember(handle, AgentResult(status="cancelled"))
        except Exception as exc:  # noqa: BLE001 - a crashed child is a failed step
            return self._remember(handle, AgentResult(status="failed", error_code="AGENT_CRASHED", error_message=f"{type(exc).__name__}: {exc}"[:300]))
        return self._remember(handle, self._to_result(handle.agent_id, outcome, bool(entry.cancel.is_set())))

    def _remember(self, handle: AgentHandle, result: AgentResult) -> AgentResult:
        with self._lock:
            self._results[handle.agent_id] = result
        return result

    def _to_result(self, agent_id: str, outcome: Any, cancelled: bool) -> AgentResult:
        payload = dict(outcome.payload or {})
        code = outcome.error_code
        if outcome.status == "succeeded":
            status = "succeeded"
        elif code == "SUBAGENT_BUDGET_EXCEEDED":
            status = "budget_exceeded"
        elif cancelled or code in _CANCELLED_CODES:
            status = "cancelled"
        else:
            status = "failed"
        spent = self.progress(AgentHandle(agent_id))
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        in_tokens = spent.input_tokens or int(usage.get("input_tokens") or 0)
        out_tokens = spent.output_tokens or int(usage.get("output_tokens") or 0)
        cost = spent.cost_usd  # None = no price for some call; a known 0.0 (free model) stays a zero
        worktree = payload.get("worktree") if isinstance(payload.get("worktree"), Mapping) else None
        return AgentResult(
            status=status,  # type: ignore[arg-type]
            text=str(outcome.content or ""),
            structured_output=payload.get("structured_output"),
            error_code=code if status != "succeeded" else None,
            error_message=(str(outcome.summary or "")[:300] if status != "succeeded" else None),
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cost_usd=cost,
            cost_known=cost is not None,
            worktree=dict(worktree) if worktree else None,
            schema_errors=tuple(str(item) for item in (payload.get("schema_errors") or ())),
        )

    # ----------------------------------------------------------------- cancel
    def cancel(self, handle: AgentHandle, reason: str) -> None:
        entry = BACKGROUND_AGENTS.get(self._conversation, handle.agent_id)
        if entry is not None:
            entry.cancel.set()  # the child and every agent it started watch this event

    # --------------------------------------------------------------- progress
    def progress(self, handle: AgentHandle) -> AgentProgress:
        """K11 from the session's ``ProgressTracker`` (subtree usage, last activity, current tool)."""
        if handle.agent_id in self._failed:
            return AgentProgress()
        try:
            payload = self._session.child_progress(handle.agent_id)
        except Exception:  # noqa: BLE001 - a probe that fails must not stall or kill the step
            return AgentProgress()
        return AgentProgress(
            last_activity_at=_parse_time(payload.get("last_activity_at")),
            tool_calls=int(payload.get("tool_calls") or 0),
            current_tool=str(payload["current_tool"]) if payload.get("current_tool") else None,
            input_tokens=int(payload.get("input_tokens") or 0),
            output_tokens=int(payload.get("output_tokens") or 0),
            cost_usd=_known_cost(payload),
        )

    # --------------------------------------------------------------- worktree
    def discard_worktree(self, handle: AgentHandle) -> None:
        """K19. The journal kept the id (``set_step_agent``); an empty id means the id never reached the journal,
        so the agents of this run that the journal does not know are the ones to clean."""
        if self._discard is None:
            return
        ids = [handle.agent_id] if handle.agent_id else (self._orphan_agents(self._run_id) if self._orphan_agents else [])
        for agent_id in ids:
            try:
                self._discard(agent_id)
            except Exception:  # noqa: BLE001
                logger.warning("could not discard the worktree of %s", agent_id, exc_info=True)


def _known_cost(payload: Mapping[str, Any]) -> float | None:
    """The tracker's cost, or ``None`` while it is unknown (a payload without the flag falls back to "non-zero")."""
    flag = payload.get("cost_known")
    known = bool(payload.get("cost_usd")) if flag is None else bool(flag)
    return float(payload["cost_usd"]) if known and payload.get("cost_usd") is not None else None


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


# ================================================================================ gate
class SessionGateRunner:
    """``GateRunner`` through the ordinary Bash tool path; ``run_tool`` is the runtime's ``_run_toolset``."""

    def __init__(self, run_tool: Callable[[list[dict[str, str]]], list[dict[str, object]]], tool_name: str = "run_command") -> None:
        self._run_tool = run_tool
        self._tool_name = tool_name
        self._counter = itertools.count(1)

    def run_command(self, command: str, timeout_seconds: int, cancel: CancelToken) -> CommandResult:
        if cancel.is_set():
            return CommandResult(None, cancelled=True)
        import json

        timeout = max(1, min(int(timeout_seconds or MAX_GATE_TIMEOUT_SECONDS), MAX_GATE_TIMEOUT_SECONDS))
        call = {
            "id": f"pipeline_gate_{next(self._counter)}",
            "name": self._tool_name,
            # ``auto_background`` off: a foreground command that runs past a minute must still hand back its exit code.
            "arguments": json.dumps(
                {"command": command, "timeout_seconds": timeout, "auto_background": False, "description": "Gate do pipeline"}
            ),
        }
        try:
            results = self._run_tool([call])
        except Exception as exc:  # noqa: BLE001 - the tool path failing is a failed gate, not a dead run
            logger.exception("gate command could not run")
            return CommandResult(None, f"{type(exc).__name__}: {exc}"[:500], error_code="GATE_TOOL_FAILED")
        return classify_gate_result(results[0] if results else {}, cancelled=cancel.is_set(), clamped=timeout < int(timeout_seconds or 0))


def classify_gate_result(result: Mapping[str, Any], *, cancelled: bool = False, clamped: bool = False) -> CommandResult:
    """Spike S4: the exit code is the ``exit=<n>`` line of the Bash result; its absence says how the command ended."""
    content = str(result.get("content") or "")
    tail = content[-_OUTPUT_TAIL_CHARS:]
    code = str(result.get("error_code") or "")
    lowered = content.lower()
    # The error code decides first: a refusal or a stop never ran the command, and the text it quotes (an ``exit=1``
    # line among it) must not become the exit code an ``expect: nonzero`` gate would accept.
    if code in _CANCELLED_CODES:
        return CommandResult(None, tail, cancelled=True, error_code=code)
    if code in _DENIED_CODES or (code == "TOOL_REFUSED" and "is blocked because" in lowered):
        return CommandResult(None, tail, denied=True, error_code=code)
    found = _EXIT_LINE.search(content)
    if found is not None:
        return CommandResult(int(found.group(1)), tail)
    if cancelled and code:
        return CommandResult(None, tail, cancelled=True, error_code=code)
    if "timed out after" in lowered:
        note = f"\n[o comando foi limitado a {MAX_GATE_TIMEOUT_SECONDS} s, o teto da ferramenta Bash]" if clamped else ""
        return CommandResult(None, tail + note, timed_out=True, error_code=code or None)
    return CommandResult(None, tail, error_code=code or "GATE_TOOL_FAILED")


# ================================================================================ host
def engine_of(session: Any) -> Any:
    for holder in (getattr(session, "chat_store", None), getattr(session, "store", None), getattr(session, "agents_store", None)):
        engine = getattr(holder, "_engine", None) or getattr(holder, "engine", None)
        if engine is not None:
            return engine
    return None


def pipeline_store_for(session: Any) -> PipelineStore | None:
    """The journal of this session's database (tests may set ``session.pipeline_store``)."""
    store = getattr(session, "pipeline_store", None)
    if store is not None:
        return store
    engine = engine_of(session)
    return PipelineStore(engine) if engine is not None else None


def _setting(session: Any, path: str, default: Any) -> Any:
    settings = getattr(session, "effective_settings", None)
    if settings is None:
        return default
    try:
        from agentos.settings.registry import resolve

        return resolve(settings, path).value
    except Exception:  # noqa: BLE001 - an unreadable setting means its default
        return default


def build_validation_context(session: Any) -> ValidationContext:
    """The live system as ``validate_context`` needs it, read again when the run starts."""
    from agentos.code_mode.agent_worktree import AgentWorktreeManager

    agents, favorites = session.pipeline_catalog()
    turn = session.turn
    models = {str(prov): frozenset(ids) for prov, ids in favorites.items() if ids}
    own_provider, own_model = str(turn.get("provider") or ""), str(turn.get("model_id") or "")
    if own_provider and own_model:
        models[own_provider] = frozenset({*models.get(own_provider, frozenset()), own_model})
    try:
        is_git = bool(AgentWorktreeManager(Path(session.workspace.root)).is_git_repo())
    except Exception:  # noqa: BLE001
        is_git = False

    def efforts(provider: str, model: str) -> tuple[str, ...] | None:
        from agentos.agentic.effort_levels import levels_for

        try:
            levels = tuple(levels_for(provider, model).levels)
        except Exception:  # noqa: BLE001
            return None
        return levels or None

    return ValidationContext(
        pipelines_enabled=bool(_setting(session, "pipelines.enabled", True)),
        known_agents=frozenset(agents),
        models=models or None,
        supported_efforts=efforts,
        worktree_enabled=bool(_setting(session, "orchestration.worktree.enabled", False)),
        is_git=is_git,
        verifier_available=True,  # the verifier port reports "no model" itself at run time (G19: it never skips silently)
        default_provider=own_provider or None,
    )


def make_worktree_discarder(session: Any) -> Callable[[str], object]:
    from agentos.code_mode.agent_worktree import AgentWorktreeManager
    from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore

    engine = engine_of(session)
    store = AgentWorktreeStore(engine) if engine is not None else None
    manager = AgentWorktreeManager(Path(session.workspace.root), store=store)
    return manager.discard_worktree


def _is_alive(conversation_id: str, agent_id: str, alive: set[str]) -> bool:
    entry = BACKGROUND_AGENTS.get(str(conversation_id or ""), str(agent_id))
    return entry is not None and entry.status in alive


def make_orphan_finder(session: Any, store: PipelineStore) -> Callable[[str], list[str]]:
    """Agents started for a run that the journal never learned about (the process died between launch and journal)."""
    from sqlalchemy import select

    from agentos.persistence.postgres.schema import agent_runs

    engine = engine_of(session)

    def find(run_id: str) -> list[str]:
        if engine is None:
            return []
        known = {row.agent_id for row in store.steps_of(run_id) if row.agent_id}
        with engine.connect() as connection:
            rows = connection.execute(
                select(agent_runs.c.agent_id, agent_runs.c.conversation_id).where(
                    agent_runs.c.pipeline_run_id == run_id, agent_runs.c.state.in_(_RUNNING_AGENT_STATES)
                )
            ).all()
        # A sibling launched a moment ago by a parallel ``map`` is registered in memory before the journal learns its
        # id: it is alive, so it is no orphan. An orphan is a row of a process that died.
        alive = {"running", "stalled", "queued"}
        return [
            agent_id for agent_id, conversation_id in rows
            if agent_id not in known and not _is_alive(conversation_id, agent_id, alive)
        ]

    return find


def build_ports(
    session: Any,
    store: PipelineStore,
    request: PipelineRequest,
    *,
    run_tool: Callable[[list[dict[str, str]]], list[dict[str, object]]],
    tool_name: str,
    cancelled: Callable[[], bool],
    changed_paths: Callable[[], Any] | None,
) -> DriverPorts:
    from agentos.host_shell import current_host_shell

    turn = session.turn
    try:
        discard = make_worktree_discarder(session)
        orphans = make_orphan_finder(session, store)
    except Exception:  # noqa: BLE001 - without a database there is no worktree to find
        discard = orphans = None
    agents, _ = session.pipeline_catalog()
    return DriverPorts(
        launcher=SessionAgentLauncher(session, store, run_id=request.run_id, discard=discard, orphan_agents=orphans),
        gate_runner=SessionGateRunner(run_tool, tool_name),
        sink=TurnSessionEventSink(session),
        verifier=SessionVerifier.for_session(session),
        change_scope=SessionChangeScope(changed_paths),
        quota=SessionQuota.for_session(session),
        permission_mode=str(getattr(session, "permission_mode", "") or "") or None,
        cancelled=cancelled,
        validation_context=lambda: build_validation_context(session),
        agent_digest=lambda name: agents.get(name) or None,
        shell_dialect=str(getattr(current_host_shell(), "dialect", "posix") or "posix"),
        default_provider=str(turn.get("provider") or "") or None,
        default_model=str(turn.get("model_id") or "") or None,
        on_engine=lambda engine: session.attach_pipeline_budget(engine.usage_budget),
        budget_ceiling=ceiling_from_settings(getattr(session, "effective_settings", None)),
        pause_requested=lambda: pause_flag_set(store, request.run_id),
    )


def run_pipeline_turn(
    session: Any,
    request: PipelineRequest,
    turn: Mapping[str, Any],
    *,
    run_tool: Callable[[list[dict[str, str]]], list[dict[str, object]]],
    tool_name: str = "run_command",
    cancelled: Callable[[], bool] = lambda: False,
    changed_paths: Callable[[], Any] | None = None,
) -> DriverResult:
    """What ``AgenticTurnRuntime._run_pipeline`` calls: build the ports of this session and drive the run."""
    store = pipeline_store_for(session)
    if store is None:
        return DriverResult("failed", "PIPELINE_STORE_UNAVAILABLE", "O diário de pipelines não está disponível neste ambiente.\n")
    ports = build_ports(session, store, request, run_tool=run_tool, tool_name=tool_name, cancelled=cancelled, changed_paths=changed_paths)
    return PipelineDriver(store, request, turn, ports).run()


__all__ = [
    "MAX_GATE_TIMEOUT_SECONDS",
    "SessionAgentLauncher",
    "SessionGateRunner",
    "build_ports",
    "build_validation_context",
    "classify_gate_result",
    "engine_of",
    "pipeline_store_for",
    "run_pipeline_turn",
]
