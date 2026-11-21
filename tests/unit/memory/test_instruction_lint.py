from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

from agentos.memory.instruction_lint import (
    INSTRUCTION_LINT_INTERVAL,
    InstructionLinter,
    InstructionLintState,
    LintContradiction,
    LintReport,
)
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.workers.scheduler import _maybe_run_instruction_lint


def _make_engine(tmp_path: Path, name: str = "lint.db"):
    engine = create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    upgrade(engine, "head")
    return engine


def test_real_case_orin_md_subagent_model_contradiction_cites_file_and_line(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    orin_md = workspace / "ORIN.md"
    orin_md.write_text(
        "# Diretrizes do projeto\n"
        "Sempre execute testes antes de abrir PR.\n"
        "Use claude-sonnet-4-6 em subagentes para tarefas de revisao.\n",
        encoding="utf-8",
    )

    engine = _make_engine(tmp_path)
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        saved = store.save(
            "Para subagentes de revisao usar sempre claude-sonnet-5-5.",
            ("subagentes",),
            kind="preference",
            source="user_explicit",
        )
        memory_id = str(saved["memory_id"])
        # Unrelated memory should not be sent or cause noise
        store.save(
            "Prefere cafe expresso sem acucar pela manha.",
            ("pessoal",),
            kind="preference",
            source="user_explicit",
        )

        captured_prompts: list[tuple[str, str]] = []

        def fake_caller(system_prompt: str, user_prompt: str) -> str:
            captured_prompts.append((system_prompt, user_prompt))
            return json.dumps(
                [
                    {
                        "memory_id": memory_id,
                        "file": "ORIN.md",
                        "line": 3,
                        "quoted_line": "Use claude-sonnet-4-6 em subagentes para tarefas de revisao.",
                        "reason": "ORIN.md exige claude-sonnet-4-6 em subagentes, mas a memoria pede claude-sonnet-5-5.",
                    }
                ]
            )

        linter = InstructionLinter(engine=engine, model_caller=fake_caller)
        report = linter.run("u1", workspace, dry_run=False)

        assert isinstance(report, LintReport)
        assert report.dry_run is False
        assert report.warning is None
        assert report.estimated_calls == 1
        assert report.memories_checked == 1
        assert "ORIN.md" in report.files_checked
        assert len(captured_prompts) == 1
        assert len(report.contradictions) == 1

        item = report.contradictions[0]
        assert isinstance(item, LintContradiction)
        assert item.memory_id == memory_id
        assert "claude-sonnet-5-5" in item.memory_fact
        assert item.file == "ORIN.md"
        assert item.line == 3
        assert "claude-sonnet-4-6" in item.quoted_line
        assert "claude-sonnet-4-6" in item.reason

        # Linter must never modify memories or instruction files
        assert orin_md.read_text(encoding="utf-8").splitlines()[2] == (
            "Use claude-sonnet-4-6 em subagentes para tarefas de revisao."
        )
        rows = store._all()
        assert len(rows) == 2
        assert all(r["status"] == "active" for r in rows)
    finally:
        engine.dispose()


def test_discards_hallucinated_quote_unknown_file_bad_line_and_unknown_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text(
        "Linha um do arquivo.\n"
        "Use claude-sonnet-4-6 em subagentes.\n",
        encoding="utf-8",
    )

    engine = _make_engine(tmp_path, "hallucination.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        saved = store.save(
            "Subagentes devem usar claude-sonnet-5-5.",
            kind="preference",
            source="user_explicit",
        )
        memory_id = str(saved["memory_id"])

        def hallucinatory_caller(_sys: str, _usr: str) -> str:
            return json.dumps(
                {
                    "contradictions": [
                        {
                            # Quoted line does NOT exist in ORIN.md line 2
                            "memory_id": memory_id,
                            "file": "ORIN.md",
                            "line": 2,
                            "quoted_line": "Texto completamente inventado que nao existe no arquivo.",
                            "reason": "Alucinacao de trecho.",
                        },
                        {
                            # Line out of bounds
                            "memory_id": memory_id,
                            "file": "ORIN.md",
                            "line": 99,
                            "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                            "reason": "Linha fora do intervalo.",
                        },
                        {
                            # File not loaded
                            "memory_id": memory_id,
                            "file": "INEXISTENTE.md",
                            "line": 1,
                            "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                            "reason": "Arquivo inexistente.",
                        },
                        {
                            # Memory ID does not exist
                            "memory_id": "mem_nao_existe",
                            "file": "ORIN.md",
                            "line": 2,
                            "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                            "reason": "Memoria falsa.",
                        },
                        {
                            # Valid entry must survive filtering
                            "memory_id": memory_id,
                            "file": "ORIN.md",
                            "line": 2,
                            "quoted_line": "Use claude-sonnet-4-6 em subagentes.",
                            "reason": "Conflito real de modelo de subagentes.",
                        },
                    ]
                }
            )

        linter = InstructionLinter(engine=engine, model_caller=hallucinatory_caller)
        report = linter.run("u1", workspace, dry_run=False)

        assert len(report.contradictions) == 1
        assert report.contradictions[0].memory_id == memory_id
        assert report.contradictions[0].file == "ORIN.md"
        assert report.contradictions[0].line == 2
        assert report.contradictions[0].reason == "Conflito real de modelo de subagentes."
    finally:
        engine.dispose()


def test_without_available_model_returns_empty_report_with_warning(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text("Sempre rode pytest com uv run pytest.\n", encoding="utf-8")

    engine = _make_engine(tmp_path, "nomodel.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        store.save("Rode pytest diretamente sem uv.", kind="operational", source="user_explicit")

        linter = InstructionLinter(engine=engine, model_caller=None, provider_resolver=lambda _u: None)
        report = linter.run("u1", workspace, dry_run=False)

        assert report.contradictions == []
        assert report.dry_run is False
        assert report.files_checked == ["CLAUDE.md"]
        assert report.memories_checked == 1
        assert report.estimated_calls == 1
        assert report.warning is not None
        assert "modelo" in report.warning.lower()
    finally:
        engine.dispose()


def test_dry_run_makes_zero_model_calls_and_estimates_cost(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".orin").mkdir()
    (workspace / ".orin" / "ORIN.md").write_text(
        "@extra-rules.md\nUse uv run python para rodar scripts.\n",
        encoding="utf-8",
    )
    (workspace / ".orin" / "extra-rules.md").write_text(
        "Nunca faca commit direto na main.\n",
        encoding="utf-8",
    )

    engine = _make_engine(tmp_path, "dryrun.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        store.save("Fazer commit direto na branch main quando urgente.", kind="operational", source="user_explicit")

        calls = 0

        def should_not_be_called(_sys: str, _usr: str) -> str:
            nonlocal calls
            calls += 1
            raise AssertionError("dry_run nao deve chamar o modelo")

        linter = InstructionLinter(engine=engine, model_caller=should_not_be_called)
        report = linter.run("u1", workspace, dry_run=True)

        assert calls == 0
        assert report.dry_run is True
        assert report.contradictions == []
        assert report.memories_checked == 1
        assert report.estimated_calls == 1
        assert ".orin/ORIN.md" in report.files_checked
        assert ".orin/extra-rules.md" in report.files_checked
    finally:
        engine.dispose()


def test_loads_subdirectory_instruction_files_and_only_checks_active_memories(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    sub = workspace / "services" / "billing"
    sub.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Todos os endpoints retornam JSON.\n", encoding="utf-8")
    (sub / "AGENTS.md").write_text("Billing usa decimal exato em centavos.\n", encoding="utf-8")

    engine = _make_engine(tmp_path, "subdirs.db")
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        active = store.save("Billing pode usar float para centavos.", kind="architecture", source="user_explicit")
        deleted = store.save("Endpoints retornam XML em vez de JSON.", kind="architecture", source="user_explicit")
        store.forget(str(deleted["memory_id"]))

        seen_prompt = ""

        def fake_caller(_sys: str, user_prompt: str) -> str:
            nonlocal seen_prompt
            seen_prompt = user_prompt
            return json.dumps(
                [
                    {
                        "memory_id": str(active["memory_id"]),
                        "file": "services/billing/AGENTS.md",
                        "line": 1,
                        "quoted_line": "Billing usa decimal exato em centavos.",
                        "reason": "Float contradiz decimal exato em centavos.",
                    }
                ]
            )

        linter = InstructionLinter(engine=engine, model_caller=fake_caller)
        report = linter.run("u1", workspace, dry_run=False)

        assert "AGENTS.md" in report.files_checked
        assert "services/billing/AGENTS.md" in report.files_checked
        assert str(deleted["memory_id"]) not in seen_prompt
        assert len(report.contradictions) == 1
        assert report.contradictions[0].file == "services/billing/AGENTS.md"
    finally:
        engine.dispose()


def test_periodic_routine_respects_setting_flag_and_24h_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ORIN.md").write_text("Use claude-sonnet-4-6 em subagentes.\n", encoding="utf-8")

    engine = _make_engine(tmp_path, "periodic.db")
    state_file = tmp_path / "instruction-lint-state.json"
    state = InstructionLintState(state_file)
    try:
        store = PostgresAgentMemoryStore(engine, user_id="u1")
        store.save("Use claude-sonnet-5-5 em subagentes.", kind="preference", source="user_explicit")

        calls = 0

        def counting_caller(_sys: str, _usr: str) -> str:
            nonlocal calls
            calls += 1
            return "[]"

        linter = InstructionLinter(engine=engine, model_caller=counting_caller)
        t0 = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

        # Disabled by default -> 0 runs
        reports_off = _maybe_run_instruction_lint(
            engine,
            targets=[("u1", workspace, None)],
            now=t0,
            enabled_override=False,
            state=state,
            linter=linter,
        )
        assert reports_off == []
        assert calls == 0

        # Enabled -> runs once
        reports_on = _maybe_run_instruction_lint(
            engine,
            targets=[("u1", workspace, None)],
            now=t0,
            enabled_override=True,
            state=state,
            linter=linter,
        )
        assert len(reports_on) == 1
        assert calls == 1

        # Again 12 hours later -> throttled (max 1x/day)
        reports_too_soon = _maybe_run_instruction_lint(
            engine,
            targets=[("u1", workspace, None)],
            now=t0 + timedelta(hours=12),
            enabled_override=True,
            state=state,
            linter=linter,
        )
        assert reports_too_soon == []
        assert calls == 1

        # After 24 hours -> runs again
        reports_next_day = _maybe_run_instruction_lint(
            engine,
            targets=[("u1", workspace, None)],
            now=t0 + INSTRUCTION_LINT_INTERVAL + timedelta(minutes=1),
            enabled_override=True,
            state=state,
            linter=linter,
        )
        assert len(reports_next_day) == 1
        assert calls == 2
    finally:
        engine.dispose()
