"""A person's own subagents, made in the interface (``user_agents``).

They join the agents read from files in each turn (origin ``orin``): above the person's own files, below a
repository's and a managed policy's. Nothing here runs an agent; the session bounds what one may do exactly as it
bounds an agent file.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from agentos.agentic.agent_definitions import AgentDefinition

from .schema import provider_configurations, provider_model_catalog, user_agents

MAX_USER_AGENTS = 100
ORIGIN = "orin"
FIELDS = (
    "description", "prompt", "provider", "models", "tools", "disallowed_tools", "effort", "permission_mode",
    "max_turns", "enabled",
)


class UserAgentExists(ValueError):
    """There is already an agent with this name for this person."""


class UserAgentLimit(ValueError):
    """This person already has the maximum number of agents."""


def enabled_providers(connection: Connection, user_id: str) -> set[str]:
    """The providers this person has enabled (Antigravity only once its risk was acknowledged)."""
    rows = connection.execute(
        select(provider_configurations.c.provider, provider_configurations.c.risk_acknowledged_at).where(
            provider_configurations.c.user_id == user_id,
            provider_configurations.c.enabled.is_(True),
        )
    ).all()
    found: set[str] = set()
    for row in rows:
        name = str(row.provider or "")
        if not name or (name == "google-antigravity" and row.risk_acknowledged_at is None):
            continue
        found.add(name)
    return found


def catalog_models(connection: Connection, user_id: str, provider: str, model_ids: Sequence[str]) -> set[str]:
    """Which of ``model_ids`` are in this person's catalog of ``provider``."""
    if not model_ids:
        return set()
    rows = connection.execute(
        select(provider_model_catalog.c.model_id).where(
            provider_model_catalog.c.user_id == user_id,
            provider_model_catalog.c.provider == provider,
            provider_model_catalog.c.model_id.in_(list(model_ids)),
        )
    ).all()
    return {str(row.model_id) for row in rows}


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def public(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        "name": str(row["name"]),
        "description": str(row["description"]),
        "prompt": str(row["prompt"]),
        "provider": row["provider"] or None,
        "models": [str(item) for item in (row["models_json"] or [])],
        "tools": None if row["tools_json"] is None else [str(item) for item in row["tools_json"]],
        "disallowed_tools": [str(item) for item in (row["disallowed_tools_json"] or [])],
        "effort": row["effort"] or None,
        "permission_mode": row["permission_mode"] or None,
        "max_turns": row["max_turns"],
        "enabled": bool(row["enabled"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def definition_of(agent: Mapping[str, Any]) -> AgentDefinition:
    """The definition a turn reads from a saved agent (``public`` shape)."""
    models = tuple(str(item) for item in agent.get("models") or ())
    tools = agent.get("tools")
    digest = hashlib.sha256(json.dumps(
        {key: agent.get(key) for key in ("name", *FIELDS)}, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()
    return AgentDefinition(
        name=str(agent["name"]),
        description=str(agent["description"]),
        prompt=str(agent["prompt"]),
        origin=ORIGIN,
        tools=None if tools is None else tuple(str(item) for item in tools),
        disallowed_tools=tuple(str(item) for item in agent.get("disallowed_tools") or ()),
        model=models[0] if models else None,
        provider=agent.get("provider") or None,
        models=models,
        permission_mode=agent.get("permission_mode") or None,
        max_turns=agent.get("max_turns"),
        effort=agent.get("effort") or None,
        digest=digest,
    )


def _columns(values: Mapping[str, Any]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key in FIELDS:
        if key not in values:
            continue
        value = values[key]
        if key == "models":
            out["models_json"] = list(value or [])
        elif key == "tools":
            out["tools_json"] = None if value is None else list(value)
        elif key == "disallowed_tools":
            out["disallowed_tools_json"] = list(value or [])
        elif key == "enabled":
            out["enabled"] = bool(value)
        else:
            out[key] = value
    return out


class UserAgentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def list(self, user_id: str) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(user_agents).where(user_agents.c.user_id == user_id).order_by(user_agents.c.name)
            ).mappings().all()
        return [public(row) for row in rows]

    def get(self, user_id: str, name: str) -> dict[str, object] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(user_agents).where(user_agents.c.user_id == user_id, user_agents.c.name == name)
            ).mappings().first()
        return public(row) if row else None

    def create(self, user_id: str, name: str, values: Mapping[str, Any]) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._lock:
            try:
                with self._engine.begin() as connection:
                    count = connection.execute(
                        select(func.count()).select_from(user_agents).where(user_agents.c.user_id == user_id)
                    ).scalar_one()
                    if count >= MAX_USER_AGENTS:
                        raise UserAgentLimit(f"at most {MAX_USER_AGENTS} agents")
                    connection.execute(insert(user_agents).values(
                        user_id=user_id, name=name, created_at=now, updated_at=now,
                        **{"models_json": [], "disallowed_tools_json": [], "enabled": True, **_columns(values)},
                    ))
            except IntegrityError as error:
                raise UserAgentExists(name) from error
        created = self.get(user_id, name)
        assert created is not None
        return created

    def update(self, user_id: str, name: str, values: Mapping[str, Any]) -> dict[str, object] | None:
        with self._lock, self._engine.begin() as connection:
            result = connection.execute(
                update(user_agents)
                .where(user_agents.c.user_id == user_id, user_agents.c.name == name)
                .values(updated_at=datetime.now(UTC), **_columns(values))
            )
        return self.get(user_id, name) if result.rowcount else None

    def delete(self, user_id: str, name: str) -> bool:
        with self._lock, self._engine.begin() as connection:
            result = connection.execute(
                delete(user_agents).where(user_agents.c.user_id == user_id, user_agents.c.name == name)
            )
        return bool(result.rowcount)

    def definitions(self, user_id: str) -> list[AgentDefinition]:
        """The person's enabled agents, as a turn reads them."""
        return [definition_of(agent) for agent in self.list(user_id) if agent["enabled"]]


__all__ = [
    "MAX_USER_AGENTS", "ORIGIN", "UserAgentExists", "UserAgentLimit", "UserAgentStore", "catalog_models",
    "definition_of", "enabled_providers", "public",
]
