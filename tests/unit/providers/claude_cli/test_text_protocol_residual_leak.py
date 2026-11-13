"""Unit tests for bracket/escaped Tool Use & Tool Result leak prevention across text_protocol, native_turn, and opencode."""
from __future__ import annotations

import json

from agentos.provider_catalog.opencode import _parse_orin_tool_calls
from agentos.providers.claude_cli.text_protocol import (
    ResidualToolTextStreamFilter,
    ToolCallStreamParser,
    strip_residual_tool_protocol_blocks,
)


def test_tool_call_stream_parser_parses_escaped_bracket_tool_use_and_stops_on_escaped_result() -> None:
    parser = ToolCallStreamParser(allowed_names={"manage_session", "Read"})
    leaked_payload = (
        "Vou verificar a sessão.\n"
        "&#91;Tool Use id=call_123 name=manage_session]\n"
        '{"name": "manage_session", "arguments": {"action": "inspect", "conversation_id": "chat_1"}}\n'
        "&#91;/Tool Use]&#91;Tool Result id=call_123 name=manage_session status=succeeded]\n"
        '{"ok": true, "state": "running"}\n'
        "&#91;/Tool Result]AssistantA sessão continua rodando."
    )
    events = []
    # Stream in small chunks to exercise boundary holding
    for idx in range(0, len(leaked_payload), 7):
        events.extend(parser.feed(leaked_payload[idx : idx + 7]))
    events.extend(parser.finish())

    text_out = "".join(ev.text for ev in events if ev.kind == "text")
    tool_calls = [ev for ev in events if ev.kind == "tool_call"]
    assert "Tool Use" not in text_out
    assert "Tool Result" not in text_out
    assert "&#91;" not in text_out
    assert text_out.strip() == "Vou verificar a sessão."
    assert len(tool_calls) == 1
    assert tool_calls[0].call_id == "call_123"
    assert tool_calls[0].name == "manage_session"
    assert json.loads(tool_calls[0].arguments) == {
        "action": "inspect",
        "conversation_id": "chat_1",
    }
    assert any(ev.kind == "stop" for ev in events)


def test_tool_call_stream_parser_uses_header_name_hint_when_body_has_direct_args() -> None:
    parser = ToolCallStreamParser(allowed_names={"manage_session"})
    events = parser.feed(
        "[Tool Use id=call_direct name=manage_session]\n"
        '{"action": "list"}\n'
        "[/Tool Use]"
    )
    events.extend(parser.finish())
    tool_calls = [ev for ev in events if ev.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "manage_session"
    assert json.loads(tool_calls[0].arguments) == {"action": "list"}


def test_residual_tool_text_stream_filter_strips_chunked_escaped_blocks_and_glued_assistant() -> None:
    stream_filter = ResidualToolTextStreamFilter()
    raw = (
        "&#91;Tool Use id=call_99 name=manage_session]\n"
        '{"name": "manage_session", "arguments": {"action": "inspect"}}\n'
        "&#91;/Tool Use]&#91;Tool Result id=call_99 name=manage_session status=succeeded]\n"
        '{"state": "completed"}\n'
        "&#91;/Tool Result]AssistantPronto! O serviço terminou sem erros."
    )
    pieces: list[str] = []
    for idx in range(0, len(raw), 5):
        pieces.append(stream_filter.feed(raw[idx : idx + 5]))
    pieces.append(stream_filter.finish())
    final_text = "".join(pieces)
    assert final_text == "Pronto! O serviço terminou sem erros."


def test_strip_residual_tool_protocol_blocks_preserves_markdown_code_fences() -> None:
    doc_text = (
        "Exemplo de formato interno:\n"
        "```text\n"
        "[Tool Use id=1 name=demo]\n"
        "{}\n"
        "[/Tool Use]\n"
        "```\n"
        "&#91;Tool Use id=leak name=manage_session]\n"
        '{"action": "list"}\n'
        "&#91;/Tool Use]&#91;Tool Result id=leak]\n"
        "{}\n"
        "&#91;/Tool Result]AssistantTexto visível."
    )
    cleaned = strip_residual_tool_protocol_blocks(doc_text)
    assert "[Tool Use id=1 name=demo]" in cleaned
    assert "id=leak" not in cleaned
    assert "Texto visível." in cleaned
    assert "AssistantTexto" not in cleaned


def test_opencode_parse_orin_tool_calls_handles_escaped_bracket_calls_and_results() -> None:
    raw = (
        "Checando sessões.\n"
        "&#91;Tool Use id=call_oc name=manage_session]\n"
        '{"name": "manage_session", "arguments": {"action": "list"}}\n'
        "&#91;/Tool Use]&#91;Tool Result id=call_oc]\n"
        '{"sessions": []}\n'
        "&#91;/Tool Result]Assistant"
    )
    visible, calls = _parse_orin_tool_calls(raw, {"manage_session"})
    assert visible == "Checando sessões."
    assert len(calls) == 1
    assert calls[0][1] == "manage_session"
    assert json.loads(calls[0][2]) == {"action": "list"}
