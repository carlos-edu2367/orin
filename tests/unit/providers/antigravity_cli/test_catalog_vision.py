"""Antigravity models that see images are catalogued as such.

agy's stream-json input accepts only text, but an image returned in an MCP tool
result reaches the model (checked against agy 1.5.3). Orin's view_file returns
the picture that way, so Gemini and Claude must be listed with the ``image``
input modality; otherwise the composer says "this model cannot see" and every
attachment is routed to the visual-reading model instead.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Iterator

import pytest
from sqlalchemy import create_engine, update

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata, provider_model_catalog
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderModelCatalogService
from agentos.providers.antigravity_cli import catalog as antigravity_catalog
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.catalog import (
    ANTIGRAVITY_MODELS_CACHE_TTL_SECONDS,
    FALLBACK_ANTIGRAVITY_MODELS,
    antigravity_input_modalities,
    clear_antigravity_models_cache,
    discover_antigravity_models,
)
from agentos.providers.cli_adapter import CliModelInfo, CliTurnRequest
from agentos.providers.cli_registry import CLI_PROVIDERS


@pytest.fixture(autouse=True)
def _isolated_antigravity_models_cache() -> Iterator[None]:
    clear_antigravity_models_cache()
    yield
    clear_antigravity_models_cache()


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("gemini-4-argon-high", ("text", "image", "audio", "video")),
        ("gemini-3.8-flash-low", ("text", "image", "audio", "video")),
        ("claude-opus-5-5-medium", ("text", "image")),
        ("google-antigravity/gemini-4-argon-low", ("text", "image", "audio", "video")),
        ("gpt-oss-120b-medium", ("text",)),
        ("some-future-model", ("text",)),
    ],
)
def test_input_modalities_follow_the_model_family(slug: str, expected: tuple[str, ...]) -> None:
    assert antigravity_input_modalities(slug) == expected


def test_fallback_catalog_declares_vision_for_gemini_and_claude() -> None:
    by_id = {model.id: model.input_modalities for model in FALLBACK_ANTIGRAVITY_MODELS}
    assert by_id["gemini-4-argon-high"] == ("text", "image", "audio", "video")
    assert by_id["claude-sonnet-5-5-low"] == ("text", "image")
    assert by_id["gpt-oss-120b-medium"] == ("text",)
    assert all(model.input_modalities for model in FALLBACK_ANTIGRAVITY_MODELS)


def test_adapter_says_images_reach_the_model_through_tool_results() -> None:
    assert AntigravityCliAdapter.capabilities.images_in_tool_results is True


def _service(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("test-secret-key-for-cli-registry-32b")
    PostgresProviderConfigurationAdapter(engine, cipher=cipher).configure(
        {
            "provider": "google-antigravity",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
            "risk_acknowledged": True,
        }
    )
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", AntigravityCliAdapter)
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: None,
    )
    service = ProviderModelCatalogService(PostgresProviderCatalogRepository(engine, cipher=cipher))
    return engine, service, ProviderCatalogContext(user_id="user_1", purpose="provider.catalog")


def test_list_refreshes_a_catalog_stored_as_text_only_and_keeps_favorites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    service.favorite(ctx, "google-antigravity", "gemini-4-argon-high", True)
    # The catalog an older Orin stored: every model text-only.
    with engine.begin() as connection:
        connection.execute(update(provider_model_catalog).values(input_modalities=["text"]))

    models = {item.model_id: item for item in service.list(ctx, "google-antigravity")}

    assert models["gemini-4-argon-high"].input_modalities == ("text", "image", "audio", "video")
    assert models["gpt-oss-120b-medium"].input_modalities == ("text",)
    assert models["gemini-4-argon-high"].is_favorite is True


def test_a_text_only_custom_model_does_not_force_a_refresh_on_every_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    service.add_custom(ctx, "google-antigravity", "gemini-custom-preview")
    with engine.begin() as connection:
        connection.execute(
            update(provider_model_catalog)
            .where(provider_model_catalog.c.model_id == "gemini-custom-preview")
            .values(input_modalities=["text"])
        )
    calls: list[str] = []
    original = service.refresh
    monkeypatch.setattr(service, "refresh", lambda c, p: calls.append(p) or original(c, p))

    service.list(ctx, "google-antigravity")

    assert calls == []


def test_a_custom_gemini_model_is_added_as_seeing_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _, service, ctx = _service(monkeypatch)
    service.refresh(ctx, "google-antigravity")
    record = service.add_custom(ctx, "google-antigravity", "gemini-custom-preview")
    assert record.input_modalities == ("text", "image", "audio", "video")


def test_fallback_includes_gemini_4_carbon_at_top_with_readable_labels() -> None:
    top_three = FALLBACK_ANTIGRAVITY_MODELS[:3]
    assert [m.id for m in top_three] == [
        "gemini-4-carbon-medium",
        "gemini-4-carbon-high",
        "gemini-4-carbon-low",
    ]
    assert [m.display_name for m in top_three] == [
        "Gemini 4 Carbon (Medium) (Assinatura Antigravity)",
        "Gemini 4 Carbon (High) (Assinatura Antigravity)",
        "Gemini 4 Carbon (Low) (Assinatura Antigravity)",
    ]
    assert all(m.context_window == 1_000_000 for m in top_three)
    assert all(
        m.input_modalities == ("text", "image", "audio", "video")
        for m in top_three
    )
    for model in FALLBACK_ANTIGRAVITY_MODELS:
        assert model.display_name.endswith("(Assinatura Antigravity)")
        assert not model.display_name.startswith(model.id)


def test_discover_parses_agy_labels_and_falls_back_to_slug_when_label_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = (
        "Fetching available models...\n"
        "gemini-4-carbon-high\tGemini 4 Carbon (High)\n"
        "gemini-5-experimental-low\t   \n"
        "claude-opus-5-5-high\t\n"
    )
    monkeypatch.setattr(
        antigravity_catalog.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=stdout, stderr=""
        ),
    )

    models = discover_antigravity_models("/fake/bin/agy", run_models_cmd=True)

    assert [(m.id, m.display_name, m.context_window, m.supported_efforts) for m in models] == [
        (
            "gemini-4-carbon-high",
            "Gemini 4 Carbon (High) (Assinatura Antigravity)",
            1_000_000,
            ("high",),
        ),
        (
            "gemini-5-experimental-low",
            "gemini-5-experimental-low (Assinatura Antigravity)",
            1_000_000,
            ("low",),
        ),
        (
            "claude-opus-5-5-high",
            "claude-opus-5-5-high (Assinatura Antigravity)",
            200_000,
            ("high",),
        ),
    ]


def test_models_without_cache_returns_fallback_without_running_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: "/fake/bin/agy",
    )

    def _forbid_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run must not be called by models()")

    monkeypatch.setattr(antigravity_catalog.subprocess, "run", _forbid_subprocess)

    adapter = AntigravityCliAdapter()
    assert adapter.models() == FALLBACK_ANTIGRAVITY_MODELS


def test_refresh_models_populates_cache_and_subsequent_models_returns_live_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: "/fake/bin/agy",
    )
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=(
                "Fetching available models...\n"
                "gemini-4-xenon-high\tGemini 4 Xenon (High)\n"
                "gemini-4-carbon-medium\tGemini 4 Carbon (Medium)\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(antigravity_catalog.subprocess, "run", _fake_run)

    adapter = AntigravityCliAdapter()
    refreshed = adapter.refresh_models()

    assert len(calls) == 1
    assert [m.id for m in refreshed] == [
        "gemini-4-xenon-high",
        "gemini-4-carbon-medium",
    ]
    assert refreshed[0].display_name == "Gemini 4 Xenon (High) (Assinatura Antigravity)"

    # Subsequent models() calls (even on a fresh adapter instance) read the cache without subprocess
    second_adapter = AntigravityCliAdapter()
    assert second_adapter.models() == refreshed
    assert len(calls) == 1


def test_cache_is_keyed_by_binary_path_and_expires_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(antigravity_catalog.time, "monotonic", lambda: clock[0])

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="gemini-4-xenon-low\tGemini 4 Xenon (Low)\n",
            stderr="",
        )

    monkeypatch.setattr(antigravity_catalog.subprocess, "run", _fake_run)

    adapter_a = AntigravityCliAdapter(binary="/opt/agy-a/agy")
    adapter_b = AntigravityCliAdapter(binary="/opt/agy-b/agy")

    live_a = adapter_a.refresh_models()
    assert [m.id for m in adapter_a.models()] == ["gemini-4-xenon-low"]
    # Different binary path does not share adapter_a's cache entry
    assert adapter_b.models() == FALLBACK_ANTIGRAVITY_MODELS

    # Advance past the 30-minute TTL
    clock[0] += ANTIGRAVITY_MODELS_CACHE_TTL_SECONDS + 1.0
    assert adapter_a.models() == FALLBACK_ANTIGRAVITY_MODELS
    assert live_a != FALLBACK_ANTIGRAVITY_MODELS


@pytest.mark.parametrize("failure_mode", ["nonzero_exit", "timeout", "exception"])
def test_agy_failure_returns_fallback_and_preserves_valid_cache(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: "/fake/bin/agy",
    )

    def _failing_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure_mode == "nonzero_exit":
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="error"
            )
        if failure_mode == "timeout":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=15.0)
        raise OSError("binary execution failed")

    # 1. With empty cache, failure returns fallback and does not poison cache
    monkeypatch.setattr(antigravity_catalog.subprocess, "run", _failing_run)
    adapter = AntigravityCliAdapter()
    assert adapter.refresh_models() == FALLBACK_ANTIGRAVITY_MODELS
    assert adapter.models() == FALLBACK_ANTIGRAVITY_MODELS

    # 2. Populate valid cache first
    monkeypatch.setattr(
        antigravity_catalog.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="gemini-4-xenon-high\tGemini 4 Xenon (High)\n",
            stderr="",
        ),
    )
    live_models = adapter.refresh_models()
    assert [m.id for m in live_models] == ["gemini-4-xenon-high"]
    assert adapter.models() == live_models

    # 3. Subsequent failure returns fallback and does NOT erase the valid cache
    monkeypatch.setattr(antigravity_catalog.subprocess, "run", _failing_run)
    assert adapter.refresh_models() == FALLBACK_ANTIGRAVITY_MODELS
    assert adapter.models() == live_models


def test_service_refresh_uses_adapter_refresh_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, ctx = _service(monkeypatch)
    refresh_calls: list[int] = []
    live_model = CliModelInfo(
        id="gemini-4-carbon-high",
        display_name="Gemini 4 Carbon (High) (Assinatura Antigravity)",
        context_window=1_000_000,
        supported_efforts=("high",),
        input_modalities=("text", "image", "audio", "video"),
    )
    brand_new_model = CliModelInfo(
        id="gemini-4-xenon-medium",
        display_name="Gemini 4 Xenon (Medium) (Assinatura Antigravity)",
        context_window=1_000_000,
        supported_efforts=("medium",),
        input_modalities=("text", "image", "audio", "video"),
    )

    def _fake_refresh_models(self: AntigravityCliAdapter, env: object = None) -> tuple[CliModelInfo, ...]:
        refresh_calls.append(1)
        return (live_model, brand_new_model)

    monkeypatch.setattr(AntigravityCliAdapter, "refresh_models", _fake_refresh_models)

    receipt = service.refresh(ctx, "google-antigravity")
    stored = {item.model_id: item for item in service.list(ctx, "google-antigravity")}

    assert len(refresh_calls) == 1
    assert receipt.count == 3
    assert set(stored) == {"gemini-4-carbon-high", "gemini-4-xenon-medium", "antigravity-image"}
    assert stored["gemini-4-xenon-medium"].display_name == "Gemini 4 Xenon (Medium) (Assinatura Antigravity)"
    assert stored["gemini-4-xenon-medium"].input_modalities == ("text", "image", "audio", "video")
    assert stored["antigravity-image"].output_modalities == ("image",)


def test_build_command_infers_supported_efforts_from_suffix_for_unknown_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda env=None: None,
    )
    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("system prompt", encoding="utf-8")
    adapter = AntigravityCliAdapter()

    # Unknown model with '-high' suffix allows matching 'high' effort
    cmd_match = adapter.build_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-5-unreleased-high",
            effort="high",
        )
    )
    assert "--model" in cmd_match
    assert cmd_match[cmd_match.index("--model") + 1] == "gemini-5-unreleased-high"
    assert "--effort" in cmd_match
    assert cmd_match[cmd_match.index("--effort") + 1] == "high"

    # Unknown model with '-high' suffix rejects conflicting 'low' effort
    cmd_mismatch = adapter.build_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-5-unreleased-high",
            effort="low",
        )
    )
    assert "--effort" not in cmd_mismatch

    # Unknown model without suffix infers standard efforts ('low', 'medium', 'high')
    cmd_std = adapter.build_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-5-unreleased",
            effort="medium",
        )
    )
    assert "--effort" in cmd_std
    assert cmd_std[cmd_std.index("--effort") + 1] == "medium"

    cmd_non_std = adapter.build_command(
        CliTurnRequest(
            binary="agy",
            system_prompt_file=sys_file,
            model="gemini-5-unreleased",
            effort="xhigh",
        )
    )
    assert "--effort" not in cmd_non_std

