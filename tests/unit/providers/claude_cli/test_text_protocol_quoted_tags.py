"""A tag quoted in inline code, or loose in the middle of a sentence, is prose: no filter or parser may cut it."""
from __future__ import annotations

from agentos.agentic.text_sanitize import new_assistant_text_stream, sanitize_assistant_text
from agentos.providers.claude_cli.text_protocol import ToolCallStreamParser

REPORTED = [
    "O modo native_history=False (`<orin_tool_call>`) continua igual. Resto do relatorio aqui.",
    'Use `<invoke name="Bash">` para X. Depois segue.',
    "Texto com `[Tool Use]` inline e mais texto.",
]


def _stream(text: str, step: int) -> str:
    stream = new_assistant_text_stream()
    return "".join(stream.feed(text[i : i + step]) for i in range(0, len(text), step)) + stream.finish()


def _parse(text: str, step: int, allowed: set[str] | None = None) -> tuple[str, list[str], bool]:
    parser = ToolCallStreamParser(allowed)
    events = []
    for i in range(0, len(text), step):
        events.extend(parser.feed(text[i : i + step]))
    events.extend(parser.finish())
    shown = "".join(event.text or "" for event in events if event.kind == "text")
    calls = [event.name or "" for event in events if event.kind == "tool_call"]
    return shown, calls, parser.stopped


def test_the_three_reported_texts_survive_sanitize_and_stream() -> None:
    for text in REPORTED:
        assert sanitize_assistant_text(text) == text
        for step in (1, 3, 7, 4096):
            assert _stream(text, step) == text


def test_plain_text_and_inline_code_are_not_held_while_streaming() -> None:
    stream = new_assistant_text_stream()
    assert stream.feed("Texto comum sem nada especial. ") == "Texto comum sem nada especial. "
    assert stream.feed('Cito `<invoke name="Bash">` e ') == 'Cito `<invoke name="Bash">` e '
    assert stream.feed("sigo `com codigo") == "sigo `com codigo"
    assert stream.finish() == ""


def test_a_mid_sentence_tag_that_never_closes_comes_back_at_the_end() -> None:
    text = 'A tag <invoke name="Bash"> abre um bloco so no comeco da linha. Fim.'
    assert sanitize_assistant_text(text) == text
    assert _stream(text, 5) == text
    stream = new_assistant_text_stream()
    stream.feed(text)
    assert stream.saw_text_tool_call is False


def test_a_line_start_invoke_without_closing_is_still_removed_and_flagged() -> None:
    text = 'Vou rodar.\n<invoke name="Bash"> <parameter name="command">ls'
    stream = new_assistant_text_stream()
    shown = stream.feed(text) + stream.finish()
    assert shown == "Vou rodar.\n"
    assert stream.saw_text_tool_call is True


def test_parser_keeps_quoted_tags_as_text() -> None:
    for text in (
        *REPORTED,
        "Marcador citado: `[Tool Result id=1]` e ``</function_results>`` no relatorio. Fim.",
    ):
        for step in (1, 4, 4096):
            shown, calls, stopped = _parse(text, step, {"Bash"})
            assert (shown, calls, stopped) == (text, [], False), (text, step)


def test_parser_still_runs_a_real_orin_tool_call_after_a_quoted_one() -> None:
    text = (
        "Cito `<orin_tool_call>` aqui.\n"
        '<orin_tool_call>{"name": "Bash", "arguments": {"command": "ls"}}</orin_tool_call>'
    )
    for step in (1, 5, 4096):
        shown, calls, stopped = _parse(text, step, {"Bash"})
        assert calls == ["Bash"], step
        assert shown == "Cito `<orin_tool_call>` aqui.\n"
        assert stopped is False


def test_opencode_result_stripper_keeps_quoted_and_mid_sentence_markers() -> None:
    from agentos.provider_catalog.opencode import _strip_hallucinated_tool_results

    quoted = "O marcador `[Tool Result id=1]` e citado aqui. Resto do texto."
    loose = "O marcador [Tool Result id=1] no meio da frase fica. Resto."
    assert _strip_hallucinated_tool_results(quoted) == quoted
    assert _strip_hallucinated_tool_results(loose) == loose
    assert _strip_hallucinated_tool_results("Feito.\n[Tool Result id=1 status=ok]\nok") == "Feito."
    assert _strip_hallucinated_tool_results("Antes [Tool Result id=1]x[/Tool Result] depois.") == "Antes  depois."


def test_parser_still_stops_on_a_real_hallucinated_result() -> None:
    shown, calls, stopped = _parse("Feito.\n[Tool Result id=1 name=Bash status=ok]\nok", 4096, {"Bash"})
    assert stopped is True
    assert calls == []
    assert shown == "Feito."
