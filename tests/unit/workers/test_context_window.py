from __future__ import annotations

from pathlib import Path

import pytest

from agentos.providers.claude_cli.provider import DEFAULT_CLAUDE_CLI_BRIDGE_URL
import agentos.providers.claude_cli.provider as provider_mod
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.workers.chat import ChatWorker

FIXTURES = Path(__file__).resolve().parents[1] / "providers" / "claude_cli" / "fixtures"


def test_context_window_for_returns_effective_200k_even_when_catalog_says_1m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = ChatWorker.__new__(ChatWorker)
    worker._claude_account_reader = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json")
    monkeypatch.setattr(
        worker,
        "_catalog_row_for",
        lambda _turn: {
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            "context_window": 1_000_000,
        },
    )
    monkeypatch.setattr(
        "agentos.providers.claude_cli.catalog.cached_cli_context_window_for",
        lambda _model, **_kw: 1_000_000,
    )
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    turn = {
        "user_id": "u1",
        "provider": "anthropic",
        "model_id": "claude-fable-5-2",
    }

    ew = worker._effective_window_for(turn)
    assert ew is not None
    assert ew.tokens == 200_000
    assert ew.source == "account_credits"
    assert ew.rule_index == 3
    assert worker._context_window_for(turn) == 200_000


def test_context_window_for_respects_manual_override_over_catalog_and_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = ChatWorker.__new__(ChatWorker)
    worker._claude_account_reader = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json")
    monkeypatch.setattr(
        worker,
        "_catalog_row_for",
        lambda _turn: {
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            "context_window": 1_000_000,
        },
    )
    monkeypatch.setattr(
        worker,
        "_context_window_settings",
        lambda _turn: ({"anthropic/claude-fable-5-2": 320_000}, False),
    )
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    turn = {
        "user_id": "u1",
        "provider": "anthropic",
        "model_id": "claude-fable-5-2",
    }
    ew = worker._effective_window_for(turn)
    assert ew is not None
    assert ew.tokens == 320_000
    assert ew.source == "override"
    assert ew.rule_index == 1
    assert worker._context_window_for(turn) == 320_000
