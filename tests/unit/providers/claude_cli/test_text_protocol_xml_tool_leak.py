"""Anthropic-native XML tool syntax (`<function_calls>`, `<invoke>`, `<function_results>`) written as plain text.

Regression: a Code-mode reply showed raw `</output></result></function_results>... <invoke name="Bash">` text: the
model typed a tool call (and a made-up result) instead of calling the tool, and the display filter only knew
`[Tool Use]`, `[Tool Result]`, `<orin_tool_call>` and `<tool_call>`."""
from __future__ import annotations

import pytest

from agentos.providers.claude_cli.text_protocol import (
    ResidualToolTextStreamFilter,
    ToolCallStreamParser,
    strip_residual_tool_protocol_blocks,
)

PRINT_TEXT = (
    "</output>\n"
    "</result>\n"
    "</function_results>Por código: os 10 PACOTE_344 são idênticos ao texto oficial...\n"
    "Gravo os 21 suspeitos da w1 como meus bloqueios e listo os 19 restantes para ler.\n"
    '<invoke name="Bash"> <parameter name="command">cd /c/Users/... && node -e " ... "'
)
PRINT_VISIBLE = (
    "Por código: os 10 PACOTE_344 são idênticos ao texto oficial...\n"
    "Gravo os 21 suspeitos da w1 como meus bloqueios e listo os 19 restantes para ler.\n"
)


def _stream(raw: str, size: int) -> tuple[str, ResidualToolTextStreamFilter]:
    flt = ResidualToolTextStreamFilter()
    out = [flt.feed(raw[i : i + size]) for i in range(0, len(raw), size)]
    out.append(flt.finish())
    return "".join(out), flt


def test_strip_removes_the_print_sample() -> None:
    assert strip_residual_tool_protocol_blocks(PRINT_TEXT) == PRINT_VISIBLE


def test_strip_removes_closed_function_calls_and_results_blocks() -> None:
    raw = (
        "Antes.\n"
        "<function_calls>\n"
        '<invoke name="Read"><parameter name="file_path">a.py</parameter></invoke>\n'
        "</function_calls>\n"
        "<function_results>\n"
        "<result><output>conteudo</output></result>\n"
        "</function_results>\n"
        "Depois."
    )
    cleaned = strip_residual_tool_protocol_blocks(raw)
    assert cleaned.startswith("Antes.")
    assert cleaned.endswith("Depois.")
    for leaked in ("invoke", "function_", "conteudo", "<result>", "<output>", "parameter"):
        assert leaked not in cleaned


def test_strip_handles_html_escaped_form() -> None:
    raw = (
        "Vou ler.\n"
        "&lt;function_calls&gt;\n"
        "&lt;invoke name=&quot;Read&quot;&gt;&lt;parameter name=&quot;file_path&quot;&gt;a.py&lt;/parameter&gt;&lt;/invoke&gt;\n"
        "&lt;/function_calls&gt;\n"
        "Pronto."
    )
    cleaned = strip_residual_tool_protocol_blocks(raw)
    assert "invoke" not in cleaned
    assert "function_calls" not in cleaned
    assert cleaned.startswith("Vou ler.")
    assert cleaned.endswith("Pronto.")


def test_strip_unclosed_invoke_runs_to_end_of_text() -> None:
    assert strip_residual_tool_protocol_blocks('Ok.\n<invoke name="Bash"><parameter name="command">ls') == "Ok.\n"


def test_strip_orphan_closers_only_with_function_results() -> None:
    assert strip_residual_tool_protocol_blocks("</output></result></function_results>Texto") == "Texto"
    assert strip_residual_tool_protocol_blocks("</function_results>Texto") == "Texto"


@pytest.mark.parametrize(
    "legit",
    [
        "Use a tag <result> para o valor e <output> para a saída.",
        "O HTML <output>42</output> e <result>ok</result> deve ficar.",
        "Fechando </output> sozinho em prosa.",
        "A função invoke(name) chama o handler; <invoker> não é tag.",
    ],
)
def test_strip_keeps_legit_result_output_and_invoke_text(legit: str) -> None:
    assert strip_residual_tool_protocol_blocks(legit) == legit


def test_strip_keeps_fenced_code() -> None:
    raw = (
        "Exemplo:\n"
        "```xml\n"
        '<function_calls><invoke name="Bash"></invoke></function_calls>\n'
        "</output></result></function_results>\n"
        "```\n"
        "Fim."
    )
    assert strip_residual_tool_protocol_blocks(raw) == raw


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13])
def test_stream_filter_removes_the_print_sample_in_any_chunking(size: int) -> None:
    out, flt = _stream(PRINT_TEXT, size)
    assert out == PRINT_VISIBLE
    assert flt.saw_text_tool_call is True


@pytest.mark.parametrize("size", [1, 3, 8])
def test_stream_filter_removes_escaped_blocks_in_chunks(size: int) -> None:
    raw = (
        "Antes. &lt;function_calls&gt;&lt;invoke name=&quot;Bash&quot;&gt;"
        "&lt;parameter name=&quot;command&quot;&gt;ls&lt;/parameter&gt;&lt;/invoke&gt;&lt;/function_calls&gt;"
        " Depois."
    )
    out, flt = _stream(raw, size)
    assert out == "Antes.  Depois."
    assert flt.saw_text_tool_call is True


@pytest.mark.parametrize("size", [1, 4, 9])
def test_stream_filter_keeps_legit_xml_and_flags_nothing(size: int) -> None:
    raw = "Veja <result>ok</result>, <output>42</output> e </output> solto, mais <invoice>1</invoice>."
    out, flt = _stream(raw, size)
    assert out == raw
    assert flt.saw_text_tool_call is False


def test_stream_filter_does_not_touch_code_fences() -> None:
    raw = "Doc:\n```xml\n<function_calls><invoke name=\"Bash\"></invoke></function_calls>\n```\nFim."
    out, flt = _stream(raw, 4)
    assert out == raw
    assert flt.saw_text_tool_call is False


def test_stream_filter_does_not_delay_plain_text() -> None:
    flt = ResidualToolTextStreamFilter()
    assert flt.feed("Olá, mundo. ") == "Olá, mundo. "
    # A trailing `<` may be the start of a tag: only that character is held back.
    assert flt.feed("a < b e <inv") == "a < b e "
    assert flt.feed("oice>") == "<invoice>"
    assert flt.finish() == ""


def test_stream_filter_unclosed_invoke_at_eof_is_dropped() -> None:
    out, flt = _stream('Texto.\n<invoke name="Bash"> <parameter name="command">ls', 6)
    assert out == "Texto.\n"
    assert flt.saw_text_tool_call is True


def test_text_protocol_parser_stops_at_hallucinated_function_results() -> None:
    parser = ToolCallStreamParser(allowed_names={"Read"})
    raw = (
        "Vou ler.\n"
        '<orin_tool_call>{"name": "Read", "arguments": {"file_path": "a.py"}}</orin_tool_call>'
        "<function_results>\n<result><output>inventado</output></result>\n</function_results>\nResposta."
    )
    events = []
    for i in range(0, len(raw), 6):
        events.extend(parser.feed(raw[i : i + 6]))
    events.extend(parser.finish())
    text = "".join(ev.text for ev in events if ev.kind == "text")
    assert [ev.name for ev in events if ev.kind == "tool_call"] == ["Read"]
    assert "inventado" not in text
    assert "function_results" not in text
    assert any(ev.kind == "stop" for ev in events)
