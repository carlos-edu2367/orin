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
from datetime import UTC, datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.mcp import public_summary, row_to_config, row_to_tool
from agentos.persistence.postgres.schema import mcp_server_tools, mcp_servers
from agentos.persistence.provider_secrets import ProviderSecretCipher

from .models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport, slugify, tools_digest as _tools_digest

MAX_STATE_REASON_CHARS = 512
_ALLOWED_PROPOSAL_FIELDS = frozenset({
    "user_id", "slug", "display_name", "transport", "command", "args", "url",
    "secret_names", "catalog_id", "tool_allowlist",
})

Connector = Callable[[McpServerConfig, Mapping[str, str]], tuple[str, tuple[McpToolDescriptor, ...]]]


class McpServiceError(ValueError):
    """Raised for any request the MCP server library will not act on."""


class McpServerNotFound(McpServiceError):
    """Raised when a server_id/slug does not resolve to this user's row."""


class McpConnectionFailed(McpServiceError):
    """Raised by approve()/test() when the connect() callable itself failed.

    A distinct type from the general validation error so a caller (the HTTP
    gateway) can map it to a gateway-style status instead of a plain
    client-input error — the request was well-formed, the remote server was not
    reachable or rejected the credential.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher.from_environment(required=True)


class McpServerService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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
        try:
            transport = McpTransport(str(command.get("transport") or ""))
        except ValueError as error:
            raise McpServiceError("transport must be 'stdio' or 'http'") from error
        try:
            slug = slugify(str(command.get("slug") or display_name))
        except ValueError as error:
            raise McpServiceError(str(error)) from error

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(mcp_servers.c.server_id).where(mcp_servers.c.user_id == user_id, mcp_servers.c.slug == slug)
            ).first()
            if existing is not None:
                raise McpServiceError(f"a server named '{slug}' already exists for this user")

            candidate = McpServerConfig(
                server_id=f"mcp_{uuid4().hex}", user_id=user_id, slug=slug, display_name=display_name,
                transport=transport, command=command.get("command"), args=tuple(command.get("args") or ()),
                url=command.get("url"), secret_names=tuple(str(item) for item in command.get("secret_names") or ()),
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
                created_at=now, updated_at=now,
            ))
        return self.get(user_id, candidate.server_id)

    def approve(self, *, user_id: str, server_id: str, secrets: Mapping[str, str], connect: Connector) -> dict[str, Any]:
        row = self._row(user_id, server_id)
        config = row_to_config(row)
        try:
            protocol_version, tools = connect(config, secrets)
        except Exception as error:
            reason = str(error)[:MAX_STATE_REASON_CHARS]
            with self.engine.begin() as connection:
                connection.execute(
                    update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                    .values(state_reason=reason, updated_at=_now())
                )
            raise McpConnectionFailed(f"could not connect to '{config.display_name}': {reason}") from error

        ciphertext = _cipher().encrypt(json.dumps(dict(secrets)))
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id))
            if tools:
                connection.execute(insert(mcp_server_tools), [
                    {"server_id": server_id, "name": item.name, "description": item.description,
                     "input_schema": dict(item.input_schema), "enabled": True, "discovered_at": now}
                    for item in tools
                ])
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(secrets_ciphertext=ciphertext, protocol_version=protocol_version,
                       tools_digest=_tools_digest(tools), state=McpServerState.ACTIVE.value,
                       state_reason="", updated_at=now)
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

    def remove(self, user_id: str, server_id: str) -> None:
        self._row(user_id, server_id)
        with self.engine.begin() as connection:
            connection.execute(delete(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id))
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
        secrets = self._decrypt_secrets(row["secrets_ciphertext"])
        try:
            protocol_version, tools = connect(config, secrets)
        except Exception as error:
            return {"connected": False, "protocol_version": "", "tools": [], "error": str(error)[:MAX_STATE_REASON_CHARS]}

        now = _now()
        with self.engine.begin() as connection:
            connection.execute(delete(mcp_server_tools).where(mcp_server_tools.c.server_id == server_id))
            if tools:
                connection.execute(insert(mcp_server_tools), [
                    {"server_id": server_id, "name": item.name, "description": item.description,
                     "input_schema": dict(item.input_schema), "enabled": True, "discovered_at": now}
                    for item in tools
                ])
            connection.execute(
                update(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
                .values(protocol_version=protocol_version, tools_digest=_tools_digest(tools), updated_at=now)
            )
        return {"connected": True, "protocol_version": protocol_version, "tools": [item.name for item in tools], "error": None}


__all__ = ["McpConnectionFailed", "McpServerNotFound", "McpServerService", "McpServiceError"]
