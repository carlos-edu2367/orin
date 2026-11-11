"""Application-facing MCP server library: propose, approve, run, retire.

``propose`` never accepts a credential value, only its name — the value is
supplied later, at ``approve``, and is cast into an ``httpx``/subprocess-ready
form only long enough to hand to ``connect``. What lands on disk is either
nothing (state=pending_approval) or a single Fernet-encrypted JSON blob
(state=active), through the same ``ProviderSecretCipher`` already used for
provider credentials.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.configuration.capabilities import CapabilityUnavailable
from agentos.persistence.postgres.mcp import public_summary, row_to_config, row_to_tool
from agentos.persistence.postgres.schema import (
    mcp_oauth_clients, mcp_server_tools, mcp_servers, oauth_pending_authorizations, oauth_tokens,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher

from .file_servers import redact_secret_values
from .models import McpAuthKind, McpServerConfig, McpServerState, McpToolDescriptor, McpTransport, slugify, tools_digest as _tools_digest
from .transport_http import HttpUnauthorized

MAX_STATE_REASON_CHARS = 512
_ALLOWED_PROPOSAL_FIELDS = frozenset({
    "user_id", "slug", "display_name", "transport", "command", "args", "url",
    "secret_names", "headers", "catalog_id", "tool_allowlist",
})

# RFC 7230 §3.2.6 token characters for HTTP header field-names.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$")
_FORBIDDEN_MCP_HEADERS = frozenset({
    "accept",
    "connection",
    "content-length",
    "content-type",
    "cookie",
    "host",
    "keep-alive",
    "mcp-protocol-version",
    "mcp-session-id",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})

Connector = Callable[[McpServerConfig, Mapping[str, str]], tuple[str, tuple[McpToolDescriptor, ...]]]


class McpServiceError(ValueError):
    """Raised for any request the MCP server library will not act on."""


def validate_mcp_header_name(header_name: str) -> str:
    if not header_name or not _HEADER_NAME_RE.fullmatch(header_name):
        raise McpServiceError(f"invalid HTTP header name '{header_name[:64]}'")
    if header_name.lower() in _FORBIDDEN_MCP_HEADERS:
        raise McpServiceError(f"HTTP header '{header_name}' is reserved by the transport and cannot be set")
    return header_name


def validate_mcp_secret_names(secret_names: Iterable[str]) -> tuple[str, ...]:
    validated: list[str] = []
    seen: set[str] = set()
    for raw in secret_names:
        name = str(raw or "").strip()
        if not name:
            continue
        if name.lower().startswith("header:"):
            header_name = validate_mcp_header_name(name[len("header:"):])
            name = f"header:{header_name}"
        if name not in seen:
            seen.add(name)
            validated.append(name)
    return tuple(validated)


def validate_mcp_secrets(secrets: Mapping[str, str], *, expected_names: Iterable[str] = ()) -> dict[str, str]:
    for expected in expected_names:
        if str(expected).lower().startswith("header:"):
            validate_mcp_header_name(str(expected)[len("header:"):])
    cleaned: dict[str, str] = {}
    for raw_name, raw_value in secrets.items():
        name = str(raw_name or "").strip()
        value = str(raw_value or "")
        if not name:
            raise McpServiceError("credential name cannot be empty")
        if name.lower().startswith("header:"):
            header_name = validate_mcp_header_name(name[len("header:"):])
            name = f"header:{header_name}"
        if "\r" in value or "\n" in value or "\x00" in value:
            raise McpServiceError(f"credential '{name}' cannot contain CR, LF or NUL characters")
        cleaned[name] = value
    return cleaned


def _sent_header_names(secrets: Mapping[str, str], expected_names: Iterable[str] = ()) -> list[str]:
    names: list[str] = []
    for key in (*secrets.keys(), *expected_names):
        if key.startswith("header:"):
            header_name = key[len("header:"):]
            if header_name and not any(existing.lower() == header_name.lower() for existing in names):
                names.append(header_name)
    if ("token" in secrets or "token" in expected_names) and not any(
        existing.lower() == "authorization" for existing in names
    ):
        names.append("Authorization")
    return names


class McpServerNotFound(McpServiceError):
    """Raised when a server_id/slug does not resolve to this user's row."""


class McpConnectionFailed(McpServiceError):
    """Raised by approve()/test() when the connect() callable itself failed.

    A distinct type from the general validation error so a caller (the HTTP
    gateway) can map it to a gateway-style status instead of a plain
    client-input error — the request was well-formed, the remote server was not
    reachable or rejected the credential.
    """


class McpAuthorizationRequired(McpServiceError):
    """The server answered 401 with no credential offered: it signs users in with OAuth."""


def _now() -> datetime:
    return datetime.now(UTC)


def _cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher.from_environment(required=True)


class McpServerService:
    def __init__(self, engine: Engine, *, allow_stdio: bool = True) -> None:
        self.engine = engine
        self.allow_stdio = allow_stdio

    def _require_transport(self, transport: McpTransport | str) -> None:
        if not self.allow_stdio and McpTransport(transport) is McpTransport.STDIO:
            raise CapabilityUnavailable("mcp_stdio")

    # -- reads ------------------------------------------------------------

    def _row(self, user_id: str, server_id: str) -> Mapping[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
            ).mappings().first()
        if row is None:
            raise McpServerNotFound(f"no MCP server '{server_id}' for this user")
        return row

    def _tool_count(self, server_id: str) -> int:
        with self.engine.connect() as connection:
            return len(connection.execute(
                select(mcp_server_tools.c.id).where(mcp_server_tools.c.server_id == server_id)
            ).all())

    def _tools(self, server_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id).order_by(mcp_server_tools.c.name)
            ).mappings().all()
        return [{"name": str(row["name"]), "description": str(row["description"]), "enabled": bool(row["enabled"])} for row in rows]

    def get(self, user_id: str, server_id: str) -> dict[str, Any]:
        row = self._row(user_id, server_id)
        tools = self._tools(server_id)
        return {**public_summary(row, tool_count=len(tools)), "tools": tools}

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mcp_servers).where(mcp_servers.c.user_id == user_id).order_by(mcp_servers.c.created_at)
            ).mappings().all()
        return [public_summary(row, tool_count=self._tool_count(str(row["server_id"]))) for row in rows]

    def active_servers(self, user_id: str) -> list[tuple[McpServerConfig, tuple[McpToolDescriptor, ...], dict[str, str]]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mcp_servers).where(mcp_servers.c.user_id == user_id, mcp_servers.c.state == McpServerState.ACTIVE.value)
            ).mappings().all()
            # Rows written before the instance became a server stay in the
            # database but never spawn: a stdio server is a host process.
            rows = [row for row in rows if self.allow_stdio or row["transport"] != McpTransport.STDIO.value]
            bundles: list[tuple[McpServerConfig, tuple[McpToolDescriptor, ...], dict[str, str]]] = []
            for row in rows:
                tool_rows = connection.execute(
                    select(mcp_server_tools).where(
                        mcp_server_tools.c.server_id == row["server_id"], mcp_server_tools.c.enabled.is_(True)
                    )
                ).mappings().all()
                secrets = self._decrypt_secrets(row["secrets_ciphertext"])
                bundles.append((row_to_config(row), tuple(row_to_tool(item) for item in tool_rows), secrets))
        return bundles

    @staticmethod
    def _decrypt_secrets(ciphertext: str | None) -> dict[str, str]:
        if not ciphertext:
            return {}
        raw = json.loads(_cipher().decrypt(str(ciphertext)))
        return {str(key): str(value) for key, value in raw.items()}

    @staticmethod
    def _replace_tools(connection: Any, server_id: str, tools: tuple[McpToolDescriptor, ...], now: datetime) -> None:
        connection.execute(delete(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id))
        if tools:
            connection.execute(insert(mcp_server_tools), [
                {"server_id": server_id, "name": item.name, "description": item.description,
                 "input_schema": dict(item.input_schema), "enabled": True, "discovered_at": now}
                for item in tools
            ])

    # -- writes -------------------------------------------------------------

    def propose(self, command: Mapping[str, Any]) -> dict[str, Any]:
        rejected = set(command) - _ALLOWED_PROPOSAL_FIELDS
        if rejected:
            raise McpServiceError(
                f"propose does not accept {', '.join(sorted(rejected))}. Only secret_names is accepted; "
                "credential values are supplied at approve time."
            )
        user_id = str(command.get("user_id") or "")
        if not user_id:
            raise McpServiceError("a proposal requires user_id")
        display_name = str(command.get("display_name") or "").strip()
        if not display_name:
            raise McpServiceError("a proposal requires display_name")
        command = dict(command)
        catalog_id = command.get("catalog_id")
        if catalog_id and not command.get("transport"):
            from .catalog import find_catalog_entry
            entry = find_catalog_entry(str(catalog_id))
            if entry is None:
                raise McpServiceError(f"unknown MCP catalog_id '{catalog_id}'")
            command.setdefault("transport", entry.transport.value)
            if entry.command is not None:
                command.setdefault("command", entry.command)
            if entry.args and not command.get("args"):
                command["args"] = list(entry.args)
            if entry.url is not None:
                command.setdefault("url", entry.url)
            if entry.secrets and not command.get("secret_names"):
                command["secret_names"] = [item.name for item in entry.secrets]
        try:
            transport = McpTransport(str(command.get("transport") or ""))
        except ValueError as error:
            raise McpServiceError("transport must be 'stdio' or 'http'") from error
        if transport not in (McpTransport.STDIO, McpTransport.HTTP):
            raise McpServiceError("transport must be 'stdio' or 'http'")  # SSE and WebSocket come only from configuration files
        self._require_transport(transport)
        try:
            slug = slugify(str(command.get("slug") or display_name))
        except ValueError as error:
            raise McpServiceError(str(error)) from error

        raw_secret_names = [str(item) for item in (command.get("secret_names") or ())]
        raw_headers = command.get("headers")
        if raw_headers is not None:
            if isinstance(raw_headers, (str, bytes, Mapping)) or not isinstance(raw_headers, Iterable):
                raise McpServiceError(
                    "propose accepts header names only (e.g. headers=['Authorization']); "
                    "credential values are supplied at approve time."
                )
            for item in raw_headers:
                clean_header = str(item or "").strip()
                if clean_header.lower().startswith("header:"):
                    clean_header = clean_header[len("header:"):].strip()
                if ":" in clean_header or any(ch.isspace() for ch in clean_header):
                    raise McpServiceError(
                        "propose does not accept header values; pass header names only (e.g. 'Authorization')."
                    )
                validate_mcp_header_name(clean_header)
                raw_secret_names.append(f"header:{clean_header}")
        secret_names = validate_mcp_secret_names(raw_secret_names)

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(mcp_servers.c.server_id).where(mcp_servers.c.user_id == user_id, mcp_servers.c.slug == slug)
            ).first()
            if existing is not None:
                raise McpServiceError(f"a server named '{slug}' already exists for this user")

            candidate = McpServerConfig(
                server_id=f"mcp_{uuid4().hex}", user_id=user_id, slug=slug, display_name=display_name,
                transport=transport, command=command.get("command"), args=tuple(command.get("args") or ()),
                url=command.get("url"), secret_names=secret_names,
                catalog_id=command.get("catalog_id"),
                tool_allowlist=tuple(command.get("tool_allowlist")) if command.get("tool_allowlist") is not None else None,
            )
            now = _now()
            connection.execute(insert(mcp_servers).values(
                server_id=candidate.server_id, user_id=candidate.user_id, slug=candidate.slug,
                display_name=candidate.display_name, catalog_id=candidate.catalog_id,
                transport=candidate.transport.value, command=candidate.command, args=list(candidate.args),
                url=candidate.url, secret_names=list(candidate.secret_names), secrets_ciphertext=None,
                tool_allowlist=list(candidate.tool_allowlist) if candidate.tool_allowlist is not None else None,
                state=McpServerState.PENDING_APPROVAL.value, state_reason="", protocol_version="", tools_digest="",
                auth_kind=(McpAuthKind.STATIC if candidate.secret_names else McpAuthKind.NONE).value,
                created_at=now, updated_at=now,
            ))
        return self.get(user_id, candidate.server_id)

    def approve(self, *, user_id: str, server_id: str, secrets: Mapping[str, str], connect: Connector) -> dict[str, Any]:
        row = self._row(user_id, server_id)
        config = row_to_config(row)
        self._require_transport(config.transport)
        cleaned_secrets = validate_mcp_secrets(secrets, expected_names=config.secret_names)
        try:
            protocol_version, tools = connect(config, cleaned_secrets)
        except HttpUnauthorized as error:
            if cleaned_secrets or config.secret_names:
                sent_headers = _sent_header_names(cleaned_secrets, config.secret_names)
                if len(sent_headers) == 1:
                    reason = f"o servidor recusou a credencial informada (401) no cabeçalho {sent_headers[0]}"
                elif len(sent_headers) > 1:
                    reason = f"o servidor recusou a credencial informada (401) nos cabeçalhos {', '.join(sent_headers)}"
                else:
                    reason = "o servidor recusou a credencial informada (401)"
                with self.engine.begin() as connection:
                    connection.execute(
                        update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                        .values(auth_kind=McpAuthKind.STATIC.value, state_reason=reason[:MAX_STATE_REASON_CHARS], updated_at=_now())
                    )
                raise McpConnectionFailed(f"could not connect to '{config.display_name}': {reason}") from error
            with self.engine.begin() as connection:
                connection.execute(
                    update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                    .values(auth_kind=McpAuthKind.OAUTH.value, state_reason="", updated_at=_now())
                )
            raise McpAuthorizationRequired(
                f"'{config.display_name}' asks you to sign in (or configure an Authorization header with a static key)"
            ) from error
        except Exception as error:
            reason = redact_secret_values(str(error), cleaned_secrets.values(), url=config.url)[:MAX_STATE_REASON_CHARS]
            with self.engine.begin() as connection:
                connection.execute(
                    update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                    .values(state_reason=reason, updated_at=_now())
                )
            raise McpConnectionFailed(f"could not connect to '{config.display_name}': {reason}") from error

        merged_secret_names = list(validate_mcp_secret_names((*config.secret_names, *cleaned_secrets.keys())))
        auth_kind = McpAuthKind.STATIC.value if (cleaned_secrets or merged_secret_names) else config.auth_kind.value
        ciphertext = _cipher().encrypt(json.dumps(dict(cleaned_secrets)))
        now = _now()
        with self.engine.begin() as connection:
            self._replace_tools(connection, server_id, tools, now)
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(secret_names=merged_secret_names, secrets_ciphertext=ciphertext, protocol_version=protocol_version,
                       tools_digest=_tools_digest(tools), state=McpServerState.ACTIVE.value,
                       auth_kind=auth_kind, state_reason="", updated_at=now)
            )
        return self.get(user_id, server_id)

    def set_enabled(self, user_id: str, server_id: str, *, enabled: bool) -> dict[str, Any]:
        self._row(user_id, server_id)
        target = McpServerState.ACTIVE if enabled else McpServerState.DISABLED
        with self.engine.begin() as connection:
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(state=target.value, updated_at=_now())
            )
        return self.get(user_id, server_id)

    def set_tool_enabled(self, user_id: str, server_id: str, tool_name: str, *, enabled: bool) -> dict[str, Any]:
        self._row(user_id, server_id)
        with self.engine.begin() as connection:
            connection.execute(
                update(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id, mcp_server_tools.c.name == tool_name)
                .values(enabled=enabled)
            )
        return self.get(user_id, server_id)

    def activate_after_authorization(self, user_id: str, server_id: str, connect: Connector) -> dict[str, Any]:
        """Discover tools with the fresh sign-in and activate; used by the OAuth callback and Reconectar."""
        row = self._row(user_id, server_id)
        config = row_to_config(row)
        self._require_transport(config.transport)
        try:
            protocol_version, tools = connect(config, {})
        except Exception as error:
            reason = f"Login concluído, mas a conexão falhou: {error}"[:MAX_STATE_REASON_CHARS]
            with self.engine.begin() as connection:
                connection.execute(
                    update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                    .values(state_reason=reason, updated_at=_now())
                )
            raise McpConnectionFailed(f"could not connect to '{config.display_name}': {error}") from error
        now = _now()
        with self.engine.begin() as connection:
            self._replace_tools(connection, server_id, tools, now)
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(secrets_ciphertext=None, protocol_version=protocol_version, tools_digest=_tools_digest(tools),
                        state=McpServerState.ACTIVE.value, state_reason="", auth_kind=McpAuthKind.OAUTH.value,
                        updated_at=now)
            )
        return self.get(user_id, server_id)

    def remove(self, user_id: str, server_id: str) -> None:
        self._row(user_id, server_id)
        with self.engine.begin() as connection:
            connection.execute(delete(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id))
            connection.execute(delete(oauth_pending_authorizations).where(oauth_pending_authorizations.c.server_id == server_id))
            connection.execute(delete(mcp_oauth_clients).where(mcp_oauth_clients.c.server_id == server_id))
            connection.execute(delete(oauth_tokens).where(oauth_tokens.c.user_id == user_id, oauth_tokens.c.provider_id == server_id))
            connection.execute(delete(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id))

    def test(self, user_id: str, slug: str, connect: Connector) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(mcp_servers).where(mcp_servers.c.user_id == user_id, mcp_servers.c.slug == slug)
            ).mappings().first()
        if row is None:
            raise McpServerNotFound(f"no MCP server named '{slug}' for this user")
        server_id = str(row["server_id"])
        config = row_to_config(row)
        self._require_transport(config.transport)
        secrets = self._decrypt_secrets(row["secrets_ciphertext"])
        try:
            protocol_version, tools = connect(config, secrets)
        except Exception as error:
            redacted = redact_secret_values(str(error), secrets.values(), url=config.url)[:MAX_STATE_REASON_CHARS]
            return {"connected": False, "protocol_version": "", "tools": [], "error": redacted}

        now = _now()
        with self.engine.begin() as connection:
            self._replace_tools(connection, server_id, tools, now)
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(protocol_version=protocol_version, tools_digest=_tools_digest(tools), updated_at=now)
            )
        return {"connected": True, "protocol_version": protocol_version, "tools": [item.name for item in tools], "error": None}


__all__ = ["McpAuthorizationRequired", "McpConnectionFailed", "McpServerNotFound", "McpServerService", "McpServiceError"]
