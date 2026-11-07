from __future__ import annotations

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import COMPACTION_HEADING, COMPACTION_SECTIONS, AgenticLimits, AgenticTurnRuntime


# Wording that used to appear in the compaction header and the trim marker.
# Both told the model to go and do again what it had already done, which is
# the exact behaviour the trilha is trying to remove.
REWORK_PHRASES = ("re-read", "re-run", "confirmar detalhes", "confirm details")


class _SummarizingProvider:
    """Returns a summary that already carries the four required sections."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(request)
        body = "\n".join(f"### {section}\n- detail" for section in COMPACTION_SECTIONS)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=body)])


class _SilentProvider:
    """A provider that returns nothing usable, forcing the fallback."""

    def stream(self, request):
        return iter(())


def _runtime(provider, **limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(
        store=object(), provider=provider, system_prompt="prompt",
        limits=AgenticLimits(**limits), context_reporting=False,
    )


def _bulky_messages() -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "user", "content": f"passo {index} " + "x" * 600} for index in range(10)
    ]
    messages.append({"role": "user", "content": "reformule o orçamento"})
    return messages


def test_the_summary_prompt_asks_for_every_required_section() -> None:
    provider = _SummarizingProvider()
    runtime = _runtime(provider, max_context_tokens=1_000)
    messages = _bulky_messages()
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])

    instruction = str(provider.requests[0]["messages"][0]["content"])
    for section in COMPACTION_SECTIONS:
        assert section in instruction


def test_the_compacted_replacement_keeps_the_structured_sections() -> None:
    runtime = _runtime(_SummarizingProvider(), max_context_tokens=1_000)
    messages = _bulky_messages()
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])

    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    for section in COMPACTION_SECTIONS:
        assert section in str(replacement["content"])


def test_the_compaction_header_does_not_invite_rework() -> None:
    runtime = _runtime(_SummarizingProvider(), max_context_tokens=1_000)
    messages = _bulky_messages()
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])

    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    header = str(replacement["content"]).lower()
    for phrase in REWORK_PHRASES:
        assert phrase not in header


def test_the_fallback_still_produces_every_section() -> None:
    """A provider that cannot summarize must not degrade to shapeless prose."""
    runtime = _runtime(_SilentProvider(), max_context_tokens=1_000)
    messages = _bulky_messages()
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])

    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    for section in COMPACTION_SECTIONS:
        assert section in str(replacement["content"])


def test_the_fallback_names_the_tools_whose_results_were_folded_away() -> None:
    runtime = _runtime(_SilentProvider(), max_context_tokens=1_000)
    messages: list[dict[str, object]] = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "orcamento.xlsx"}'}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "y" * 900},
    ]
    messages += [{"role": "user", "content": f"passo {index} " + "x" * 600} for index in range(8)]
    messages.append({"role": "user", "content": "reformule o orçamento"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])

    replacement = next(item for item in messages if str(item.get("content", "")).startswith(COMPACTION_HEADING))
    assert "read_file" in str(replacement["content"])


def test_the_trim_marker_does_not_instruct_the_model_to_redo_work() -> None:
    runtime = _runtime(_SummarizingProvider(), max_context_tokens=2_000)
    messages = [{"role": "user", "content": "build the report"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = runtime._request_messages(messages)

    marker = next(item for item in window if "omitidas" in str(item.get("content", "")) or "omitted" in str(item.get("content", "")))
    text = str(marker["content"]).lower()
    for phrase in REWORK_PHRASES:
        assert phrase not in text


def test_skill_body_puts_user_request_at_top_so_first_request_text_sees_it() -> None:
    from agentos.conversations.chat import _skill_body

    huge_instructions = "STEP " * 2_000  # 10,000 chars (> 2,000 limit of _first_request_text)
    expanded = _skill_body(
        {
            "id": "superpowers:writing-plans",
            "name": "writing-plans",
            "version": "1.0.0",
            "instructions": huge_instructions,
        },
        "1- pode criar novo slug\n2- pode sim, vamo parar os outros 3 testes",
    )
    first_req = str(expanded)[:2_000]
    assert "1- pode criar novo slug" in first_req
    assert expanded.startswith("Pedido da pessoa: 1- pode criar novo slug")


def test_compact_source_preserves_latest_user_and_assistant_messages_when_early_steps_exceed_budget() -> None:
    runtime = _runtime(_SilentProvider(), max_context_tokens=24_000)
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "Pergunta inicial do turno 1"},
    ]
    for i in range(40):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c_{i}",
                "name": "read_file",
                "content": f"EARLY_TOOL_{i}_" + ("z" * 5_000),
            }
        )
    messages.append(
        {
            "role": "assistant",
            "content": "Diagnóstico final do turno 1: criamos novo slug ou reabrimos?",
        }
    )
    messages.append(
        {
            "role": "user",
            "content": "Resposta decisiva do usuário: pode criar novo slug!",
        }
    )
    indices = set(range(len(messages)))
    source = runtime._compact_source(messages, indices)

    assert "Diagnóstico final do turno 1: criamos novo slug ou reabrimos?" in source
    assert "Resposta decisiva do usuário: pode criar novo slug!" in source
    # Chronological order: assistant diagnosis appears before user reply
    assert source.index("Diagnóstico final do turno 1") < source.index(
        "Resposta decisiva do usuário"
    )
    # Adversarial: out-of-range and negative indices must be ignored without raising IndexError
    assert runtime._compact_source(messages, {-5, 0, 9999}) != ""
    assert runtime._compact_source(messages, set()) == ""


def test_f01_compact_source_compresses_tool_result_dicts_without_mutating_and_preserves_user_turns() -> None:
    runtime = _runtime(_SilentProvider(), max_context_tokens=24_000)
    huge_tool_content = "TOOL_START_" + ("x" * 47_500) + "_TOOL_END"
    tool_msg = {
        "role": "tool",
        "tool_call_id": "call_big",
        "name": "read_file",
        "content": huge_tool_content,
    }
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "Decisão inicial importante do usuário no começo"},
        tool_msg,
        {"role": "assistant", "content": "Pergunta ao final do turno 1"},
    ]
    source = runtime._compact_source(messages, {0, 1, 2})
    # Must not mutate the original tool_msg in place
    assert tool_msg["content"] == huge_tool_content
    # Because the 47.5KB tool result is compressed to ~1600 chars inside _compact_source,
    # the earlier user message at index 0 is NOT starved out of the 48K budget!
    assert "Decisão inicial importante do usuário no começo" in source
    assert "Pergunta ao final do turno 1" in source
    assert "[compressed:" in source


def test_f08_request_messages_system_prefix_and_omission_marker_honesty() -> None:
    runtime = _runtime(_SilentProvider(), max_context_tokens=1_000)
    # Leading system message exceeds available budget, followed by a tiny system message
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "S" * 6_000},
        {"role": "system", "content": "tiny_second_system"},
        {"role": "user", "content": "older_user_dropped " + ("u" * 4_000)},
        {"role": "assistant", "content": "older_assistant_dropped " + ("a" * 4_000)},
        {"role": "user", "content": "current_user"},
    ]
    window = runtime._request_messages(messages)
    contents = [str(m.get("content") or "") for m in window]
    # Because messages[0] did not fit, the loop must break and NOT include tiny_second_system out of order
    assert "tiny_second_system" not in contents
    # And because messages[0] (the summary) was NOT kept, the omission marker must not falsely claim
    # "O que já foi decidido e apurado está resumido acima."
    markers = [c for c in contents if "mensagens anteriores foram omitidas" in c]
    assert len(markers) == 1
    assert "está resumido acima" not in markers[0]


def test_f09_expand_skill_preserves_closing_tag_and_request_footer_under_truncation() -> None:
    from agentos.conversations.chat import MAX_EXPANDED_COMMAND_BODY, _expand_skill

    class _FakeSkillLib:
        def get(self, _req: object) -> dict[str, object]:
            return {
                "id": "writing-plans",
                "name": "Writing Plans",
                "version": "1.0.0",
                "available": True,
                "instructions": "I" * (MAX_EXPANDED_COMMAND_BODY + 5_000),
            }

    expanded = _expand_skill(
        _FakeSkillLib(),
        "user-1",
        "writing-plans",
        "1- pode criar novo slug, 2- pode sim",
    )
    assert expanded is not None
    assert len(expanded.body) <= MAX_EXPANDED_COMMAND_BODY
    assert expanded.body.startswith("Pedido da pessoa: 1- pode criar novo slug, 2- pode sim")
    assert "</agentos-skill-instructions>" in expanded.body
    assert expanded.body.endswith("Pedido da pessoa: 1- pode criar novo slug, 2- pode sim")



