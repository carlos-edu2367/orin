"""Typed, redacted configuration contracts for the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


class ConfigurationScope(StrEnum):
    GLOBAL = "GLOBAL"
    WORKSPACE = "WORKSPACE"
    AGENT = "AGENT"
    EXECUTION = "EXECUTION"


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"
    SECRET_REFERENCE = "SECRET_REFERENCE"


class MergeStrategy(StrEnum):
    REPLACE = "REPLACE"
    INTERSECT = "INTERSECT"
    MINIMUM = "MINIMUM"
    APPEND_UNIQUE = "APPEND_UNIQUE"


@dataclass(frozen=True, slots=True)
class SecretReference:
    """An opaque reference. It intentionally cannot contain secret material."""

    secret_id: str
    version: str
    owner_scope: str
    purpose: str
    provider_ref: str = "local"

    def __post_init__(self) -> None:
        for name, value in (
            ("secret_id", self.secret_id),
            ("version", self.version),
            ("owner_scope", self.owner_scope),
            ("purpose", self.purpose),
            ("provider_ref", self.provider_ref),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 255:
                raise ValueError(f"{name} must be a bounded non-blank string")

    def __repr__(self) -> str:
        return "SecretReference(<redacted>)"


@dataclass(frozen=True, slots=True)
class ConfigurationDescriptor:
    key: str
    value_type: type[Any]
    allowed_scopes: frozenset[ConfigurationScope]
    default: Any = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    merge_strategy: MergeStrategy = MergeStrategy.REPLACE
    execution_overridable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip() or len(self.key) > 255:
            raise ValueError("configuration key must be a bounded non-blank string")
        if not self.allowed_scopes:
            raise ValueError("configuration descriptor requires an allowed scope")
        if self.default is not None and not isinstance(self.default, self.value_type):
            raise ValueError("configuration default has an invalid type")
        if self.sensitivity is Sensitivity.SECRET_REFERENCE and self.value_type is not SecretReference:
            raise ValueError("secret-reference descriptors must use SecretReference")


@dataclass(frozen=True, slots=True)
class ResolvedConfigurationValue:
    value: Any
    scope: ConfigurationScope
    source_version: int
    sensitivity: Sensitivity


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    snapshot_id: str
    values: Mapping[str, ResolvedConfigurationValue]
    catalog_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def create(cls, values: Mapping[str, ResolvedConfigurationValue]) -> "ConfigurationSnapshot":
        return cls(snapshot_id=f"cfg_{uuid4().hex}", values=values)

