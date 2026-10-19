from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools.spec import ToolRegistry, ToolRegistryError, ToolSpec, provider_schema


class _Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(description="Shown text.")


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file_path: str = Field(description="Where.")
    limit: int | None = Field(None, ge=1, le=800)
    mode: Literal["a", "b"] | None = None
    after: int | None = Field(None, alias="-A")
    items: list[_Inner] | None = None


def _spec(key: str = "read_file", **overrides) -> ToolSpec:
    values = dict(
        key=key, description="Read it.", legacy_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler="handle", kind="filesystem", read_only=True,
    )
    values.update(overrides)
    return ToolSpec(**values)


class _Toolset:
    def handle(self, **kwargs):
        return {"summary": "ok", "content": "ok"}


def test_provider_schema_has_no_titles_refs_or_null_unions() -> None:
    schema = provider_schema(_Input)
    text = repr(schema)
    assert "'title':" not in text and "$ref" not in text and "$defs" not in text and "anyOf" not in text
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["file_path"]
    assert schema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 800}
    assert schema["properties"]["mode"] == {"type": "string", "enum": ["a", "b"]}
    assert "-A" in schema["properties"] and "after" not in schema["properties"]
    inner = schema["properties"]["items"]["items"]
    assert inner["properties"]["label"] == {"type": "string", "description": "Shown text."}
    assert inner["required"] == ["label"] and inner["additionalProperties"] is False


def test_registry_resolves_key_name_alias_and_case() -> None:
    registry = ToolRegistry([_spec(aliases=("view_text",)), _spec("run_command", handler="handle", kind="terminal")])
    assert registry.get("read_file").key == "read_file"
    assert registry.get("Read").key == "read_file"
    assert registry.get("read").key == "read_file"
    assert registry.get("view_text").key == "read_file"
    assert registry.key_for("BASH") == "run_command"
    assert registry.get("mcp__srv__tool") is None
    assert registry.get("") is None and registry.get("nope") is None


def test_registry_refuses_a_spelling_claimed_twice() -> None:
    with pytest.raises(ToolRegistryError):
        ToolRegistry([_spec(), _spec("write_file", aliases=("read",))])


def test_registry_keeps_spec_order() -> None:
    registry = ToolRegistry([_spec("write_file", order=20), _spec(order=10)])
    assert [spec.key for spec in registry.specs()] == ["read_file", "write_file"]


def test_bind_resolves_callables_and_fills_registry_metadata() -> None:
    spec = _spec(
        description=lambda toolset: f"Read for {type(toolset).__name__}.",
        policy_tags=lambda toolset: ("audit",),
        input_model=_Input, public_description="Public read.",
    )
    definition = spec.bind(_Toolset())
    assert definition.name == "read_file" and definition.public_name == "Read"
    assert definition.description == "Read for _Toolset."
    assert definition.policy_tags == ("audit",)
    assert definition.aliases == ("Read",)
    assert definition.permission_category == "read"
    assert definition.concurrency_safe is True and definition.input_model is _Input
    assert definition.handler(path="x")["content"] == "ok"
    assert spec.public_schema()[0] == "Public read." and "file_path" in spec.public_schema()[1]["properties"]
    assert _spec().public_schema() is None


def test_concurrency_follows_read_only_unless_set() -> None:
    assert _spec(read_only=False).bind(_Toolset()).concurrency_safe is False
    assert _spec(read_only=True, concurrency_safe=False).bind(_Toolset()).concurrency_safe is False
