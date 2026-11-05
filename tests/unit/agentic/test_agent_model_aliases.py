from __future__ import annotations

from agentos.agentic.agent_definitions import DEFAULT_MODEL_ALIASES, resolve_agent_model
from agentos.providers.claude_cli.catalog import FAMILY_DEFAULTS


def test_default_aliases_are_the_5x_family() -> None:
    assert resolve_agent_model("opus", {}) == "claude-opus-5-5"
    assert resolve_agent_model("sonnet", {}) == "claude-sonnet-5-5"
    assert resolve_agent_model("haiku", {}) == "claude-haiku-5-5"
    assert resolve_agent_model("fable", None) == "claude-fable-5-1"
    assert dict(DEFAULT_MODEL_ALIASES) == dict(FAMILY_DEFAULTS)
