"""Independent verification gate, correction rounds, and self-verify switch (0.13 WS-A5).

Coordinates:
- A-RF2 / A-RF3: threshold-driven independent verification via ``measure_turn_changes``
- A-RF4 / A-RF5 / A-RF12: model selection via ``select_verifier`` + visible cost events
- A-RF7: mandatory ``resolve_findings`` for proven blocker/major findings, re-verification
  rounds up to ``orchestration.verifier.maxRounds``, and ``completed_with_caveats`` closing
- A-RF8: ``orchestration.selfVerify.enabled`` (default False) governing legacy self-verify
- A-RF9: ``orchestration.verifier.includeSubagents`` (default False)
- A-RF10: duck-typed ``ReviewRecorder`` persistence hook
- E-RF21: ``EffortEscalator.on_verifier_failed`` integration
- D-RF10: ``MemoryPromotionGate.on_verified`` integration
- Package C K17 contract: ``decide(turn, runtime)`` and
  ``run_independent(turn, summary, choice, *, trigger_reason="files", scope_files=None)``
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
from typing import Any, Literal

# Ensure Package E and Package A settings keys are registered before resolving settings.
from agentos.settings import keys_effort_quota as _keys_effort_quota  # noqa: F401
from agentos.settings import keys_verifier as _keys_verifier  # noqa: F401
from agentos.settings.loader import EffectiveSettings
from agentos.settings.registry import REGISTRY, resolve as resolve_setting

from .change_measure import ChangeSummary, measure_turn_changes
from .effort_resolver import EffortContext, EffortEscalator, EscalationAdvice, resolve_effort
from .events import AgentActivityEventType
from .verification_findings import (
    CheckedCommand,
    Finding,
    FindingResolution,
    NormalizedReport,
    validate_report,
)
from .verifier_policy import (
    AuthorModel,
    AvailableVerifierModel,
    VerifierChoice,
    select_verifier,
)

logger = logging.getLogger(__name__)

GateKind = Literal["self_verify", "independent", "skip"]
OutcomeVerdict = Literal["pass", "fail", "inconclusive", "skipped"]
GateActionKind = Literal["continue", "self_verify", "complete", "complete_with_caveats"]

_READ_ONLY_SUBAGENT_TYPES: frozenset[str] = frozenset({"verifier", "explore", "plan"})
_MAX_RESOLVE_REMINDERS = 3


@dataclass(frozen=True, slots=True)
class GateDecision:
    kind: GateKind
    reason: str
    summary: ChangeSummary | None = None
    choice: VerifierChoice | None = None


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    verdict: OutcomeVerdict
    passed: bool
    rounds: int
    review_ids: tuple[str, ...]
    report: NormalizedReport | None
    checked: tuple[CheckedCommand, ...]
    findings: tuple[Finding, ...]
    open_findings: tuple[Finding, ...]
    resolutions: dict[str, FindingResolution]
    completed_with_caveats: bool
    choice: VerifierChoice | None
    summary: ChangeSummary | None
    trigger_reason: str
    skipped_reason: str | None = None
    escalation_advice: EscalationAdvice | None = None


@dataclass(frozen=True, slots=True)
class VerificationGateAction:
    action: GateActionKind
    system_message: str | None = None
    outcome: VerificationOutcome | None = None
    open_findings: tuple[Finding, ...] = ()
    reason: str = ""


@dataclass(slots=True)
class TurnGateState:
    round_num: int = 0
    review_ids: list[str] = field(default_factory=list)
    active_review_id: str | None = None
    awaiting_resolution: bool = False
    resolve_reminders: int = 0
    last_decision: GateDecision | None = None
    last_outcome: VerificationOutcome | None = None
    last_summary: ChangeSummary | None = None
    last_choice: VerifierChoice | None = None
    trigger_reason: str = "files"
    independent_settled: bool = False
    completed_with_caveats: bool = False
    caveat_findings: tuple[Finding, ...] = ()


def _get_nested(mapping: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    if dotted in mapping:
        return True, mapping[dotted]
    current: Any = mapping
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def read_gate_setting(settings: Any, path: str, default: Any = None) -> Any:
    """Resolve a setting from ``EffectiveSettings`` or a plain mapping with safe defaults."""
    if settings is None:
        if path in REGISTRY:
            return REGISTRY[path].default
        return default
    if isinstance(settings, EffectiveSettings):
        if path in REGISTRY:
            return resolve_setting(settings, path).value
        val = settings.get(path)
        return default if val is None else val
    if isinstance(settings, Mapping):
        found, val = _get_nested(settings, path)
        if found and val is not None:
            return val
        if path in REGISTRY:
            return REGISTRY[path].default
        return default
    if path in REGISTRY:
        return REGISTRY[path].default
    return default


def is_self_verify_enabled(settings: Any) -> bool:
    """Return whether ``orchestration.selfVerify.enabled`` is active (default False)."""
    return bool(read_gate_setting(settings, "orchestration.selfVerify.enabled", False))


def is_include_subagents_enabled(settings: Any) -> bool:
    """Return whether ``orchestration.verifier.includeSubagents`` is active (default False)."""
    return bool(read_gate_setting(settings, "orchestration.verifier.includeSubagents", False))


def _subagent_kind(turn: Mapping[str, Any]) -> str:
    raw = str(
        turn.get("subagent_type")
        or turn.get("agent_type")
        or turn.get("builtin_type")
        or ""
    ).strip().lower()
    if raw == "general-purpose":
        return "general"
    return raw


def should_include_subagent_in_gate(turn: Mapping[str, Any], settings: Any) -> bool:
    """Return True if a subagent turn is eligible for verification gating (A-RF9)."""
    if not turn.get("is_subagent"):
        return True
    kind = _subagent_kind(turn)
    if kind in _READ_ONLY_SUBAGENT_TYPES:
        return False
    return is_include_subagents_enabled(settings)


def _format_evidence(finding: Finding) -> str:
    ev = finding.evidence
    if ev is None:
        return "sem evidência"
    if ev.kind in {"failing_test", "command"}:
        excerpt = f" — {ev.output_excerpt}" if ev.output_excerpt else ""
        return f"{ev.kind}: `{ev.command}` (exit_code={ev.exit_code}){excerpt}"
    if ev.kind == "code_ref":
        return f"code_ref: {ev.file}:{ev.line} — {ev.reason}"
    return ev.kind


def format_findings_for_implementer(
    outcome: VerificationOutcome,
    *,
    max_rounds: int,
) -> str:
    """Build the system message returned to the implementer with proven blocking findings."""
    model_desc = ""
    if outcome.choice is not None and outcome.choice.model:
        model_desc = f" ({outcome.choice.model}, rodada {outcome.rounds}/{max_rounds})"
    lines: list[str] = [
        f"[Sistema — Verificação independente{model_desc}] "
        "Foram encontrados achados bloqueantes com prova objetiva que exigem resposta antes de concluir:",
    ]
    for item in outcome.open_findings:
        line = (
            f"- [{item.id}] ({item.severity}/{item.category}) {item.claim} "
            f"| Prova: {_format_evidence(item)}"
        )
        if item.suggested_fix:
            line += f" | Sugestão: {item.suggested_fix}"
        lines.append(line)
    ids_csv = ", ".join(item.id for item in outcome.open_findings)
    lines.append(
        "Você NÃO pode responder ao usuário enquanto estes achados estiverem abertos. "
        f"Corrija ou avalie cada item e chame `ResolveFindings` (`resolve_findings`) cobrindo todos os IDs ({ids_csv}) "
        "com status `fixed` (após corrigir), `confirmed` (se confirmado sem correção) ou `rejected` (com `reason` obrigatório)."
    )
    return "\n".join(lines)


def format_unresolved_reminder(
    open_findings: Sequence[Finding],
    unresolved_ids: Sequence[str],
) -> str:
    """Build the reminder reinjected when the implementer tries to answer without resolving all IDs."""
    pending_set = set(unresolved_ids)
    lines = [
        "[Sistema — Resolução obrigatória pendente] "
        "Você ainda não chamou `ResolveFindings` (`resolve_findings`) para todos os achados bloqueantes provados.",
        "Achados ainda sem resolução:",
    ]
    for item in open_findings:
        if item.id in pending_set:
            lines.append(f"- [{item.id}] ({item.severity}) {item.claim}")
    lines.append(
        "Chame `ResolveFindings` (`resolve_findings`) informando `fixed`, `confirmed` ou `rejected` "
        "(com `reason` obrigatório para `rejected`) para os IDs pendentes: "
        + ", ".join(unresolved_ids)
        + "."
    )
    return "\n".join(lines)


def format_caveats_summary(open_findings: Sequence[Finding]) -> str:
    """Short human-readable summary of unresolved blocking findings for completed_with_caveats."""
    if not open_findings:
        return "Concluído com pendências de verificação."
    parts = [f"{item.id} ({item.severity}): {item.claim}" for item in open_findings[:6]]
    suffix = f" (+{len(open_findings) - 6} mais)" if len(open_findings) > 6 else ""
    return "Concluído com pendências: " + "; ".join(parts) + suffix


def _build_verifier_prompt(
    *,
    files: Sequence[str],
    summary: ChangeSummary | None,
    trigger_reason: str,
    round_num: int,
    provider: str | None,
    model: str | None,
) -> str:
    """Build a concise, context-budget-aware prompt for the independent verifier subagent."""
    max_file_entries = 80
    try:
        from agentos.providers.claude_cli.effective_window import effective_window_for

        if provider and model:
            win = effective_window_for(provider=provider, model_id=model)
            if win.tokens < 64_000:
                max_file_entries = 30
    except Exception:  # noqa: BLE001
        pass

    shown = list(files[:max_file_entries])
    overflow = len(files) - len(shown)

    lines: list[str] = [
        f"Realize uma verificação independente das alterações deste turno (rodada {round_num}, motivo: {trigger_reason}).",
    ]
    if summary is not None:
        lines_desc = f"{summary.lines} linhas" if summary.lines_known else "linhas desconhecidas"
        lines.append(f"Escopo medido: {summary.files} arquivo(s), {lines_desc}.")
        if summary.sensitive_hits:
            lines.append("Caminhos sensíveis atingidos: " + ", ".join(summary.sensitive_hits[:20]))
    if shown:
        lines.append("Arquivos alterados:")
        for rel in shown:
            lines.append(f"- {rel}")
        if overflow > 0:
            lines.append(f"- ... (+{overflow} arquivo(s) adicionais)")
    lines.append(
        "Instruções: leia as alterações relevantes, rode os testes/linters/builds disponíveis no projeto para "
        "comprovar o comportamento, NÃO edite arquivos, e finalize chamando `ReportVerification` "
        "(`report_verification`) com `verdict` (`pass` | `fail` | `inconclusive`), `checked` "
        "(`[{command, exit_code, summary}]`), `findings` (cada achado com prova objetiva: `failing_test`/`command` "
        "com `command`+`exit_code` ou `code_ref` com `file`+`line`+`reason`) e `unverified`."
    )
    return "\n".join(lines)


def _extract_report_from_child_outcome(child_outcome: Any) -> NormalizedReport:
    """Extract and normalize a ``NormalizedReport`` from a child subagent's result."""
    if isinstance(child_outcome, NormalizedReport):
        return child_outcome

    payload: Mapping[str, Any] | None = None
    content = ""
    if isinstance(child_outcome, Mapping):
        payload = child_outcome
        content = str(child_outcome.get("content") or child_outcome.get("summary") or "")
    else:
        raw_payload = getattr(child_outcome, "payload", None)
        if isinstance(raw_payload, Mapping):
            payload = raw_payload
        content = str(getattr(child_outcome, "content", "") or getattr(child_outcome, "summary", "") or "")

    candidate: Any = None
    if payload is not None:
        if isinstance(payload.get("verification_report"), NormalizedReport):
            return payload["verification_report"]
        if isinstance(payload.get("verification_report"), Mapping):
            candidate = payload["verification_report"]
        elif isinstance(payload.get("report"), NormalizedReport):
            return payload["report"]
        elif isinstance(payload.get("report"), Mapping):
            candidate = payload["report"]
        elif "verdict" in payload or "passed" in payload:
            candidate = payload

    if isinstance(candidate, Mapping):
        # ``report_verification`` embeds the full normalized dict under ``report`` while keeping
        # ``findings`` as summary text for backwards compatibility. Prefer ``report`` when present.
        nested_report = candidate.get("report")
        raw_to_parse = nested_report if isinstance(nested_report, Mapping) else candidate
        try:
            return validate_report(raw_to_parse)
        except ValueError:
            logger.debug("Failed to validate nested verification report payload", exc_info=True)

    return validate_report(
        {
            "verdict": "inconclusive",
            "checked": [],
            "findings": [],
            "unverified": ["O verificador concluiu sem chamar ReportVerification."],
            "summary": content[:500] if content else "Verificação inconclusiva.",
        }
    )


def _extract_usage_from_child_outcome(child_outcome: Any) -> tuple[int | None, int | None, float | None]:
    payload = child_outcome if isinstance(child_outcome, Mapping) else getattr(child_outcome, "payload", None)
    if not isinstance(payload, Mapping):
        return None, None, None
    usage = payload.get("usage")
    source = usage if isinstance(usage, Mapping) else payload

    def _opt_int(val: Any) -> int | None:
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        return None

    def _opt_float(val: Any) -> float | None:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        return None

    input_tokens = _opt_int(source.get("input_tokens"))
    output_tokens = _opt_int(source.get("output_tokens"))
    cost_usd = _opt_float(source.get("cost_usd"))
    return input_tokens, output_tokens, cost_usd


def _is_child_cancelled_or_failed(child_outcome: Any) -> tuple[bool, str | None]:
    if isinstance(child_outcome, NormalizedReport):
        return False, None
    if isinstance(child_outcome, Mapping):
        status = str(child_outcome.get("status") or "succeeded").lower()
        error_code = str(child_outcome.get("error_code") or "") or None
        if status in {"failed", "cancelled"}:
            return True, error_code or status
        return False, None
    status = str(getattr(child_outcome, "status", "succeeded") or "succeeded").lower()
    error_code = getattr(child_outcome, "error_code", None)
    if status in {"failed", "cancelled"}:
        return True, str(error_code or status)
    return False, None


class VerificationGate:
    """Orchestrates independent verification, self-verify fallback, and A-RF7 correction rounds."""

    def __init__(
        self,
        *,
        session: Any = None,
        settings: Any = None,
        available_models: (
            Sequence[AvailableVerifierModel | Mapping[str, Any]]
            | Callable[[], Sequence[AvailableVerifierModel | Mapping[str, Any]]]
            | None
        ) = None,
        quota_service: Any = None,
        review_recorder: Any = None,
        effort_escalator: EffortEscalator | None = None,
        promotion_gate: Any = None,
        run_child: Callable[..., Any] | None = None,
        event_emitter: Callable[[str, str, Mapping[str, Any]], None] | None = None,
        checkpoint_store: Any = None,
        workspace_root: Path | str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._available_models = available_models
        self._quota_service = quota_service
        self._review_recorder = review_recorder
        self._effort_escalator = effort_escalator
        self._promotion_gate = promotion_gate
        self._run_child = run_child
        self._event_emitter = event_emitter
        self._checkpoint_store = checkpoint_store
        self._workspace_root = workspace_root

    # -- dependency resolution helpers ---------------------------------------

    def _effective_settings(self, runtime: Any = None) -> Any:
        if self._settings is not None:
            return self._settings
        if runtime is not None:
            rt_settings = getattr(runtime, "effective_settings", None)
            if rt_settings is not None:
                return rt_settings
        if self._session is not None:
            return getattr(self._session, "effective_settings", None)
        return None

    def _get_quota_service(self) -> Any:
        if self._quota_service is not None:
            return self._quota_service
        if self._session is not None:
            getter = getattr(self._session, "_child_quota_service", None)
            if callable(getter):
                return getter()
            return getattr(self._session, "_quota_service", None)
        return None

    def _get_review_recorder(self) -> Any:
        if self._review_recorder is not None:
            return self._review_recorder
        if self._session is not None:
            return getattr(self._session, "review_recorder", None) or getattr(self._session, "_review_recorder", None)
        return None

    def _get_effort_escalator(self) -> EffortEscalator:
        if self._effort_escalator is not None:
            return self._effort_escalator
        if self._session is not None:
            esc = getattr(self._session, "effort_escalator", None) or getattr(self._session, "_effort_escalator", None)
            if isinstance(esc, EffortEscalator):
                return esc
        self._effort_escalator = EffortEscalator()
        return self._effort_escalator

    def _resolve_available_models(self, turn: Mapping[str, Any]) -> list[AvailableVerifierModel | Mapping[str, Any]]:
        if self._available_models is not None:
            raw = self._available_models() if callable(self._available_models) else self._available_models
            return list(raw)

        candidates: list[AvailableVerifierModel] = []
        seen: set[tuple[str, str]] = set()

        if self._session is not None:
            catalog_fn = getattr(self._session, "_child_routing_catalog", None)
            if callable(catalog_fn):
                favorites, configured = catalog_fn()
                configured_set = set(configured) if configured is not None else None
                for prov, models in favorites.items():
                    is_conf = True if configured_set is None else (prov in configured_set)
                    for model in models:
                        key = (prov, model)
                        if key not in seen:
                            seen.add(key)
                            candidates.append(
                                AvailableVerifierModel(
                                    provider=prov,
                                    model=model,
                                    configured=is_conf,
                                    is_favorite=True,
                                )
                            )

        author_prov = str(turn.get("provider") or "").strip()
        author_model = str(turn.get("model_id") or "").strip()
        if author_prov and author_model and (author_prov, author_model) not in seen:
            candidates.append(
                AvailableVerifierModel(
                    provider=author_prov,
                    model=author_model,
                    configured=True,
                    is_favorite=True,
                )
            )
        return candidates

    def _emit_event(
        self,
        event_type: str,
        summary: str,
        payload: Mapping[str, Any],
        *,
        turn: Mapping[str, Any] | None = None,
        runtime: Any = None,
    ) -> None:
        if callable(self._event_emitter):
            self._event_emitter(event_type, summary, dict(payload))
            return
        recorded_in_session = False
        if self._session is not None and callable(getattr(self._session, "_record", None)):
            try:
                evt_enum = AgentActivityEventType(event_type)
                self._session._record(evt_enum, summary, dict(payload))
                recorded_in_session = True
            except Exception:  # noqa: BLE001 - activity emission never breaks a turn
                logger.debug("Failed to record verifier event %s on session", event_type, exc_info=True)
        if runtime is not None and turn is not None and callable(getattr(runtime, "_life", None)):
            try:
                runtime._life(turn, event_type, summary=summary, **dict(payload))
            except Exception:  # noqa: BLE001
                if not recorded_in_session:
                    logger.debug("Failed to emit lifecycle verifier event %s", event_type, exc_info=True)

    def _has_verifiable_changes(self, turn: Mapping[str, Any], runtime: Any) -> bool:
        if runtime is None:
            return False
        toolset = getattr(runtime, "toolset", None)
        change_events_fn = getattr(runtime, "_toolset_change_events", None)
        verified_at = int(getattr(runtime, "_verified_at_change_events", 0) or 0)
        if callable(change_events_fn):
            try:
                if int(change_events_fn()) > verified_at:
                    return True
            except Exception:  # noqa: BLE001
                pass
        if toolset is not None:
            changed_paths = getattr(toolset, "_changed_paths", None)
            if changed_paths:
                # Only treat as unverified if change_events wasn't already settled.
                ev_fn = getattr(toolset, "change_events", None)
                if callable(ev_fn):
                    try:
                        if int(ev_fn()) > verified_at:
                            return True
                    except Exception:  # noqa: BLE001
                        return True
                elif verified_at == 0:
                    return True
        needs_fn = getattr(runtime, "_needs_verification", None)
        if callable(needs_fn):
            try:
                if bool(needs_fn()):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _can_self_verify(self, runtime: Any) -> bool:
        if runtime is None:
            return False
        needs_fn = getattr(runtime, "_needs_verification", None)
        if callable(needs_fn):
            try:
                return bool(needs_fn())
            except Exception:  # noqa: BLE001
                return False
        return False

    def _measure_changes(
        self,
        turn: Mapping[str, Any],
        runtime: Any,
        *,
        settings: Any,
        use_cache: bool = True,
    ) -> ChangeSummary:
        toolset = getattr(runtime, "toolset", None) if runtime is not None else None
        checkpoint_store = self._checkpoint_store
        if checkpoint_store is None and toolset is not None:
            checkpoint_store = getattr(toolset, "_checkpoint_store", None)
        if checkpoint_store is None and self._session is not None:
            checkpoint_store = getattr(self._session, "file_history", None)
        ws_root = self._workspace_root
        if ws_root is None and self._session is not None:
            ws = getattr(self._session, "workspace", None)
            ws_root = getattr(ws, "root", ws)
        turn_id = str(turn.get("turn_id") or "") or None
        return measure_turn_changes(
            toolset,
            checkpoint_store,
            ws_root,
            turn_id=turn_id,
            turn=turn,
            settings=settings,
            use_cache=use_cache,
        )

    def _record_skipped_review(
        self,
        turn: Mapping[str, Any],
        *,
        summary: ChangeSummary | None,
        choice: VerifierChoice | None,
        trigger_reason: str,
        round_num: int,
        author_effort: str | None,
    ) -> str | None:
        recorder = self._get_review_recorder()
        if recorder is None:
            return None
        start_fn = getattr(recorder, "start", None)
        finish_fn = getattr(recorder, "finish", None)
        if not callable(start_fn):
            return None
        review_id = start_fn(
            user_id=str(turn.get("user_id") or ""),
            conversation_id=str(turn.get("conversation_id") or ""),
            turn_id=str(turn.get("turn_id") or ""),
            agent_id=str(turn.get("agent_id") or getattr(self._session, "main_agent_id", "main") or "main"),
            round=round_num,
            trigger_reason=trigger_reason,
            files_changed=summary.files if summary is not None else 0,
            lines_changed=(summary.lines if (summary is not None and summary.lines_known) else None),
            author_provider=str(turn.get("provider") or ""),
            author_model=str(turn.get("model_id") or ""),
            author_effort=author_effort,
            reviewer_provider=choice.provider if choice is not None else None,
            reviewer_model=choice.model if choice is not None else None,
            reviewer_effort=choice.effort if choice is not None else None,
            policy=choice.policy if choice is not None else "cross_family",
            fallback_step=choice.fallback_step if choice is not None else 4,
        )
        if callable(finish_fn) and review_id is not None:
            finish_fn(
                review_id,
                verdict="skipped",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        return str(review_id) if review_id is not None else None

    def _promote_memories(self, turn: Mapping[str, Any], verdict: str, settings: Any, runtime: Any) -> None:
        turn_id = str(turn.get("turn_id") or "").strip()
        learn_only = bool(read_gate_setting(settings, "memory.agent.learnFromVerifiedOnly", True))
        if self._promotion_gate is not None:
            on_verified = getattr(self._promotion_gate, "on_verified", None)
            if callable(on_verified):
                on_verified(turn_id, verdict, learn_from_verified_only=learn_only)
                return
        if self._session is not None:
            promote_fn = getattr(self._session, "_promote_agent_memories", None)
            if callable(promote_fn):
                promote_fn(verdict)
                return
        if runtime is not None:
            on_ver = getattr(runtime, "_on_verification", None)
            if callable(on_ver):
                on_ver(verdict == "pass")

    def _handle_effort_escalation(
        self,
        turn: Mapping[str, Any],
        verdict: str,
        *,
        settings: Any,
        runtime: Any,
        author_effort: str | None,
    ) -> EscalationAdvice | None:
        escalator = self._get_effort_escalator()
        turn_id = str(turn.get("turn_id") or "").strip() or "turn-default"
        if verdict == "pass":
            escalator.reset(turn_id)
            return None
        if verdict != "fail":
            return None

        author_provider = str(turn.get("provider") or "")
        author_model = str(turn.get("model_id") or "")
        advice = escalator.on_verifier_failed(
            turn_id,
            provider=author_provider,
            model_id=author_model,
            current_effort=author_effort,
            settings=settings,
        )
        if advice.escalated and advice.next_effort:
            if runtime is not None:
                try:
                    runtime.effort = advice.next_effort
                except Exception:  # noqa: BLE001
                    pass
            self._emit_event(
                AgentActivityEventType.EFFORT_ADJUSTED.value,
                f"Esforço elevado para '{advice.next_effort}' após {advice.failures} reprovações do verificador",
                {
                    "requested": author_effort,
                    "applied": advice.next_effort,
                    "reason": "verifier_failure_escalation",
                    "failures": advice.failures,
                    "model": author_model,
                },
                turn=turn,
                runtime=runtime,
            )
        elif advice.propose_model_switch:
            self._emit_event(
                AgentActivityEventType.EFFORT_ADJUSTED.value,
                f"Esforço já no teto após {advice.failures} reprovações do verificador; considere trocar de modelo",
                {
                    "requested": author_effort,
                    "applied": advice.next_effort,
                    "reason": "propose_model_switch",
                    "propose_model_switch": True,
                    "failures": advice.failures,
                    "model": author_model,
                },
                turn=turn,
                runtime=runtime,
            )
        return advice

    # -- public K17 contract -------------------------------------------------

    def decide(self, turn: Mapping[str, Any], runtime: Any) -> GateDecision:
        """Decide whether this turn needs independent verification, legacy self-verify, or skip."""
        settings = self._effective_settings(runtime)

        if turn.get("is_subagent") and not should_include_subagent_in_gate(turn, settings):
            kind = _subagent_kind(turn)
            reason = "verifier_subagent" if kind == "verifier" else "subagent_excluded"
            return GateDecision(kind="skip", reason=reason)

        if not self._has_verifiable_changes(turn, runtime):
            return GateDecision(kind="skip", reason="no_changes")

        verifier_enabled = bool(read_gate_setting(settings, "orchestration.verifier.enabled", False))
        self_verify_enabled = is_self_verify_enabled(settings)

        if verifier_enabled:
            summary = self._measure_changes(turn, runtime, settings=settings, use_cache=True)
            if summary.triggered:
                author_effort = (
                    getattr(runtime, "effort", None)
                    if runtime is not None
                    else (str(turn.get("effort") or "").strip() or None)
                )
                author = AuthorModel(
                    provider=str(turn.get("provider") or ""),
                    model=str(turn.get("model_id") or ""),
                    effort=author_effort,
                )
                policy = str(read_gate_setting(settings, "orchestration.verifier.modelPolicy", "cross_family"))
                explicit = read_gate_setting(settings, "orchestration.verifier.explicit", None)
                tiers = read_gate_setting(settings, "orchestration.modelTiers", None)
                quota_enabled = bool(read_gate_setting(settings, "quota.policy.enabled", False))
                quota_block = int(read_gate_setting(settings, "quota.policy.blockBackgroundPercent", 90))
                requested_effort = str(read_gate_setting(settings, "orchestration.verifier.effort", "high") or "high")

                available = self._resolve_available_models(turn)
                choice = select_verifier(
                    author=author,
                    available=available,
                    policy=policy,
                    explicit=explicit,
                    quota=self._get_quota_service(),
                    tiers=tiers if isinstance(tiers, Mapping) else None,
                    requested_effort=requested_effort,
                    quota_policy_enabled=quota_enabled,
                    quota_block_percent=quota_block,
                )

                # Resolve final effort via effort_resolver rule 5 when a model was chosen.
                if choice.available and choice.provider and choice.model:
                    resolved_eff = resolve_effort(
                        EffortContext(
                            provider=choice.provider,
                            model_id=choice.model,
                            call_effort=choice.effort if policy == "explicit" and explicit and explicit.get("effort") else None,
                            is_subagent=True,
                            subagent_type="verifier",
                            is_verifier=True,
                            settings=settings,
                        )
                    )
                    if resolved_eff.level is not None and resolved_eff.level != choice.effort:
                        choice = replace(
                            choice,
                            effort=resolved_eff.level,
                            effort_adjustment=resolved_eff.adjustment or choice.effort_adjustment,
                        )

                selected_payload = choice.to_event_payload()
                selected_payload["quota_policy_enabled"] = quota_enabled
                selected_summary = (
                    f"Modelo do verificador selecionado: {choice.model} ({choice.provider})"
                    if choice.available
                    else f"Nenhum modelo disponível para o verificador ({choice.reason})"
                )
                self._emit_event(
                    AgentActivityEventType.VERIFIER_MODEL_SELECTED.value,
                    selected_summary,
                    selected_payload,
                    turn=turn,
                    runtime=runtime,
                )

                if choice.fallback_step == 4 or not choice.available:
                    trigger_reason = summary.reason or "files"
                    review_id = self._record_skipped_review(
                        turn,
                        summary=summary,
                        choice=choice,
                        trigger_reason=trigger_reason,
                        round_num=1,
                        author_effort=author_effort,
                    )
                    self._emit_event(
                        AgentActivityEventType.VERIFIER_FINISHED.value,
                        "Verificação independente pulada: nenhum modelo disponível (assinaturas bloqueadas ou indisponíveis)",
                        {
                            "review_id": review_id,
                            "round": 1,
                            "verdict": "skipped",
                            "passed": False,
                            "reviewer_provider": None,
                            "reviewer_model": None,
                            "effort": None,
                            "trigger_reason": trigger_reason,
                            "files_changed": summary.files,
                            "lines_changed": summary.lines if summary.lines_known else None,
                            "lines_known": summary.lines_known,
                            "checked": [],
                            "findings": [],
                            "blocking_count": 0,
                            "unproven_count": 0,
                            "unverified": [],
                            "skipped_reason": choice.reason,
                            "fallback_step": 4,
                        },
                        turn=turn,
                        runtime=runtime,
                    )
                    if self_verify_enabled and self._can_self_verify(runtime):
                        return GateDecision(
                            kind="self_verify",
                            reason="verifier_unavailable_fallback_self_verify",
                            summary=summary,
                            choice=choice,
                        )
                    return GateDecision(
                        kind="skip",
                        reason="verifier_unavailable",
                        summary=summary,
                        choice=choice,
                    )

                return GateDecision(
                    kind="independent",
                    reason=summary.reason or "files",
                    summary=summary,
                    choice=choice,
                )

            # Below threshold
            if self_verify_enabled and self._can_self_verify(runtime):
                return GateDecision(kind="self_verify", reason="below_threshold_self_verify", summary=summary)
            return GateDecision(kind="skip", reason="below_threshold", summary=summary)

        # Verifier disabled -> honour A-RF8 (selfVerify.enabled, default False)
        if self_verify_enabled and self._can_self_verify(runtime):
            return GateDecision(kind="self_verify", reason="self_verify_enabled")
        return GateDecision(kind="skip", reason="disabled")

    def run_independent(
        self,
        turn: Mapping[str, Any],
        summary: ChangeSummary | None,
        choice: VerifierChoice,
        *,
        trigger_reason: str = "files",
        scope_files: Sequence[str] | None = None,
        round_num: int = 1,
        previous_review_ids: tuple[str, ...] = (),
        runtime: Any = None,
    ) -> VerificationOutcome:
        """Launch the independent ``verifier`` subagent and record review, findings, events, escalation, and promotion.

        Signature adheres to Package C contract K17:
        ``run_independent(turn, summary, choice, *, trigger_reason="files", scope_files=None) -> VerificationOutcome``.
        """
        settings = self._effective_settings(runtime)
        author_effort = (
            getattr(runtime, "effort", None)
            if runtime is not None
            else (str(turn.get("effort") or "").strip() or None)
        )
        files_list = (
            tuple(str(p) for p in scope_files if str(p).strip())
            if scope_files is not None
            else (summary.changed_files if summary is not None else ())
        )
        files_changed = len(files_list) if scope_files is not None else (summary.files if summary is not None else 0)
        lines_known = summary.lines_known if summary is not None else False
        lines_changed = summary.lines if (summary is not None and summary.lines_known) else None

        # A-RF12: visible cost notice BEFORE launching the verifier subagent.
        effort_label = choice.effort or "default"
        start_message = (
            f"Verificando com {choice.model} ({choice.provider}, esforço {effort_label}): {trigger_reason}"
        )
        self._emit_event(
            AgentActivityEventType.VERIFIER_STARTED.value,
            start_message,
            {
                "round": round_num,
                "reviewer_provider": choice.provider,
                "reviewer_model": choice.model,
                "effort": choice.effort,
                "policy": choice.policy,
                "fallback_step": choice.fallback_step,
                "trigger_reason": trigger_reason,
                "files_changed": files_changed,
                "lines_changed": lines_changed,
                "lines_known": lines_known,
                "scope_files": list(files_list),
                "message": start_message,
            },
            turn=turn,
            runtime=runtime,
        )

        recorder = self._get_review_recorder()
        review_id: str | None = None
        if recorder is not None and callable(getattr(recorder, "start", None)):
            raw_id = recorder.start(
                user_id=str(turn.get("user_id") or ""),
                conversation_id=str(turn.get("conversation_id") or ""),
                turn_id=str(turn.get("turn_id") or ""),
                agent_id=str(turn.get("agent_id") or getattr(self._session, "main_agent_id", "main") or "main"),
                round=round_num,
                trigger_reason=trigger_reason,
                files_changed=files_changed,
                lines_changed=lines_changed,
                author_provider=str(turn.get("provider") or ""),
                author_model=str(turn.get("model_id") or ""),
                author_effort=author_effort,
                reviewer_provider=choice.provider,
                reviewer_model=choice.model,
                reviewer_effort=choice.effort,
                policy=choice.policy,
                fallback_step=choice.fallback_step,
            )
            review_id = str(raw_id) if raw_id is not None else None

        all_review_ids = (*previous_review_ids, *( (review_id,) if review_id else () ))

        child_runner = self._run_child
        if child_runner is None and self._session is not None:
            child_runner = getattr(self._session, "run_child", None)
        if not callable(child_runner):
            if recorder is not None and review_id and callable(getattr(recorder, "finish", None)):
                recorder.finish(review_id, verdict="skipped", input_tokens=None, output_tokens=None, cost_usd=None)
            self._emit_event(
                AgentActivityEventType.VERIFIER_FINISHED.value,
                "Verificação independente pulada: executor de subagente indisponível",
                {
                    "review_id": review_id,
                    "round": round_num,
                    "verdict": "skipped",
                    "passed": False,
                    "reviewer_provider": choice.provider,
                    "reviewer_model": choice.model,
                    "effort": choice.effort,
                    "trigger_reason": trigger_reason,
                    "files_changed": files_changed,
                    "lines_changed": lines_changed,
                    "lines_known": lines_known,
                    "checked": [],
                    "findings": [],
                    "blocking_count": 0,
                    "unproven_count": 0,
                    "unverified": [],
                    "skipped_reason": "no_child_runner",
                },
                turn=turn,
                runtime=runtime,
            )
            return VerificationOutcome(
                verdict="skipped",
                passed=False,
                rounds=round_num,
                review_ids=all_review_ids,
                report=None,
                checked=(),
                findings=(),
                open_findings=(),
                resolutions={},
                completed_with_caveats=False,
                choice=choice,
                summary=summary,
                trigger_reason=trigger_reason,
                skipped_reason="no_child_runner",
            )

        prompt = _build_verifier_prompt(
            files=files_list,
            summary=summary,
            trigger_reason=trigger_reason,
            round_num=round_num,
            provider=choice.provider,
            model=choice.model,
        )

        try:
            child_outcome = child_runner(
                "verifier",
                prompt,
                provider=choice.provider,
                model_id=choice.model,
                effort=choice.effort,
                description=f"Verificação independente (rodada {round_num})",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Independent verifier execution failed: %s", exc, exc_info=True)
            reason = f"verifier_error:{type(exc).__name__}"
            if recorder is not None and review_id and callable(getattr(recorder, "finish", None)):
                recorder.finish(review_id, verdict="skipped", input_tokens=None, output_tokens=None, cost_usd=None)
            self._emit_event(
                AgentActivityEventType.VERIFIER_FINISHED.value,
                f"Verificação independente interrompida ({reason})",
                {
                    "review_id": review_id,
                    "round": round_num,
                    "verdict": "skipped",
                    "passed": False,
                    "reviewer_provider": choice.provider,
                    "reviewer_model": choice.model,
                    "effort": choice.effort,
                    "trigger_reason": trigger_reason,
                    "files_changed": files_changed,
                    "lines_changed": lines_changed,
                    "lines_known": lines_known,
                    "checked": [],
                    "findings": [],
                    "blocking_count": 0,
                    "unproven_count": 0,
                    "unverified": [],
                    "skipped_reason": reason,
                },
                turn=turn,
                runtime=runtime,
            )
            return VerificationOutcome(
                verdict="skipped",
                passed=False,
                rounds=round_num,
                review_ids=all_review_ids,
                report=None,
                checked=(),
                findings=(),
                open_findings=(),
                resolutions={},
                completed_with_caveats=False,
                choice=choice,
                summary=summary,
                trigger_reason=trigger_reason,
                skipped_reason=reason,
            )

        child_failed, fail_reason = _is_child_cancelled_or_failed(child_outcome)
        if child_failed:
            reason = fail_reason or "verifier_failed"
            if recorder is not None and review_id and callable(getattr(recorder, "finish", None)):
                recorder.finish(review_id, verdict="skipped", input_tokens=None, output_tokens=None, cost_usd=None)
            self._emit_event(
                AgentActivityEventType.VERIFIER_FINISHED.value,
                f"Verificação independente não concluída ({reason})",
                {
                    "review_id": review_id,
                    "round": round_num,
                    "verdict": "skipped",
                    "passed": False,
                    "reviewer_provider": choice.provider,
                    "reviewer_model": choice.model,
                    "effort": choice.effort,
                    "trigger_reason": trigger_reason,
                    "files_changed": files_changed,
                    "lines_changed": lines_changed,
                    "lines_known": lines_known,
                    "checked": [],
                    "findings": [],
                    "blocking_count": 0,
                    "unproven_count": 0,
                    "unverified": [],
                    "skipped_reason": reason,
                },
                turn=turn,
                runtime=runtime,
            )
            return VerificationOutcome(
                verdict="skipped",
                passed=False,
                rounds=round_num,
                review_ids=all_review_ids,
                report=None,
                checked=(),
                findings=(),
                open_findings=(),
                resolutions={},
                completed_with_caveats=False,
                choice=choice,
                summary=summary,
                trigger_reason=trigger_reason,
                skipped_reason=reason,
            )

        report = _extract_report_from_child_outcome(child_outcome)
        input_tokens, output_tokens, cost_usd = _extract_usage_from_child_outcome(child_outcome)

        if recorder is not None and review_id:
            if callable(getattr(recorder, "add_findings", None)) and report.findings:
                recorder.add_findings(review_id, report.findings)
            if callable(getattr(recorder, "finish", None)):
                recorder.finish(
                    review_id,
                    verdict=report.verdict,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                )

        blocking = report.blocking_findings
        unproven_count = sum(1 for item in report.findings if item.severity == "unproven")
        finish_summary = (
            f"Verificação independente aprovada ({choice.model})"
            if report.verdict == "pass"
            else (
                f"Verificação independente encontrou {len(blocking)} achado(s) bloqueante(s) ({choice.model})"
                if blocking
                else f"Verificação independente: {report.verdict} ({choice.model})"
            )
        )
        self._emit_event(
            AgentActivityEventType.VERIFIER_FINISHED.value,
            finish_summary,
            {
                "review_id": review_id,
                "round": round_num,
                "verdict": report.verdict,
                "passed": report.passed,
                "reviewer_provider": choice.provider,
                "reviewer_model": choice.model,
                "effort": choice.effort,
                "trigger_reason": trigger_reason,
                "files_changed": files_changed,
                "lines_changed": lines_changed,
                "lines_known": lines_known,
                "checked": [c.to_dict() for c in report.checked],
                "findings": [f.to_dict() for f in report.findings],
                "blocking_count": len(blocking),
                "unproven_count": unproven_count,
                "unverified": list(report.unverified),
            },
            turn=turn,
            runtime=runtime,
        )

        # D-RF10: promote agent memories only on verified pass (never on fail/inconclusive).
        self._promote_memories(turn, report.verdict, settings, runtime)

        # E-RF21: track consecutive failures and escalate effort or propose model switch at cap.
        advice = self._handle_effort_escalation(
            turn,
            report.verdict,
            settings=settings,
            runtime=runtime,
            author_effort=author_effort,
        )

        return VerificationOutcome(
            verdict=report.verdict,
            passed=report.passed,
            rounds=round_num,
            review_ids=all_review_ids,
            report=report,
            checked=report.checked,
            findings=report.findings,
            open_findings=blocking,
            resolutions={},
            completed_with_caveats=False,
            choice=choice,
            summary=summary,
            trigger_reason=trigger_reason,
            escalation_advice=advice,
        )

    # -- turn-end orchestration for runtime.py and native_turn.py ------------

    def _state_for(self, runtime: Any) -> TurnGateState:
        state = getattr(runtime, "_verification_gate_state", None)
        if not isinstance(state, TurnGateState):
            state = TurnGateState()
            if runtime is not None:
                runtime._verification_gate_state = state
        return state

    def _register_open_findings(
        self,
        runtime: Any,
        outcome: VerificationOutcome,
        state: TurnGateState,
    ) -> None:
        toolset = getattr(runtime, "toolset", None)
        setter = getattr(toolset, "set_open_verification_findings", None)
        active_review_id = outcome.review_ids[-1] if outcome.review_ids else None
        state.active_review_id = active_review_id
        recorder = self._get_review_recorder()

        def _on_resolve(resolutions: tuple[FindingResolution, ...]) -> None:
            if recorder is not None and active_review_id and callable(getattr(recorder, "set_resolution", None)):
                for item in resolutions:
                    recorder.set_resolution(active_review_id, item.id, item.status, item.reason)

        if callable(setter):
            setter(outcome.open_findings, on_resolve=_on_resolve)
        state.awaiting_resolution = True
        state.resolve_reminders = 0

    def _mark_verified(self, runtime: Any) -> None:
        if runtime is None:
            return
        fn = getattr(runtime, "_toolset_change_events", None)
        if callable(fn):
            try:
                runtime._verified_at_change_events = int(fn())
            except Exception:  # noqa: BLE001
                pass

    def evaluate_turn_end(
        self,
        turn: Mapping[str, Any],
        runtime: Any,
        *,
        allow_continuation: bool = True,
    ) -> VerificationGateAction:
        """Evaluate the verification gate whenever the implementer attempts to finish its turn.

        Shared between classic ``AgenticTurnRuntime`` and ``NativeTurnRunner`` so both
        loops stay thin and behave identically.
        """
        settings = self._effective_settings(runtime)
        max_rounds = max(1, int(read_gate_setting(settings, "orchestration.verifier.maxRounds", 2)))
        state = self._state_for(runtime)
        toolset = getattr(runtime, "toolset", None)

        # Phase 1: We already ran independent verification and handed proven blocking findings to the implementer.
        if state.awaiting_resolution:
            open_findings: tuple[Finding, ...] = ()
            unresolved_ids: tuple[str, ...] = ()
            resolutions: dict[str, FindingResolution] = {}
            if toolset is not None:
                if callable(getattr(toolset, "open_verification_findings", None)):
                    open_findings = tuple(toolset.open_verification_findings())
                if callable(getattr(toolset, "unresolved_verification_finding_ids", None)):
                    unresolved_ids = tuple(toolset.unresolved_verification_finding_ids())
                if callable(getattr(toolset, "verification_resolutions", None)):
                    resolutions = dict(toolset.verification_resolutions())
            elif state.last_outcome is not None:
                open_findings = state.last_outcome.open_findings
                unresolved_ids = tuple(item.id for item in open_findings)

            if unresolved_ids:
                if allow_continuation and state.resolve_reminders < _MAX_RESOLVE_REMINDERS:
                    state.resolve_reminders += 1
                    return VerificationGateAction(
                        action="continue",
                        system_message=format_unresolved_reminder(open_findings, unresolved_ids),
                        outcome=state.last_outcome,
                        open_findings=open_findings,
                        reason="unresolved_findings",
                    )
                pending_set = set(unresolved_ids)
                still_open = tuple(item for item in open_findings if item.id in pending_set)
                clear_fn = getattr(toolset, "clear_open_verification_findings", None)
                if callable(clear_fn):
                    clear_fn()
                state.awaiting_resolution = False
                state.independent_settled = True
                state.completed_with_caveats = True
                state.caveat_findings = still_open
                self._mark_verified(runtime)
                final_outcome = (
                    replace(
                        state.last_outcome,
                        open_findings=still_open,
                        resolutions=resolutions,
                        completed_with_caveats=True,
                    )
                    if state.last_outcome is not None
                    else None
                )
                state.last_outcome = final_outcome
                return VerificationGateAction(
                    action="complete_with_caveats",
                    outcome=final_outcome,
                    open_findings=still_open,
                    reason="unresolved_findings_exhausted",
                )

            # All open blocking finding IDs have been covered by resolve_findings!
            clear_fn = getattr(toolset, "clear_open_verification_findings", None)
            if callable(clear_fn):
                clear_fn()
            state.awaiting_resolution = False
            state.resolve_reminders = 0

            fixed_findings = tuple(
                item for item in open_findings
                if item.id in resolutions and resolutions[item.id].status == "fixed"
            )
            confirmed_findings = tuple(
                item for item in open_findings
                if item.id in resolutions and resolutions[item.id].status == "confirmed"
            )

            if fixed_findings:
                if state.round_num < max_rounds and state.last_choice is not None:
                    next_round = state.round_num + 1
                    refreshed_summary = self._measure_changes(turn, runtime, settings=settings, use_cache=False)
                    merged_files = tuple(
                        dict.fromkeys(
                            [
                                *(state.last_summary.changed_files if state.last_summary is not None else ()),
                                *refreshed_summary.changed_files,
                            ]
                        )
                    )
                    outcome = self.run_independent(
                        turn,
                        refreshed_summary,
                        state.last_choice,
                        trigger_reason=state.trigger_reason,
                        scope_files=merged_files or None,
                        round_num=next_round,
                        previous_review_ids=tuple(state.review_ids),
                        runtime=runtime,
                    )
                    state.round_num = next_round
                    state.review_ids = list(outcome.review_ids)
                    state.last_summary = refreshed_summary
                    state.last_outcome = outcome

                    if outcome.verdict == "skipped":
                        state.independent_settled = True
                        self._mark_verified(runtime)
                        if confirmed_findings:
                            state.completed_with_caveats = True
                            state.caveat_findings = confirmed_findings
                            return VerificationGateAction(
                                action="complete_with_caveats",
                                outcome=replace(outcome, open_findings=confirmed_findings, resolutions=resolutions, completed_with_caveats=True),
                                open_findings=confirmed_findings,
                                reason="reverification_skipped_with_confirmed_findings",
                            )
                        return VerificationGateAction(
                            action="complete",
                            outcome=replace(outcome, resolutions=resolutions),
                            reason="reverification_skipped",
                        )

                    if not outcome.open_findings:
                        state.independent_settled = True
                        self._mark_verified(runtime)
                        return VerificationGateAction(
                            action="complete",
                            outcome=replace(outcome, resolutions=resolutions),
                            reason="reverification_passed",
                        )

                    # Still has proven blocking findings after re-verification!
                    if next_round >= max_rounds or not allow_continuation:
                        state.independent_settled = True
                        state.completed_with_caveats = True
                        state.caveat_findings = outcome.open_findings
                        self._mark_verified(runtime)
                        caveat_outcome = replace(outcome, resolutions=resolutions, completed_with_caveats=True)
                        state.last_outcome = caveat_outcome
                        return VerificationGateAction(
                            action="complete_with_caveats",
                            outcome=caveat_outcome,
                            open_findings=outcome.open_findings,
                            reason="max_rounds_exhausted",
                        )

                    # More rounds available (e.g. maxRounds > 2): hand new findings to implementer.
                    self._register_open_findings(runtime, outcome, state)
                    return VerificationGateAction(
                        action="continue",
                        system_message=format_findings_for_implementer(outcome, max_rounds=max_rounds),
                        outcome=outcome,
                        open_findings=outcome.open_findings,
                        reason="reverification_failed",
                    )

                # maxRounds already reached (e.g. maxRounds=1) and implementer marked fixed without another verification round.
                caveats = tuple(dict.fromkeys((*confirmed_findings, *fixed_findings)))
                state.independent_settled = True
                state.completed_with_caveats = True
                state.caveat_findings = caveats
                self._mark_verified(runtime)
                final_outcome = (
                    replace(state.last_outcome, open_findings=caveats, resolutions=resolutions, completed_with_caveats=True)
                    if state.last_outcome is not None
                    else None
                )
                state.last_outcome = final_outcome
                return VerificationGateAction(
                    action="complete_with_caveats",
                    outcome=final_outcome,
                    open_findings=caveats,
                    reason="max_rounds_exhausted",
                )

            if confirmed_findings:
                state.independent_settled = True
                state.completed_with_caveats = True
                state.caveat_findings = confirmed_findings
                self._mark_verified(runtime)
                final_outcome = (
                    replace(state.last_outcome, open_findings=confirmed_findings, resolutions=resolutions, completed_with_caveats=True)
                    if state.last_outcome is not None
                    else None
                )
                state.last_outcome = final_outcome
                return VerificationGateAction(
                    action="complete_with_caveats",
                    outcome=final_outcome,
                    open_findings=confirmed_findings,
                    reason="confirmed_unfixed_findings",
                )

            # All blocking findings were rejected with valid reasons.
            state.independent_settled = True
            self._mark_verified(runtime)
            final_outcome = (
                replace(state.last_outcome, open_findings=(), resolutions=resolutions, completed_with_caveats=False)
                if state.last_outcome is not None
                else None
            )
            state.last_outcome = final_outcome
            return VerificationGateAction(
                action="complete",
                outcome=final_outcome,
                reason="all_findings_rejected",
            )

        # Phase 2: Initial gate evaluation for this turn.
        if not allow_continuation:
            return VerificationGateAction(action="complete", reason="budget_exhausted")

        if state.independent_settled and not self._has_verifiable_changes(turn, runtime):
            return VerificationGateAction(action="complete", outcome=state.last_outcome, reason="already_verified")

        decision = self.decide(turn, runtime)
        state.last_decision = decision

        if decision.kind == "skip":
            return VerificationGateAction(action="complete", reason=decision.reason)

        if decision.kind == "self_verify":
            return VerificationGateAction(action="self_verify", reason=decision.reason)

        # decision.kind == "independent" (replaces self-verify on this turn — never runs both)
        assert decision.choice is not None
        outcome = self.run_independent(
            turn,
            decision.summary,
            decision.choice,
            trigger_reason=decision.reason,
            round_num=1,
            runtime=runtime,
        )
        state.round_num = 1
        state.review_ids = list(outcome.review_ids)
        state.last_summary = decision.summary
        state.last_choice = decision.choice
        state.trigger_reason = decision.reason
        state.last_outcome = outcome

        if outcome.verdict == "skipped":
            if is_self_verify_enabled(settings) and self._can_self_verify(runtime):
                return VerificationGateAction(
                    action="self_verify",
                    outcome=outcome,
                    reason="verifier_skipped_fallback_self_verify",
                )
            state.independent_settled = True
            self._mark_verified(runtime)
            return VerificationGateAction(action="complete", outcome=outcome, reason="verifier_skipped")

        if not outcome.open_findings:
            state.independent_settled = True
            self._mark_verified(runtime)
            return VerificationGateAction(action="complete", outcome=outcome, reason="independent_passed")

        # Proven blocker/major findings exist: hand them to the implementer with mandatory resolve_findings.
        self._register_open_findings(runtime, outcome, state)
        return VerificationGateAction(
            action="continue",
            system_message=format_findings_for_implementer(outcome, max_rounds=max_rounds),
            outcome=outcome,
            open_findings=outcome.open_findings,
            reason="blocking_findings_open",
        )


__all__ = [
    "GateActionKind",
    "GateDecision",
    "GateKind",
    "OutcomeVerdict",
    "TurnGateState",
    "VerificationGate",
    "VerificationGateAction",
    "VerificationOutcome",
    "format_caveats_summary",
    "format_findings_for_implementer",
    "format_unresolved_reminder",
    "is_include_subagents_enabled",
    "is_self_verify_enabled",
    "read_gate_setting",
    "should_include_subagent_in_gate",
]
