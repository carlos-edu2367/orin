"""Turn cached MCP descriptors into native ToolDefinitions.

Building the tool set must not touch the network: the definitions come from the
discovery cache. A session opens on the first call to that server and closes
with the turn, so a configured-but-unused server costs nothing.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from agentos.agentic.agent_context import get_current_agent_id
from agentos.agentic.agent_tools import ToolOutcome, _bounded
from .models import McpAuthKind, McpServerConfig, McpToolDescriptor, McpTransport, qualified_tool_name
from .sanitize import MAX_DESCRIPTION_CHARS

if TYPE_CHECKING:
    from .auth import McpOAuth
    from .client import McpClient
    from .token_source import TokenSource

# Playwright (playwright-core) when the browser channel it was told to use is not installed where it looks.
_MISSING_BROWSER = re.compile(r"Chromium distribution '([A-Za-z0-9_-]{1,40})' is not found")


def _missing_browser_hint(channel: str) -> str:
    return (
        f"[Orin] The '{channel}' browser this Playwright server uses was not found on this machine. Do not retry "
        "the same call. Tell the person to either install Google Chrome (https://www.google.com/chrome/) or "
        "change the Playwright MCP server's arguments to use Microsoft Edge, which ships with Windows: "
        "add `--browser msedge` (Configurações > MCP > Playwright). "
        f"Installing it for Playwright alone also works: `npx playwright install {channel}`."
    )


MAX_MCP_RESULT_CHARS = 100_000
MAX_MCP_RAW_RESULT_CHARS = 500_000
MAX_IMAGES_PER_CALL = 4
DEFAULT_MCP_OUTPUT_TOKENS = 25_000
BROWSER_SERVER_MARKERS: tuple[str, ...] = ("@playwright/mcp",)
BROWSER_TIMEOUT_SECONDS = 120.0
MAX_BROWSER_SESSIONS = 4
DEFAULT_BROWSER_SLOT_TIMEOUT_SECONDS = 30.0

# (server id, agent id, workspace root): a browser server gets one session per agent and per workspace, so two
# conversations on different projects never share the folder the first one pointed it at.
SessionKey = tuple[str, str | None, str | None]


def _is_browser_server(config: McpServerConfig) -> bool:
    """True when ``config`` launches a browser MCP server (such as ``@playwright/mcp``)."""
    tokens = (str(config.command or ""), *(str(arg or "") for arg in (config.args or ())))
    for token in tokens:
        lowered = token.strip().lower()
        if any(marker in lowered for marker in BROWSER_SERVER_MARKERS):
            return True
    return False


def _with_isolated_arg(config: McpServerConfig) -> McpServerConfig:
    """Append ``--isolated`` to ``config.args`` when not already present."""
    args = tuple(config.args or ())
    if any(str(arg).strip() == "--isolated" or str(arg).strip().startswith("--isolated=") for arg in args):
        return config
    return replace(config, args=(*args, "--isolated"))


def _with_workspace_output(config: McpServerConfig, workspace_root: str | os.PathLike[str] | None) -> McpServerConfig:
    """Start a browser server in the conversation's workspace and write its automatic files under it.

    `@playwright/mcp` only reads and writes inside its workspace root (the working folder when the client
    announces none) and puts a screenshot taken without a file name in `--output-dir`. Started from the
    worker's folder it refused every path in the project and saved images where no tool of the turn may read
    them. A server that already sets its own folder or `--output-dir` keeps it.
    """
    if not workspace_root:
        return config
    root = os.fspath(workspace_root)
    args = tuple(config.args or ())
    if not any(str(arg).strip() == "--output-dir" or str(arg).strip().startswith("--output-dir=") for arg in args):
        args = (*args, "--output-dir", os.path.join(root, ".orin", "playwright"))
    return replace(config, cwd=config.cwd or root, args=args)


def mcp_output_limit_chars() -> int:
    """How much of an MCP tool's answer reaches the model before the rest goes to a file: ``MAX_MCP_OUTPUT_TOKENS``
    (default 25,000 tokens, counted as 4 characters each; at least 1,000 and at most 250,000 tokens)."""
    try:
        tokens = int(str(os.environ.get("MAX_MCP_OUTPUT_TOKENS") or DEFAULT_MCP_OUTPUT_TOKENS).strip())
    except ValueError:
        tokens = DEFAULT_MCP_OUTPUT_TOKENS
    return max(1_000, min(tokens, 250_000)) * 4

ServerBundle = tuple[McpServerConfig, tuple[McpToolDescriptor, ...], Mapping[str, str]]
Connector = Callable[[McpServerConfig, Mapping[str, str]], tuple[str, tuple[McpToolDescriptor, ...]]]


def build_client(config: McpServerConfig, secrets: Mapping[str, str], *,
                 token_source: "TokenSource | None" = None) -> "McpClient":
    # Imported here so building a tool set never imports a transport it will not
    # use, and so the client module stays free of a cycle back into this one.
    from .client import McpClient
    from .transport_http import HttpTransport
    from .transport_sse import SseTransport
    from .transport_sdk import SdkRelayTransport
    from .transport_stdio import DEFAULT_TIMEOUT_SECONDS, StdioTransport
    from .transport_ws import WebSocketTransport

    # W5: a server from a configuration file was written by the person (or approved from the repository), not
    # proposed by the model: it may use any launcher, start in its folder, and reach a local or plain-http address.
    from_file = config.origin != "orin"
    custom_headers: dict[str, str] = {}
    for name, value in secrets.items():
        if name.startswith("header:"):
            header_name = name[len("header:"):]
            if header_name:
                for existing in [k for k in custom_headers if k.lower() == header_name.lower()]:
                    del custom_headers[existing]
                custom_headers[header_name] = value
    if config.transport is McpTransport.SDK:
        return McpClient(SdkRelayTransport(server_name=config.display_name, relay_key=str(secrets.get("relay") or "")))
    if config.transport is McpTransport.STDIO:
        stdio_timeout = BROWSER_TIMEOUT_SECONDS if _is_browser_server(config) else DEFAULT_TIMEOUT_SECONDS
        transport = StdioTransport(command=str(config.command), args=config.args, env=dict(secrets), cwd=config.cwd,
                                   timeout=stdio_timeout, allow_any_command=from_file)
    elif config.transport is McpTransport.SSE:
        transport = SseTransport(url=str(config.url), headers=custom_headers if from_file else {}, allow_local=from_file)
    elif config.transport is McpTransport.WEBSOCKET:
        transport = WebSocketTransport(url=str(config.url), headers=custom_headers if from_file else {}, allow_local=from_file)
    else:
        headers: dict[str, str] = {}
        has_explicit_auth = any(k.lower() == "authorization" for k in custom_headers)
        if "token" in secrets and token_source is None and not has_explicit_auth:
            token_val = str(secrets["token"]).strip()
            headers["authorization"] = (
                f"Bearer {token_val[7:].strip()}" if token_val.lower().startswith("bearer ") else f"Bearer {token_val}"
            )
        headers.update(custom_headers)
        transport = HttpTransport(url=str(config.url), headers=headers, token_source=token_source, allow_local=from_file)
    return McpClient(transport)


def discover(config: McpServerConfig, secrets: Mapping[str, str], *,
             token_source: "TokenSource | None" = None) -> tuple[str, tuple[McpToolDescriptor, ...]]:
    """Open a short-lived session, list its tools, and close.

    This is the one place a proposed or already-approved server gets turned
    into evidence it actually works: McpServerService.approve() and .test()
    both take this as their `connect` callable, so there is exactly one
    implementation of "what a live connection attempt means" in the codebase.
    """
    client = build_client(config, secrets, token_source=token_source) if token_source is not None else build_client(config, secrets)
    try:
        negotiation = client.initialize()
        tools = client.list_tools()
    finally:
        client.close()
    return negotiation.protocol_version, tools


def connector_for(oauth: "McpOAuth | None") -> Connector:
    """The approve/test/activate connector: an OAuth-signed server connects with its token source.

    A server that asked for sign-in but has none yet connects bare, so its
    401 reads as "sign in" again instead of as a lost sign-in.
    """
    def connect(config: McpServerConfig, secrets: Mapping[str, str]) -> tuple[str, tuple[McpToolDescriptor, ...]]:
        signed_in = oauth is not None and config.auth_kind is McpAuthKind.OAUTH and oauth.has_sign_in(config)
        source = oauth.token_source(config) if signed_in else None
        return discover(config, secrets, token_source=source)
    return connect


class McpToolProvider:
    def __init__(
        self,
        bundles: Iterable[ServerBundle],
        client_factory: Callable[..., Any] = build_client,
        oauth: "McpOAuth | None" = None,
        *,
        max_browser_sessions: int = MAX_BROWSER_SESSIONS,
        browser_slot_timeout: float = DEFAULT_BROWSER_SLOT_TIMEOUT_SECONDS,
        workspace_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._workspace_root: str | None = os.fspath(workspace_root) if workspace_root else None
        self._bundles = list(bundles)
        self._client_factory = client_factory
        self._oauth = oauth
        self._sessions: dict[SessionKey, Any] = {}
        # Subagents run in threads and share this provider; one MCP session is
        # not safe to drive from two threads, and a refresh must not race itself.
        # Browser servers get a dedicated session and lock per (server_id, agent_id).
        self._locks: dict[SessionKey, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._max_browser_sessions = max(1, int(max_browser_sessions))
        self._browser_slot_timeout = max(0.0, float(browser_slot_timeout))
        self._browser_keys: set[SessionKey] = set()
        self._browser_cond = threading.Condition()
        # W5: answers a server's request for input during a call: (server slug, request params) -> answer.
        self.elicitation_handler: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None

    @property
    def open_session_count(self) -> int:
        return len(self._sessions)

    def bind_workspace(self, workspace_root: str | os.PathLike[str] | None) -> None:
        """The folder browser servers start in and write to (the conversation's workspace); set before the first call."""
        self._workspace_root = os.fspath(workspace_root) if workspace_root else None

    def add_bundles(self, bundles: Iterable[ServerBundle]) -> None:
        """Servers read from configuration files join this turn's servers (W5)."""
        self._bundles.extend(bundles)

    def retain(self, keep: Callable[[McpServerConfig], bool]) -> list[McpServerConfig]:
        """Drop the servers ``keep`` refuses (``allowedMcpServers``/``deniedMcpServers``); returns the dropped ones."""
        dropped = [config for config, _, _ in self._bundles if not keep(config)]
        gone = {config.server_id for config in dropped}
        self._bundles = [bundle for bundle in self._bundles if bundle[0].server_id not in gone]
        return dropped

    def server_slugs(self) -> frozenset[str]:
        return frozenset(config.slug for config, _, _ in self._bundles)

    # -- resources and tool refresh (W5) ------------------------------------

    def find_server(self, server: str) -> ServerBundle | None:
        target = str(server or "").strip().lower()
        for bundle in self._bundles:
            if target in (bundle[0].slug.lower(), bundle[0].display_name.lower()):
                return bundle
        return None

    def _targets(self, server: str | None) -> list[ServerBundle]:
        if not server:
            return list(self._bundles)
        found = self.find_server(server)
        return [found] if found is not None else []

    def _session_key(self, config: McpServerConfig) -> SessionKey:
        if _is_browser_server(config):
            return (config.server_id, get_current_agent_id(), self._workspace_root)
        return (config.server_id, None, None)

    @staticmethod
    def _normalize_key(key: SessionKey | tuple[str, str | None] | str) -> SessionKey:
        if isinstance(key, str):
            return (key, None, None)
        if len(key) == 2:
            return (key[0], key[1], None)
        return key  # type: ignore[return-value]

    def _with_session(self, config: McpServerConfig, secrets: Mapping[str, str], action: Callable[[Any], Any]) -> Any:
        from .protocol import McpProtocolError

        key = self._session_key(config)
        with self._lock(key):
            try:
                client = self._session(config, secrets, key=key)
                return action(client)
            except RuntimeError as error:
                if not isinstance(error, McpProtocolError):
                    self._evict_session(key)
                raise

    def list_resources(self, server: str | None = None) -> list[dict[str, Any]]:
        """Every resource of the servers (or of one), each tagged with its server; a server that fails says why."""
        found: list[dict[str, Any]] = []
        for config, _tools, secrets in self._targets(server):
            try:
                items = self._with_session(config, secrets, lambda client: client.list_resources())
            except Exception as error:  # noqa: BLE001 - one server's failure is reported, the rest are listed
                found.append({"server": config.slug, "error": str(error)[:300]})
                continue
            found.extend({"server": config.slug, **item} for item in items)
        return found

    def read_resource(self, server: str, uri: str) -> tuple[Mapping[str, Any], ...]:
        bundle = self.find_server(server)
        if bundle is None:
            raise LookupError(f"no MCP server named {server!r}")
        config, _tools, secrets = bundle
        return self._with_session(config, secrets, lambda client: client.read_resource(uri))

    def get_prompt(self, server: str, prompt: str, words: Sequence[str]) -> str:
        """Run one of a server's prompts: ``words`` fill its arguments in order (the last one takes the rest)."""
        bundle = self.find_server(server)
        if bundle is None:
            raise LookupError(f"no MCP server named {server!r}")
        config, _tools, secrets = bundle
        known = self._with_session(config, secrets, lambda client: client.list_prompts())
        spec = next((item for item in known if item["name"] == prompt), None)
        if spec is None:
            raise LookupError(f"{config.display_name} has no prompt named {prompt!r}")
        names = [item["name"] for item in spec["arguments"]]
        values = list(words)
        if names and len(values) > len(names):
            values = [*values[: len(names) - 1], " ".join(values[len(names) - 1:])]
        arguments = dict(zip(names, values))
        missing = [item["name"] for item in spec["arguments"] if item["required"] and not arguments.get(item["name"])]
        if missing:
            raise ValueError(f"missing argument(s): {', '.join(missing)} (expected: {' '.join(names)})")
        messages = self._with_session(config, secrets, lambda client: client.get_prompt(prompt, arguments))
        return _render_prompt(messages)

    def _replace_tools(self, server_id: str, tools: tuple[McpToolDescriptor, ...]) -> None:
        self._bundles = [(config, tools if config.server_id == server_id else old, secrets) for config, old, secrets in self._bundles]

    def refresh_tools(self, server: str | None = None) -> list[dict[str, Any]]:
        """List the servers' tools again. A file server's list is replaced; Orin's own servers keep the approved list."""
        report: list[dict[str, Any]] = []
        for config, tools, secrets in self._targets(server):
            try:
                fresh = self._with_session(config, secrets, lambda client: client.list_tools())
            except Exception as error:  # noqa: BLE001
                report.append({"server": config.slug, "error": str(error)[:300]})
                continue
            before, after = {item.name for item in tools}, {item.name for item in fresh}
            entry: dict[str, Any] = {"server": config.slug, "added": sorted(after - before), "removed": sorted(before - after)}
            if config.origin == "orin":
                entry["applied"] = False
                entry["note"] = "Orin's own servers keep the tools the person approved; a change needs a new approval in Settings > MCP."
            else:
                self._replace_tools(config.server_id, tuple(fresh))
                entry["applied"] = True
            report.append(entry)
        return report

    def _follow_changes(self, config: McpServerConfig, client: Any) -> None:
        """A file server that announced new tools during a call gets its list replaced (Orin's own keep theirs)."""
        changed = getattr(client, "changed", None)
        if not changed or "tools" not in changed:
            return
        changed.discard("tools")
        if config.origin == "orin":
            return
        try:
            self._replace_tools(config.server_id, tuple(client.list_tools()))
        except Exception:  # noqa: BLE001 - the old list stays; RefreshMcpTools can try again
            pass

    def _lock(self, key: SessionKey | str) -> threading.Lock:
        normalized = self._normalize_key(key)
        with self._locks_guard:
            return self._locks.setdefault(normalized, threading.Lock())

    def _acquire_browser_slot(self, key: SessionKey, config: McpServerConfig) -> None:
        deadline = time.monotonic() + self._browser_slot_timeout
        with self._browser_cond:
            while key not in self._browser_keys and len(self._browser_keys) >= self._max_browser_sessions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Limite de {self._max_browser_sessions} sessões simultâneas de navegador atingido "
                        f"({config.display_name}). Use menos subagentes em paralelo com o navegador ou "
                        f"execute testes via `npx playwright test` (Too many concurrent browser sessions; "
                        f"use fewer subagents with the browser or run `npx playwright test`)."
                    )
                self._browser_cond.wait(timeout=remaining)
            self._browser_keys.add(key)

    def _release_browser_slot(self, key: SessionKey) -> None:
        with self._browser_cond:
            if key in self._browser_keys:
                self._browser_keys.discard(key)
                self._browser_cond.notify_all()

    def _session(self, config: McpServerConfig, secrets: Mapping[str, str], *, key: SessionKey | None = None) -> Any:
        resolved_key = key if key is not None else self._session_key(config)
        client = self._sessions.get(resolved_key)
        if client is None:
            is_browser = _is_browser_server(config)
            effective_config = (
                _with_isolated_arg(config)
                if (is_browser and resolved_key[1] is not None)
                else config
            )
            if is_browser:
                effective_config = _with_workspace_output(effective_config, resolved_key[2])
            if is_browser:
                self._acquire_browser_slot(resolved_key, effective_config)
            try:
                if self._oauth is not None and effective_config.auth_kind is McpAuthKind.OAUTH:
                    client = self._client_factory(
                        effective_config, secrets, token_source=self._oauth.token_source(effective_config)
                    )
                else:
                    client = self._client_factory(effective_config, secrets)
                setter = getattr(client, "set_elicitation_handler", None)
                if callable(setter) and self.elicitation_handler is not None:
                    handler = self.elicitation_handler
                    setter(lambda params, slug=effective_config.slug: handler(slug, params))
                try:
                    client.initialize()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass
                    raise
                self._sessions[resolved_key] = client
            except Exception:
                if is_browser:
                    self._release_browser_slot(resolved_key)
                raise
        return client

    def _evict_session(self, key: SessionKey | str) -> None:
        normalized = self._normalize_key(key)
        broken = self._sessions.pop(normalized, None)
        self._release_browser_slot(normalized)
        if broken is not None:
            try:
                broken.close()
            except Exception:
                pass

    def release_agent(self, agent_id: str | None) -> None:
        """Close and remove any MCP sessions opened on behalf of ``agent_id``."""
        target = str(agent_id or "").strip()
        if not target or target == "main":
            return
        with self._locks_guard:
            candidate_keys = [
                key
                for key in set(self._sessions.keys()) | set(self._locks.keys())
                if key[1] == target
            ]
        for key in candidate_keys:
            with self._lock(key):
                self._evict_session(key)

    def _handler(self, config: McpServerConfig, secrets: Mapping[str, str], tool: McpToolDescriptor):
        def call(**arguments: Any) -> ToolOutcome:
            from .protocol import McpProtocolError
            from .token_source import McpReauthRequired

            payload = {"tool_kind": "mcp", "mcp_server": config.slug, "mcp_tool": tool.name}
            key = self._session_key(config)
            try:
                with self._lock(key):
                    try:
                        client = self._session(config, secrets, key=key)
                        result = client.call_tool(tool.name, arguments)
                        self._follow_changes(config, client)
                    except McpReauthRequired:
                        self._evict_session(key)
                        raise
                    except RuntimeError as error:
                        if not isinstance(error, McpProtocolError):
                            self._evict_session(key)
                        raise
            except McpReauthRequired as error:
                from .file_servers import redact_secret_values
                message = redact_secret_values(str(error), secrets.values(), url=config.url)
                return ToolOutcome("failed", message[:240], message[:MAX_MCP_RESULT_CHARS], payload, "MCP_REAUTH_REQUIRED")
            except (McpProtocolError, RuntimeError) as error:
                from .file_servers import redact_secret_values
                message = redact_secret_values(f"{config.display_name}: {error}", secrets.values(), url=config.url)
                return ToolOutcome("failed", message[:240], message[:MAX_MCP_RESULT_CHARS], payload, "MCP_UNAVAILABLE")
            text, images = _render(result.content)
            if result.is_error:
                summary = f"{config.display_name} recusou {tool.name}"
                missing = _MISSING_BROWSER.search(text or "")
                if missing is not None:
                    channel = missing.group(1)
                    summary = (f"{config.display_name}: o navegador '{channel}' não foi encontrado; "
                               "instale o Google Chrome ou configure o servidor com --browser msedge")
                    text = f"{text}\n\n{_missing_browser_hint(channel)}"
                return ToolOutcome("failed", summary[:240],
                                   text or "the server reported a tool error", payload, "MCP_TOOL_ERROR")
            return ToolOutcome("succeeded", f"{config.display_name} · {tool.name}"[:240], text, payload, None, images)

        return call


    def definitions(self) -> tuple[Any, ...]:
        from agentos.agentic.agent_tools import ToolDefinition

        built: list[ToolDefinition] = []
        for config, tools, secrets in self._bundles:
            for tool in tools:
                description = f"[{config.display_name}] {tool.description}".strip()[:MAX_DESCRIPTION_CHARS + 128]
                built.append(ToolDefinition(
                    qualified_tool_name(config.slug, tool.name),
                    description,
                    dict(tool.input_schema),
                    self._handler(config, secrets, tool),
                    "mcp",
                    read_only=False,
                    policy_tags=("mcp", "mutates", f"mcp:{config.slug}"),
                    max_result_size_chars=mcp_output_limit_chars(),
                ))
        return tuple(built)

    def server_summaries(self) -> tuple[dict[str, Any], ...]:
        summaries: list[dict[str, Any]] = []
        for config, tools, _ in self._bundles:
            summaries.append({
                "slug": config.slug,
                "display_name": config.display_name,
                "tool_count": len(tools),
            })
        return tuple(summaries)

    def search_documents(self) -> tuple[Any, ...]:
        from .search import McpToolSearchDocument

        docs: list[McpToolSearchDocument] = []
        for config, tools, _ in self._bundles:
            for tool in tools:
                docs.append(
                    McpToolSearchDocument.from_descriptor(
                        server_slug=config.slug,
                        server_name=config.display_name,
                        descriptor=tool,
                    )
                )
        return tuple(docs)

    def search_tools(
        self,
        query: str = "",
        *,
        server: str | None = None,
        limit: int = 10,
        semantic_scorer: Any = None,
    ) -> list[Any]:
        from .search import McpToolSearchEngine

        engine = McpToolSearchEngine(self.search_documents(), semantic_scorer=semantic_scorer)
        return engine.search(query, server=server, limit=limit)

    def inspect_server(
        self,
        server: str,
        *,
        include_schemas: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        target = (server or "").strip().lower()
        if not target:
            return None
        matched_bundle = None
        for config, tools, secrets in self._bundles:
            if config.slug.lower() == target or config.display_name.lower() == target:
                matched_bundle = (config, tools, secrets)
                break
        if matched_bundle is None:
            for config, tools, secrets in self._bundles:
                if target in config.slug.lower() or target in config.display_name.lower():
                    matched_bundle = (config, tools, secrets)
                    break
        if matched_bundle is None:
            return None

        config, tools, _ = matched_bundle
        safe_offset = max(0, int(offset or 0))
        safe_limit = max(1, min(int(limit or 200), 500))
        page = tools[safe_offset : safe_offset + safe_limit]
        tool_items: list[dict[str, Any]] = []
        for tool in page:
            props = tool.input_schema.get("properties") if isinstance(tool.input_schema, Mapping) else {}
            req = tool.input_schema.get("required") if isinstance(tool.input_schema, Mapping) else ()
            entry: dict[str, Any] = {
                "name": tool.name,
                "qualified_name": qualified_tool_name(config.slug, tool.name),
                "description": tool.description,
                "parameters": list(props.keys()) if isinstance(props, Mapping) else [],
                "required": list(req) if isinstance(req, Sequence) and not isinstance(req, (str, bytes)) else [],
            }
            if include_schemas:
                entry["input_schema"] = dict(tool.input_schema)
            tool_items.append(entry)

        return {
            "slug": config.slug,
            "display_name": config.display_name,
            "transport": config.transport.value,
            "tool_count": len(tools),
            "offset": safe_offset,
            "limit": safe_limit,
            "returned_count": len(tool_items),
            "has_more": (safe_offset + safe_limit) < len(tools),
            "tools": tool_items,
        }

    def close(self) -> None:
        for client in list(self._sessions.values()):
            try:
                client.close()
            except Exception:  # closing must never fail a finished turn
                pass
        self._sessions.clear()
        with self._browser_cond:
            self._browser_keys.clear()
            self._browser_cond.notify_all()


def _render_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        content = message.get("content") if isinstance(message.get("content"), Mapping) else {}
        kind = str(content.get("type") or "")
        if kind == "text":
            body = str(content.get("text") or "")
        elif kind == "resource":
            resource = content.get("resource") if isinstance(content.get("resource"), Mapping) else {}
            body = str(resource.get("text") or resource.get("uri") or "")
        else:
            body = f"[{kind or 'unknown'} content]"
        lines.append(f"[{str(message.get('role') or 'user')[:20]}] {body}")
    text, _ = _bounded("\n\n".join(lines), 60_000)
    return text


def _render(content: Sequence[Mapping[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    parts: list[str] = []
    images: list[dict[str, str]] = []
    for block in content:
        kind = str(block.get("type") or "")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "image" and len(images) < MAX_IMAGES_PER_CALL:
            images.append({"media_type": str(block.get("mimeType") or "image/png"), "data": str(block.get("data") or "")})
        elif kind == "resource":
            resource = block.get("resource") if isinstance(block.get("resource"), Mapping) else {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
    text, _ = _bounded("\n".join(part for part in parts if part), MAX_MCP_RAW_RESULT_CHARS)
    return text, images


__all__ = [
    "BROWSER_SERVER_MARKERS",
    "BROWSER_TIMEOUT_SECONDS",
    "Connector",
    "DEFAULT_BROWSER_SLOT_TIMEOUT_SECONDS",
    "MAX_BROWSER_SESSIONS",
    "MAX_MCP_RESULT_CHARS",
    "McpToolProvider",
    "ServerBundle",
    "_is_browser_server",
    "build_client",
    "connector_for",
    "discover",
]

