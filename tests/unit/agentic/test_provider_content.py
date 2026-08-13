from agentos.agentic.provider_content import image_block, project_messages

IMAGE = image_block("image/png", "QUJD")


def test_anthropic_gets_a_base64_source():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "anthropic")
    assert projected[0]["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def test_openai_gets_a_data_uri():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "openrouter")
    assert projected[0]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


def test_ollama_moves_images_to_their_own_field():
    projected = project_messages([{"role": "user", "content": [{"type": "text", "text": "leia"}, IMAGE]}], "ollama")
    assert projected[0] == {"role": "user", "content": "leia", "images": ["QUJD"]}


def test_a_text_only_message_is_untouched():
    messages = [{"role": "user", "content": "olá"}]
    assert project_messages(messages, "anthropic") == messages


def test_projection_does_not_mutate_the_input():
    original = [{"role": "user", "content": [IMAGE]}]
    project_messages(original, "ollama")
    assert original[0]["content"] == [IMAGE]
