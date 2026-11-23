"""Typed activity events of the pipeline engine and of ``/goal`` (WS-C5, plan P4 and P5).

Every constructor returns a ``PipelineEvent`` (type, pt-BR summary, bounded payload) that
``AgentActivityEvent`` accepts without redaction surprises:

* the payload never uses the keys the activity contract redacts (``prompt``, ``output``, ``raw_args`` ...);
  big results travel as ``output_ref``;
* every payload fits ``MAX_PAYLOAD_BYTES`` and the activity log's own limits (64 entries, 512 characters per
  text, nesting of four levels); top-level lists are cut and report ``<key>_truncated`` and ``<key>_total``.

``TurnSessionEventSink`` is the concrete sink that records through ``TurnSession._record``. Recording is best
effort: a failure is logged and never reaches the engine.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Protocol

from agentos.agentic.events import AgentActivityEventType as T

logger = logging.getLogger("agentos.pipelines.events")

MAX_PAYLOAD_BYTES: Final = 8 * 1024
MAX_TEXT_CHARS: Final = 500
MAX_ITEM_TEXT_CHARS: Final = 200
MAX_SUMMARY_CHARS: Final = 400
_MAX_NODES: Final = 56  # the activity log refuses a payload with more than 64 keys and list items in total
_LIST_CAPS: Final = (20, 10, 5, 2, 1, 0)
_MAX_DEPTH: Final = 3
_FORBIDDEN_KEYS: Final = frozenset({"prompt", "output", "raw_args"})


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    type: T
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ bounding


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _plain(value: Any, *, depth: int, text_limit: int) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _clip(value, text_limit)
    if depth >= _MAX_DEPTH:
        return _clip(str(value), text_limit)
    if isinstance(value, Mapping):
        return {
            _clip(str(key), 64): _plain(item, depth=depth + 1, text_limit=MAX_ITEM_TEXT_CHARS)
            for key, item in value.items()
            if str(key) not in _FORBIDDEN_KEYS
        }
    if isinstance(value, Sequence):
        return [_plain(item, depth=depth + 1, text_limit=MAX_ITEM_TEXT_CHARS) for item in value]
    return _clip(str(value), text_limit)


def _nodes(value: Any) -> int:
    if isinstance(value, Mapping):
        return len(value) + sum(_nodes(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_nodes(item) for item in value)
    return 0


def _fits(payload: Mapping[str, Any]) -> bool:
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return size <= MAX_PAYLOAD_BYTES and _nodes(payload) <= _MAX_NODES


def bound_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` that respects the size and shape limits, cutting lists when needed."""
    forbidden = _FORBIDDEN_KEYS & {str(key) for key in payload}
    if forbidden:
        raise ValueError(f"pipeline payload uses redacted keys: {sorted(forbidden)}")
    plain = {str(key): _plain(value, depth=0, text_limit=MAX_TEXT_CHARS) for key, value in payload.items()}
    for cap in _LIST_CAPS:
        candidate: dict[str, Any] = {}
        for key, value in plain.items():
            if isinstance(value, list) and len(value) > cap:
                candidate[key] = value[:cap]
                candidate[f"{key}_truncated"] = True
                candidate[f"{key}_total"] = len(value)
            else:
                candidate[key] = value
        if _fits(candidate):
            return candidate
    return candidate  # pragma: no cover - only reachable with an absurd number of scalar keys


def _event(event_type: T, summary: str, **payload: Any) -> PipelineEvent:
    return PipelineEvent(event_type, _clip(summary, MAX_SUMMARY_CHARS), bound_payload(payload))


def _where(step_id: str, item_key: str = "") -> str:
    return f"{step_id} ({item_key})" if item_key else step_id


def _cost(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


# ----------------------------------------------------------------- pipeline


def pipeline_started(
    *,
    run_id: str,
    pipeline_id: str,
    source: str,
    definition_hash: str,
    steps_total: int,
    input_names: Sequence[str],
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STARTED,
        f"Pipeline {pipeline_id} iniciado ({steps_total} etapas)",
        run_id=run_id, pipeline_id=pipeline_id, source=source, definition_hash=definition_hash,
        steps_total=steps_total, input_names=list(input_names),
    )


def step_started(
    *,
    run_id: str,
    step_id: str,
    item_key: str,
    attempt: int,
    agent: str | None,
    provider: str | None,
    model: str | None,
    effort: str | None,
    loop_count: int,
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STEP_STARTED,
        f"Etapa {_where(step_id, item_key)} iniciada (tentativa {attempt})",
        run_id=run_id, step_id=step_id, item_key=item_key, attempt=attempt, agent=agent, provider=provider,
        model=model, effort=effort, loop_count=loop_count,
    )


def step_finished(
    *,
    run_id: str,
    step_id: str,
    item_key: str,
    state: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal | float | None,
    cost_known: bool,
    duration_ms: int,
    output_ref: str | None,
    error_code: str | None,
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STEP_FINISHED,
        f"Etapa {_where(step_id, item_key)} terminou: {state}",
        run_id=run_id, step_id=step_id, item_key=item_key, state=state, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=_cost(cost_usd), cost_known=cost_known, duration_ms=duration_ms,
        output_ref=output_ref, error_code=error_code,
    )


def step_reused(
    *, run_id: str, step_id: str, item_key: str, loop_count: int, from_attempt: int, input_hash: str
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STEP_REUSED,
        f"Etapa {_where(step_id, item_key)} reaproveitada do diário (tentativa {from_attempt})",
        run_id=run_id, step_id=step_id, item_key=item_key, loop_count=loop_count, from_attempt=from_attempt,
        input_hash=input_hash,
    )


def step_retrying(
    *,
    run_id: str,
    step_id: str,
    item_key: str,
    attempt: int,
    next_attempt: int,
    reason: str,
    delay_ms: int,
    error_code: str | None,
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STEP_RETRYING,
        f"Etapa {_where(step_id, item_key)}: nova tentativa em {delay_ms / 1000:g} s ({reason})",
        run_id=run_id, step_id=step_id, item_key=item_key, attempt=attempt, next_attempt=next_attempt,
        reason=reason, delay_ms=delay_ms, error_code=error_code,
    )


def gate_evaluated(
    *,
    run_id: str,
    step_id: str,
    item_key: str,
    gate_kind: str,
    passed: bool,
    exit_code: int | None,
    verdict: str | None,
    findings_count: int,
    findings: Sequence[Mapping[str, Any]] = (),
) -> PipelineEvent:
    """``findings`` carries compact rows (id, severity, claim); the full report lives in the journal."""
    compact = [
        {
            "id": str(item.get("id", "")),
            "severity": str(item.get("severity", "")),
            "claim": str(item.get("claim", "")),
        }
        for item in findings
    ]
    return _event(
        T.PIPELINE_GATE_EVALUATED,
        f"Gate {gate_kind} da etapa {_where(step_id, item_key)}: {'aprovado' if passed else 'reprovado'}",
        run_id=run_id, step_id=step_id, item_key=item_key, gate_kind=gate_kind, passed=passed,
        exit_code=exit_code, verdict=verdict, findings_count=findings_count, findings=compact,
    )


def loop_iteration(*, run_id: str, step_id: str, loop_count: int, max_loops: int, reason: str) -> PipelineEvent:
    return _event(
        T.PIPELINE_LOOP,
        f"Etapa {step_id}: volta {loop_count} de {max_loops} ({reason})",
        run_id=run_id, step_id=step_id, loop_count=loop_count, max_loops=max_loops, reason=reason,
    )


def step_stalled(
    *, run_id: str, step_id: str, item_key: str, agent_id: str | None, idle_seconds: int, action: str
) -> PipelineEvent:
    return _event(
        T.PIPELINE_STALLED,
        f"Etapa {_where(step_id, item_key)} sem progresso há {idle_seconds // 60} min ({action})",
        run_id=run_id, step_id=step_id, item_key=item_key, agent_id=agent_id, idle_seconds=idle_seconds,
        action=action,
    )


def degraded(*, run_id: str, step_id: str | None, reason: str, detail: str, effect: str) -> PipelineEvent:
    return _event(
        T.PIPELINE_DEGRADED,
        f"Pipeline degradado: {reason}",
        run_id=run_id, step_id=step_id, reason=reason, detail=detail, effect=effect,
    )


def budget_exceeded(*, run_id: str, scope: str, limit_kind: str, limit: float, used: float) -> PipelineEvent:
    return _event(
        T.PIPELINE_BUDGET_EXCEEDED,
        f"Orçamento excedido ({scope}): {limit_kind}",
        run_id=run_id, scope=scope, limit_kind=limit_kind, limit=limit, used=used,
    )


def pipeline_finished(
    *,
    run_id: str,
    state: str,
    stop_reason: str | None,
    used_tokens: int,
    used_cost_usd: Decimal | float | None,
    cost_known: bool,
    duration_ms: int,
    steps_succeeded: int,
    steps_failed: int,
) -> PipelineEvent:
    return _event(
        T.PIPELINE_FINISHED,
        f"Pipeline finalizado: {state}",
        run_id=run_id, state=state, stop_reason=stop_reason, used_tokens=used_tokens,
        used_cost_usd=_cost(used_cost_usd), cost_known=cost_known, duration_ms=duration_ms,
        steps_succeeded=steps_succeeded, steps_failed=steps_failed,
    )


def pipeline_resumed(
    *, run_id: str, reused_steps: int, pending_steps: int, definition_changed: bool
) -> PipelineEvent:
    return _event(
        T.PIPELINE_RESUMED,
        f"Pipeline retomado: {reused_steps} etapas reaproveitadas, {pending_steps} pendentes",
        run_id=run_id, reused_steps=reused_steps, pending_steps=pending_steps,
        definition_changed=definition_changed,
    )


# --------------------------------------------------------------------- goal


def goal_started(*, run_id: str, condition: str, max_tokens: int | None, max_turns: int | None) -> PipelineEvent:
    return _event(
        T.GOAL_STARTED,
        "Meta iniciada",
        run_id=run_id, condition=condition, max_tokens=max_tokens, max_turns=max_turns,
    )


def goal_evaluated(
    *, run_id: str, turn_index: int, met: bool, reason: str, evaluator_model: str | None
) -> PipelineEvent:
    return _event(
        T.GOAL_EVALUATED,
        f"Meta avaliada no turno {turn_index}: {'cumprida' if met else 'não cumprida'}",
        run_id=run_id, turn_index=turn_index, met=met, reason=reason, evaluator_model=evaluator_model,
    )


def goal_continued(*, run_id: str, turn_index: int, used_tokens: int) -> PipelineEvent:
    return _event(
        T.GOAL_CONTINUED,
        f"Meta continuada (turno {turn_index})",
        run_id=run_id, turn_index=turn_index, used_tokens=used_tokens,
    )


def goal_finished(
    *, run_id: str, state: str, stop_reason: str | None, turns: int, used_tokens: int
) -> PipelineEvent:
    return _event(
        T.GOAL_FINISHED,
        f"Meta encerrada: {state}",
        run_id=run_id, state=state, stop_reason=stop_reason, turns=turns, used_tokens=used_tokens,
    )


def subagent_budget_exceeded(
    *,
    agent_id: str,
    parent_agent_id: str | None,
    run_id: str | None,
    limit_kind: str,
    limit: float,
    used: float,
) -> PipelineEvent:
    return _event(
        T.SUBAGENT_BUDGET_EXCEEDED,
        f"Subagente {agent_id} excedeu o orçamento ({limit_kind})",
        agent_id=agent_id, parent_agent_id=parent_agent_id, run_id=run_id, limit_kind=limit_kind, limit=limit,
        used=used,
    )


# --------------------------------------------------------------------- sink


class _Recorder(Protocol):
    def _record(
        self,
        event_type: T,
        summary: str,
        payload: dict[str, object] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None: ...


class TurnSessionEventSink:
    """Records ``PipelineEvent`` through ``TurnSession._record`` (the activity log of the running turn)."""

    def __init__(self, session: _Recorder) -> None:
        self._session = session

    def emit(
        self, event: PipelineEvent, *, agent_id: str | None = None, parent_agent_id: str | None = None
    ) -> None:
        try:
            self._session._record(
                event.type, event.summary, dict(event.payload), agent_id=agent_id, parent_agent_id=parent_agent_id
            )
        except Exception:  # noqa: BLE001 - recording is best effort and must not break a running pipeline
            logger.exception("could not record %s", getattr(event.type, "value", event.type))


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "PipelineEvent",
    "TurnSessionEventSink",
    "bound_payload",
    "budget_exceeded",
    "degraded",
    "gate_evaluated",
    "goal_continued",
    "goal_evaluated",
    "goal_finished",
    "goal_started",
    "loop_iteration",
    "pipeline_finished",
    "pipeline_resumed",
    "pipeline_started",
    "step_finished",
    "step_retrying",
    "step_reused",
    "step_stalled",
    "step_started",
    "subagent_budget_exceeded",
]
