"""Unit tests for frozen per-agent memory summary generation (WS-D5 / D-RF9)."""

from __future__ import annotations

from agentos.memory.agent_summary import DEFAULT_AGENT_SUMMARY_MAX_CHARS, freeze_summary


def test_freeze_summary_returns_empty_for_no_memories_or_non_positive_budget() -> None:
    assert freeze_summary([], "revisor-sql") == ""
    assert (
        freeze_summary(
            [{"memory_id": "m1", "fact": "Usar indices parciais.", "status": "active"}],
            "revisor-sql",
            max_chars=0,
        )
        == ""
    )
    assert (
        freeze_summary(
            [{"memory_id": "m1", "fact": "Usar indices parciais.", "status": "active"}],
            "revisor-sql",
            max_chars=-50,
        )
        == ""
    )


def test_freeze_summary_ignores_non_active_and_superseded_memories() -> None:
    items = [
        {"memory_id": "m1", "fact": "Proposta pendente.", "status": "proposed"},
        {"memory_id": "m2", "fact": "Quarentenada.", "status": "quarantined"},
        {"memory_id": "m3", "fact": "Superada.", "status": "active", "superseded_by": "m4"},
        {"memory_id": "m4", "fact": "Ativa valida.", "status": "active", "kind": "operational"},
    ]
    summary = freeze_summary(items, "revisor-sql", max_chars=4000)
    assert summary.startswith("## O que o agente revisor-sql aprendeu")
    assert "Ativa valida." in summary
    assert "Proposta pendente." not in summary
    assert "Quarentenada." not in summary
    assert "Superada." not in summary


def test_freeze_summary_is_deterministic_regardless_of_input_order() -> None:
    m1 = {"memory_id": "m1", "fact": "Preferir CTEs nomeadas.", "kind": "preference", "confidence": 0.9, "status": "active"}
    m2 = {"memory_id": "m2", "fact": "Tabela orders tem 50M linhas.", "kind": "fact", "confidence": 0.8, "status": "active"}
    m3 = {"memory_id": "m3", "fact": "Rodar EXPLAIN ANALYZE antes de migrar.", "kind": "operational", "confidence": 0.95, "status": "active"}

    s_a = freeze_summary([m1, m2, m3], "revisor-sql")
    s_b = freeze_summary([m3, m2, m1], "revisor-sql")
    s_c = freeze_summary([m2, m1, m3], "revisor-sql")

    assert s_a == s_b == s_c
    assert s_a.index("Preferir CTEs nomeadas.") < s_a.index("Tabela orders tem 50M linhas.")


def test_freeze_summary_enforces_strict_max_chars_cap() -> None:
    memories = [
        {
            "memory_id": f"m{i:03d}",
            "fact": f"Regra SQL numero {i:03d}: verificar planos de execucao em tabelas particionadas de faturamento.",
            "kind": "operational",
            "confidence": 0.9,
            "status": "active",
        }
        for i in range(80)
    ]
    for budget in (120, 400, 1000, DEFAULT_AGENT_SUMMARY_MAX_CHARS):
        summary = freeze_summary(memories, "revisor-sql", max_chars=budget)
        assert len(summary) <= budget
        assert summary.startswith("## O que o agente revisor-sql aprendeu")


def test_freeze_summary_is_immutable_snapshot_after_generation() -> None:
    source = [
        {"memory_id": "m1", "fact": "Usar indice GIN em colunas JSONB.", "status": "active"},
    ]
    frozen = freeze_summary(source, "revisor-sql", max_chars=4000)
    # Simulating writes that arrive mid-run must not mutate the already frozen prompt section.
    source.append({"memory_id": "m2", "fact": "Nova regra gravada no meio do subagente.", "status": "active"})
    assert "Usar indice GIN em colunas JSONB." in frozen
    assert "Nova regra gravada no meio do subagente." not in frozen
