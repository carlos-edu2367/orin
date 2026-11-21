"""Tests for WS-A1: per-call provider/model/effort routing on Agent and resolve_child_target."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.agent_definitions import AgentCatalog, AgentDefinition
from agentos.agentic.child_routing import (
    CHILD_MODEL_NOT_FAVORITE,
    CHILD_PROVIDER_PINNED,
    CHILD_PROVIDER_UNAVAILABLE,
    CHILD_QUOTA_BLOCKED,
    ChildTarget,
    resolve_child_target,
)
from agentos.agentic.session import TurnSession
from agentos.agentic.tools.agents.agent import AgentInput
from agentos.agentic.tools.public_names import PublicToolNames, arguments_in
from agentos.providers.cli_adapter import CliTurnRequest
from agentos.providers.codex_cli.command import build_codex_command
from agentos.quota.models import Confidence, QuotaStatus
from agentos.settings.loader import EffectiveSettings, SettingsLayer
from tests.unit.agentic.test_nested_subagents import _TreeStore
from tests.unit.agentic.test_parallel_and_background_subagents import _text_batch


class _FakeQuotaService:
    def __init__(self, statuses: dict[str, QuotaStatus]) -> None:
        self._statuses = statuses
        self.calls: list[tuple[str, str, str | None]] = []

    def status_for(self, user_id: str, provider: str, model_id: str | None = None) -> QuotaStatus:
        self.calls.append((user_id, provider, model_id))
        return self._statuses.get(
            provider,
            QuotaStatus(max_percent=10.0, rejected=False, confidence=Confidence.MEASURED, observed_at=datetime.now(UTC)),
        )


class _FakeCodexProvider:
    """Fake Codex provider that records incoming stream requests and builds real Codex CLI argv."""

    def __init__(self, reply: str = "verificado via codex") -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []
        self.codex_argv: list[list[str]] = []

    def stream(self, request: dict[str, Any]):
        self.requests.append(dict(request))
        cli_req = CliTurnRequest(
            binary="codex",
            system_prompt_file=Path("prompt.md"),
            model=str(request.get("model") or "gpt-6-sol"),
            effort=request.get("effort"),
        )
        self.codex_argv.append(build_codex_command(cli_req))
        return iter(_text_batch(self.reply))


def _claude_parent_turn() -> dict[str, object]:
    return {
        "turn_id": "turn-a1-1",
        "conversation_id": "conv-a1-1",
        "user_id": "user-a1",
        "execution_id": "exec-a1-1",
        "assistant_message_id": "msg-a1-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "code_mode": "code",
        "prompt": "Delegue para o Codex",
    }


def _settings_with(data: dict[str, Any]) -> EffectiveSettings:
    return EffectiveSettings(
        data=data,
        provenance={k: "user" for k in data},
        layers=(SettingsLayer(name="user", path=None, data=data),),
        errors=(),
        trusted=True,
    )


def _make_session(
    tmp_path: Path,
    *,
    child_provider_factory,
    favorites_by_provider: dict[str, tuple[str, ...]] | None = None,
    configured_providers: tuple[str, ...] | None = None,
    quota_service: object | None = None,
    definitions: list[AgentDefinition] | None = None,
    settings_data: dict[str, Any] | None = None,
) -> TurnSession:
    turn = _claude_parent_turn()
    store = _TreeStore()
    favs = favorites_by_provider if favorites_by_provider is not None else {
        "anthropic": ("claude-opus-5-5", "claude-sonnet-5-0"),
        "openai-codex": ("gpt-6-sol", "gpt-6-luna"),
    }
    session = TurnSession(
        turn=turn,
        store=store,
        provider_factory=lambda: _FakeCodexProvider("parent"),
        workspace_root=tmp_path,
        child_model_ids=favs.get("anthropic", ()),
        child_provider_factory=child_provider_factory,
        favorites_by_provider=favs,
        configured_providers=configured_providers,
        quota_service=quota_service,
    )
    if definitions is not None:
        session._agent_definitions = AgentCatalog(agents={d.name: d for d in definitions})
    if settings_data is not None:
        session.effective_settings = _settings_with(settings_data)
    return session


# -- Criterion 1: parent on Claude CLI -> child on Codex with model_reasoning_effort="high" -------------------


def test_claude_parent_calls_codex_child_with_high_effort_via_agent_tool(tmp_path: Path) -> None:
    fake_codex = _FakeCodexProvider("revisao codex concluida")
    routed: list[tuple[str, str | None]] = []

    def factory(model_id: str, provider: str | None = None):
        routed.append((model_id, provider))
        assert provider == "openai-codex"
        return fake_codex

    session = _make_session(
        tmp_path,
        child_provider_factory=factory,
        configured_providers=("anthropic", "openai-codex"),
    )
    toolset = session.build_runtime().toolset
    # Public Agent call arguments -> internal run_subagent arguments
    raw_args = arguments_in(
        "run_subagent",
        {
            "subagent_type": "general",
            "provider": "openai-codex",
            "model_id": "gpt-6-sol",
            "effort": "high",
            "description": "Revisar no Codex",
            "prompt": "Verifique a arquitetura.",
        },
    )
    outcome = toolset.run_subagent(**raw_args)

    assert outcome.status == "succeeded", outcome.content
    assert "revisao codex concluida" in outcome.content
    assert routed == [("gpt-6-sol", "openai-codex")]
    assert len(fake_codex.requests) == 1
    assert fake_codex.requests[0]["provider"] == "openai-codex"
    assert fake_codex.requests[0]["model"] == "gpt-6-sol"
    assert fake_codex.requests[0]["effort"] == "high"
    # Verify Codex CLI command builder translates effort="high" to model_reasoning_effort="high"
    assert 'model_reasoning_effort="high"' in fake_codex.codex_argv[0]


def test_run_child_internal_api_routes_provider_model_and_effort(tmp_path: Path) -> None:
    fake_codex = _FakeCodexProvider("ok via run_child")
    routed: list[tuple[str, str | None]] = []

    def factory(model_id: str, provider: str | None = None):
        routed.append((model_id, provider))
        return fake_codex

    session = _make_session(
        tmp_path,
        child_provider_factory=factory,
        configured_providers=("anthropic", "openai-codex"),
    )
    outcome = session.run_child(
        "verifier",
        "Verifique o diff.",
        provider="openai-codex",
        model_id="gpt-6-sol",
        effort="high",
        description="Verificação cruzada",
    )
    assert outcome.status == "succeeded", outcome.content
    assert routed == [("gpt-6-sol", "openai-codex")]
    assert fake_codex.requests[0]["effort"] == "high"


def test_background_subagent_preserves_provider_routing(tmp_path: Path) -> None:
    fake_codex = _FakeCodexProvider("bg codex ok")
    routed: list[tuple[str, str | None]] = []

    def factory(model_id: str, provider: str | None = None):
        routed.append((model_id, provider))
        return fake_codex

    session = _make_session(
        tmp_path,
        child_provider_factory=factory,
        configured_providers=("anthropic", "openai-codex"),
    )
    started = session._run_subagent(
        "general",
        "Execute em segundo plano",
        provider="openai-codex",
        model_id="gpt-6-sol",
        effort="high",
        run_in_background=True,
    )
    assert started.status == "succeeded", started.content
    agent_id = str(started.payload["agent_id"])
    checked = session._check_subagent(agent_id, wait_seconds=5)
    assert checked.status == "succeeded", checked.content
    assert "bg codex ok" in checked.content
    assert routed == [("gpt-6-sol", "openai-codex")]
    assert fake_codex.requests[0]["effort"] == "high"


# -- Backwards compatibility & error codes ---------------------------------------------------------------------


def test_omitted_provider_keeps_parent_provider_behavior(tmp_path: Path) -> None:
    fake_parent = _FakeCodexProvider("parent provider reply")
    routed: list[tuple[str, str | None]] = []

    def factory(model_id: str, provider: str | None = None):
        routed.append((model_id, provider))
        return fake_parent

    session = _make_session(tmp_path, child_provider_factory=factory)
    outcome = session._run_subagent("general", "Tarefa normal", model_id="claude-sonnet-5-0")
    assert outcome.status == "succeeded", outcome.content
    assert routed == [("claude-sonnet-5-0", None)]
    assert fake_parent.requests[0]["provider"] == "anthropic"


def test_model_outside_chosen_provider_favorites_returns_child_model_not_favorite(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        child_provider_factory=lambda _m, **_k: _FakeCodexProvider(),
        configured_providers=("anthropic", "openai-codex"),
    )
    outcome = session._run_subagent(
        "general",
        "Teste modelo invalido",
        provider="openai-codex",
        model_id="gpt-99-inexistente",
    )
    assert outcome.status == "failed"
    assert outcome.error_code == CHILD_MODEL_NOT_FAVORITE


def test_pinned_definition_rejects_different_provider_with_child_provider_pinned(tmp_path: Path) -> None:
    pinned_def = AgentDefinition(
        name="revisor-codex",
        description="Revisor fixo no Codex",
        prompt="Revise.",
        origin="user",
        provider="openai-codex",
        models=("gpt-6-sol",),
    )
    session = _make_session(
        tmp_path,
        child_provider_factory=lambda _m, **_k: _FakeCodexProvider(),
        configured_providers=("anthropic", "openai-codex", "google-antigravity"),
        favorites_by_provider={
            "anthropic": ("claude-opus-5-5",),
            "openai-codex": ("gpt-6-sol",),
            "google-antigravity": ("gemini-4-neon-high",),
        },
        definitions=[pinned_def],
    )
    outcome = session._run_subagent(
        "revisor-codex",
        "Revise com outro provedor",
        provider="google-antigravity",
        model_id="gemini-4-neon-high",
    )
    assert outcome.status == "failed"
    assert outcome.error_code == CHILD_PROVIDER_PINNED


def test_unconfigured_provider_returns_child_provider_unavailable(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        child_provider_factory=lambda _m, **_k: _FakeCodexProvider(),
        configured_providers=("anthropic",),
    )
    outcome = session._run_subagent(
        "general",
        "Tente provedor nao configurado",
        provider="google-antigravity",
        model_id="gemini-4-neon-high",
    )
    assert outcome.status == "failed"
    assert outcome.error_code == CHILD_PROVIDER_UNAVAILABLE


def test_quota_policy_enabled_blocks_when_rejected_or_over_threshold(tmp_path: Path) -> None:
    rejected_quota = _FakeQuotaService(
        {
            "openai-codex": QuotaStatus(
                max_percent=95.0,
                rejected=True,
                confidence=Confidence.MEASURED,
                observed_at=datetime.now(UTC),
            )
        }
    )
    session_enabled = _make_session(
        tmp_path,
        child_provider_factory=lambda _m, **_k: _FakeCodexProvider(),
        configured_providers=("anthropic", "openai-codex"),
        quota_service=rejected_quota,
        settings_data={"quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}}},
    )
    blocked = session_enabled._run_subagent(
        "general",
        "Rode no Codex",
        provider="openai-codex",
        model_id="gpt-6-sol",
    )
    assert blocked.status == "failed"
    assert blocked.error_code == CHILD_QUOTA_BLOCKED

    # With quota.policy.enabled=False, it NEVER blocks even when rejected=True and 100%
    session_disabled = _make_session(
        tmp_path,
        child_provider_factory=lambda _m, **_k: _FakeCodexProvider("permitido sem politica"),
        configured_providers=("anthropic", "openai-codex"),
        quota_service=rejected_quota,
        settings_data={"quota": {"policy": {"enabled": False, "blockBackgroundPercent": 90}}},
    )
    allowed = session_disabled._run_subagent(
        "general",
        "Rode no Codex",
        provider="openai-codex",
        model_id="gpt-6-sol",
    )
    assert allowed.status == "succeeded", allowed.content


# -- Direct unit tests of resolve_child_target & tool schemas --------------------------------------------------


def test_resolve_child_target_picks_first_favorite_when_model_omitted_on_new_provider() -> None:
    target = resolve_child_target(
        requested_provider="openai-codex",
        requested_model=None,
        parent_provider="anthropic",
        favorites_by_provider={
            "anthropic": ["claude-opus-5-5"],
            "openai-codex": ["gpt-6-sol", "gpt-6-luna"],
        },
        definition_pin=None,
        configured_providers=("anthropic", "openai-codex"),
        effort="high",
    )
    assert isinstance(target, ChildTarget)
    assert target.provider == "openai-codex"
    assert target.model_id == "gpt-6-sol"
    assert target.model_chain == ("gpt-6-sol",)
    assert target.effort == "high"
    assert target.pinned is True


def test_resolve_child_target_fails_when_switched_provider_has_no_favorites() -> None:
    outcome = resolve_child_target(
        requested_provider="openai-codex",
        requested_model=None,
        parent_provider="anthropic",
        favorites_by_provider={"anthropic": ["claude-opus-5-5"], "openai-codex": []},
        definition_pin=None,
        configured_providers=("anthropic", "openai-codex"),
    )
    assert not isinstance(outcome, ChildTarget)
    assert outcome.error_code == CHILD_PROVIDER_UNAVAILABLE


def test_resolve_child_target_quota_threshold_without_rejection() -> None:
    quota = _FakeQuotaService(
        {
            "openai-codex": QuotaStatus(
                max_percent=91.0,
                rejected=False,
                confidence=Confidence.MEASURED,
                observed_at=datetime.now(UTC),
            )
        }
    )
    blocked = resolve_child_target(
        "openai-codex",
        "gpt-6-sol",
        "anthropic",
        {"openai-codex": ["gpt-6-sol"]},
        None,
        quota,
        user_id="u1",
        configured_providers=("anthropic", "openai-codex"),
        quota_policy_enabled=True,
        quota_block_percent=90,
    )
    assert not isinstance(blocked, ChildTarget)
    assert blocked.error_code == CHILD_QUOTA_BLOCKED

    allowed = resolve_child_target(
        "openai-codex",
        "gpt-6-sol",
        "anthropic",
        {"openai-codex": ["gpt-6-sol"]},
        None,
        quota,
        user_id="u1",
        configured_providers=("anthropic", "openai-codex"),
        quota_policy_enabled=False,
        quota_block_percent=90,
    )
    assert isinstance(allowed, ChildTarget)
    assert allowed.provider == "openai-codex"


def test_agent_public_schema_exposes_provider_and_maps_model_to_model_id() -> None:
    schema = AgentInput.model_json_schema()
    assert "provider" in schema["properties"]
    assert "model" in schema["properties"]
    assert "effort" in schema["properties"]
    mapped = arguments_in(
        "run_subagent",
        {"subagent_type": "general-purpose", "provider": "openai-codex", "model": "gpt-6-sol", "prompt": "check"},
    )
    assert mapped["provider"] == "openai-codex"
    assert mapped["model_id"] == "gpt-6-sol"
    assert mapped["subagent_type"] == "general"
    assert mapped["task"] == "check"
    # PublicToolNames description mentions provider selection
    translator = PublicToolNames()
    out_schemas = translator.schemas_out(
        [{"type": "function", "function": {"name": "run_subagent", "description": "x", "parameters": {}}}]
    )
    assert out_schemas[0]["function"]["name"] == "Agent"
    assert "provider" in out_schemas[0]["function"]["parameters"]["properties"]
    assert "provider" in out_schemas[0]["function"]["description"]
