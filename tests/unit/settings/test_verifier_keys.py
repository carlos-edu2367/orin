"""Unit tests for WS-A2 verifier settings keys in registry and loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.model_tiers import DEFAULT_MODEL_TIERS
from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings, load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.settings.registry import EFFORT_LEVELS, REGISTRY, ResolvedSetting, Scope, resolve, validate_change


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _synthetic(*layers: tuple[str, dict]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


def test_ws_a2_verifier_keys_registered_with_defaults_and_scopes() -> None:
    expected = {
        "orchestration.verifier.enabled": (False, Scope.PROJECT_OFF_ONLY, "boolean"),
        "orchestration.verifier.modelPolicy": ("cross_family", Scope.USER_ONLY, "choice"),
        "orchestration.verifier.explicit": (None, Scope.USER_ONLY, "object"),
        "orchestration.verifier.effort": ("high", Scope.USER_ONLY, "choice"),
        "orchestration.verifier.maxRounds": (2, Scope.PROJECT_LOWER_ONLY, "number"),
        "orchestration.verifier.includeSubagents": (False, Scope.USER_ONLY, "boolean"),
        "orchestration.modelTiers": (DEFAULT_MODEL_TIERS, Scope.USER_ONLY, "map"),
    }
    empty = empty_settings()
    for path, (default_value, scope, key_type) in expected.items():
        assert path in REGISTRY, f"missing key {path}"
        key = REGISTRY[path]
        assert key.scope is scope
        assert key.type == key_type
        assert key.label.strip()
        assert key.description.strip()
        resolved = resolve(empty, path)
        assert resolved == ResolvedSetting(value=default_value, layer="default", default=default_value)

    assert REGISTRY["orchestration.verifier.modelPolicy"].choices == ("cross_family", "same_model", "explicit")
    assert REGISTRY["orchestration.verifier.effort"].choices == EFFORT_LEVELS
    assert REGISTRY["orchestration.verifier.maxRounds"].minimum == 1
    assert REGISTRY["orchestration.verifier.maxRounds"].maximum == 5

    # WS-A5 registers orchestration.selfVerify.enabled (default False, PROJECT_OFF_ONLY)
    assert "orchestration.selfVerify.enabled" in REGISTRY
    sv_key = REGISTRY["orchestration.selfVerify.enabled"]
    assert sv_key.type == "boolean"
    assert sv_key.default is False
    assert sv_key.scope is Scope.PROJECT_OFF_ONLY
    assert resolve(empty, "orchestration.selfVerify.enabled") == ResolvedSetting(
        value=False, layer="default", default=False
    )


def test_verifier_keys_validation() -> None:
    assert validate_change("orchestration.verifier.enabled", True) is True
    assert validate_change("orchestration.verifier.modelPolicy", "same_model") == "same_model"
    assert validate_change("orchestration.verifier.modelPolicy", "explicit") == "explicit"
    assert validate_change("orchestration.verifier.effort", "xhigh") == "xhigh"
    assert validate_change("orchestration.verifier.maxRounds", 1) == 1
    assert validate_change("orchestration.verifier.maxRounds", 5) == 5
    assert validate_change("orchestration.verifier.includeSubagents", True) is True

    # Nullable explicit object
    assert validate_change("orchestration.verifier.explicit", None) is None
    assert validate_change(
        "orchestration.verifier.explicit",
        {"provider": "openai-codex", "model": "gpt-6.1-sol"},
    ) == {"provider": "openai-codex", "model": "gpt-6.1-sol"}
    assert validate_change(
        "orchestration.verifier.explicit",
        {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "high"},
    ) == {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "high"}

    # modelTiers accepts both {id: {"family": ..., "tier": ...}} and {id: tier}
    assert validate_change(
        "orchestration.modelTiers",
        {"custom-m": {"family": "openai", "tier": 4}},
    ) == {"custom-m": {"family": "openai", "tier": 4}}
    assert validate_change(
        "orchestration.modelTiers",
        {"claude-opus-5-5": 5},
    ) == {"claude-opus-5-5": {"family": "anthropic", "tier": 5}}

    # Reject invalid values
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.modelPolicy", "random")
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.effort", "super_high")
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.maxRounds", 0)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.maxRounds", 6)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.explicit", "openai-codex/gpt-6.1-sol")
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.explicit", {"provider": "openai-codex"})
    with pytest.raises(ValueError):
        validate_change(
            "orchestration.verifier.explicit",
            {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "invalid"},
        )
    with pytest.raises(ValueError):
        validate_change(
            "orchestration.verifier.explicit",
            {"provider": "openai-codex", "model": "gpt-6.1-sol", "extra": 1},
        )
    with pytest.raises(ValueError):
        validate_change("orchestration.modelTiers", {"claude-opus-5-5": -1})
    with pytest.raises(ValueError):
        validate_change("orchestration.modelTiers", {"claude-opus-5-5": True})
    with pytest.raises(ValueError):
        validate_change("orchestration.modelTiers", {"claude-opus-5-5": {"family": "", "tier": 4}})


def test_verifier_keys_scopes_in_registry_and_loader(tmp_path: Path) -> None:
    # PROJECT_OFF_ONLY on enabled
    repo_tries_on = _synthetic(("project", {"orchestration": {"verifier": {"enabled": True}}}))
    assert resolve(repo_tries_on, "orchestration.verifier.enabled") == ResolvedSetting(False, "default", False)

    repo_turns_off = _synthetic(
        ("user", {"orchestration": {"verifier": {"enabled": True}}}),
        ("project", {"orchestration": {"verifier": {"enabled": False}}}),
    )
    assert resolve(repo_turns_off, "orchestration.verifier.enabled") == ResolvedSetting(False, "project", False)

    # PROJECT_LOWER_ONLY on maxRounds (default=2)
    repo_tries_raise_default = _synthetic(("project", {"orchestration": {"verifier": {"maxRounds": 5}}}))
    assert resolve(repo_tries_raise_default, "orchestration.verifier.maxRounds") == ResolvedSetting(2, "default", 2)

    repo_lowers_default = _synthetic(("project", {"orchestration": {"verifier": {"maxRounds": 1}}}))
    assert resolve(repo_lowers_default, "orchestration.verifier.maxRounds") == ResolvedSetting(1, "project", 2)

    user_4_repo_2 = _synthetic(
        ("user", {"orchestration": {"verifier": {"maxRounds": 4}}}),
        ("project", {"orchestration": {"verifier": {"maxRounds": 2}}}),
    )
    assert resolve(user_4_repo_2, "orchestration.verifier.maxRounds") == ResolvedSetting(2, "project", 2)

    user_3_repo_5 = _synthetic(
        ("user", {"orchestration": {"verifier": {"maxRounds": 3}}}),
        ("project", {"orchestration": {"verifier": {"maxRounds": 5}}}),
    )
    assert resolve(user_3_repo_5, "orchestration.verifier.maxRounds") == ResolvedSetting(3, "user", 2)

    # USER_ONLY keys merged and protected end-to-end via load_effective_settings
    project = tmp_path / "repo-verifier"
    _write(
        orin_user_dir() / "settings.json",
        {
            "orchestration": {
                "verifier": {
                    "enabled": True,
                    "modelPolicy": "explicit",
                    "explicit": {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "high"},
                    "effort": "xhigh",
                    "maxRounds": 4,
                    "includeSubagents": True,
                },
                "modelTiers": {"custom-model": {"family": "custom", "tier": 4}},
            }
        },
    )
    _write(
        project / ".orin" / "settings.json",
        {
            "orchestration": {
                "verifier": {
                    "enabled": True,
                    "modelPolicy": "same_model",
                    "explicit": {"provider": "anthropic-cli", "model": "claude-haiku-5-5"},
                    "effort": "low",
                    "maxRounds": 2,
                    "includeSubagents": False,
                },
                "modelTiers": {"claude-opus-5-5": {"family": "anthropic", "tier": 1}},
            }
        },
    )
    _write(orin_managed_dir() / "managed-settings.json", {})

    loaded = load_effective_settings(project, trusted=True)
    assert resolve(loaded, "orchestration.verifier.enabled") == ResolvedSetting(True, "user", False)
    assert resolve(loaded, "orchestration.verifier.modelPolicy") == ResolvedSetting("explicit", "user", "cross_family")
    assert resolve(loaded, "orchestration.verifier.explicit") == ResolvedSetting(
        {"provider": "openai-codex", "model": "gpt-6.1-sol", "effort": "high"},
        "user",
        None,
    )
    assert resolve(loaded, "orchestration.verifier.effort") == ResolvedSetting("xhigh", "user", "high")
    assert resolve(loaded, "orchestration.verifier.maxRounds") == ResolvedSetting(2, "project", 2)
    assert resolve(loaded, "orchestration.verifier.includeSubagents") == ResolvedSetting(True, "user", False)

    resolved_tiers = resolve(loaded, "orchestration.modelTiers")
    assert resolved_tiers.layer == "user"
    assert resolved_tiers.value["custom-model"] == {"family": "custom", "tier": 4}
    # Project override on claude-opus-5-5 was ignored; default tier 4 preserved
    assert resolved_tiers.value["claude-opus-5-5"] == {"family": "anthropic", "tier": 4}
