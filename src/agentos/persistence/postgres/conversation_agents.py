"""Durable registry of the agents that exist inside one conversation.

The main agent is implicit and always present; rows here are the subagents the
main agent created while working. Persisting them is what lets the overview and
the reopened conversation show the same agent graph the run actually had.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from .schema import conversation_agent_usage, conversation_agents


class ConversationAgentStore:
    def __init__(self, engine: Engine, *, conversation_id: str, user_id: str) -> None:
        self._engine = engine
        self._conversation_id = conversation_id
        self._user_id = user_id

    def agent_id_for(self, name: str) -> str:
        digest = sha256(f"{self._conversation_id}|{name.strip().lower()}".encode()).hexdigest()[:24]
        return f"agent:{self._conversation_id}:{digest}"

    def create(self, name: str, role: str, *, parent_agent_id: str, provider: str | None = None, model_id: str | None = None) -> dict[str, object]:
        now = datetime.now(UTC)
        agent_id = self.agent_id_for(name)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(conversation_agents).where(conversation_agents.c.agent_id == agent_id)
            ).mappings().first()
            if existing is not None:
                return {**dict(existing), "created": False}
            connection.execute(insert(conversation_agents).values(
                agent_id=agent_id, conversation_id=self._conversation_id, user_id=self._user_id,
                parent_agent_id=parent_agent_id, name=name.strip()[:120], role=role.strip()[:512],
                provider=provider.strip()[:32] if provider else None,
                model_id=model_id.strip()[:512] if model_id else None,
                state="idle", created_at=now, updated_at=now,
            ))
        return {
            "agent_id": agent_id, "name": name.strip()[:120], "role": role.strip()[:512],
            "parent_agent_id": parent_agent_id, "provider": provider.strip()[:32] if provider else None,
            "model_id": model_id.strip()[:512] if model_id else None, "state": "idle", "created": True,
        }

    def find(self, name: str) -> dict[str, object] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(conversation_agents).where(conversation_agents.c.agent_id == self.agent_id_for(name))
            ).mappings().first()
        return dict(row) if row else None

    def set_state(self, agent_id: str, state: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(conversation_agents).where(conversation_agents.c.agent_id == agent_id).values(state=state, updated_at=datetime.now(UTC)))

    def set_model(self, agent_id: str, model_id: str) -> None:
        """Persist a safe pre-run model fallback for one child agent."""
        if not model_id.strip():
            raise ValueError("model_id must be non-blank")
        with self._engine.begin() as connection:
            connection.execute(
                update(conversation_agents)
                .where(conversation_agents.c.agent_id == agent_id)
                .values(model_id=model_id.strip()[:512], updated_at=datetime.now(UTC))
            )

    def record_usage(
        self,
        agent_id: str,
        provider: str,
        model_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        values = (input_tokens, output_tokens, total_tokens)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("token usage cannot be negative")
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            current = connection.execute(
                select(conversation_agent_usage).where(
                    conversation_agent_usage.c.conversation_id == self._conversation_id,
                    conversation_agent_usage.c.agent_id == agent_id,
                )
            ).mappings().first()
            reported = any(value is not None for value in values)
            if current is None:
                connection.execute(insert(conversation_agent_usage).values(
                    conversation_id=self._conversation_id, agent_id=agent_id, user_id=self._user_id,
                    provider=provider[:32], model_id=model_id[:512], input_tokens=input_tokens,
                    output_tokens=output_tokens, total_tokens=total_tokens, usage_reported=reported,
                    updated_at=now,
                ))
                return
            def accumulated(key: str, amount: int | None) -> int | None:
                existing = current[key]
                if amount is None:
                    return existing
                return int(existing or 0) + amount
            connection.execute(update(conversation_agent_usage).where(
                conversation_agent_usage.c.conversation_id == self._conversation_id,
                conversation_agent_usage.c.agent_id == agent_id,
            ).values(
                provider=provider[:32], model_id=model_id[:512],
                input_tokens=accumulated("input_tokens", input_tokens),
                output_tokens=accumulated("output_tokens", output_tokens),
                total_tokens=accumulated("total_tokens", total_tokens),
                usage_reported=bool(current["usage_reported"]) or reported, updated_at=now,
            ))

    def usage_by_agent(self) -> dict[str, dict[str, object]]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(conversation_agent_usage).where(
                conversation_agent_usage.c.conversation_id == self._conversation_id,
                conversation_agent_usage.c.user_id == self._user_id,
            )).mappings().all()
        return {
            str(row["agent_id"]): {
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"], "usage_reported": bool(row["usage_reported"]),
            }
            for row in rows
        }

    def list(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(conversation_agents)
                .where(conversation_agents.c.conversation_id == self._conversation_id)
                .order_by(conversation_agents.c.created_at)
            ).mappings().all()
        return [
            {
                "agent_id": row["agent_id"], "name": row["name"], "role": row["role"],
                "parent_agent_id": row["parent_agent_id"], "provider": row["provider"],
                "model_id": row["model_id"], "state": row["state"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]


__all__ = ["ConversationAgentStore"]
