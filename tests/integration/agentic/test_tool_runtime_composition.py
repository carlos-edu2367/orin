from dataclasses import replace

import pytest

from agentos.resources.service import ResourceManagerService
from agentos.tool_runtime import (
    AtomicTool,
    InMemoryToolRegistry,
    SensitiveOperationContext,
    ToolInvocationRequest,
    ToolRuntimeService,
    ToolRef,
)
from agentos.tool_runtime.catalog import ActionResult, ToolCatalog, default_tool_catalog
from agentos.tool_runtime.production import ProductionToolRuntime, RuntimeHooks
from agentos.tool_runtime.runtime import ToolRuntimeHooks


def context(user_id="user-1"):
    return SensitiveOperationContext(
        user_id,
        "workspace-1",
        "agent-1",
        "execution-1",
        "correlation-1",
        "agentic.tool",
        f"user:{user_id}",
    )


class SensitiveTool:
    def execute(self, call):
        return {
            "summary": "private execution",
            "artifact_refs": ["artifact:1"],
        }


def runtime(*, owner_hook=None, policy_hook=None, audit_sink=None):
    catalog = default_tool_catalog()
    entry = next(item for item in catalog.descriptors if item.descriptor.tool_ref.tool_id == "artifact.inspect")
    catalog = ToolCatalog(version=2, descriptors=(replace(entry, descriptor=replace(entry.descriptor, tool_ref=ToolRef("artifact.inspect", 2))),))
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(catalog.descriptors[0].descriptor, SensitiveTool(), integrity="sha256:test")
    hooks = RuntimeHooks(
        owner=owner_hook,
        policy=policy_hook,
        audit=audit_sink,
    )
    return ProductionToolRuntime(
        catalog=catalog,
        registry=registry,
        runtime=ToolRuntimeService(registry, ResourceManagerService()),
        hooks=hooks,
    )


def request(invocation_id="invocation-1", user_id="user-1"):
    return ToolInvocationRequest(
        invocation_id,
        ToolRef("artifact.inspect", 2),
        context(user_id),
        {"reference": "artifact:1"},
        "idem-1",
    )


def test_composed_runtime_reaches_registered_tool_and_returns_sanitized_typed_result():
    audit = []
    composed = runtime(audit_sink=audit.append)

    result = composed.invoke(request())

    assert isinstance(result, ActionResult)
    assert result.status == "SUCCEEDED"
    assert result.to_provider() == {
        "status": "SUCCEEDED",
        "summary": "artifact.inspect completed",
        "artifact_refs": ["artifact:1"],
    }
    assert "C:/workspace/hidden.txt" not in str(result.to_provider())
    assert audit and audit[-1]["status"] == "SUCCEEDED"
    assert "raw_output" not in audit[-1]


def test_provider_tools_and_invoke_apply_policy_and_owner_hooks():
    calls = []
    composed = runtime(
        owner_hook=lambda context, _descriptor: context.user_id == "user-1",
        policy_hook=lambda _context, descriptor: calls.append(descriptor.name) is None,
    )

    provider_tools = composed.provider_tools(context(), policy=lambda _context, _descriptor: True)
    assert [item.name for item in provider_tools] == ["artifact.inspect"]
    composed.invoke(request())
    with pytest.raises(PermissionError, match="owner"):
        composed.invoke(request(user_id="user-2"))
    assert calls


def test_composed_runtime_denies_unregistered_tool_before_adapter_execution():
    composed = runtime()

    with pytest.raises(LookupError, match="registered"):
        composed.invoke(
            replace(request(), tool_ref=ToolRef("artifact.inspect", 99))
        )


def test_provider_projection_excludes_catalog_descriptors_without_registered_bindings():
    composed = ProductionToolRuntime(
        resource_manager=ResourceManagerService(),
        tool_bindings={"artifact.inspect": SensitiveTool()},
    )

    names = [item.name for item in composed.provider_tools(context(), policy=True)]

    assert names == ["artifact.inspect"]


def test_registry_authorization_is_required_for_provider_projection_and_invoke():
    catalog = default_tool_catalog()
    entry = next(item for item in catalog.descriptors if item.name == "artifact.inspect")
    registry = InMemoryToolRegistry(authorization=lambda *_args: False)
    registry.register_bootstrap(entry.descriptor, SensitiveTool(), integrity="sha256:test")
    composed = ProductionToolRuntime(
        catalog=catalog,
        registry=registry,
        runtime=ToolRuntimeService(registry, ResourceManagerService()),
    )

    assert composed.provider_tools(context()) == ()
    result = composed.invoke(
        ToolInvocationRequest("registry-denied", entry.tool_ref, context(), {"reference": "artifact:1"}, "registry-denied-key")
    )
    assert result.status == "FAILED"
    assert result.error_category == "UNAUTHORIZED"


def test_policy_hook_none_and_typeerror_fail_closed_without_retrying():
    calls = []

    def hook(_context, _descriptor, _arguments):
        calls.append(1)
        raise TypeError("bug inside policy")

    composed = runtime(policy_hook=hook)
    with pytest.raises(PermissionError, match="policy"):
        composed.invoke(request())
    assert calls == [1]

    composed = runtime(policy_hook=lambda *_args: None)
    with pytest.raises(PermissionError, match="policy"):
        composed.invoke(request("none-hook"))


def test_runtime_engine_accepts_policy_hooks_and_audits_only_sanitized_fields():
    catalog = default_tool_catalog()
    entry = next(item for item in catalog.descriptors if item.name == "artifact.inspect")
    entry = replace(entry, descriptor=replace(entry.descriptor, tool_ref=ToolRef("artifact.inspect", 2)))
    registry = InMemoryToolRegistry()
    registry.register_bootstrap(entry.descriptor, SensitiveTool(), integrity="sha256:test")
    seen = []
    hooks = ToolRuntimeHooks(
        authorization=lambda _context, _descriptor, _arguments: seen.append("authorization") or True,
        owner=lambda _context, _descriptor: seen.append("owner") or True,
        policy=lambda _context, _descriptor, _arguments: seen.append("policy") or True,
        idempotency=lambda _request, _descriptor: seen.append("idempotency") or True,
        limits=lambda _request, _descriptor: seen.append("limits") or True,
        lease=lambda _context, _descriptor, _request: seen.append("lease") or True,
        cancellation=lambda _request: seen.append("cancellation") or True,
        audit=lambda event: seen.append(event),
    )
    engine = ToolRuntimeService(registry, ResourceManagerService(), hooks=hooks)

    outcome = engine.invoke(request())

    assert outcome.__class__.__name__ == "ToolSucceeded"  # the façade owns the public projection
    assert all(label in seen for label in ("owner", "policy", "authorization", "idempotency", "limits", "lease", "cancellation"))
    audit = next(item for item in seen if isinstance(item, dict))
    assert "raw_output" not in audit
    assert "credential" not in audit
