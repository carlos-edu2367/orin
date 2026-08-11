"""Immutable tool descriptors and safe projections for provider-facing calls."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from agentos.events.models import DataClassification
from agentos.resources.models import ResourceCapability, ResourceType

from .models import (
    ResourceRequirement,
    ToolDescriptor,
    ToolIdempotency,
    ToolLimits,
    ToolRef,
)


MAX_PUBLIC_SUMMARY = 256
MAX_ARTIFACT_REFS = 16


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _bounded(value: object, name: str, maximum: int = MAX_PUBLIC_SUMMARY) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded non-blank string")
    return value


@dataclass(frozen=True, slots=True)
class ProviderToolProjection:
    """The only descriptor shape that may cross the provider boundary."""

    name: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        _bounded(self.name, "name")
        _bounded(self.description, "description")
        object.__setattr__(self, "input_schema", _freeze(self.input_schema))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": _thaw(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class CatalogDescriptor:
    descriptor: ToolDescriptor
    family: str
    owner_scope: str = "execution"
    policy_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptor",
            replace(
                self.descriptor,
                input_schema=_freeze(self.descriptor.input_schema),
                output_schema=_freeze(self.descriptor.output_schema),
            ),
        )
        _bounded(self.family, "family", 64)
        _bounded(self.owner_scope, "owner_scope", 64)
        tags = tuple(_bounded(item, "policy_tag", 64) for item in self.policy_tags)
        if len(set(tags)) != len(tags):
            raise ValueError("policy tags must be unique")
        object.__setattr__(self, "policy_tags", tags)

    @property
    def tool_ref(self):
        return self.descriptor.tool_ref

    @property
    def name(self):
        return self.descriptor.name

    def provider_projection(self) -> ProviderToolProjection:
        return ProviderToolProjection(
            self.descriptor.name,
            self.descriptor.description,
            self.descriptor.input_schema,
        )


def _policy_allows(policy: object, context: object, descriptor: CatalogDescriptor) -> bool:
    if policy is None:
        return True
    if callable(policy):
        try:
            decision = policy(context, descriptor)
        except TypeError:
            decision = policy(context, descriptor.descriptor)
        return decision is not False
    for method_name in ("allows_tool", "allows"):
        method = getattr(policy, method_name, None)
        if method is not None:
            try:
                decision = method(context, descriptor)
            except TypeError:
                decision = method(context, descriptor.descriptor)
            return decision is not False
    if isinstance(policy, Mapping):
        allowed = policy.get("allowed_tools", policy.get("tools"))
        if allowed is None:
            decision = policy.get(descriptor.descriptor.tool_ref.tool_id, policy.get(descriptor.name, True))
            return decision is not False
        return descriptor.descriptor.tool_ref.tool_id in allowed or descriptor.name in allowed
    if isinstance(policy, (set, frozenset, tuple, list)):
        return descriptor.descriptor.tool_ref.tool_id in policy or descriptor.name in policy
    return bool(policy)


class ToolCatalog:
    """A published, version-exact set of immutable tool descriptors."""

    __slots__ = ("_catalog_id", "_version", "_descriptors", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise FrozenInstanceError("cannot assign to field on immutable tool catalog")
        object.__setattr__(self, name, value)

    def __init__(self, *, version: int, descriptors: tuple[CatalogDescriptor, ...], catalog_id: str = "agentos.tools") -> None:
        if not isinstance(version, int) or version < 1:
            raise ValueError("catalog version must be positive")
        entries = tuple(descriptors)
        refs = tuple(entry.tool_ref for entry in entries)
        if len(set(refs)) != len(refs):
            raise ValueError("catalog descriptors must be unique")
        if len({entry.name for entry in entries}) != len(entries):
            raise ValueError("catalog descriptor names must be unique")
        _bounded(catalog_id, "catalog_id", 128)
        self._catalog_id = catalog_id
        self._version = version
        self._descriptors = entries
        self._sealed = True

    @property
    def catalog_id(self) -> str:
        return self._catalog_id

    @property
    def version(self) -> int:
        return self._version

    @property
    def descriptors(self) -> tuple[CatalogDescriptor, ...]:
        return self._descriptors

    def resolve(self, tool_ref: ToolRef) -> CatalogDescriptor:
        for entry in self._descriptors:
            if entry.tool_ref == tool_ref:
                return entry
        raise LookupError("tool version is not registered in the catalog")

    def provider_projection(self, *, context: object, policy: object = None) -> tuple[ProviderToolProjection, ...]:
        return tuple(
            entry.provider_projection()
            for entry in self._descriptors
            if _policy_allows(policy, context, entry)
        )


def _schema(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> Mapping[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _descriptor(
    tool_id: str,
    family: str,
    description: str,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    *,
    capabilities: tuple[ResourceCapability, ...] = (),
    resource_type: ResourceType | None = None,
    idempotency: ToolIdempotency = ToolIdempotency.IDEMPOTENT_WITH_KEY,
    tags: tuple[str, ...] = (),
) -> CatalogDescriptor:
    requirements = () if resource_type is None else (
        ResourceRequirement(resource_type, capabilities or (ResourceCapability.INSPECT,)),
    )
    descriptor = ToolDescriptor(
        ToolRef(tool_id, 1),
        tool_id,
        description,
        input_schema,
        output_schema,
        tags,
        requirements,
        ToolLimits(__import__("datetime").timedelta(seconds=30), 64 * 1024, 64 * 1024, 32),
        False,
        True,
        idempotency,
        DataClassification.INTERNAL,
    )
    return CatalogDescriptor(descriptor, family, policy_tags=tags)


def default_tool_catalog() -> ToolCatalog:
    text = {"type": "string"}
    integer = {"type": "integer"}
    object_output = _schema({"summary": text, "artifact_refs": {"type": "array", "items": text}}, ("summary",))
    descriptors = (
        _descriptor(
            "filesystem.read", "filesystem", "Read a workspace file by logical path.",
            _schema({"path": text}, ("path",)),
            _schema({"bytes": text, "bytes_read": integer}),
            capabilities=(ResourceCapability.FILESYSTEM_READ,), resource_type=ResourceType.FILESYSTEM,
        ),
        _descriptor(
            "filesystem.write", "filesystem", "Write bounded text to a workspace file.",
            _schema({"path": text, "data": text}, ("path", "data")),
            _schema({"bytes_written": integer}),
            capabilities=(ResourceCapability.FILESYSTEM_WRITE,), resource_type=ResourceType.FILESYSTEM,
        ),
        _descriptor(
            "artifact.inspect", "artifact", "Inspect an authorized artifact reference.",
            _schema({"reference": text}, ("reference",)),
            _schema({
                "summary": text,
                "artifact_refs": {"type": "array", "items": text},
                "artifact_id": text,
                "size_bytes": integer,
                "state": text,
            }),
            capabilities=(ResourceCapability.INSPECT,), resource_type=None,
        ),
        _descriptor(
            "terminal.execute", "terminal", "Execute one policy-authorized terminal action.",
            _schema({"session_id": text, "command": text, "cwd": text, "maximum_output_bytes": integer}, ("session_id", "command")),
            _schema({"command_id": text, "accepted": {"type": "boolean"}}, ("command_id", "accepted")),
            capabilities=(ResourceCapability.TERMINAL_SESSION,), resource_type=ResourceType.TERMINAL,
        ),
        _descriptor(
            "browser.navigate", "browser", "Navigate an authorized browser page.",
            _schema({"page_id": text, "expected_page_version": integer, "url": text}, ("page_id", "expected_page_version", "url")),
            _schema({"page_id": text, "url": text, "version": integer}, ("page_id", "url", "version")),
            capabilities=(ResourceCapability.BROWSER_NAVIGATE,), resource_type=ResourceType.BROWSER,
        ),
        _descriptor(
            "delegation.delegate", "delegation", "Create a bounded delegated action from an authorized request reference.",
            _schema({"child_agent_ref": text, "request_ref": text}, ("child_agent_ref", "request_ref")),
            object_output,
        ),
    )
    return ToolCatalog(version=1, descriptors=descriptors)


class ActionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Bounded public result; raw adapter output is intentionally not retained."""

    action_id: str
    status: ActionStatus
    summary: str
    error_category: str | None = None
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded(self.action_id, "action_id")
        object.__setattr__(self, "status", ActionStatus(self.status))
        _bounded(self.summary, "summary")
        if self.error_category is not None:
            _bounded(self.error_category, "error_category", 64)
        refs = tuple(_bounded(ref, "artifact_ref", 256) for ref in self.artifact_refs)
        if len(refs) > MAX_ARTIFACT_REFS or len(set(refs)) != len(refs):
            raise ValueError("artifact references are invalid")
        object.__setattr__(self, "artifact_refs", refs)

    @classmethod
    def success(cls, *, action_id: str, summary: str, raw_output: object = None, artifact_refs: tuple[str, ...] = ()):
        return cls(action_id, ActionStatus.SUCCEEDED, summary, artifact_refs=artifact_refs)

    @classmethod
    def failed(cls, *, action_id: str, summary: str, error_category: str, raw_output: object = None):
        return cls(action_id, ActionStatus.FAILED, summary, error_category=error_category)

    @classmethod
    def cancelled(cls, *, action_id: str, summary: str = "Action cancelled"):
        return cls(action_id, ActionStatus.CANCELLED, summary, error_category="CANCELLED")

    def to_provider(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status.value, "summary": self.summary}
        if self.error_category is not None:
            payload["error_category"] = self.error_category
        if self.artifact_refs:
            payload["artifact_refs"] = list(self.artifact_refs)
        return payload

    def to_event(self) -> dict[str, Any]:
        return {"action_id": self.action_id, **self.to_provider()}


TypedActionResult = ActionResult
ProviderToolSchema = ProviderToolProjection
ToolDescriptorCatalog = ToolCatalog


__all__ = [
    "ActionResult",
    "ActionStatus",
    "CatalogDescriptor",
    "ProviderToolProjection",
    "ProviderToolSchema",
    "ToolCatalog",
    "ToolDescriptorCatalog",
    "TypedActionResult",
    "default_tool_catalog",
]
