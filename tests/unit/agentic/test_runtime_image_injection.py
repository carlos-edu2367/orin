from agentos.agentic.runtime import AgenticTurnRuntime

TURN = {"turn_id": "t1", "conversation_id": "c1", "provider": "anthropic", "user_id": "u1"}


def test_an_image_result_becomes_a_following_user_message():
    result = {"id": "call-1", "content": "Imagem anexada.", "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}]}
    messages = AgenticTurnRuntime._tool_result_messages(TURN, result)
    assert len(messages) == 2
    assert messages[0]["content"][0]["type"] == "tool_result"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][-1] == {"type": "image", "media_type": "image/png", "data": "QUJD"}


def test_a_text_result_still_produces_one_message():
    messages = AgenticTurnRuntime._tool_result_messages(TURN, {"id": "call-1", "content": "ok"})
    assert len(messages) == 1


def test_the_openai_shape_is_preserved_for_the_tool_message():
    turn = {**TURN, "provider": "openrouter"}
    messages = AgenticTurnRuntime._tool_result_messages(turn, {"id": "call-1", "content": "ok", "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}]})
    assert messages[0]["role"] == "tool" and messages[0]["tool_call_id"] == "call-1"
    assert messages[1]["role"] == "user"


def test_a_browser_screenshot_is_captioned_as_the_current_page_not_a_file():
    result = {
        "id": "call-1", "name": "browse_page",
        "content": "https://example.test/page\n\nsome page text",
        "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}],
    }
    messages = AgenticTurnRuntime._tool_result_messages(TURN, result)
    caption = messages[1]["content"][0]
    assert caption["type"] == "text"
    assert caption["text"] == "Captura da página atual (https://example.test/page):"


def test_a_non_browser_image_keeps_the_generic_file_caption():
    result = {
        "id": "call-1", "name": "view_file",
        "content": "some description",
        "images": [{"type": "image", "media_type": "image/png", "data": "QUJD"}],
    }
    messages = AgenticTurnRuntime._tool_result_messages(TURN, result)
    assert messages[1]["content"][0]["text"] == "Conteúdo visual do arquivo solicitado:"
