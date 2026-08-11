from dataclasses import FrozenInstanceError
import json

import pytest

from agentos.tool_runtime.catalog import (
    ActionResult,
    CatalogDescriptor,
    ProviderToolProjection,
    ToolCatalog,
    default_tool_catalog,
)
from agentos.tool_runtime.adapters import sanitize_adapter_result
from agentos.tool_runtime.registry import InMemoryToolRegistry


def test_default_catalog_is_immutable_versioned_and_covers_the_five_action_families():
    catalog = default_tool_catalog()

    assert catalog.version == 1
    assert {item.family for item in catalog.descriptors} == {
        "filesystem",
        "artifact",
        "terminal",
        "browser",
        "delegation",
    }
    assert all(item.descriptor.tool_ref.version == 1 for item in catalog.descriptors)

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        catalog.descriptors[0].family = "other"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        catalog.version = 2
    with pytest.raises(TypeError):
        catalog.descriptors[0].descriptor.input_schema["secret"] = {"type": "string"}


def test_provider_projection_contains_only_public_tool_contract():
    projection = default_tool_catalog().provider_projection(
        context=object(),
        policy=lambda _context, descriptor: descriptor.family == "filesystem",
    )

    assert projection
    assert all(isinstance(item, ProviderToolProjection) for item in projection)
    assert {field for item in projection for field in item.to_dict()} == {
        "name",
        "description",
        "input_schema",
    }
    assert all(
        "tool_ref" not in item.to_dict()
        and "permissions" not in item.to_dict()
        and "owner" not in item.to_dict()
        and "policy" not in item.to_dict()
        for item in projection
    )
    json.dumps(projection[0].to_dict())


def test_typed_action_result_sanitizes_raw_output_and_provider_payload():
    raw = {
        "summary": "internal summary",
        "path": "C:/private/workspace/secret.txt",
        "credential": "Bearer secret",
        "prompt": "private prompt",
        "handle": "opaque-handle",
        "raw_output": "full command output",
        "artifact_ref": "artifact:authorized-1",
    }

    result = ActionResult.success(
        action_id="action-1",
        summary="Arquivo lido",
        raw_output=raw,
        artifact_refs=("artifact:authorized-1",),
    )

    assert result.status == "SUCCEEDED"
    assert result.summary == "Arquivo lido"
    assert result.artifact_refs == ("artifact:authorized-1",)
    assert result.to_provider() == {
        "status": "SUCCEEDED",
        "summary": "Arquivo lido",
        "artifact_refs": ["artifact:authorized-1"],
    }
    assert all(value not in str(result.to_provider()) for key, value in raw.items() if key != "artifact_ref")


def test_catalog_rejects_duplicate_versioned_references():
    catalog = default_tool_catalog()
    duplicate = catalog.descriptors[0]

    with pytest.raises(ValueError, match="unique"):
        ToolCatalog(
            version=2,
            descriptors=(duplicate, duplicate),
        )


def test_catalog_descriptor_is_frozen_at_the_boundary():
    descriptor = default_tool_catalog().descriptors[0]

    assert isinstance(descriptor, CatalogDescriptor)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        descriptor.owner_scope = "other-owner"


def test_adapter_result_sanitizer_keeps_only_bounded_public_summary_and_artifacts():
    sanitized = sanitize_adapter_result(
        {
            "summary": "bounded",
            "artifact_ref": "artifact:1",
            "path": "C:/private/file.txt",
            "credential": "secret",
            "prompt": "private prompt",
            "raw_output": "full output",
        }
    )

    assert sanitized == {"summary": "bounded", "artifact_refs": ["artifact:1"]}


def test_registry_authorized_listing_returns_descriptors_without_factories():
    catalog = default_tool_catalog()
    entry = catalog.descriptors[0]
    registry = InMemoryToolRegistry(
        authorization=lambda _context, action, descriptor: action == "list" and descriptor.name == entry.name
    )
    factory = object()
    registry.register_bootstrap(entry.descriptor, factory, integrity="sha256:test")

    listed = registry.list_authorized(object())

    assert listed == (entry.descriptor,)
    assert factory not in listed
