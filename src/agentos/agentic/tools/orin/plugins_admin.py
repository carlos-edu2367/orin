"""Plugins admin tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class PluginTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def search_plugin(self, query: str = "") -> dict[str, Any]:
        results = self._plugin_service.search(str(query or ""), user_id=self._plugin_user_id)
        return {"summary": f"{len(results)} plugin(s) encontrado(s)", "content": json.dumps(results, ensure_ascii=False), "payload": {"results": results, "tool_kind": "plugin"}}

    def inspect_plugin(self, reference: str) -> dict[str, Any]:
        try:
            plugin = self._plugin_service.inspect(user_id=self._plugin_user_id, reference=str(reference))
        except Exception as error:
            raise AgentToolError(str(error)) from error
        return {"summary": f"Inspecionou o plugin {plugin.get('display_name', plugin.get('plugin_id', reference))}", "content": json.dumps(plugin, ensure_ascii=False), "payload": {"plugin": plugin, "tool_kind": "plugin"}}

    def install_plugin(self, reference: str) -> ToolOutcome:
        plugin = self.inspect_plugin(reference)["payload"]["plugin"]
        return ToolOutcome("succeeded", f"Aguardando aprovação do plugin {plugin.get('display_name', plugin['plugin_id'])}", "The plugin was inspected and is waiting for the user's approval card. Stop here and wait for their next message.", {"plugin_approval": True, "wait_for_user": True, "plugin": plugin, "tool_kind": "plugin"})

    def list_plugins(self) -> dict[str, Any]:
        results = self._plugin_service.list(self._plugin_user_id)
        return {"summary": f"{len(results)} plugin(s) instalado(s)", "content": json.dumps(results, ensure_ascii=False), "payload": {"plugins": results, "tool_kind": "plugin"}}

    def uninstall_plugin(self, plugin_id: str, confirmed: bool = False) -> ToolOutcome:
        if not confirmed:
            raise AgentToolError("Ask the user to confirm the removal first, then call this again with confirmed=true.")
        try:
            result = self._plugin_service.remove(user_id=self._plugin_user_id, plugin_id=str(plugin_id))
        except Exception as error:
            raise AgentToolError(str(error)) from error
        return ToolOutcome("succeeded", f"Removeu o plugin {plugin_id}", json.dumps(result), {**result, "tool_kind": "plugin"})


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="search_plugin",
        description="Search the plugin marketplaces Orin knows for a plugin by name or subject.",
        legacy_schema=_schema({"query": _TEXT}, ("query",)),
        handler="search_plugin",
        kind="plugin",
        read_only=True,
        available=lambda self: (self._plugin_service is not None and self._plugin_user_id),
        order=500,
    ),
    ToolSpec(
        key="inspect_plugin",
        description="Download a plugin and report its skills, MCP servers and subagents without activating anything. Nothing in the package is executed.",
        legacy_schema=_schema({"reference": _TEXT}, ("reference",)),
        handler="inspect_plugin",
        kind="plugin",
        available=lambda self: (self._plugin_service is not None and self._plugin_user_id),
        order=510,
    ),
    ToolSpec(
        key="install_plugin",
        description="Propose installing a plugin; the user must approve the contribution card before activation.",
        legacy_schema=_schema({"reference": _TEXT}, ("reference",)),
        handler="install_plugin",
        kind="plugin",
        policy_tags=("mutates",),
        available=lambda self: (self._plugin_service is not None and self._plugin_user_id),
        order=520,
    ),
    ToolSpec(
        key="list_plugins",
        description="List this user's installed plugins and their states.",
        legacy_schema=_schema({}),
        handler="list_plugins",
        kind="plugin",
        read_only=True,
        available=lambda self: (self._plugin_service is not None and self._plugin_user_id),
        order=530,
    ),
    ToolSpec(
        key="uninstall_plugin",
        description="Remove an installed plugin after the user explicitly confirmed the removal.",
        legacy_schema=_schema({"plugin_id": _TEXT, "confirmed": {"type": "boolean"}}, ("plugin_id",)),
        handler="uninstall_plugin",
        kind="plugin",
        policy_tags=("mutates",),
        available=lambda self: (self._plugin_service is not None and self._plugin_user_id),
        order=540,
    ),
)
