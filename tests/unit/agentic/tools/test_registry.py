"""W4: every built-in tool is a ToolSpec in its family module; the builder is a registry lookup."""
from __future__ import annotations

import importlib
import inspect

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.public_names import PUBLIC_NAMES


def _registry():
    return importlib.import_module("agentos.agentic.tools.registry").default_registry()


def test_every_builtin_tool_is_a_spec_with_a_public_name() -> None:
    specs = _registry().specs()
    keys = [spec.key for spec in specs]
    assert len(keys) == len(set(keys)) >= 65
    assert [spec.order for spec in specs] == sorted(spec.order for spec in specs)
    missing = [key for key in keys if key not in PUBLIC_NAMES]
    assert missing == []


def test_each_spec_lives_with_its_handler() -> None:
    for spec in _registry().specs():
        owner = next(
            klass for klass in AgentToolset.__mro__[1:]
            if spec.handler in vars(klass)
        )
        family = importlib.import_module(owner.__module__)
        assert spec in family.SPECS, f"{spec.key} is not declared next to {owner.__name__}.{spec.handler}"


def test_the_builder_is_a_registry_lookup() -> None:
    source = inspect.getsource(AgentToolset._build_definitions)
    assert "default_registry" in source and "ToolDefinition(" not in source
