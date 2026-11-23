"""``VerifierPort`` over A's pieces (plan K17): ``select_verifier`` + ``VerificationGate.run_independent``.

A pipeline ``verifier`` gate is an explicit request in the YAML, so it never depends on
``orchestration.verifier.enabled`` or ``orchestration.selfVerify.enabled`` (both off by default, G19): the model is
chosen here with the policy of the gate, and ``run_independent`` runs it with ``trigger_reason="pipeline"`` and the
files the pipeline changed. Review rows, findings and the ``verifier.*`` events are A's own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agentos.agentic.verification_gate import VerificationGate, read_gate_setting
from agentos.agentic.verifier_policy import AuthorModel, AvailableVerifierModel, select_verifier

from ..ports import VerifierRequest, VerifierResult

logger = logging.getLogger("agentos.pipelines.adapters.verifier")

VERIFIER_UNAVAILABLE = "GATE_VERIFIER_UNAVAILABLE"
VERIFIER_CANCELLED = "VERIFIER_CANCELLED"


class SessionVerifier:
    """One review per call. ``child_progress`` reports the verifier child's subtree usage (tokens, cost)."""

    def __init__(
        self,
        *,
        session: Any,
        turn: Mapping[str, Any],
        settings: Any,
        available: Callable[[], list[AvailableVerifierModel]],
        quota_service: Any = None,
        child_progress: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._session = session
        self._turn = turn
        self._settings = settings
        self._available = available
        self._quota_service = quota_service
        self._child_progress = child_progress

    @classmethod
    def for_session(cls, session: Any) -> SessionVerifier:
        turn = session.turn
        _agents, favorites = session.pipeline_catalog()

        def available() -> list[AvailableVerifierModel]:
            seen: set[tuple[str, str]] = set()
            models: list[AvailableVerifierModel] = []
            for provider, ids in favorites.items():
                for model in ids:
                    if (provider, model) not in seen:
                        seen.add((provider, model))
                        models.append(AvailableVerifierModel(provider=provider, model=model, configured=True, is_favorite=True))
            own = (str(turn.get("provider") or ""), str(turn.get("model_id") or ""))
            if all(own) and own not in seen:
                models.append(AvailableVerifierModel(provider=own[0], model=own[1], configured=True, is_favorite=True))
            return models

        return cls(
            session=session, turn=turn, settings=getattr(session, "effective_settings", None), available=available,
            quota_service=session.child_quota_service(), child_progress=session.child_progress,
        )

    # ------------------------------------------------------------------ choice
    def _choose(self, request: VerifierRequest) -> Any:
        settings, turn = self._settings, self._turn
        explicit = None
        if request.explicit and all(request.explicit):
            explicit = {"provider": request.explicit[0], "model": request.explicit[1], "effort": request.effort}
        policy = "explicit" if explicit else str(request.policy or read_gate_setting(settings, "orchestration.verifier.modelPolicy", "cross_family"))
        if policy == "explicit" and explicit is None:
            explicit = read_gate_setting(settings, "orchestration.verifier.explicit", None)
        tiers = read_gate_setting(settings, "orchestration.modelTiers", None)
        user_id = str(turn.get("user_id") or "")
        quota: Any = None
        status_for = getattr(self._quota_service, "status_for", None)
        if callable(status_for):

            def quota(provider: str, model: str) -> Any:  # noqa: F811 - the lookup select_verifier calls per candidate
                try:
                    return status_for(user_id, provider, model_id=model)
                except TypeError:
                    return status_for(provider, model)

        return select_verifier(
            author=AuthorModel(str(turn.get("provider") or ""), str(turn.get("model_id") or ""), str(turn.get("effort") or "") or None),
            available=self._available(),
            policy=policy,
            explicit=explicit,
            quota=quota,
            tiers=tiers if isinstance(tiers, Mapping) else None,
            requested_effort=str(request.effort or read_gate_setting(settings, "orchestration.verifier.effort", "high") or "high"),
            quota_policy_enabled=bool(read_gate_setting(settings, "quota.policy.enabled", False)),
            quota_block_percent=int(read_gate_setting(settings, "quota.policy.blockBackgroundPercent", 90)),
            user_id=user_id,
        )

    # ------------------------------------------------------------------ review
    def review(self, request: VerifierRequest, cancel: threading.Event) -> VerifierResult:
        if cancel.is_set():
            return VerifierResult("inconclusive", error_code=VERIFIER_CANCELLED)
        try:
            choice = self._choose(request)
        except ValueError as exc:  # an unknown policy name
            return VerifierResult("inconclusive", error_code=VERIFIER_UNAVAILABLE, findings=({"kind": "verifier_unavailable", "claim": str(exc)},))
        if not choice.available:
            return VerifierResult(
                "inconclusive", error_code=VERIFIER_UNAVAILABLE, fallback_step=int(choice.fallback_step),
                findings=({"kind": "verifier_unavailable", "claim": f"nenhum modelo disponível para revisar ({choice.reason})"},),
            )
        self._session._record(  # the choice is visible before the cost is incurred (spec 5.3, P5)
            _model_selected(), f"Modelo do verificador selecionado: {choice.model} ({choice.provider})", choice.to_event_payload(),
        )
        seen: dict[str, Any] = {}

        def run_child(*args: Any, **kwargs: Any) -> Any:
            # The child watches the run's cancel event (cancel, deadline, pause) and belongs to the run like a step's child.
            kwargs.setdefault("hook_cancel", cancel)
            if request.pipeline_run_id:
                kwargs.setdefault("pipeline_run_id", request.pipeline_run_id)
            outcome = self._session.run_child(*args, **kwargs)
            seen["outcome"] = outcome
            return outcome

        gate = VerificationGate(
            session=self._session, settings=self._settings, quota_service=self._quota_service, run_child=run_child,
        )
        outcome = gate.run_independent(self._turn, None, choice, trigger_reason=request.trigger_reason, scope_files=request.scope_files)
        return self._result(outcome, choice, seen.get("outcome"))

    def _result(self, outcome: Any, choice: Any, child: Any) -> VerifierResult:
        tokens_in, tokens_out, cost = self._usage(child)
        reviewer = f"{choice.provider}/{choice.model}"
        review_id = outcome.review_ids[-1] if outcome.review_ids else None
        if outcome.verdict not in {"pass", "fail", "inconclusive"}:  # "skipped": the verifier child never ran to a verdict
            return VerifierResult(
                "inconclusive", error_code=VERIFIER_UNAVAILABLE, review_id=review_id, reviewer=reviewer,
                fallback_step=int(choice.fallback_step), input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
                findings=({"kind": "verifier_unavailable", "claim": f"a verificação foi pulada ({outcome.skipped_reason or 'sem motivo'})"},),
            )
        return VerifierResult(
            outcome.verdict, findings=tuple(item.to_dict() for item in outcome.findings), review_id=review_id, reviewer=reviewer,
            fallback_step=int(choice.fallback_step), input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost,
        )

    def _usage(self, child: Any) -> tuple[int, int, float | None]:
        payload = dict(getattr(child, "payload", None) or {})
        agent_id = str(payload.get("agent_id") or "")
        if agent_id and self._child_progress is not None:
            try:
                snapshot = self._child_progress(agent_id)
                spent_in, spent_out = int(snapshot.get("input_tokens") or 0), int(snapshot.get("output_tokens") or 0)
                if spent_in or spent_out:
                    cost = snapshot.get("cost_usd")
                    known = snapshot.get("cost_known")
                    return spent_in, spent_out, float(cost) if (known if known is not None else cost) else None
            except Exception:  # noqa: BLE001
                logger.debug("could not read the verifier child's usage", exc_info=True)
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), None


def _model_selected() -> Any:
    from agentos.agentic.events import AgentActivityEventType

    return AgentActivityEventType.VERIFIER_MODEL_SELECTED


__all__ = ["SessionVerifier", "VERIFIER_CANCELLED", "VERIFIER_UNAVAILABLE"]
