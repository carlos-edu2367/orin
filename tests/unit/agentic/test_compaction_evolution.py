from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from sqlalchemy import create_engine

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.compaction import (
    COMPACT_RETAINED_USER_TOKENS,
    COMPACTION_HEADING,
    COMPACTION_INSTRUCTION,
    COMPACTION_SECTIONS,
    RETAINED_USER_REQUESTS_HEADING,
    build_retained_user_requests_section,
)
from agentos.agentic.loop_settings import BudgetMeter
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata
from agentos.providers.models import ProviderUsage


class _SilentProvider:
    def stream(self, request):
        return iter(())


class _RecordingSummaryProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(request)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=self.text)])


def _runtime(provider=None, **limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(
        store=object(),
        provider=provider or _SilentProvider(),
        system_prompt="prompt",
        limits=AgenticLimits(**limits),
        context_reporting=False,
    )


def _db_store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _turn(store: PostgresChatStore, message: str, key: str, conversation_id: str | None = None) -> dict:
    receipt = store.create(
        user_id="u-compact",
        message=message,
        provider="openrouter",
        model_id="m",
        idempotency_key=key,
        conversation_id=conversation_id,
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


# ---------------------------------------------------------------------------
# (a) _compact_source: Head + User Spine + Tail & Head+Tail tool compression
# ---------------------------------------------------------------------------


def test_a_compact_source_preserves_initial_user_prompt_user_spine_and_omission_marker_across_45_large_tools() -> None:
    runtime = _runtime(max_context_tokens=20_000, context_window_tokens=20_000)
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": "PROMPT_INICIAL_CRITICO: refatorar modulo de pagamentos mantendo compatibilidade v1",
        },
        {
            "role": "assistant",
            "content": "Vou inspecionar os arquivos de pagamento.",
        },
    ]
    for i in range(45):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "name": "run_command",
                "content": (
                    f"HEAD_LOG_{i}\n"
                    + "\n".join(f"linha_{i}_{line}: " + ("x" * 80) for line in range(60))
                    + f"\nTRACEBACK_FINAL_{i}: exit_code=0"
                ),
            }
        )
        if i == 20:
            messages.append(
                {
                    "role": "user",
                    "content": "RESTRICAO_SPINE_MEIO: nunca remova o campo legacy_token do payload",
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": "<system-reminder>lembrete volatil que deve ser ignorado no spine</system-reminder>",
                }
            )

    messages.append(
        {
            "role": "assistant",
            "content": "Concluí a bateria de 45 comandos.",
        }
    )
    messages.append(
        {
            "role": "user",
            "content": "PEDIDO_RECENTE_TAIL: agora gere os testes de regressao",
        }
    )

    indices = set(range(len(messages)))
    source = runtime._compact_source(messages, indices)

    # Head preserved
    assert "PROMPT_INICIAL_CRITICO: refatorar modulo de pagamentos mantendo compatibilidade v1" in source
    # User Spine preserved
    assert "RESTRICAO_SPINE_MEIO: nunca remova o campo legacy_token do payload" in source
    # Tail preserved
    assert "PEDIDO_RECENTE_TAIL: agora gere os testes de regressao" in source
    # Gap marker inserted where intermediate tool outputs were omitted
    assert "passos intermediários de ferramenta omitidos" in source
    # Strictly chronological order without duplicates
    pos_head = source.index("PROMPT_INICIAL_CRITICO")
    pos_spine = source.index("RESTRICAO_SPINE_MEIO")
    pos_tail = source.index("PEDIDO_RECENTE_TAIL")
    assert pos_head < pos_spine < pos_tail
    assert source.count("PROMPT_INICIAL_CRITICO") == 1
    assert source.count("RESTRICAO_SPINE_MEIO") == 1
    assert source.count("PEDIDO_RECENTE_TAIL") == 1
    # Tool outputs in Tail keep both head (60%) and tail (40%, including exit codes/tracebacks)
    assert "HEAD_LOG_44" in source
    assert "TRACEBACK_FINAL_44: exit_code=0" in source


def test_a_compress_tool_message_preserves_60_percent_head_and_40_percent_tail_with_line_and_char_counts() -> None:
    lines = [f"line_{i:03d}: " + ("A" * 40) for i in range(100)]
    lines[0] = "START_HEADER: pytest session starts"
    lines[-1] = "END_TRACEBACK: AssertionError at line 99 (exit code 1)"
    raw_output = "\n".join(lines)
    assert len(raw_output) > 4_000

    compressed_msg = AgenticTurnRuntime._compress_tool_message(
        {"role": "tool", "tool_call_id": "t1", "content": raw_output}
    )
    compressed = str(compressed_msg["content"])

    assert "START_HEADER: pytest session starts" in compressed
    assert "END_TRACEBACK: AssertionError at line 99 (exit code 1)" in compressed
    assert "[compressed:" in compressed
    assert "chars" in compressed and "lines omitted" in compressed


# ---------------------------------------------------------------------------
# (b) Verbatim User Requests retention (helper, history_for_turn, multi-compact)
# ---------------------------------------------------------------------------


def test_b_build_retained_user_requests_filters_non_user_and_truncates_over_8k_chars() -> None:
    assert COMPACT_RETAINED_USER_TOKENS == 16_000
    long_request = ("INICIO_LOG_" + ("A" * 4_500)) + ("MEIO_DESCARTADO_" * 200) + (("Z" * 4_500) + "_FIM_LOG_")
    assert len(long_request) > 8_000

    messages: list[dict[str, object]] = [
        {"role": "user", "content": "<system-reminder>only reminder</system-reminder>"},
        {
            "role": "user",
            "content": "Primeiro pedido real <system-reminder>inline reminder</system-reminder> com contexto",
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "tool output not user"}],
        },
        {"role": "user", "_tool_image_result": True, "content": "Conteúdo visual do arquivo solicitado:"},
        {"role": "system", "content": f"{COMPACTION_HEADING}\nold summary"},
        {"role": "user", "content": long_request},
    ]

    section = build_retained_user_requests_section(messages)
    assert section.startswith(RETAINED_USER_REQUESTS_HEADING)
    assert "only reminder" not in section
    assert "inline reminder" not in section
    assert "tool output not user" not in section
    assert "[ Turno #1 ]\nPrimeiro pedido real  com contexto" in section or "Primeiro pedido real" in section
    assert "[ Turno #2 ]" in section
    assert "INICIO_LOG_" in section
    assert "_FIM_LOG_" in section
    assert "...[trecho intermediário omitido (" in section
    assert "MEIO_DESCARTADO_" not in section


def test_b_history_for_turn_retains_verbatim_user_requests_up_to_16k_tokens_without_duplication() -> None:
    store = _db_store()
    t1 = _turn(store, "Pedido 1: criar endpoint /health", "k1")
    store.delta(t1, "Criado /health")
    store.finish(t1)
    conv_id = str(t1["conversation_id"])

    t2 = _turn(store, "Pedido 2: adicionar autenticacao JWT estrita", "k2", conv_id)
    store.delta(t2, "Adicionado JWT")
    store.finish(t2)

    t3 = _turn(store, "Pedido 3: rodar suite de integracao", "k3", conv_id)
    # Record a boundary where summary ALREADY has a partial Verbatim User Requests section
    partial_summary = (
        "\n".join(f"### {s}\n- ok" for s in COMPACTION_SECTIONS)
        + f"\n\n{RETAINED_USER_REQUESTS_HEADING}\n[ Turno #1 ]\nPedido 1: criar endpoint /health"
    )
    store.record_compact_boundary(
        t3,
        kind="compact",
        trigger="auto",
        pre_tokens=2000,
        post_tokens=400,
        summary=partial_summary,
    )

    history = store.history_for_turn(t3)
    assert history[0]["role"] == "system"
    compacted_text = str(history[0]["content"])
    # Must contain the heading exactly once (idempotent, no duplication)
    assert compacted_text.count(RETAINED_USER_REQUESTS_HEADING) == 1
    # Must contain both authoritative user messages <= through_sequence in chronological order
    assert "[ Turno #1 ]\nPedido 1: criar endpoint /health" in compacted_text
    assert "[ Turno #2 ]\nPedido 2: adicionar autenticacao JWT estrita" in compacted_text
    assert compacted_text.index("Pedido 1") < compacted_text.index("Pedido 2")
    # Current turn (t3) is in history[1:], not duplicated in through_sequence
    assert [m["content"] for m in history[1:]] == ["Pedido 3: rodar suite de integracao"]


def test_b_multiple_intra_turn_compactions_preserve_original_user_requests() -> None:
    summary_text = "\n".join(f"### {s}\n- resumo" for s in COMPACTION_SECTIONS)
    provider = _RecordingSummaryProvider(summary_text)
    runtime = _runtime(provider=provider, max_context_tokens=4_000)

    messages: list[dict[str, object]] = [
        {"role": "user", "content": "PEDIDO_ORIGINAL_1: configurar banco SQLite WAL"},
        {"role": "assistant", "content": "ok " + ("a" * 2_000)},
        {"role": "user", "content": "PEDIDO_ORIGINAL_2: ativar foreign keys"},
        {"role": "assistant", "content": "ok " + ("b" * 2_000)},
        {"role": "user", "content": "PEDIDO_ATUAL_3: criar tabela de sessoes"},
    ]
    runtime._pinned_index = len(messages) - 1

    # First compaction folds PEDIDO_ORIGINAL_1 and PEDIDO_ORIGINAL_2
    assert runtime._run_compaction(messages, {"turn_id": "t1"}, keep_units=1) is True
    first_compacted = str(messages[0]["content"])
    assert RETAINED_USER_REQUESTS_HEADING in first_compacted
    assert "PEDIDO_ORIGINAL_1: configurar banco SQLite WAL" in first_compacted
    assert "PEDIDO_ORIGINAL_2: ativar foreign keys" in first_compacted

    # Add more messages and run a second compaction that folds the first compaction_message + PEDIDO_ATUAL_3
    messages.append({"role": "assistant", "content": "tabela criada " + ("c" * 2_000)})
    messages.append({"role": "user", "content": "PEDIDO_NOVO_4: adicionar indice por user_id"})
    messages.append({"role": "assistant", "content": "indice criado " + ("d" * 2_000)})
    messages.append({"role": "user", "content": "PEDIDO_FINAL_5: validar migracao"})
    runtime._pinned_index = len(messages) - 1

    assert runtime._run_compaction(messages, {"turn_id": "t1"}, keep_units=1) is True
    second_compacted = str(messages[0]["content"])
    assert second_compacted.count(RETAINED_USER_REQUESTS_HEADING) == 1
    assert "PEDIDO_ORIGINAL_1: configurar banco SQLite WAL" in second_compacted
    assert "PEDIDO_ORIGINAL_2: ativar foreign keys" in second_compacted
    assert "PEDIDO_ATUAL_3: criar tabela de sessoes" in second_compacted


# ---------------------------------------------------------------------------
# (c) Summarizer output budget, large <analysis>, and context-error retry
# ---------------------------------------------------------------------------


def test_c_compaction_instruction_limits_analysis_and_output_tokens_follow_formula() -> None:
    assert "350" in COMPACTION_INSTRUCTION

    provider = _RecordingSummaryProvider("\n".join(f"### {s}\n- ok" for s in COMPACTION_SECTIONS))
    # Default max_output_tokens=None -> (8192 // 2) = 4096
    rt_default = _runtime(provider=provider, max_context_tokens=16_000)
    rt_default._request_compaction_summary("source text", {"turn_id": "t"})
    assert provider.requests[-1]["max_output_tokens"] == 4_096

    # Small max_output_tokens=1000 -> floored at 2048
    rt_small = _runtime(provider=provider, max_context_tokens=16_000, max_output_tokens=1_000)
    rt_small._request_compaction_summary("source text", {"turn_id": "t"})
    assert provider.requests[-1]["max_output_tokens"] == 2_048

    # Large max_output_tokens=20000 -> capped at 6000
    rt_large = _runtime(provider=provider, max_context_tokens=16_000, max_output_tokens=20_000)
    rt_large._request_compaction_summary("source text", {"turn_id": "t"})
    assert provider.requests[-1]["max_output_tokens"] == 6_000


def test_c_large_analysis_and_12k_char_summary_do_not_truncate_final_sections() -> None:
    large_analysis = "<analysis>\n" + ("thinking word " * 1_500) + "\n</analysis>\n"
    sections_text = []
    for section in COMPACTION_SECTIONS:
        # Make total clean summary > 10,000 chars (exceeding old 8,000 char cap)
        filler = f"detalhe importante de {section}: " + ("D" * 1_200)
        sections_text.append(f"### {section}\n- {filler}")
    sections_text[-1] += "\n- NEXT_STEP_SENTINEL_PRESERVED"
    full_reply = large_analysis + "\n".join(sections_text)

    provider = _RecordingSummaryProvider(full_reply)
    runtime = _runtime(provider=provider, max_context_tokens=4_000)
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "Pedido inicial que deve permanecer literal"},
        {"role": "assistant", "content": "resposta " + ("x" * 15_000)},
        {"role": "user", "content": "Pedido atual"},
    ]
    runtime._pinned_index = len(messages) - 1

    assert runtime._run_compaction(messages, {"turn_id": "t"}, keep_units=1) is True
    compacted_content = str(messages[0]["content"])
    assert "<analysis>" not in compacted_content
    for section in COMPACTION_SECTIONS:
        assert f"### {section}" in compacted_content
    assert "NEXT_STEP_SENTINEL_PRESERVED" in compacted_content
    assert RETAINED_USER_REQUESTS_HEADING in compacted_content
    assert "Pedido inicial que deve permanecer literal" in compacted_content


def test_c_request_compaction_summary_retries_once_halving_source_on_context_length_error() -> None:
    class _OverflowThenSuccessProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise RuntimeError("context_length_exceeded: prompt is too long")
            body = "\n".join(f"### {s}\n- recovered" for s in COMPACTION_SECTIONS)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text=body),
                    NormalizedStreamItem(
                        StreamKind.USAGE,
                        2,
                        usage=ProviderUsage(500, 100, 600, None, measurement="CONFIRMED"),
                    ),
                ]
            )

    meter = BudgetMeter()
    provider = _OverflowThenSuccessProvider()
    runtime = AgenticTurnRuntime(
        store=object(),
        provider=provider,
        limits=AgenticLimits(max_context_tokens=8_000),
        cost_estimator=lambda inp, out: Decimal("0.10"),
        budget_meter=meter,
    )
    large_source = "HEAD_ANCHOR\n" + ("\n".join(f'{{"role":"tool","content":"{"T" * 500}"}}' for _ in range(40))) + "\nTAIL_ANCHOR"
    summary = runtime._request_compaction_summary(large_source, {"turn_id": "t"})

    assert "### Primary Request and Intent" in summary
    assert len(provider.requests) == 2
    first_sent = str(provider.requests[0]["messages"][1]["content"])
    second_sent = str(provider.requests[1]["messages"][1]["content"])
    assert len(second_sent) <= len(first_sent) * 0.65
    assert "HEAD_ANCHOR" in second_sent
    assert "TAIL_ANCHOR" in second_sent
    assert meter.total == Decimal("0.10")


# ---------------------------------------------------------------------------
# (d) _dedupe_stale_reads: duplicate key, full-covering-partial, stale pre-edit
# ---------------------------------------------------------------------------


def test_d_dedupe_stale_reads_elides_duplicate_key_and_partial_covered_by_full_read() -> None:
    runtime = _runtime()
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "analise src/app.py e src/utils.py"},
        # 1. First read of src/app.py (same _read_key as later read)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/app.py","offset":1,"limit":50}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "conteudo_antigo_app " + ("a" * 400),
            "_read_path": "src/app.py",
            "_read_key": ("src/app.py", 1, 50),
            "_read_full": False,
            "_read_sha": "sha1",
        },
        # 2. Partial read of src/utils.py (will be covered by a full read later)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/utils.py","offset":10,"limit":20}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c2",
            "content": "conteudo_parcial_utils " + ("u" * 400),
            "_read_path": "src/utils.py",
            "_read_key": ("src/utils.py", 10, 20),
            "_read_full": False,
            "_read_sha": "sha_u",
        },
        # 3. Duplicate read of src/app.py with same _read_key
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/app.py","offset":1,"limit":50}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c3",
            "content": "conteudo_novo_app " + ("a" * 400),
            "_read_path": "src/app.py",
            "_read_key": ("src/app.py", 1, 50),
            "_read_full": False,
            "_read_sha": "sha1",
        },
        # 4. Full read of src/utils.py covering the earlier partial read
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/utils.py"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c4",
            "content": "conteudo_integral_utils " + ("u" * 800),
            "_read_path": "src/utils.py",
            "_read_key": ("src/utils.py", 1, 2000),
            "_read_full": True,
            "_read_sha": "sha_u",
        },
        # 5. Latest tool call batch (last_call): results after this MUST NOT be touched
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c5", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/unread.py"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c5",
            "content": "conteudo_nao_lido_ainda " + ("z" * 500),
            "_read_path": "src/unread.py",
            "_read_key": ("src/unread.py", 1, 2000),
            "_read_full": True,
            "_read_sha": "sha_z",
        },
    ]
    runtime._pinned_index = 0

    elided = runtime._dedupe_stale_reads(messages)
    assert elided == 2
    assert messages[2]["content"] == "[duplicate read elided: newer read of src/app.py appears later in this conversation]"
    assert messages[4]["content"] == "[duplicate read elided: newer read of src/utils.py appears later in this conversation]"
    # Newer reads stay intact
    assert str(messages[6]["content"]).startswith("conteudo_novo_app ")
    assert str(messages[8]["content"]).startswith("conteudo_integral_utils ")
    # Unread result after last_call stays intact
    assert str(messages[10]["content"]).startswith("conteudo_nao_lido_ainda ")


def test_d_dedupe_stale_reads_marks_large_reads_before_edit_or_write_keeping_last_2_interactions_and_unread_after_last_call() -> None:
    runtime = _runtime()
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "edite src/service.py"},
        # Interaction 1 on src/service.py: large read (> MICROCOMPACT_MIN_CHARS) -> should become stale
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/service.py"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "LEITURA_1_GRANDE_" + ("s" * 500),
            "_read_path": "src/service.py",
            "_read_key": ("src/service.py", 1, 2000),
            "_read_full": True,
            "_read_sha": "sha_v1",
        },
        # Interaction 2 on src/service.py: edit_file
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "edit_file", "arguments": '{"path":"src/service.py"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c2",
            "content": "Applied 1 edit(s) to src/service.py",
            "_mutated_path": "src/service.py",
        },
        # Interaction 3 on src/service.py: large read (within last 2 interactions on src/service.py -> MUST STAY INTACT)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/service.py","offset":1,"limit":100}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c3",
            "content": "LEITURA_3_RECENTE_" + ("s" * 500),
            "_read_path": "src/service.py",
            "_read_key": ("src/service.py", 1, 100),
            "_read_full": False,
            "_read_sha": "sha_v2",
        },
        # Interaction 4 on src/service.py: write_file (within last 2 interactions on src/service.py)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"src/service.py"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c4",
            "content": "Wrote 400 bytes to src/service.py.",
            "_mutated_path": "src/service.py",
        },
        # Final tool call (last_call): batch after this has a duplicate read of src/service.py that MUST NOT be touched
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c5", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/service.py","offset":1,"limit":100}'}},
                {"id": "c6", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/service.py","offset":1,"limit":100}'}},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c5",
            "content": "UNREAD_BATCH_1_" + ("u" * 400),
            "_read_path": "src/service.py",
            "_read_key": ("src/service.py", 1, 100),
            "_read_full": False,
            "_read_sha": "sha_v3",
        },
        {
            "role": "tool",
            "tool_call_id": "c6",
            "content": "UNREAD_BATCH_2_" + ("u" * 400),
            "_read_path": "src/service.py",
            "_read_key": ("src/service.py", 1, 100),
            "_read_full": False,
            "_read_sha": "sha_v3",
        },
    ]
    runtime._pinned_index = 0

    elided = runtime._dedupe_stale_reads(messages)
    assert elided == 1
    assert messages[2]["content"] == "[stale read elided: src/service.py was modified later in this conversation]"
    # Interaction 3 (one of the last 2 interactions before last_call) stays intact!
    assert str(messages[6]["content"]).startswith("LEITURA_3_RECENTE_")
    # Unread results after last_call (indices 10 and 11) are never touched!
    assert str(messages[10]["content"]).startswith("UNREAD_BATCH_1_")
    assert str(messages[11]["content"]).startswith("UNREAD_BATCH_2_")


def test_d_run_toolset_and_tool_result_messages_annotate_read_and_mutation_metadata(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-dedupe-meta")
    toolset = AgentToolset(workspace, code_mode_active=True)
    target = workspace.root / "mod.py"
    target.write_text("\n".join(f"x_{i} = {i}" for i in range(30)) + "\n", encoding="utf-8")

    runtime = AgenticTurnRuntime(
        store=object(),
        provider=_SilentProvider(),
        toolset=toolset,
        limits=AgenticLimits(),
    )
    turn = {"turn_id": "t1", "conversation_id": "conv-dedupe-meta", "provider": "openrouter"}

    read_res = runtime._run_toolset(
        turn,
        [{"id": "call_r1", "name": "read_file", "arguments": '{"path": "mod.py", "offset": 1, "limit": 10}'}],
    )
    read_msgs = runtime._tool_result_messages(turn, read_res[0])
    assert read_msgs[0]["_read_key"] == ("mod.py", 1, 10)
    assert read_msgs[0]["_read_full"] is False
    assert isinstance(read_msgs[0]["_read_sha"], str) and len(str(read_msgs[0]["_read_sha"])) == 64

    full_res = runtime._run_toolset(
        turn,
        [{"id": "call_r2", "name": "read_file", "arguments": '{"path": "mod.py", "force_reload": true}'}],
    )
    full_msgs = runtime._tool_result_messages(turn, full_res[0])
    assert full_msgs[0]["_read_key"][0] == "mod.py"
    assert full_msgs[0]["_read_full"] is True

    edit_res = runtime._run_toolset(
        turn,
        [{"id": "call_e1", "name": "edit_file", "arguments": '{"path": "mod.py", "old_text": "x_0 = 0", "new_text": "x_0 = 999"}'}],
    )
    edit_msgs = runtime._tool_result_messages(turn, edit_res[0])
    assert edit_msgs[0]["_mutated_path"] == "mod.py"


def test_h4_attach_retained_user_requests_does_not_duplicate_oversized_messages_across_compactions() -> None:
    from agentos.agentic.compaction import (
        _stringify_content,
        attach_retained_user_requests,
        extract_retained_user_requests,
    )

    oversized_req = "INICIO_PEDIDO_GIGANTE_" + ("A" * 9_500) + "_FIM_PEDIDO_GIGANTE"
    second_req = "Segundo pedido curto após o primeiro"

    # Compaction 1: attaches retained requests (truncating oversized_req with marker)
    summary_1 = attach_retained_user_requests(
        "### Primary Request and Intent\n- Resumo 1",
        [{"role": "user", "content": oversized_req}],
    )
    extracted_1 = extract_retained_user_requests(summary_1)
    assert len(extracted_1) == 1
    assert "...[trecho intermediário omitido" in extracted_1[0]

    # Compaction 2 / history_for_turn: receives summary_1 (which has the truncated prior request)
    # AND the raw un-truncated oversized_req in user_messages!
    summary_2 = attach_retained_user_requests(
        summary_1,
        [
            {"role": "user", "content": oversized_req},
            {"role": "user", "content": second_req},
        ],
    )
    extracted_2 = extract_retained_user_requests(summary_2)
    assert len(extracted_2) == 2
    assert summary_2.count("INICIO_PEDIDO_GIGANTE_") == 1
    assert extracted_2[1] == second_req
    assert _stringify_content([{"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("Z" * 5000)}}]) == "[media attachment omitted]"


def test_m3_dedupe_stale_reads_handles_multiple_anthropic_tool_result_blocks_in_single_user_message() -> None:
    runtime = _runtime(max_context_tokens=20_000)
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "Analise a.py e b.py"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_a1", "name": "read_file", "input": {"path": "src/a.py"}},
                {"type": "tool_use", "id": "tu_b1", "name": "read_file", "input": {"path": "src/b.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_a1", "content": "CONTEUDO_A_V1_" + ("a" * 300)},
                {"type": "tool_result", "tool_use_id": "tu_b1", "content": "CONTEUDO_B_INTACTO_" + ("b" * 300)},
            ],
        },
        # Later duplicate read of src/a.py only
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_a2", "name": "read_file", "input": {"path": "src/a.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_a2", "content": "CONTEUDO_A_V1_" + ("a" * 300)},
            ],
        },
        # Final tool call (last_call)
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_final", "name": "read_file", "input": {"path": "src/c.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_final", "content": "CONTEUDO_C"},
            ],
        },
    ]
    runtime._pinned_index = 0
    elided = runtime._dedupe_stale_reads(messages)
    assert elided == 1
    blocks = messages[2]["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["content"] == "[duplicate read elided: newer read of src/a.py appears later in this conversation]"
    # Sibling block in the exact same user message MUST remain untouched!
    assert str(blocks[1]["content"]).startswith("CONTEUDO_B_INTACTO_")


def test_m4_strip_analysis_block_unclosed_has_sections_and_multimodal_compaction_source() -> None:
    from agentos.agentic.runtime import strip_analysis_block

    valid_sections = "\n".join(f"### {s}\n- item" for s in COMPACTION_SECTIONS)
    # 1. Unclosed <analysis> followed by ### Primary Request and Intent strips the scratchpad and keeps sections
    unclosed_with_sections = f"<analysis>\nPensamento interno sem tag de fechamento\n{valid_sections}"
    stripped = strip_analysis_block(unclosed_with_sections)
    assert "<analysis>" not in stripped
    assert "Pensamento interno" not in stripped
    assert stripped.startswith("### Primary Request and Intent")
    assert AgenticTurnRuntime._has_sections(stripped) is True

    # 2. Unclosed <analysis> truncated before any ### section returns "" so fallback triggers
    unclosed_truncated = "<analysis>\nPensamento truncado no meio sem nenhuma seção markdown..."
    assert strip_analysis_block(unclosed_truncated) == ""

    # 3. _has_sections rejects plain text that mentions section names without markdown headings
    prose_only = " ".join(COMPACTION_SECTIONS)
    assert AgenticTurnRuntime._has_sections(prose_only) is False

    # 4. Multimodal base64 blocks in messages are omitted in _compact_source
    runtime = _runtime(max_context_tokens=20_000)
    b64_payload = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" * 600
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analise esta captura de tela"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_payload}},
            ],
        },
        {"role": "assistant", "content": "Entendido."},
    ]
    source = runtime._compact_source(messages, {0, 1})
    assert "Analise esta captura de tela" in source
    assert "[media attachment omitted]" in source
    assert b64_payload[:64] not in source


def test_m5_render_code_turn_context_truncates_huge_git_status() -> None:
    from agentos.code_mode.system_prompt import render_code_turn_context

    huge_git = ("M src/head_file.py\n" * 300) + ("?? src/middle_file.py\n" * 400) + ("M src/tail_file.py\n" * 200)
    assert len(huge_git) > 8_000
    rendered = render_code_turn_context(
        permission_mode="accept_edits",
        plan_path=None,
        permits_push=False,
        environment={"platform": "win32", "os": "Windows", "shell": "bash", "python": "3.13"},
        workspace_root="C:/repo",
        workspace_is_local=True,
        git_status=huge_git,
        public_names=True,
        is_git_repo=True,
    )
    assert "...[git status truncated (" in rendered
    assert "M src/head_file.py" in rendered
    assert "M src/tail_file.py" in rendered
    assert len(rendered) < 8_000

