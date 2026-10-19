"""Plugins admin tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolOutcome,
)


class PluginTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def search_plugin(self, query: str = "") -> dict[str, Any]:
        results = self._plugin_service.search(str(query or ""))
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
