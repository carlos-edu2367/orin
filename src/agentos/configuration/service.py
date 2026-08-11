from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Callable, Iterable

from .models import ConfigurationDescriptor, ConfigurationScope, ConfigurationSnapshot, ResolvedConfigurationValue, SecretReference, Sensitivity


_PRECEDENCE = (
    ConfigurationScope.GLOBAL,
    ConfigurationScope.WORKSPACE,
    ConfigurationScope.AGENT,
    ConfigurationScope.EXECUTION,
)


class ConfigurationManager:
    """Small in-process implementation used by bootstrap and tests.

    Durable implementations belong behind this same boundary; values are never
    exposed as an untyped settings dictionary.
    """

    def __init__(self, descriptors: Iterable[ConfigurationDescriptor]) -> None:
        items = tuple(descriptors)
        self._descriptors = {item.key: item for item in items}
        if not self._descriptors:
            raise ValueError("configuration catalog cannot be empty")
        if len(self._descriptors) != len(items):
            raise ValueError("configuration keys must be unique")
        self._values: dict[tuple[ConfigurationScope, str], tuple[object, int]] = {}
        self._version = 0

    def set(self, key: str, value: object, *, scope: ConfigurationScope) -> int:
        descriptor = self._descriptor(key)
        if scope not in descriptor.allowed_scopes:
            raise PermissionError("configuration scope is not delegated")
        if scope is ConfigurationScope.EXECUTION and not descriptor.execution_overridable:
            raise PermissionError("configuration key is not execution-overridable")
        if not isinstance(value, descriptor.value_type):
            raise TypeError("configuration value has an invalid type")
        self._version += 1
        self._values[(scope, key)] = (value, self._version)
        return self._version

    def unset(self, key: str, *, scope: ConfigurationScope) -> int:
        self._descriptor(key)
        self._values.pop((scope, key), None)
        self._version += 1
        return self._version

    def resolve(
        self,
        *,
        required_keys: Iterable[str],
        execution_overrides: dict[str, object] | None = None,
    ) -> ConfigurationSnapshot:
        overrides = execution_overrides or {}
        values: dict[str, ResolvedConfigurationValue] = {}
        for key in required_keys:
            descriptor = self._descriptor(key)
            selected: tuple[object, int, ConfigurationScope] | None = None
            for scope in _PRECEDENCE:
                candidate = self._values.get((scope, key))
                if candidate is not None:
                    selected = (candidate[0], candidate[1], scope)
            if key in overrides:
                if not descriptor.execution_overridable:
                    raise PermissionError("configuration key is not execution-overridable")
                candidate = overrides[key]
                if not isinstance(candidate, descriptor.value_type):
                    raise TypeError("execution override has an invalid type")
                selected = (candidate, self._version, ConfigurationScope.EXECUTION)
            if selected is None:
                if descriptor.default is None:
                    raise ValueError(f"required configuration key is missing: {key}")
                selected = (descriptor.default, 0, ConfigurationScope.GLOBAL)
            value, version, scope = selected
            values[key] = ResolvedConfigurationValue(value, scope, version, descriptor.sensitivity)
        return ConfigurationSnapshot.create(values)

    def inspect(self) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, descriptor in self._descriptors.items():
            resolved = self.resolve(required_keys=(key,)).values[key].value
            output[key] = "<secret-reference>" if descriptor.sensitivity is Sensitivity.SECRET_REFERENCE else (
                "<redacted>" if descriptor.sensitivity is Sensitivity.SENSITIVE else resolved
            )
        return output

    def _descriptor(self, key: str) -> ConfigurationDescriptor:
        try:
            return self._descriptors[key]
        except KeyError as error:
            raise KeyError("unknown configuration key") from error


@dataclass(frozen=True, slots=True)
class SecretHandle:
    handle_id: str
    secret_id: str
    version: int
    expires_at: datetime

    def __repr__(self) -> str:
        return "SecretHandle(<ephemeral>)"


@dataclass(slots=True)
class _SecretState:
    owner_scope: str
    purpose: str
    active_version: int
    revoked: bool = False


class SecretRegistry:
    """Lifecycle gate only: no plaintext is accepted, retained, or returned."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._states: dict[str, _SecretState] = {}

    def rotate(self, secret_id: str, *, owner_scope: str, purpose: str, overlap: timedelta) -> SecretReference:
        if overlap < timedelta(0):
            raise ValueError("secret overlap cannot be negative")
        state = self._states.get(secret_id)
        if state is None:
            state = _SecretState(owner_scope, purpose, 1)
            self._states[secret_id] = state
        else:
            if state.owner_scope != owner_scope or state.purpose != purpose:
                raise PermissionError("secret ownership cannot change during rotation")
            state.active_version += 1
            state.revoked = False
        return SecretReference(secret_id, str(state.active_version), owner_scope, purpose)

    def revoke(self, secret_id: str) -> None:
        try:
            self._states[secret_id].revoked = True
        except KeyError as error:
            raise PermissionError("secret is not available") from error

    def resolve(self, reference: SecretReference, *, owner_scope: str, purpose: str, consumer: str) -> SecretHandle:
        if not consumer.strip() or reference.owner_scope != owner_scope or reference.purpose != purpose:
            raise PermissionError("secret reference is not authorized")
        state = self._states.get(reference.secret_id)
        if state is None or state.revoked or state.owner_scope != owner_scope or state.purpose != purpose:
            raise PermissionError("secret reference is not available")
        if reference.version not in ("CURRENT", str(state.active_version)):
            raise PermissionError("secret version is not active")
        return SecretHandle(f"handle_{reference.secret_id}_{state.active_version}", reference.secret_id, state.active_version, self._now() + timedelta(minutes=5))
