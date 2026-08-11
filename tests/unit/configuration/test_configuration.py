from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentos.configuration import (
    ConfigurationDescriptor,
    ConfigurationManager,
    ConfigurationScope,
    SecretReference,
    SecretRegistry,
    Sensitivity,
)


def test_configuration_snapshot_is_typed_redacted_and_immutable() -> None:
    manager = ConfigurationManager(
        descriptors=(
            ConfigurationDescriptor(
                key="runtime.max_iterations",
                value_type=int,
                default=8,
                allowed_scopes=frozenset({ConfigurationScope.GLOBAL, ConfigurationScope.EXECUTION}),
            ),
            ConfigurationDescriptor(
                key="provider.api_key",
                value_type=SecretReference,
                allowed_scopes=frozenset({ConfigurationScope.GLOBAL}),
                sensitivity=Sensitivity.SECRET_REFERENCE,
            ),
        )
    )
    manager.set("runtime.max_iterations", 5, scope=ConfigurationScope.GLOBAL)
    manager.set(
        "provider.api_key",
        SecretReference("sec_123", "CURRENT", "workspace-1", "provider"),
        scope=ConfigurationScope.GLOBAL,
    )

    snapshot = manager.resolve(required_keys=("runtime.max_iterations", "provider.api_key"))

    assert snapshot.values["runtime.max_iterations"].value == 5
    assert snapshot.values["provider.api_key"].value.secret_id == "sec_123"
    assert manager.inspect()["provider.api_key"] == "<secret-reference>"
    with pytest.raises(TypeError):
        snapshot.values["runtime.max_iterations"] = None  # type: ignore[index]


def test_enabled_provider_requires_secret_reference_and_revoked_secret_never_resolves() -> None:
    registry = SecretRegistry(now=lambda: datetime(2026, 8, 7, tzinfo=UTC))
    registry.rotate("sec_123", owner_scope="workspace-1", purpose="provider", overlap=timedelta(0))
    reference = SecretReference("sec_123", "CURRENT", "workspace-1", "provider")

    handle = registry.resolve(reference, owner_scope="workspace-1", purpose="provider", consumer="provider")
    registry.revoke("sec_123")

    assert handle.secret_id == "sec_123"
    with pytest.raises(PermissionError):
        registry.resolve(reference, owner_scope="workspace-1", purpose="provider", consumer="provider")
