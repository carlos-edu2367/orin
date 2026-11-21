"""Unit tests for optional small-model ConflictAdjudicator (WS-D3 / D-RF6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.memory.conflict import ExistingMemoryCandidate
from agentos.memory.conflict_adjudicator import ConflictAdjudicator, parse_adjudication_response
from agentos.memory.models_write import compute_text_hash


@dataclass
class _FakeEvent:
    kind: str
    text: str


class _StubProvider:
    def __init__(self, response: str | None = None, *, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any]):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            yield _FakeEvent(kind="text", text=self.response)


def _candidates() -> tuple[ExistingMemoryCandidate, ...]:
    return (
        ExistingMemoryCandidate(
            memory_id="mem_0",
            fact="O projeto usa pnpm no frontend.",
            text_hash=compute_text_hash("O projeto usa pnpm no frontend."),
            source_kind="agent",
            trust=1,
        ),
        ExistingMemoryCandidate(
            memory_id="mem_1",
            fact="Os scripts de CI rodam em Linux.",
            text_hash=compute_text_hash("Os scripts de CI rodam em Linux."),
            source_kind="agent",
            trust=1,
        ),
    )


def test_adjudicator_disabled_never_calls_provider_and_falls_back_to_conflict() -> None:
    provider = _StubProvider('{"decision": "supersede", "target_index": 0}')
    adjudicator = ConflictAdjudicator(provider=provider, provider_name="openai", model_id="gpt-mini")

    decision = adjudicator.adjudicate(
        "O projeto usa npm no frontend.",
        _candidates(),
        llm_adjudication_enabled=False,
    )

    assert len(provider.calls) == 0
    assert decision.action == "conflict"
    assert decision.target_id == "mem_0"
    assert decision.status == "proposed"


def test_adjudicator_valid_json_decisions_and_never_accepts_rewritten_text() -> None:
    # Even if a model tries to smuggle rewritten text in extra JSON keys, only decision+target_index are honored.
    provider = _StubProvider(
        '```json\n{"decision": "supersede", "target_index": 1, "fact": "TEXTO INVENTADO PELO MODELO"}\n```'
    )
    adjudicator = ConflictAdjudicator(provider=provider, provider_name="openai", model_id="gpt-mini")

    decision = adjudicator.adjudicate(
        "Os scripts de CI rodam em Ubuntu 24.04.",
        _candidates(),
        llm_adjudication_enabled=True,
    )

    assert len(provider.calls) == 1
    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert not hasattr(decision, "fact")
    assert "TEXTO INVENTADO" not in decision.reason


def test_adjudicator_valid_add_and_noop_decisions() -> None:
    add_provider = _StubProvider('{"decision": "add", "target_index": null}')
    add_adj = ConflictAdjudicator(provider=add_provider)
    d_add = add_adj.adjudicate("Novo fato independente.", _candidates(), llm_adjudication_enabled=True)
    assert d_add.action == "add"
    assert d_add.target_id is None
    assert d_add.status == "active"

    noop_provider = _StubProvider('{"decision": "noop", "target_index": 0}')
    noop_adj = ConflictAdjudicator(provider=noop_provider)
    d_noop = noop_adj.adjudicate("O projeto usa pnpm no frontend.", _candidates(), llm_adjudication_enabled=True)
    assert d_noop.action == "noop"
    assert d_noop.target_id == "mem_0"
    assert d_noop.status == "active"


def test_adjudicator_out_of_range_or_invalid_index_falls_back_to_conflict() -> None:
    for bad_payload in (
        '{"decision": "supersede", "target_index": 99}',
        '{"decision": "supersede", "target_index": -1}',
        '{"decision": "supersede", "target_index": "0"}',
        '{"decision": "supersede", "target_index": true}',
        '{"decision": "supersede"}',
        '{"decision": "delete_everything", "target_index": 0}',
        "not json at all",
    ):
        provider = _StubProvider(bad_payload)
        adjudicator = ConflictAdjudicator(provider=provider)
        decision = adjudicator.adjudicate(
            "O projeto usa npm no frontend.",
            _candidates(),
            llm_adjudication_enabled=True,
        )
        assert decision.action == "conflict", bad_payload
        assert decision.target_id == "mem_0"
        assert decision.status == "proposed"


def test_adjudicator_provider_exception_or_missing_provider_falls_back_to_conflict() -> None:
    failing_provider = _StubProvider(error=TimeoutError("model timeout"))
    adj_fail = ConflictAdjudicator(provider=failing_provider)
    d_fail = adj_fail.adjudicate("O projeto usa npm.", _candidates(), llm_adjudication_enabled=True)
    assert d_fail.action == "conflict"
    assert d_fail.target_id == "mem_0"
    assert d_fail.status == "proposed"

    adj_none = ConflictAdjudicator(provider=None)
    d_none = adj_none.adjudicate("O projeto usa npm.", _candidates(), llm_adjudication_enabled=True)
    assert d_none.action == "conflict"
    assert d_none.target_id == "mem_0"
    assert d_none.status == "proposed"


def test_adjudicator_still_protects_user_memory_from_agent_supersede() -> None:
    user_candidates = (
        ExistingMemoryCandidate(
            memory_id="mem_user",
            fact="O projeto usa pnpm.",
            text_hash=compute_text_hash("O projeto usa pnpm."),
            source_kind="user",
            trust=2,
        ),
    )
    provider = _StubProvider('{"decision": "supersede", "target_index": 0}')
    adjudicator = ConflictAdjudicator(provider=provider)

    decision = adjudicator.adjudicate(
        "O projeto usa npm.",
        user_candidates,
        llm_adjudication_enabled=True,
        candidate_source_kind="agent",
    )

    assert decision.action == "conflict"
    assert decision.target_id == "mem_user"
    assert decision.status == "proposed"
    assert decision.reason == "agent_vs_user"


def test_parse_adjudication_response_strict_validation() -> None:
    assert parse_adjudication_response('{"decision": "conflict", "target_index": 1}', 2) == ("conflict", 1)
    assert parse_adjudication_response('{"decision": "add", "target_index": null}', 2) == ("add", None)
    assert parse_adjudication_response('{"decision": "supersede", "target_index": 2}', 2) is None
    assert parse_adjudication_response('{"decision": "noop", "target_index": false}', 2) is None
