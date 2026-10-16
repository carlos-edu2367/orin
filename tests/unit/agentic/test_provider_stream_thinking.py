from __future__ import annotations

import json

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import (
    HTTPProviderStreamTransport,
    StreamKind,
    normalize_ndjson,
    normalize_sse,
    project_messages,
)


def test_agent_thought_delta_event_type_exists() -> None:
    assert hasattr(AgentActivityEventType, "AGENT_THOUGHT_DELTA")
    assert AgentActivityEventType.AGENT_THOUGHT_DELTA.value == "agent.thought_delta"


def test_stream_kind_thinking_exists() -> None:
    assert hasattr(StreamKind, "THINKING")
    assert StreamKind.THINKING.value == "thinking"


def test_normalize_sse_openai_reasoning_content_and_details() -> None:
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"Analisando "}}]}\n',
        'data: {"choices":[{"delta":{"reasoning":"arquitetura "}}]}\n',
        'data: {"choices":[{"delta":{"thinking":"do projeto. "}}]}\n',
        'data: {"choices":[{"delta":{"reasoning_details":[{"type":"reasoning.text","text":"Validando rotas."}]}}]}\n',
        'data: {"choices":[{"delta":{"content":"Pronto!"}}]}\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
        "data: [DONE]\n",
    ]

    items = list(normalize_sse(lines, provider="openai"))
    thinking_items = [item for item in items if item.kind is StreamKind.THINKING]
    text_items = [item for item in items if item.kind is StreamKind.TEXT]

    assert [item.thinking for item in thinking_items] == [
        "Analisando ",
        "arquitetura ",
        "do projeto. ",
        "Validando rotas.",
    ]
    assert [item.text for item in text_items] == ["Pronto!"]


def test_normalize_sse_openai_think_tags_across_chunks() -> None:
    lines = [
        'data: {"choices":[{"delta":{"content":"<thi"}}]}\n',
        'data: {"choices":[{"delta":{"content":"nk>Pensando sobre "}}]}\n',
        'data: {"choices":[{"delta":{"content":"o problema</th"}}]}\n',
        'data: {"choices":[{"delta":{"content":"ink>Resposta final"}}]}\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
    ]

    items = list(normalize_sse(lines, provider="openai"))
    thinking_text = "".join(item.thinking or "" for item in items if item.kind is StreamKind.THINKING)
    visible_text = "".join(item.text or "" for item in items if item.kind is StreamKind.TEXT)

    assert thinking_text == "Pensando sobre o problema"
    assert visible_text == "Resposta final"


def test_normalize_sse_anthropic_thinking_block_and_delta() -> None:
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"Passo 1: "}}\n',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"verificar estado."}}\n',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Resultado ok."}}\n',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n',
    ]

    items = list(normalize_sse(lines, provider="anthropic"))
    thinking_items = [item for item in items if item.kind is StreamKind.THINKING]
    text_items = [item for item in items if item.kind is StreamKind.TEXT]

    assert [item.thinking for item in thinking_items] == ["Passo 1: ", "verificar estado."]
    assert [item.text for item in text_items] == ["Resultado ok."]


def test_normalize_ndjson_ollama_emits_thinking_incremental_and_preserves_done() -> None:
    lines = [
        json.dumps({"message": {"role": "assistant", "thinking": "Vou usar a ferramenta ", "content": ""}, "done": False}),
        json.dumps({"message": {"role": "assistant", "thinking": "de busca.", "content": ""}, "done": False}),
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1", "function": {"name": "grep", "arguments": {"pattern": "foo"}}}],
                },
                "done": True,
                "done_reason": "tool_calls",
            }
        ),
    ]

    items = list(normalize_ndjson(lines))
    thinking_items = [item for item in items if item.kind is StreamKind.THINKING]
    tool_items = [item for item in items if item.kind is StreamKind.TOOL_CALL]

    assert [item.thinking for item in thinking_items] == ["Vou usar a ferramenta ", "de busca."]
    assert len(tool_items) == 1
    assert tool_items[0].thinking == "Vou usar a ferramenta de busca."


def test_outbound_requests_strip_internal_underscore_fields() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
            "_tool_image_result": True,
        }
    ]
    for provider in ("openai", "anthropic", "ollama"):
        projected = project_messages(messages, provider)
        assert "_tool_image_result" not in projected[0]
        transport = HTTPProviderStreamTransport("https://api.example.com", "sk-test", "test-model", provider=provider)
        try:
            _, _, body = transport._request_for(messages, [], None, None)
            assert "_tool_image_result" not in body["messages"][0]
        finally:
            transport.close()

