"""Admin tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Mapping, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, ToolDefinition, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.mcp.resources import McpResourceTools
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class McpTools(McpResourceTools):
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
                      secret_names: list[str] | None = None, headers: list[str] | None = None,
                      **rejected: Any) -> ToolOutcome:
        from agentos.mcp.catalog import find_catalog_entry

        if rejected:
            raise AgentToolError(
                "configure_mcp does not accept credential values. Pass secret_names or header names only; the user types the values in the approval card."
            )
        header_secret_names: list[str] = []
        if headers is not None:
            if isinstance(headers, (str, bytes, Mapping)) or not isinstance(headers, (list, tuple, set)):
                raise AgentToolError(
                    "configure_mcp expects 'headers' to be a list of HTTP header names (e.g. ['Authorization']), never header values."
                )
            for item in headers:
                clean_header = str(item or "").strip()
                if clean_header.lower().startswith("header:"):
                    clean_header = clean_header[len("header:"):].strip()
                if not clean_header or ":" in clean_header or any(ch.isspace() for ch in clean_header):
                    raise AgentToolError(
                        "configure_mcp does not accept credential values in 'headers'. Pass header names only (e.g. ['Authorization']); the user types the values in the approval card."
                    )
                header_secret_names.append(f"header:{clean_header}")
        command_values: dict[str, object] = {"user_id": self._mcp_user_id, "display_name": str(display_name)}
        if catalog_id:
            entry = find_catalog_entry(str(catalog_id))
            if entry is None:
                raise AgentToolError(f"'{catalog_id}' is not in the MCP catalog. Call list_mcp_catalog first, or pass transport plus command/url explicitly.")
            merged_secrets = [item.name for item in entry.secrets]
            for h in header_secret_names:
                if h not in merged_secrets:
                    merged_secrets.append(h)
            command_values.update({
                "catalog_id": entry.catalog_id, "transport": entry.transport.value,
                "command": entry.command, "args": list(entry.args), "url": entry.url,
                "secret_names": merged_secrets,
            })
        else:
            if transport not in {"stdio", "http"}:
                raise AgentToolError("transport must be 'stdio' or 'http' when no catalog_id is given.")
            merged_secrets = [str(item) for item in (secret_names or [])]
            for h in header_secret_names:
                if h not in merged_secrets:
                    merged_secrets.append(h)
            command_values.update({"transport": transport, "command": command, "args": list(args or []),
                                   "url": url, "secret_names": merged_secrets})
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

    def _native_search_documents(self) -> tuple[tuple[Any, ...], dict[str, str]]:
        from agentos.mcp.models import McpToolDescriptor
        from agentos.mcp.search import McpToolSearchDocument

        self.definitions()
        docs: list[McpToolSearchDocument] = []
        by_qualified: dict[str, str] = {}
        for internal in sorted(self.deferred_tool_names):
            definition = self._by_name.get(internal)
            if definition is None:
                continue
            schema = definition.schema()
            if self.name_translator is not None:
                schema = self.name_translator.schemas_out([schema])[0]
            fn = schema.get("function") or {}
            model_name = str(fn.get("name") or internal)
            desc = str(fn.get("description") or definition.description or "")
            params = dict(fn.get("parameters") or definition.parameters or {"type": "object"})
            doc = McpToolSearchDocument.from_descriptor(
                server_slug="",
                server_name="",
                descriptor=McpToolDescriptor(
                    name=model_name,
                    description=desc,
                    input_schema=params,
                ),
            )
            docs.append(doc)
            by_qualified[doc.qualified_name] = internal
        return tuple(docs), by_qualified

    def search_mcp_tools(
        self,
        query: str = "",
        server: str | None = None,
        limit: int = 10,
        activate: bool = True,
    ) -> dict[str, Any]:
        from dataclasses import replace
        from agentos.agentic.tool_deferral import ALWAYS_LOADED_CODE_TOOLS, CODE_MODE_CORE_TOOLS
        from agentos.mcp.search import McpToolSearchEngine

        if self._mcp_provider is None and not self._code_mode_active:
            raise AgentToolError("No active MCP servers are connected in this conversation.")
        safe_limit = max(1, min(int(limit or 10), 50))
        native_docs, native_by_qname = self._native_search_documents()
        native_engine = McpToolSearchEngine(native_docs)
        mcp_engine = McpToolSearchEngine(self._mcp_search_documents())
        text = str(query or "").strip()
        already_available: list[str] = []
        not_found: list[str] = []
        if text.lower().startswith("select:"):
            names = [name.strip() for name in text[len("select:"):].split(",") if name.strip()]
            wanted = names[:50]
            not_found.extend(f"{name} (select takes at most 50 names)" for name in names[50:])
            native_by_internal: dict[str, Any] = {}
            for item in native_engine.listing(server=server):
                internal_key = native_by_qname.get(item.qualified_name)
                if internal_key:
                    native_by_internal[internal_key] = item
            mcp_index: dict[str, Any] = {}
            for item in mcp_engine.listing(server=server):  # every tool, not only the first 200 (W4-R18)
                mcp_index.setdefault(item.qualified_name.lower(), item)
                mcp_index.setdefault(item.tool_name.lower(), item)
            available_defs = {d.name for d in self.definitions()}
            native_matches: list[Any] = []
            mcp_matches: list[Any] = []
            for name in wanted:
                internal_key = self.key_for(name)
                native_item = native_by_internal.get(internal_key)
                if native_item is not None:
                    if native_item not in native_matches:
                        native_matches.append(native_item)
                    continue
                mcp_item = mcp_index.get(name.lower())
                if mcp_item is not None:
                    if mcp_item not in mcp_matches:
                        mcp_matches.append(mcp_item)
                    continue
                is_already_available = (
                    internal_key in available_defs
                    and not internal_key.startswith("mcp__")
                    and (
                        not self._code_mode_active
                        or (
                            internal_key in CODE_MODE_CORE_TOOLS
                            and (
                                internal_key in ALWAYS_LOADED_CODE_TOOLS
                                or internal_key in self.discovered_tool_names
                                or not self.deferred_tool_names
                            )
                        )
                    )
                )
                if is_already_available:
                    already_available.append(name)
                else:
                    not_found.append(name)
            matches = native_matches + mcp_matches
        else:
            required = [word[1:].lower() for word in text.split() if word.startswith("+") and len(word) > 1]
            keywords = " ".join(word for word in text.split() if not word.startswith("+")) or " ".join(required)

            def _run_engine(eng: McpToolSearchEngine) -> list[Any]:
                if not eng.documents:
                    return []
                if required:
                    # The +terms filter the whole catalogue before the limit, so a tool past the first 200 is found (W4-R18).
                    scored_hits = eng.search(keywords, server=server, limit=200) if keywords else []
                    ranked = {item.qualified_name: rank for rank, item in enumerate(scored_hits)}
                    scores = {item.qualified_name: item.score for item in scored_hits}
                    found = [
                        replace(item, score=scores.get(item.qualified_name, 1.0))
                        for item in eng.listing(server=server)
                        if all(term in item.qualified_name.lower() for term in required)
                    ]
                    found.sort(key=lambda item: (ranked.get(item.qualified_name, len(ranked)), item.qualified_name))
                    return found[:safe_limit]
                return list(eng.search(keywords, server=server, limit=safe_limit))

            combined = _run_engine(native_engine) + _run_engine(mcp_engine)
            combined.sort(
                key=lambda item: (
                    -item.score,
                    0 if item.qualified_name in native_by_qname else 1,
                    item.server_slug,
                    item.tool_name,
                )
            )
            matches = combined[:safe_limit]
        tools_payload: list[dict[str, Any]] = []
        match_names: list[str] = []
        activated: list[str] = []
        for item in matches:
            internal_key = native_by_qname.get(item.qualified_name)
            if internal_key is not None:
                tools_payload.append({
                    "name": item.tool_name,
                    "description": item.description,
                    "input_schema": dict(item.input_schema),
                    "tool_kind": "native",
                })
                match_names.append(item.tool_name)
                if activate:
                    self.discover([internal_key])
                    activated.append(item.tool_name)
            else:
                tools_payload.append(item.to_dict(include_schema=True))
                match_names.append(item.qualified_name)
                if activate and not self._read_only_mode:
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
            "tool_output": {
                "query": text, "matches": match_names,
                "already_available": already_available, "not_found": not_found,
            },
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


class ToolSearchInput(ToolInput):
    query: str = Field(description="Keywords or a tool name.")
    max_results: int | None = Field(None, ge=1, le=50)


class ToolSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    matches: list[str]
    already_available: list[str]
    not_found: list[str]


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="list_mcp_catalog",
        description="List the MCP servers Orin knows how to connect. Use this first when the user asks to connect a tool: each entry explains what the server does and exactly which credential the user has to fetch.",
        legacy_schema=_schema({"query": {**_TEXT, "description": "Filter by name or subject, e.g. 'github'. Omit for the whole catalog."}}),
        handler="list_mcp_catalog",
        kind="mcp",
        read_only=True,
        available=lambda self: (self._mcp_service is not None and self._mcp_user_id),
        order=460,
    ),
    ToolSpec(
        key="list_mcp_servers",
        description="List the MCP servers this user already configured, with their state and tool count.",
        legacy_schema=_schema({}),
        handler="list_mcp_servers",
        kind="mcp",
        read_only=True,
        available=lambda self: (self._mcp_service is not None and self._mcp_user_id),
        order=470,
    ),
    ToolSpec(
        key="configure_mcp",
        description=(
            "Propose an MCP server connection. This never activates the server and never accepts a credential value: "
            "it creates a pending configuration and shows the user an approval card where they type any secret themselves. "
            "For remote HTTP servers authenticated by static HTTP headers (e.g. Authorization: Bearer <KEY>), pass the header "
            "names in 'headers' (e.g. ['Authorization']) or use 'header:<Name>' in 'secret_names' (e.g. ['header:Authorization']). "
            "Explain to the user what the server does and which credential they will need before calling this."
        ),
        legacy_schema=_schema({
            "display_name": {**_TEXT, "description": "How the connection appears in Settings, e.g. 'GitHub'."},
            "catalog_id": {**_TEXT, "description": "Id from list_mcp_catalog. Fills transport, command and required secrets."},
            "transport": {"type": "string", "enum": ["stdio", "http"], "description": "Only for a server outside the catalog."},
            "command": {**_TEXT, "description": "stdio launcher, e.g. 'npx'. Only for a server outside the catalog."},
            "args": {"type": "array", "items": _TEXT},
            "url": {**_TEXT, "description": "https endpoint. Only for a server outside the catalog."},
            "secret_names": {
                "type": "array",
                "items": _TEXT,
                "description": (
                    "Names of the credentials the server needs (names only — never values). "
                    "Static HTTP header servers use 'header:<Header-Name>', e.g. 'header:Authorization'."
                ),
            },
            "headers": {
                "type": "array",
                "items": _TEXT,
                "description": (
                    "Convenience list of HTTP header names for remote HTTP servers (e.g. ['Authorization', 'X-Api-Key']). "
                    "Names only — never values; the user types the values in the approval card."
                ),
            },
        }, ("display_name",)),
        handler="configure_mcp",
        kind="mcp",
        policy_tags=("mutates",),
        available=lambda self: (self._mcp_service is not None and self._mcp_user_id),
        order=480,
    ),
    ToolSpec(
        key="test_mcp_server",
        description="Re-run discovery against an already approved MCP server and report whether it answers and which tools it publishes.",
        legacy_schema=_schema({"slug": _TEXT}, ("slug",)),
        handler="test_mcp_server",
        kind="mcp",
        available=lambda self: (self._mcp_service is not None and self._mcp_user_id),
        order=490,
    ),
    ToolSpec(
        key="search_mcp_tools",
        description="Search tools across connected MCP servers by tool name, description, or JSON schema (parameter names, descriptions, enum values). Returns the full input_schema of matching tools and activates them in the current turn so they can also be called directly.",
        legacy_schema=_schema(
            {
                "query": {
                    **_TEXT,
                    "description": "Keywords, tool name, parameter name, or concept to search for (supports Portuguese and English). Leave empty to list tools alphabetically.",
                },
                "server": {
                    **_TEXT,
                    "description": "Optional MCP server slug or display name to filter results.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of matching tools to return (default 10).",
                },
                "activate": {
                    "type": "boolean",
                    "description": "Whether to activate the matched tools in the current turn's tool schemas (default true).",
                },
            },
            (),
        ),
        handler="search_mcp_tools",
        kind="mcp",
        input_model=ToolSearchInput,
        output_model=ToolSearchOutput,
        public_description="Find deferred tools (Orin's own and connected MCP servers') by keyword, or load them by name with 'select:Name1,Name2', and get their schemas so they can be called.",
        read_only=True,
        policy_tags=("mcp",),
        max_result_chars=100_000,
        available=lambda self: (self._mcp_provider is not None or self._code_mode_active),
        order=630,
    ),
    ToolSpec(
        key="inspect_mcp_server",
        description="Pull complete metadata and the full list of tools from a connected MCP server. Optionally include full JSON input_schemas or activate all returned tools in the current turn.",
        legacy_schema=_schema(
            {
                "server": {
                    **_TEXT,
                    "description": "MCP server slug or display name to inspect.",
                },
                "include_schemas": {
                    "type": "boolean",
                    "description": "If true, include the full JSON input_schema for every returned tool (default false).",
                },
                "activate_all": {
                    "type": "boolean",
                    "description": "If true, activate all returned tools in the current turn's tool schemas (default false).",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Pagination offset (default 0).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum number of tools to return in this page (default 200).",
                },
            },
            ("server",),
        ),
        handler="inspect_mcp_server",
        kind="mcp",
        read_only=True,
        policy_tags=("mcp",),
        max_result_chars=100_000,
        available=lambda self: (self._mcp_provider is not None),
        order=640,
    ),
    ToolSpec(
        key="call_mcp_tool",
        description="Invoke a tool on a connected MCP server directly by server slug/name and tool name without waiting for a new turn iteration.",
        legacy_schema=_schema(
            {
                "server": {
                    **_TEXT,
                    "description": "MCP server slug or display name (e.g. 'auryly').",
                },
                "tool": {
                    **_TEXT,
                    "description": "Tool name on the server (e.g. 'get_funnel_metrics') or qualified name ('mcp__auryly__get_funnel_metrics').",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments object matching the tool's input_schema.",
                },
            },
            ("server", "tool"),
        ),
        handler="call_mcp_tool",
        kind="mcp",
        read_only=False,
        policy_tags=("mcp", "mutates"),
        max_result_chars=100_000,
        available=lambda self: (self._mcp_provider is not None),
        order=650,
    ),
)
