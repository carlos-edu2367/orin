from agentos.reading.selection import (
    PROVIDERS_WITHOUT_INLINE_IMAGES,
    VisionModel,
    choose_vision_model,
)

CANDIDATES = (
    VisionModel("openrouter", "gpt-4o"),
    VisionModel("ollama", "qwen2.5-vl"),
    VisionModel("anthropic", "claude-sonnet"),
)


def test_the_override_wins():
    chosen = choose_vision_model(CANDIDATES, turn_provider="anthropic", override=VisionModel("openrouter", "gpt-4o"))
    assert chosen == VisionModel("openrouter", "gpt-4o")


def test_an_override_that_is_no_longer_available_is_ignored():
    chosen = choose_vision_model(CANDIDATES, turn_provider="anthropic", override=VisionModel("openai", "sumiu"))
    assert chosen == VisionModel("anthropic", "claude-sonnet")


def test_the_turn_provider_is_preferred():
    assert choose_vision_model(CANDIDATES, turn_provider="anthropic") == VisionModel("anthropic", "claude-sonnet")


def test_a_local_ollama_comes_before_any_cloud_provider():
    assert choose_vision_model(CANDIDATES, turn_provider="mistral") == VisionModel("ollama", "qwen2.5-vl")


def test_any_remaining_candidate_is_used_as_a_last_resort():
    assert choose_vision_model((VisionModel("openrouter", "gpt-4o"),), turn_provider="mistral") == VisionModel("openrouter", "gpt-4o")


def test_no_candidate_returns_none():
    assert choose_vision_model((), turn_provider="anthropic") is None


def test_cli_providers_without_inline_images_are_excluded_from_selection():
    assert "google-antigravity" in PROVIDERS_WITHOUT_INLINE_IMAGES
    assert "openai-codex" in PROVIDERS_WITHOUT_INLINE_IMAGES
    mixed = (
        VisionModel("google-antigravity", "gemini-4-argon-high"),
        VisionModel("openai-codex", "gpt-6.1-sol"),
        VisionModel("ollama", "qwen2.5-vl"),
    )
    assert choose_vision_model(
        mixed,
        turn_provider="openai-codex",
        override=VisionModel("google-antigravity", "gemini-4-argon-high"),
    ) == VisionModel("ollama", "qwen2.5-vl")
    assert choose_vision_model(
        (
            VisionModel("google-antigravity", "gemini-4-argon-high"),
            VisionModel("openai-codex", "gpt-6.1-sol"),
        ),
        turn_provider="openai-codex",
    ) is None
