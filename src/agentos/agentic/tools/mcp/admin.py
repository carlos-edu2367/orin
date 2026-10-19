"""Admin tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Mapping
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolDefinition,
    ToolOutcome,
)


class McpTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def list_mcp_catalog(self, query: str = "") -> dict[str, Any]:
        from agentos.mcp.catalog import search_catalog

        entries = [{
            "catalog_id": entry.catalog_id, "display_name": entry.display_name, "summary": entry.summary,
            "transport": entry.transport.value, "setup_instructions": entry.setup_instructions,
            "arguments": list(entry.arguments),
            "secrets": [{"name": item.name, "label": item.label, "how_to_obtain": item.how_to_obtain} for item in entry.secrets],
        } for entry in search_catalog(str(query or ""))]
        return {"summary": f"{len(entries)} servidor(es) no catálogo",
                "content": json.dumps(entries, ensure_ascii=False),
                "payload": {"entries": entries, "tool_kind": "mcp", "mcp_action": "catalog"}}

    def list_mcp_servers(self) -> dict[str, Any]:
        servers = self._mcp_service.list(self._mcp_user_id)
        return {"summary": f"{len(servers)} servidor(es) MCP configurado(s)",
                "content": json.dumps(servers, ensure_ascii=False),
                "payload": {"servers": servers, "tool_kind": "mcp", "mcp_action": "list"}}

    def configure_mcp(self, display_name: str, catalog_id: str | None = None, transport: str | None = None,
                      command: str | None = None, args: list[str] | None = None, url: str | None = None,
                      secret_names: list[str] | None = None, **rejected: Any) -> ToolOutcome:
        from agentos.mcp.catalog import find_catalog_entry

        if rejected:
            raise AgentToolError(
                "configure_mcp does not accept credential values. Pass secret_names only; the user types the values in the approval card."
            )
        command_values: dict[str, object] = {"user_id": self._mcp_user_id, "display_name": str(display_name)}
        if catalog_id:
            entry = find_catalog_entry(str(catalog_id))
            if entry is None:
                raise AgentToolError(f"'{catalog_id}' is not in the MCP catalog. Call list_mcp_catalog first, or pass transport plus command/url explicitly.")
            command_values.update({
                "catalog_id": entry.catalog_id, "transport": entry.transport.value,
                "command": entry.command, "args": list(entry.args), "url": entry.url,
                "secret_names": [item.name for item in entry.secrets],
            })
        else:
            if transport not in {"stdio", "http"}:
                raise AgentToolError("transport must be 'stdio' or 'http' when no catalog_id is given.")
            command_values.update({"transport": transport, "command": command, "args": list(args or []),
                                   "url": url, "secret_names": [str(item) for item in (secret_names or [])]})
        try:
            server = self._mcp_service.propose(command_values)
        except Exception as error:
            raise AgentToolError(str(error)) from error
        return ToolOutcome(
            "succeeded",
            f"Aguardando aprovação da conexão {server['display_name']}",
            "The connection is proposed and waiting for the user. They will fill in any credential and approve it in the card. Stop here and wait for their next message.",
            {"server": server, "mcp_approval": True, "wait_for_user": True, "tool_kind": "mcp", "mcp_action": "approval_requested"},
        )

    def test_mcp_server(self, slug: str) -> dict[str, Any]:
        from agentos.mcp.auth import McpOAuth
        from agentos.mcp.toolset import connector_for, discover

        oauth = getattr(self._mcp_provider, "_oauth", None)
        if oauth is None and getattr(self._mcp_service, "engine", None) is not None:
            oauth = McpOAuth(self._mcp_service.engine)
        connect = connector_for(oauth) if oauth is not None else discover
        result = self._mcp_service.test(self._mcp_user_id, str(slug), connect)
        return {"summary": f"Testou {slug}", "content": json.dumps(result, ensure_ascii=False),
                "payload": {**result, "tool_kind": "mcp", "mcp_action": "test"}}

    def _mcp_search_documents(self) -> tuple[Any, ...]:
        from agentos.mcp.models import McpToolDescriptor
        from agentos.mcp.search import McpToolSearchDocument

        if self._mcp_provider is None:
            return ()
        docs_fn = getattr(self._mcp_provider, "search_documents", None)
        if callable(docs_fn):
            try:
                return tuple(docs_fn())
            except Exception:
                pass
        docs: list[McpToolSearchDocument] = []
        for item in getattr(self._mcp_provider, "definitions", lambda: ())():
            name = getattr(item, "name", "")
            if isinstance(name, str) and name.startswith("mcp__"):
                parts = name.split("__", 2)
                if len(parts) == 3:
                    _, slug, bare = parts
                    docs.append(
                        McpToolSearchDocument.from_descriptor(
                            server_slug=slug,
                            server_name=slug,
                            descriptor=McpToolDescriptor(
                                name=bare,
                                description=str(getattr(item, "description", "") or ""),
                                input_schema=dict(getattr(item, "parameters", {}) or {"type": "object"}),
                            ),
                        )
                    )
        return tuple(docs)

    def search_mcp_tools(
        self,
        query: str = "",
        server: str | None = None,
        limit: int = 10,
        activate: bool = True,
    ) -> dict[str, Any]:
        from agentos.mcp.search import McpToolSearchEngine

        if self._mcp_provider is None:
            raise AgentToolError("No active MCP servers are connected in this conversation.")
        safe_limit = max(1, min(int(limit or 10), 50))
        engine = McpToolSearchEngine(self._mcp_search_documents())
        matches = engine.search(str(query or ""), server=server, limit=safe_limit)
        tools_payload = [item.to_dict(include_schema=True) for item in matches]
        activated: list[str] = []
        if activate and not self._read_only_mode:
            for item in matches:
                self._active_mcp_tool_names.add(item.qualified_name)
                activated.append(item.qualified_name)
        payload = {
            "query": str(query or ""),
            "server": server,
            "count": len(tools_payload),
            "activated": activated,
            "tools": tools_payload,
            "servers": list(self.mcp_server_summaries()),
            "tool_kind": "mcp",
            "mcp_action": "search_tools",
        }
        return {
            "summary": f"{len(tools_payload)} ferramenta(s) MCP encontrada(s)",
            "content": json.dumps(payload, ensure_ascii=False),
            "payload": payload,
        }

    def inspect_mcp_server(
        self,
        server: str,
        include_schemas: bool = False,
        activate_all: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        if self._mcp_provider is None:
            raise AgentToolError("No active MCP servers are connected in this conversation.")
        clean_server = str(server or "").strip()
        if not clean_server:
            raise AgentToolError("Provide a non-empty 'server' slug or display name.")
        safe_offset = max(0, int(offset or 0))
        safe_limit = max(1, min(int(limit or 200), 500))
        inspect_fn = getattr(self._mcp_provider, "inspect_server", None)
        inspected: dict[str, Any] | None = None
        if callable(inspect_fn):
            inspected = inspect_fn(
                clean_server,
                include_schemas=bool(include_schemas),
                offset=safe_offset,
                limit=safe_limit,
            )
        else:
            # Fallback for duck-typed providers in unit tests
            docs = [
                doc for doc in self._mcp_search_documents()
                if doc.server_slug.lower() == clean_server.lower() or clean_server.lower() in doc.server_slug.lower()
            ]
            if docs:
                page = docs[safe_offset : safe_offset + safe_limit]
                inspected = {
                    "slug": docs[0].server_slug,
                    "display_name": docs[0].server_name,
                    "transport": "unknown",
                    "tool_count": len(docs),
                    "offset": safe_offset,
                    "limit": safe_limit,
                    "returned_count": len(page),
                    "has_more": (safe_offset + safe_limit) < len(docs),
                    "tools": [
                        {
                            "name": doc.tool_name,
                            "qualified_name": doc.qualified_name,
                            "description": doc.description,
                            "parameters": list(doc.parameter_names),
                            **({"input_schema": dict(doc.input_schema)} if include_schemas else {}),
                        }
                        for doc in page
                    ],
                }
        if inspected is None:
            available = ", ".join(item["slug"] for item in self.mcp_server_summaries()) or "none"
            raise AgentToolError(f"MCP server '{clean_server}' is not connected. Connected servers: {available}.")

        activated: list[str] = []
        if activate_all and not self._read_only_mode:
            for item in inspected.get("tools", ()):
                qname = item.get("qualified_name")
                if isinstance(qname, str) and qname:
                    self._active_mcp_tool_names.add(qname)
                    activated.append(qname)
        payload = {
            **inspected,
            "activated": activated,
            "tool_kind": "mcp",
            "mcp_action": "inspect_server",
        }
        return {
            "summary": f"Inspecionou MCP {inspected['display_name']} ({inspected['returned_count']}/{inspected['tool_count']} tools)",
            "content": json.dumps(payload, ensure_ascii=False),
            "payload": payload,
        }

    def call_mcp_tool(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any] | str | None = None,
    ) -> ToolOutcome:
        from agentos.mcp.models import qualified_tool_name

        if self._read_only_mode:
            raise AgentToolError("call_mcp_tool is not allowed in read-only mode.")
        if self._mcp_provider is None:
            raise AgentToolError("No active MCP servers are connected in this conversation.")

        clean_server = str(server or "").strip()
        clean_tool = str(tool or "").strip()
        if not clean_tool:
            raise AgentToolError("Provide the MCP 'tool' name to invoke.")

        parsed_args: dict[str, Any] = {}
        if isinstance(arguments, Mapping):
            parsed_args = dict(arguments)
        elif isinstance(arguments, str) and arguments.strip():
            try:
                loaded = json.loads(arguments)
            except ValueError as err:
                raise AgentToolError(f"Invalid JSON in 'arguments': {err}") from err
            if not isinstance(loaded, Mapping):
                raise AgentToolError("'arguments' must be a JSON object.")
            parsed_args = dict(loaded)
        elif arguments is not None:
            raise AgentToolError("'arguments' must be an object/dictionary.")

        self.definitions()
        candidates: list[str] = []
        if clean_tool.startswith("mcp__"):
            candidates.append(clean_tool)
        if clean_server:
            candidates.append(qualified_tool_name(clean_server.lower(), clean_tool))
            for summary in self.mcp_server_summaries():
                if clean_server.lower() in {summary["slug"].lower(), summary["display_name"].lower()}:
                    candidates.append(qualified_tool_name(summary["slug"], clean_tool))

        resolved_def: ToolDefinition | None = None
        for qname in candidates:
            if qname in self._by_name:
                resolved_def = self._by_name[qname]
                break
        if resolved_def is None:
            suffix = f"__{clean_tool}"
            for name, definition in self._by_name.items():
                if name.startswith("mcp__") and name.endswith(suffix):
                    if not clean_server or clean_server.lower() in name.lower():
                        resolved_def = definition
                        break
        if resolved_def is None:
            raise AgentToolError(
                f"MCP tool '{clean_tool}' was not found on server '{clean_server}'. "
                "Call search_mcp_tools or inspect_mcp_server to check the exact tool name and schema."
            )

        self._active_mcp_tool_names.add(resolved_def.name)
        try:
            raw_result = resolved_def.handler(**parsed_args)
        except TypeError as err:
            raise AgentToolError(f"Invalid arguments for MCP tool '{resolved_def.name}': {err}") from err
        if isinstance(raw_result, ToolOutcome):
            return raw_result
        return ToolOutcome(
            "succeeded",
            str(raw_result.get("summary") or resolved_def.name)[:240],
            str(raw_result.get("content") or ""),
            dict(raw_result.get("payload") or {"tool_kind": "mcp"}),
        )
