"""WS-C7: the verifier (K17), change-scope (K18), reviewer-picking (K21) and quota (K22) adapters, with fake services of A, B and E."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.verifier_policy import AvailableVerifierModel
from agentos.pipelines.adapters.catalog import FavoritesModelCatalog, ModelRef, SessionChangeScope, SessionQuota
from agentos.pipelines.adapters.verifier import VERIFIER_UNAVAILABLE, SessionVerifier
from agentos.pipelines.ports import VerifierRequest

TURN = {"turn_id": "t1", "conversation_id": "c1", "user_id": "u1", "provider": "anthropic", "model_id": "claude-opus-5-5", "agent_id": "main"}


# ===================================================================== K17 verifier
class FakeSession:
    def __init__(self, outcome: ToolOutcome | Exception) -> None:
        self.turn = dict(TURN)
        self.outcome = outcome
        self.children: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.recorded: list[tuple[Any, str, dict[str, Any]]] = []
        self.effective_settings = None

    def run_child(self, *args: Any, **kwargs: Any) -> ToolOutcome:
        self.children.append((args, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def _record(self, event_type: Any, summary: str, payload: dict[str, Any] | None = None, **_: Any) -> None:
        self.recorded.append((event_type, summary, dict(payload or {})))


def _report(verdict: str, findings: list[dict[str, Any]] | None = None, **extra: Any) -> ToolOutcome:
    payload = {
        "agent_id": "agent:verifier-1",
        "usage": {"input_tokens": 40, "output_tokens": 10},
        "verification_report": {"verdict": verdict, "checked": [], "findings": findings or [], "unverified": []},
    }
    return ToolOutcome("succeeded", "verifier", f"veredito {verdict}", payload, **extra)


def _verifier(session: FakeSession, *, models: list[tuple[str, str]] | None = None, settings: Any = None, progress: Any = None) -> SessionVerifier:
    available = [AvailableVerifierModel(provider=p, model=m, configured=True, is_favorite=True) for p, m in (models if models is not None else [("openai-codex", "gpt-6.1-sol")])]
    return SessionVerifier(
        session=session, turn=session.turn, settings=settings, available=lambda: list(available), quota_service=None, child_progress=progress,
    )


def _request(**overrides: Any) -> VerifierRequest:
    values: dict[str, Any] = dict(policy="cross_family", explicit=None, effort="high", pipeline_run_id="run-1", step_id="verificar", scope_files=("src/a.py",))
    values.update(overrides)
    return VerifierRequest(**values)


def test_the_verifier_gate_runs_a_cross_family_review_with_the_pipeline_scope_and_reason() -> None:
    finding = {"id": "F1", "severity": "blocker", "category": "test", "claim": "pytest falhou",
               "evidence": {"kind": "failing_test", "command": "pytest", "exit_code": 1}}
    session = FakeSession(_report("fail", [finding]))
    result = _verifier(session, progress=lambda agent_id: {"input_tokens": 120, "output_tokens": 30, "cost_usd": 0.25}).review(_request(), threading.Event())
    assert result.verdict == "fail" and result.reviewer == "openai-codex/gpt-6.1-sol" and result.fallback_step == 1
    assert [row["id"] for row in result.findings] == ["F1"] and result.findings[0]["severity"] == "blocker"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (120, 30, 0.25)  # the child's subtree, as the session metered it
    args, kwargs = session.children[0]
    assert args[0] == "verifier" and kwargs["provider"] == "openai-codex" and kwargs["model_id"] == "gpt-6.1-sol" and kwargs["effort"] == "high"
    assert "src/a.py" in args[1]  # the prompt lists the files the pipeline changed
    selected = [item for item in session.recorded if item[0].value == "verifier.model_selected"]
    assert selected and selected[0][2]["chosen"]["model"] == "gpt-6.1-sol"  # the choice is visible before the cost
    assert any(item[0].value == "verifier.started" and item[2]["trigger_reason"] == "pipeline" for item in session.recorded)


def test_a_passing_review_returns_pass_without_findings() -> None:
    result = _verifier(FakeSession(_report("pass"))).review(_request(), threading.Event())
    assert result.verdict == "pass" and result.findings == () and result.error_code is None
    assert (result.input_tokens, result.output_tokens) == (40, 10)  # no tracker: the child's own usage payload


def test_without_any_reviewer_the_gate_reports_unavailable_never_a_silent_pass() -> None:
    session = FakeSession(_report("pass"))
    verifier = _verifier(session, models=[])  # nobody to review, not even the author
    verifier._turn = {**TURN, "provider": "", "model_id": ""}  # noqa: SLF001
    result = verifier.review(_request(), threading.Event())
    assert result.verdict == "inconclusive" and result.error_code == VERIFIER_UNAVAILABLE and session.children == []


def test_a_skipped_review_is_unavailable_and_an_unknown_policy_is_reported() -> None:
    skipped = _verifier(FakeSession(RuntimeError("child pool is gone"))).review(_request(), threading.Event())
    assert skipped.verdict == "inconclusive" and skipped.error_code == VERIFIER_UNAVAILABLE
    bad_policy = _verifier(FakeSession(_report("pass"))).review(_request(policy="whatever"), threading.Event())
    assert bad_policy.error_code == VERIFIER_UNAVAILABLE and "whatever" in bad_policy.findings[0]["claim"]


def test_an_explicit_model_in_the_yaml_wins_over_the_policy_and_a_cancelled_gate_runs_nothing() -> None:
    session = FakeSession(_report("pass"))
    models = [("openai-codex", "gpt-6.1-sol"), ("google-antigravity", "gemini-3.8-flash-high")]
    result = _verifier(session, models=models).review(
        _request(policy="cross_family", explicit=("google-antigravity", "gemini-3.8-flash-high"), effort="low"), threading.Event()
    )
    assert result.reviewer == "google-antigravity/gemini-3.8-flash-high"
    assert session.children[0][1]["provider"] == "google-antigravity"
    stop = threading.Event()
    stop.set()
    other = FakeSession(_report("pass"))
    cancelled = _verifier(other).review(_request(), stop)
    assert cancelled.error_code == "VERIFIER_CANCELLED" and other.children == []


def test_the_verifier_gate_never_depends_on_the_automatic_switches(tmp_path) -> None:
    """G19: ``orchestration.verifier.enabled`` and ``selfVerify.enabled`` are off, and the gate still reviews."""
    from agentos.settings.loader import EffectiveSettings, SettingsLayer

    data = {"orchestration": {"verifier": {"enabled": False}, "selfVerify": {"enabled": False}}}
    settings = EffectiveSettings(data=data, provenance={}, layers=(SettingsLayer("user", None, data),), errors=(), trusted=True)
    session = FakeSession(_report("pass"))
    result = _verifier(session, settings=settings).review(_request(), threading.Event())
    assert result.verdict == "pass" and len(session.children) == 1


def test_the_engine_gate_uses_the_adapter_end_to_end_for_a_failing_review() -> None:
    """The adapter's rows are what ``pass_on`` and ``findings`` of the engine consume (blocker/major only)."""
    finding = {"id": "F2", "severity": "major", "category": "bug", "claim": "off by one", "evidence": {"kind": "failing_test", "command": "pytest", "exit_code": 1}}
    unproven = {"id": "F3", "severity": "unproven", "category": "style", "claim": "talvez", "evidence": None}
    session = FakeSession(_report("fail", [finding, unproven]))
    result = _verifier(session).review(_request(), threading.Event())
    assert {row["id"] for row in result.findings} == {"F2", "F3"}
    from agentos.pipelines.gates import _is_proven

    assert [row["id"] for row in result.findings if _is_proven(row)] == ["F2"]  # the evaluator keeps only these


# ======================================================================= K18 / K22 / K21
def test_the_change_scope_is_the_toolsets_changed_paths_normalized() -> None:
    assert SessionChangeScope(lambda: ("src\\b.py", "src/a.py", "src/a.py", " ")).changed_files() == ("src/a.py", "src/b.py")
    assert SessionChangeScope(None).changed_files() == ()

    def broken():  # noqa: ANN202
        raise RuntimeError("no toolset")

    assert SessionChangeScope(broken).changed_files() == ()


@dataclass
class _Decision:
    allowed: bool = True
    should_warn: bool = False


class _Policy:
    def __init__(self, blocked: set[str], warned: set[str], broken: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.blocked, self.warned, self.broken = blocked, warned, broken
        self.asked: list[tuple[str, str]] = []

    def check_background(self, user_id: str, provider: str, *, settings: Any = None) -> _Decision:
        self.asked.append(("check", provider))
        if provider in self.broken:
            raise RuntimeError("quota service down")
        return _Decision(allowed=provider not in self.blocked)

    def warn(self, user_id: str, provider: str, *, settings: Any = None) -> _Decision:
        self.asked.append(("warn", provider))
        return _Decision(should_warn=provider in self.warned)


def test_the_quota_port_splits_providers_into_blocked_and_warned() -> None:
    policy = _Policy(blocked={"openai-codex"}, warned={"anthropic"}, broken={"google-antigravity"})
    assessment = SessionQuota(policy, user_id="u1", settings=object()).assess(["openai-codex", "anthropic", "google-antigravity", "anthropic", "ollama"])
    assert assessment.blocked == ("openai-codex",) and assessment.warn == ("anthropic",)  # a reading that fails is neither
    assert policy.asked.count(("check", "anthropic")) == 1  # a provider asked twice is assessed once


def test_the_quota_port_is_absent_without_a_quota_service() -> None:
    class NoQuota:
        turn = dict(TURN)
        effective_settings = object()

        def child_quota_service(self) -> None:
            return None

    assert SessionQuota.for_session(NoQuota()) is None


FAVORITES = {
    "anthropic": ("claude-opus-5-5", "claude-sonnet-5-5"),
    "openai-codex": ("gpt-6.1-sol", "gpt-6-luna"),
    "google-antigravity": ("gemini-3.8-flash-high",),
    "mystery": ("unknown-model-x",),
}


def test_pick_reviewers_prefers_other_families_and_never_a_tier_zero_model() -> None:
    catalog = FavoritesModelCatalog(FAVORITES)
    picked = catalog.pick_reviewers(3, ModelRef("anthropic", "claude-opus-5-5"))
    assert len(picked) == 3 and ModelRef("mystery", "unknown-model-x") not in picked
    assert [ref.provider for ref in picked[:2]] == ["google-antigravity", "openai-codex"] or {ref.provider for ref in picked[:2]} == {"google-antigravity", "openai-codex"}
    assert picked[2].provider == "anthropic"  # only two other families exist: the author's own family fills the third seat
    assert ModelRef("anthropic", "claude-opus-5-5") not in picked  # but never the author's exact model while another exists
    assert len({family for family in (ref.provider for ref in picked)}) == 3  # distinct families


def test_pick_reviewers_without_distinct_families_may_repeat_a_family_and_skips_rejected_quota() -> None:
    rejected = lambda provider, model: (provider, model) == ("openai-codex", "gpt-6.1-sol")  # noqa: E731
    catalog = FavoritesModelCatalog(FAVORITES, rejected=rejected)
    picked = catalog.pick_reviewers(5, ModelRef("anthropic", "claude-opus-5-5"), distinct_families=False)
    assert ModelRef("openai-codex", "gpt-6.1-sol") not in picked and ModelRef("openai-codex", "gpt-6-luna") in picked
    assert picked[-1].provider == "anthropic"  # the author's own family comes last
    assert ModelRef("anthropic", "claude-opus-5-5") == picked[-1] or ModelRef("anthropic", "claude-opus-5-5") not in picked[:-1]
    assert FavoritesModelCatalog(FAVORITES).pick_reviewers(0, ModelRef("anthropic", "claude-opus-5-5")) == []
    only_author = FavoritesModelCatalog({"anthropic": ("claude-opus-5-5",)})
    assert only_author.pick_reviewers(2, ModelRef("anthropic", "claude-opus-5-5")) == [ModelRef("anthropic", "claude-opus-5-5")]


@pytest.mark.parametrize("count", [1, 2, 3])
def test_pick_reviewers_is_deterministic(count: int) -> None:
    author = ModelRef("anthropic", "claude-opus-5-5")
    first = FavoritesModelCatalog(FAVORITES).pick_reviewers(count, author)
    assert first == FavoritesModelCatalog(dict(reversed(list(FAVORITES.items())))).pick_reviewers(count, author)


# ---- WS-C7 review F4: cancelling the run (or its deadline) must reach the verifier child
def test_the_verifier_child_watches_the_runs_cancel_event_and_carries_the_run_id() -> None:
    session = FakeSession(_report("pass"))
    cancel = threading.Event()
    _verifier(session).review(_request(pipeline_run_id="run-9"), cancel)
    kwargs = session.children[0][1]
    assert kwargs.get("hook_cancel") is cancel and kwargs.get("pipeline_run_id") == "run-9"
