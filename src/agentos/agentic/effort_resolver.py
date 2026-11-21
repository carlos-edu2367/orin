"""Unified reasoning effort resolution (spec 3.4 rules 1..9) and failure escalation (E-RF21)."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
import re
from typing import Any

# Ensure Package E settings keys are registered before resolving EffectiveSettings.
from agentos.settings import keys_effort_quota as _keys_effort_quota  # noqa: F401
from agentos.settings.loader import EffectiveSettings
from agentos.settings.registry import REGISTRY, resolve as resolve_setting

from .effort_levels import EFFORT_ORDER, EFFORT_RANK, EffortAdjustment, ModelEffortInfo, clamp, levels_for
from .loop_settings import operator_value

_ULTRATHINK_RE = re.compile(r"\bultrathink\b", re.IGNORECASE)
_DEFAULT_BY_TYPE: dict[str, str] = {
    "explore": "low",
    "plan": "high",
    "verifier": "high",
    "general": "inherit",
    "fork": "inherit",
}


@dataclass(frozen=True, slots=True)
class EffortContext:
    provider: str
    model_id: str
    capabilities: Collection[str] = ()
    call_effort: str | None = None
    boost: bool = False
    message: str = ""
    turn_effort: str | None = None
    definition_effort: str | None = None
    role_effort: str | None = None
    parent_effort: str | None = None
    model_pref: str | None = None
    global_effort: str | None = None
    is_subagent: bool = False
    subagent_type: str | None = None
    is_verifier: bool = False
    is_plan_mode: bool = False
    settings: Any = None
    model_info: ModelEffortInfo | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEffort:
    level: str | None
    source: str
    adjustment: EffortAdjustment | None = None


@dataclass(frozen=True, slots=True)
class EscalationAdvice:
    failures: int
    escalated: bool
    next_effort: str | None = None
    propose_model_switch: bool = False


def _norm_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in EFFORT_RANK else None


def _get_nested(data: Mapping[str, Any], dotted: str) -> Any:
    if dotted in data:
        return data[dotted]
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _read_setting(settings: Any, path: str, default: Any = None) -> Any:
    if settings is None:
        return default
    if isinstance(settings, EffectiveSettings):
        if path in REGISTRY:
            return resolve_setting(settings, path).value
        val = operator_value(settings, path)
        return default if val is None else val
    if isinstance(settings, Mapping):
        val = _get_nested(settings, path)
        return default if val is None else val
    return default


def _legacy_global_effort(settings: Any) -> str | None:
    if settings is None:
        return None
    if isinstance(settings, EffectiveSettings):
        return _norm_level(operator_value(settings, "effort")) or _norm_level(
            _read_setting(settings, "effortLevel", None)
        )
    if isinstance(settings, Mapping):
        raw_effort = settings.get("effort")
        if isinstance(raw_effort, str):
            return _norm_level(raw_effort)
        return _norm_level(settings.get("effortLevel"))
    return None


def _step_up(base: str | None, info: ModelEffortInfo) -> str:
    ordered = sorted(
        (lvl for lvl in dict.fromkeys(info.levels) if lvl in EFFORT_RANK),
        key=lambda lvl: EFFORT_RANK[lvl],
    )
    ladder = ordered if ordered else list(EFFORT_ORDER)
    anchor = _norm_level(base) or _norm_level(info.default)
    if anchor is None:
        anchor = "medium" if "medium" in ladder else ladder[0]
    if anchor in ladder:
        idx = ladder.index(anchor)
        return ladder[min(idx + 1, len(ladder) - 1)]
    anchor_rank = EFFORT_RANK[anchor]
    higher = [lvl for lvl in ladder if EFFORT_RANK[lvl] > anchor_rank]
    return higher[0] if higher else ladder[-1]


def _resolve_main_base(
    ctx: EffortContext,
    *,
    plan_mode_effort: str | None,
    effort_by_model: Mapping[str, Any],
    global_setting: str | None,
) -> tuple[str | None, str]:
    # Rule 3: turn["effort"]
    turn_lvl = _norm_level(ctx.turn_effort)
    if turn_lvl is not None:
        return turn_lvl, "turn"

    # Rule 5: role (verifier / explicit role / planMode)
    role_lvl = _norm_level(ctx.role_effort)
    if role_lvl is not None:
        return role_lvl, "role"
    if ctx.is_plan_mode and plan_mode_effort is not None:
        return plan_mode_effort, "role"

    # Rule 7: effortByModel[provider/model] or effortByModel[model]
    pref_lvl = _norm_level(ctx.model_pref)
    if pref_lvl is None and isinstance(effort_by_model, Mapping):
        pref_lvl = _norm_level(effort_by_model.get(f"{ctx.provider}/{ctx.model_id}")) or _norm_level(
            effort_by_model.get(ctx.model_id)
        )
    if pref_lvl is not None:
        return pref_lvl, "model_pref"

    # Rule 8: global effortLevel / effort
    glob_lvl = _norm_level(ctx.global_effort) or global_setting
    if glob_lvl is not None:
        return glob_lvl, "global"

    # Rule 9: none
    return None, "none"


def resolve_effort(ctx: EffortContext) -> ResolvedEffort:
    """Resolve reasoning effort following spec 3.4 rules 1..9, then subagent cap and model clamp."""
    info = ctx.model_info if ctx.model_info is not None else levels_for(ctx.provider, ctx.model_id, ctx.capabilities)

    boost_keyword = bool(_read_setting(ctx.settings, "effort.boostKeyword", False))
    plan_mode_effort = _norm_level(_read_setting(ctx.settings, "effort.planMode", None))
    subagent_policy = str(_read_setting(ctx.settings, "effort.subagentPolicy", "model_default") or "model_default").strip().lower()
    raw_by_type = _read_setting(ctx.settings, "effort.byType", _DEFAULT_BY_TYPE)
    by_type: Mapping[str, Any] = raw_by_type if isinstance(raw_by_type, Mapping) else _DEFAULT_BY_TYPE
    max_subagent = _norm_level(_read_setting(ctx.settings, "effort.maxForSubagents", None))
    raw_by_model = _read_setting(ctx.settings, "effortByModel", {})
    effort_by_model: Mapping[str, Any] = raw_by_model if isinstance(raw_by_model, Mapping) else {}
    global_setting = _norm_level(_read_setting(ctx.settings, "effortLevel", None)) or _legacy_global_effort(ctx.settings)
    verifier_setting = _norm_level(_read_setting(ctx.settings, "orchestration.verifier.effort", None))

    level: str | None = None
    source: str = "none"

    if ctx.is_subagent:
        # Rule 1: call_effort
        call_lvl = _norm_level(ctx.call_effort)
        if call_lvl is not None:
            level, source = call_lvl, "call"
        else:
            # Rule 4: definition_effort (frontmatter)
            def_lvl = _norm_level(ctx.definition_effort)
            if def_lvl is not None:
                level, source = def_lvl, "definition"
            else:
                # Rule 5: role effort (explicit, verifier, or byType)
                role_lvl = _norm_level(ctx.role_effort)
                if role_lvl is not None:
                    level, source = role_lvl, "role"
                elif ctx.is_verifier and verifier_setting is not None:
                    level, source = verifier_setting, "role"
                elif subagent_policy == "by_type":
                    sub_key = str(ctx.subagent_type or "").strip().lower()
                    if sub_key == "general-purpose":
                        sub_key = "general"
                    mapped = str(by_type.get(sub_key) or "").strip().lower()
                    if mapped == "inherit":
                        parent_lvl = _norm_level(ctx.parent_effort)
                        if parent_lvl is not None:
                            level, source = parent_lvl, "parent"
                    else:
                        mapped_lvl = _norm_level(mapped)
                        if mapped_lvl is not None:
                            level, source = mapped_lvl, "role"
                # Rule 6: parent_effort (when inherit)
                if level is None and subagent_policy == "inherit":
                    parent_lvl = _norm_level(ctx.parent_effort)
                    if parent_lvl is not None:
                        level, source = parent_lvl, "parent"
                # Rule 8: global (skipped under model_default so subagent keeps model default)
                if level is None and subagent_policy != "model_default":
                    glob_lvl = _norm_level(ctx.global_effort) or global_setting
                    if glob_lvl is not None:
                        level, source = glob_lvl, "global"
    else:
        # Rule 1 (when call_effort explicitly passed on a pipeline step)
        call_lvl = _norm_level(ctx.call_effort)
        if call_lvl is not None:
            level, source = call_lvl, "call"
        else:
            wants_boost = bool(ctx.boost) or (
                boost_keyword and bool(ctx.message) and bool(_ULTRATHINK_RE.search(ctx.message))
            )
            base_level, base_source = _resolve_main_base(
                ctx,
                plan_mode_effort=plan_mode_effort,
                effort_by_model=effort_by_model,
                global_setting=global_setting,
            )
            if wants_boost:
                level, source = _step_up(base_level, info), "boost"
            else:
                level, source = base_level, base_source

    adjustment: EffortAdjustment | None = None

    # Cap for subagents (effort.maxForSubagents)
    if ctx.is_subagent and level is not None and max_subagent is not None:
        if EFFORT_RANK[level] > EFFORT_RANK[max_subagent]:
            adjustment = EffortAdjustment(requested=level, applied=max_subagent, reason="subagent_max")
            level = max_subagent

    # Model clamp (E-RF8)
    if level is not None:
        clamped, clamp_adj = clamp(ctx.provider, ctx.model_id, level, ctx.capabilities, info=info)
        if clamp_adj is not None:
            if adjustment is not None:
                adjustment = EffortAdjustment(
                    requested=adjustment.requested,
                    applied=clamped,
                    reason=clamp_adj.reason,
                )
            else:
                adjustment = clamp_adj
        level = clamped

    return ResolvedEffort(level=level, source=source, adjustment=adjustment)


class EffortEscalator:
    """Tracks verifier rejections per turn and advises stepping up effort or proposing a model switch (E-RF21)."""

    def __init__(self, *, threshold: int = 2) -> None:
        self._threshold = max(1, int(threshold))
        self._counts: dict[str, int] = {}

    def failure_count(self, turn_id: str) -> int:
        return self._counts.get(str(turn_id), 0)

    def reset(self, turn_id: str) -> None:
        self._counts.pop(str(turn_id), None)

    def on_verifier_failed(
        self,
        turn_id: str,
        *,
        provider: str = "",
        model_id: str = "",
        current_effort: str | None = None,
        capabilities: Collection[str] = (),
        settings: Any = None,
        enabled: bool | None = None,
        model_info: ModelEffortInfo | None = None,
    ) -> EscalationAdvice:
        key = str(turn_id)
        failures = self._counts.get(key, 0) + 1
        self._counts[key] = failures

        is_enabled = (
            bool(enabled)
            if enabled is not None
            else bool(_read_setting(settings, "effort.escalateOnFailure", False))
        )
        if not is_enabled or failures < self._threshold:
            return EscalationAdvice(failures=failures, escalated=False)

        info = model_info if model_info is not None else levels_for(provider, model_id, capabilities)
        ordered = sorted(
            (lvl for lvl in dict.fromkeys(info.levels) if lvl in EFFORT_RANK),
            key=lambda lvl: EFFORT_RANK[lvl],
        )
        if not ordered:
            return EscalationAdvice(
                failures=failures,
                escalated=False,
                next_effort=None,
                propose_model_switch=True,
            )

        current = _norm_level(current_effort) or _norm_level(info.default)
        if current is None:
            return EscalationAdvice(
                failures=failures,
                escalated=True,
                next_effort=ordered[0],
                propose_model_switch=False,
            )
        if current in ordered:
            idx = ordered.index(current)
            if idx + 1 < len(ordered):
                return EscalationAdvice(
                    failures=failures,
                    escalated=True,
                    next_effort=ordered[idx + 1],
                    propose_model_switch=False,
                )
            return EscalationAdvice(
                failures=failures,
                escalated=False,
                next_effort=ordered[-1],
                propose_model_switch=True,
            )

        current_rank = EFFORT_RANK[current]
        higher = [lvl for lvl in ordered if EFFORT_RANK[lvl] > current_rank]
        if higher:
            return EscalationAdvice(
                failures=failures,
                escalated=True,
                next_effort=higher[0],
                propose_model_switch=False,
            )
        return EscalationAdvice(
            failures=failures,
            escalated=False,
            next_effort=ordered[-1],
            propose_model_switch=True,
        )


def emit_effort_events(
    record_fn: Any,
    resolved: ResolvedEffort,
    model_id: str,
    *,
    emit_turn_started: bool = True,
    agent_id: str | None = None,
) -> None:
    """Emit effort.adjusted (on adjustment) and turn.started effort metadata without breaking the turn."""
    from .events import AgentActivityEventType

    if not callable(record_fn):
        return
    kw: dict[str, Any] = {"agent_id": agent_id} if agent_id else {}
    adj = resolved.adjustment
    if adj is not None:
        summary = (
            f"Esforço '{adj.requested}' ignorado: o modelo {model_id} não aceita controle de raciocínio"
            if adj.applied is None
            else f"Esforço ajustado de '{adj.requested}' para '{adj.applied}' ({model_id})"
        )
        try:
            record_fn(
                AgentActivityEventType.EFFORT_ADJUSTED,
                summary,
                {
                    "requested": adj.requested,
                    "applied": adj.applied,
                    "reason": adj.reason,
                    "model": model_id,
                },
                **kw,
            )
        except Exception:  # noqa: BLE001
            pass
    if emit_turn_started and resolved.level is not None:
        try:
            record_fn(
                AgentActivityEventType.TURN_STARTED,
                f"Esforço definido como '{resolved.level}' ({resolved.source})",
                {
                    "effort": resolved.level,
                    "effort_source": resolved.source,
                    "model": model_id,
                },
                **kw,
            )
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "EffortContext",
    "EffortEscalator",
    "EscalationAdvice",
    "ResolvedEffort",
    "emit_effort_events",
    "resolve_effort",
]
