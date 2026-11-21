from __future__ import annotations

import httpx

from agentos.agentic.effort import THINKING_BUDGET, apply_effort
from agentos.agentic.effort_levels import EffortAdjustment
from agentos.agentic.provider_stream import HTTPProviderStreamTransport


def test_anthropic_claude_5_uses_adaptive_thinking_and_output_config_without_budget_tokens() -> None:
    payload: dict = {"model": "claude-opus-5-5", "max_tokens": 8_192}
    result = apply_effort("anthropic", payload, "high")
    assert result["thinking"] == {"type": "adaptive"}
    assert result["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in result["thinking"]
    assert result["max_tokens"] == 8_192


def test_anthropic_claude_5_explicit_cap_never_inflates_max_tokens() -> None:
    transport = HTTPProviderStreamTransport(
        "https://api.anthropic.com/v1",
        "k",
        "claude-opus-5-5",
        provider="anthropic",
        client=httpx.Client(),
    )
    _, _, payload = transport._request_for(
        [{"role": "user", "content": "hi"}],
        [],
        None,
        1_024,
        "max",
    )
    assert payload["max_tokens"] == 1_024
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "max"}
    assert "budget_tokens" not in payload["thinking"]


def test_anthropic_legacy_model_keeps_budget_tokens() -> None:
    payload: dict = {"model": "claude-sonnet-4-5", "max_tokens": 8_192}
    result = apply_effort("anthropic", payload, "high")
    assert result["thinking"] == {"type": "enabled", "budget_tokens": THINKING_BUDGET["high"]}
    assert "output_config" not in result


def test_anthropic_never_emits_disabled_thinking_even_for_fable_5_2() -> None:
    for model_id in ("claude-fable-5-2", "claude-opus-5-5", "claude-haiku-4-5-20251001"):
        for eff in (None, "", "high"):
            payload: dict = {"model": model_id, "max_tokens": 4_096}
            result = apply_effort("anthropic", payload, eff)
            thinking = result.get("thinking")
            if isinstance(thinking, dict):
                assert thinking.get("type") != "disabled"
        payload_none: dict = {"model": model_id, "max_tokens": 4_096}
        apply_effort("anthropic", payload_none, None)
        assert "thinking" not in payload_none
        assert "output_config" not in payload_none


def test_anthropic_forced_tool_choice_strips_thinking_and_records_adjustment() -> None:
    transport = HTTPProviderStreamTransport(
        "https://api.anthropic.com/v1",
        "k",
        "claude-opus-5-5",
        provider="anthropic",
        client=httpx.Client(),
    )
    tools = [{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}]
    for forced in ("required", {"type": "any"}, {"type": "tool", "name": "read_file"}):
        _, _, payload = transport._request_for(
            [{"role": "user", "content": "read"}],
            tools,
            forced,
            None,
            "high",
        )
        assert "thinking" not in payload
        assert "output_config" not in payload
        assert transport.last_effort_adjustment == EffortAdjustment(
            requested="high",
            applied=None,
            reason="forced_tool_choice",
        )


def test_openrouter_and_opencode_preserve_xhigh_without_collapsing() -> None:
    or_payload = apply_effort("openrouter", {"model": "anthropic/claude-opus-5-5"}, "xhigh", capabilities=("reasoning",))
    assert or_payload["reasoning"] == {"effort": "xhigh"}

    oc_payload = apply_effort("opencode", {"model": "gpt-5"}, "xhigh", capabilities=("reasoning",))
    assert oc_payload["reasoning_effort"] == "xhigh"
