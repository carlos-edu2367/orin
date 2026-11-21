"""Unit tests for deterministic ConflictResolver rules (WS-D3 / D-RF6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentos.memory.conflict import (
    ConflictResolver,
    ExistingMemoryCandidate,
    compute_jaccard,
    normalize,
    text_hash,
)
from agentos.memory.conflict_adjudicator import ConflictAdjudicator
from agentos.memory.models_write import (
    MemoryWriteCandidate,
    MemoryWriteContext,
    compute_text_hash,
    normalize_memory_text,
)


def _cand(
    fact: str,
    *,
    kind: str = "operational",
    confidence: float = 1.0,
    supersedes_id: str | None = None,
) -> MemoryWriteCandidate:
    return MemoryWriteCandidate(
        fact=fact,
        text_hash=compute_text_hash(fact),
        kind=kind,
        scope_type="project",
        scope_id="proj:orin",
        project_id="proj:orin",
        confidence=confidence,
        supersedes_id=supersedes_id,
    )


def _existing(
    memory_id: str,
    fact: str,
    *,
    source_kind: str = "user",
    trust: int = 2,
    confidence: float = 1.0,
    semantic_similarity: float | None = None,
    status: str = "active",
    kind: str = "operational",
) -> ExistingMemoryCandidate:
    return ExistingMemoryCandidate(
        memory_id=memory_id,
        fact=fact,
        text_hash=compute_text_hash(fact),
        kind=kind,
        scope_type="project",
        scope_id="proj:orin",
        project_id="proj:orin",
        source_kind=source_kind,
        trust=trust,
        confidence=confidence,
        semantic_similarity=semantic_similarity,
        status=status,
    )


@dataclass
class _FakeEvent:
    kind: str
    text: str


class _CountingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def stream(self, request: dict[str, Any]):
        self.calls += 1
        yield _FakeEvent(kind="text", text=self.response)


def test_normalize_and_text_hash_reuse_models_write() -> None:
    assert normalize("  O   projeto usa pnpm...  ") == normalize_memory_text("o projeto usa pnpm")
    assert text_hash("O projeto usa pnpm!") == compute_text_hash("o projeto usa pnpm")


def test_rule1_exact_normalized_hash_returns_noop() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_1", "O projeto usa pnpm.", source_kind="user", trust=2)
    ctx = MemoryWriteContext(source_kind="agent", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("  o   projeto usa pnpm! "), ctx)

    assert decision.action == "noop"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert decision.reason == "exact_hash"


def test_rule2_agent_contradicting_user_with_short_replacement_returns_conflict_criterion4() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_user", "o projeto usa pnpm", source_kind="user", trust=2)
    ctx = MemoryWriteContext(source_kind="agent", actor="agent:dev", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("o projeto usa npm"), ctx)

    assert decision.action == "conflict"
    assert decision.target_id == "mem_user"
    assert decision.status == "proposed"
    assert decision.reason == "agent_vs_user"


def test_rule2_agent_contradicting_user_with_polarity_inversion_returns_conflict_criterion4() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_user", "o projeto usa pnpm", source_kind="user", trust=2)
    ctx = MemoryWriteContext(source_kind="agent", actor="agent:dev", trust=1, existing_candidates=(existing,))

    for contradicting in ("o projeto não usa pnpm", "não usa pnpm"):
        decision = resolver.resolve(_cand(contradicting), ctx)
        assert decision.action == "conflict", contradicting
        assert decision.target_id == "mem_user"
        assert decision.status == "proposed"
        assert decision.reason == "agent_vs_user"


def test_rule2_agent_high_semantic_similarity_against_user_cannot_supersede_user() -> None:
    resolver = ConflictResolver()
    existing = _existing(
        "mem_user",
        "Usamos PostgreSQL como banco principal em produção.",
        source_kind="user",
        trust=2,
        semantic_similarity=0.99,
    )
    ctx = MemoryWriteContext(source_kind="agent", actor="agent:dev", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("Usamos SQLite como banco principal em produção."), ctx)

    assert decision.action == "conflict"
    assert decision.target_id == "mem_user"
    assert decision.status == "proposed"
    assert decision.reason == "agent_vs_user"


def test_rule3_short_portuguese_replacement_same_origin_supersedes_criterion3() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_1", "o projeto usa pnpm", source_kind="user", trust=2, confidence=0.9)
    ctx = MemoryWriteContext(source_kind="user", actor="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("o projeto usa npm", confidence=0.9), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert decision.reason == "near_duplicate_or_replacement"


def test_rule3_semantic_similarity_ge_098_supersedes() -> None:
    resolver = ConflictResolver()
    existing = _existing(
        "mem_1",
        "O build do frontend roda com Vite.",
        source_kind="agent",
        trust=1,
        semantic_similarity=0.985,
    )
    ctx = MemoryWriteContext(source_kind="agent", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("O empacotamento do frontend utiliza Vite."), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert decision.reason == "near_duplicate_or_replacement"


def test_rule3_jaccard_ge_088_without_polarity_inversion_supersedes() -> None:
    resolver = ConflictResolver()
    old_fact = (
        "No repositorio Orin executamos sempre a suite completa de testes unitarios "
        "com pytest passando a flag no cacheprovider no ambiente Windows local"
    )
    new_fact = (
        "No repositorio Orin executamos sempre a suite completa de testes unitarios "
        "com pytest passando a flag no cacheprovider no ambiente Windows"
    )
    assert compute_jaccard(old_fact, new_fact) >= 0.88
    existing = _existing("mem_1", old_fact, source_kind="user", trust=2)
    ctx = MemoryWriteContext(source_kind="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand(new_fact), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert decision.reason == "near_duplicate_or_replacement"


def test_rule4_polarity_inversion_same_origin_and_higher_or_equal_confidence_supersedes() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_1", "o projeto usa pnpm", source_kind="user", trust=2, confidence=0.8)
    ctx = MemoryWriteContext(source_kind="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("o projeto não usa pnpm", confidence=0.9), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert decision.reason == "polarity_supersede"


def test_rule4_polarity_inversion_lower_confidence_falls_through_to_conflict() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_1", "o projeto usa pnpm", source_kind="user", trust=2, confidence=0.95)
    ctx = MemoryWriteContext(source_kind="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("o projeto não usa pnpm", confidence=0.7), ctx)

    assert decision.action == "conflict"
    assert decision.target_id == "mem_1"
    assert decision.status == "proposed"


def test_rule4_user_can_supersede_agent_with_polarity_inversion() -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_agent", "o projeto usa pnpm", source_kind="agent", trust=1, confidence=0.9)
    ctx = MemoryWriteContext(source_kind="user", actor="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("o projeto não usa pnpm", confidence=0.9), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_agent"
    assert decision.status == "active"
    assert decision.reason == "polarity_supersede"


def test_rule5_ambiguous_band_without_llm_flag_returns_conflict_and_never_calls_provider() -> None:
    provider = _CountingProvider('{"decision": "supersede", "target_index": 0}')
    adjudicator = ConflictAdjudicator(provider=provider, provider_name="test", model_id="small")
    resolver = ConflictResolver(adjudicator=adjudicator, llm_adjudication_enabled=False)

    existing = _existing(
        "mem_1",
        "Deploy principal usa containers Docker no servidor Linux.",
        source_kind="agent",
        trust=1,
        semantic_similarity=0.91,
    )
    ctx = MemoryWriteContext(source_kind="agent", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("Publicação do serviço usa imagem container em staging."), ctx)

    assert decision.action == "conflict"
    assert decision.target_id == "mem_1"
    assert decision.status == "proposed"
    assert decision.reason == "ambiguous_conflict"
    assert provider.calls == 0


def test_rule5_ambiguous_band_with_llm_flag_enabled_delegates_to_adjudicator() -> None:
    provider = _CountingProvider('{"decision": "supersede", "target_index": 0}')
    adjudicator = ConflictAdjudicator(provider=provider, provider_name="test", model_id="small")
    resolver = ConflictResolver(adjudicator=adjudicator, llm_adjudication_enabled=True)

    existing = _existing(
        "mem_1",
        "Deploy principal usa containers Docker no servidor Linux.",
        source_kind="agent",
        trust=1,
        semantic_similarity=0.91,
    )
    ctx = MemoryWriteContext(source_kind="agent", trust=1, existing_candidates=(existing,))

    decision = resolver.resolve(_cand("Publicação do serviço usa imagem container em staging."), ctx)

    assert decision.action == "supersede"
    assert decision.target_id == "mem_1"
    assert decision.status == "active"
    assert provider.calls == 1


def test_rule6_distinct_facts_and_complementary_sentences_return_add() -> None:
    resolver = ConflictResolver()
    existing_short = _existing("mem_1", "o projeto usa pnpm", source_kind="user", trust=2)
    existing_comp = _existing(
        "mem_2",
        "modulo autenticacao backend valida sessao ativa usando cookies assinados",
        source_kind="user",
        trust=2,
    )
    ctx = MemoryWriteContext(
        source_kind="user",
        trust=2,
        existing_candidates=(existing_short, existing_comp),
    )

    d1 = resolver.resolve(_cand("nunca commitar credenciais no repositorio"), ctx)
    assert d1.action == "add"
    assert d1.status == "active"
    assert d1.reason == "distinct"

    d2 = resolver.resolve(
        _cand("modulo autenticacao backend valida sessao ativa gravando auditoria detalhada"),
        ctx,
    )
    assert d2.action == "add"
    assert d2.status == "active"
    assert d2.reason == "distinct"


@pytest.mark.parametrize(
    ("old_fact", "new_fact", "expected_action"),
    [
        ("o backend roda em python", "o backend roda em rust", "supersede"),
        ("o banco principal é postgres", "o banco principal é sqlite", "supersede"),
        ("a porta do servidor é 8080", "a porta do servidor é 3000", "supersede"),
        ("o projeto usa pnpm", "o projeto tem cobertura de testes", "add"),
        ("Carlos prefere café", "Maria prefere café", "add"),
    ],
)
def test_portuguese_short_sentences_matrix(old_fact: str, new_fact: str, expected_action: str) -> None:
    resolver = ConflictResolver()
    existing = _existing("mem_1", old_fact, source_kind="user", trust=2)
    ctx = MemoryWriteContext(source_kind="user", trust=2, existing_candidates=(existing,))

    decision = resolver.resolve(_cand(new_fact), ctx)
    assert decision.action == expected_action
