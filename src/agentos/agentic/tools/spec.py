"""The tool contract: one ``ToolSpec`` per tool, collected in a ``ToolRegistry``.

A spec is static: it names the tool, says how to build its ``ToolDefinition``
for one ``AgentToolset`` and, for the tools that mirror Claude Code, carries the
Pydantic ``Input``/``Output`` models whose field names are that public interface.

``key`` is Orin's stable id (``read_file``): stored transcripts, activity events,
the UI and the permission engine use it. ``name`` is the canonical Claude
Code-style name (``Read``) that the model, permission rules and hooks see. Both,
and every alias, resolve to the same spec.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agentos.agentic.tools.public_names import permission_category, public_name

MAX_RESULT_CHARS_DEFAULT = 12_000


def _always(_toolset: Any) -> bool:
    return True


def _resolve(value: Any, toolset: Any) -> Any:
    return value(toolset) if callable(value) else value


@dataclass(frozen=True, slots=True)
class ToolSpec:
    key: str
    description: str | Callable[[Any], str]
    legacy_schema: Mapping[str, Any] | Callable[[Any], Mapping[str, Any]]
    handler: str
    kind: str
    read_only: bool = False
    policy_tags: tuple[str, ...] | Callable[[Any], tuple[str, ...]] = ()
    max_result_chars: int = MAX_RESULT_CHARS_DEFAULT
    available: Callable[[Any], bool] = _always
    order: int = 0
    aliases: tuple[str, ...] = ()
    input_model: type[BaseModel] | None = None
    public_description: str = ""
    output_model: type[BaseModel] | None = None
    concurrency_safe: bool | None = None

    @property
    def name(self) -> str:
        return public_name(self.key)

    def names(self) -> tuple[str, ...]:
        """Every spelling that resolves to this tool, most specific first."""
        return tuple(dict.fromkeys((self.key, self.name, *self.aliases)))

    def public_schema(self) -> tuple[str, dict[str, Any]] | None:
        if self.input_model is None:
            return None
        return self.public_description, provider_schema(self.input_model)

    def bind(self, toolset: Any) -> Any:
        # Imported here: agent_tools imports the tool modules, which import this one.
        from agentos.agentic.agent_tools import ToolDefinition

        tags = tuple(_resolve(self.policy_tags, toolset))
        return ToolDefinition(
            self.key,
            str(_resolve(self.description, toolset)),
            _resolve(self.legacy_schema, toolset),
            getattr(toolset, self.handler),
            self.kind,
            read_only=self.read_only,
            policy_tags=tags,
            max_result_size_chars=self.max_result_chars,
            public_name=self.name,
            aliases=self.names()[1:],
            permission_category=permission_category(self.key, kind=self.kind, read_only=self.read_only, policy_tags=tags),
            concurrency_safe=self.read_only if self.concurrency_safe is None else self.concurrency_safe,
            input_model=self.input_model,
            output_model=self.output_model,
        )


class ToolRegistryError(ValueError):
    """Two specs claim the same key, name or alias: refused when the registry is built."""


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs: tuple[ToolSpec, ...] = tuple(sorted(specs, key=lambda item: item.order))
        self._by_name: dict[str, ToolSpec] = {}
        self._by_lower: dict[str, ToolSpec] = {}
        for spec in self._specs:
            for spelling in spec.names():
                owner = self._by_lower.get(spelling.lower())
                if owner is not None and owner is not spec:
                    raise ToolRegistryError(f"'{spelling}' names both {owner.key} and {spec.key}")
                self._by_name[spelling] = spec
                self._by_lower[spelling.lower()] = spec

    def specs(self) -> tuple[ToolSpec, ...]:
        return self._specs

    def get(self, name: str) -> ToolSpec | None:
        if not isinstance(name, str) or not name or name.startswith("mcp__"):
            return None
        return self._by_name.get(name) or self._by_lower.get(name.lower())

    def key_for(self, name: str) -> str | None:
        spec = self.get(name)
        return spec.key if spec is not None else None


# -- provider-safe JSON Schema from a Pydantic model -------------------------

_DROPPED_KEYS = frozenset({"title", "default", "examples"})


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A JSON Schema every provider accepts: no titles, no $ref, optional fields as plain types."""
    raw = model.model_json_schema(by_alias=True, mode="validation")
    definitions = raw.pop("$defs", {})
    return _clean(raw, definitions)


def _clean(node: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(node, list):
        return [_clean(item, definitions) for item in node]
    if not isinstance(node, Mapping):
        return node
    if "$ref" in node:
        target = definitions.get(str(node["$ref"]).rsplit("/", 1)[-1], {})
        merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
        return _clean(merged, definitions)
    options = node.get("anyOf")
    if isinstance(options, list):
        concrete = [item for item in options if not (isinstance(item, Mapping) and item.get("type") == "null")]
        if len(concrete) == 1 and len(concrete) < len(options):
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return _clean({**copy.deepcopy(concrete[0]), **rest}, definitions)
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROPPED_KEYS:
            continue
        if key == "properties" and isinstance(value, Mapping):
            cleaned[key] = {name: _clean(item, definitions) for name, item in value.items()}
        else:
            cleaned[key] = _clean(value, definitions)
    if cleaned.get("type") == "object" and "properties" in cleaned:
        cleaned.setdefault("required", [])
        cleaned.setdefault("additionalProperties", False)
    return cleaned
