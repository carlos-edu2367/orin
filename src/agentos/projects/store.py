"""Durable project ownership and project-chat queries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import agent_memories, conversations, projects


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 120:
        raise ValueError("project name must be a bounded non-blank string")
    return normalized


def _description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if len(normalized) > 2000:
        raise ValueError("project description is too long")
    return normalized or None


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    user_id: str
    workspace_id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProjectSidebarChat:
    conversation_id: str
    title: str
    state: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectSidebarItem:
    project_id: str
    name: str
    description: str | None
    chats: tuple[ProjectSidebarChat, ...]


class PostgresProjectStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, *, user_id: str, name: str, description: str | None) -> ProjectRecord:
        if not user_id.strip():
            raise ValueError("user_id must be non-blank")
        now = datetime.now(UTC)
        project_id = _id("project")
        record = ProjectRecord(project_id, user_id, f"workspace:{project_id}", _name(name), _description(description), now, now, None)
        with self._engine.begin() as connection:
            connection.execute(insert(projects).values(**{field: getattr(record, field) for field in ProjectRecord.__dataclass_fields__}))
        return record

    def get(self, project_id: str, user_id: str, *, include_archived: bool = False) -> ProjectRecord | None:
        statement = select(projects).where(projects.c.project_id == project_id, projects.c.user_id == user_id)
        if not include_archived:
            statement = statement.where(projects.c.archived_at.is_(None))
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return ProjectRecord(**dict(row)) if row else None

    def list_active(self, user_id: str) -> tuple[ProjectRecord, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(projects).where(projects.c.user_id == user_id, projects.c.archived_at.is_(None)).order_by(projects.c.updated_at.desc())
            ).mappings().all()
        return tuple(ProjectRecord(**dict(row)) for row in rows)

    def update(self, project_id: str, user_id: str, *, name: str | None = None, description: str | None = None) -> ProjectRecord | None:
        current = self.get(project_id, user_id)
        if current is None:
            return None
        values = {"updated_at": datetime.now(UTC)}
        if name is not None:
            values["name"] = _name(name)
        if description is not None:
            values["description"] = _description(description)
        with self._engine.begin() as connection:
            connection.execute(update(projects).where(projects.c.project_id == project_id, projects.c.user_id == user_id).values(**values))
        return self.get(project_id, user_id)

    def archive(self, project_id: str, user_id: str) -> bool:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            result = connection.execute(update(projects).where(projects.c.project_id == project_id, projects.c.user_id == user_id, projects.c.archived_at.is_(None)).values(archived_at=now, updated_at=now))
        return bool(result.rowcount)

    def restore(self, project_id: str, user_id: str) -> bool:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            result = connection.execute(update(projects).where(projects.c.project_id == project_id, projects.c.user_id == user_id, projects.c.archived_at.is_not(None)).values(archived_at=None, updated_at=now))
        return bool(result.rowcount)

    def sidebar(self, user_id: str) -> tuple[ProjectSidebarItem, ...]:
        statement = select(projects, conversations.c.conversation_id, conversations.c.title, conversations.c.state.label("conversation_state"), conversations.c.updated_at.label("conversation_updated_at")).outerjoin(
            conversations, conversations.c.project_id == projects.c.project_id
        ).where(projects.c.user_id == user_id, projects.c.archived_at.is_(None)).order_by(projects.c.updated_at.desc(), conversations.c.updated_at.desc())
        grouped: dict[str, list[ProjectSidebarChat]] = {}
        records: dict[str, ProjectRecord] = {}
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        for row in rows:
            record = ProjectRecord(**{key: row[key] for key in ProjectRecord.__dataclass_fields__})
            records[record.project_id] = record
            if row["conversation_id"]:
                grouped.setdefault(record.project_id, []).append(ProjectSidebarChat(str(row["conversation_id"]), str(row["title"]), str(row["conversation_state"]), row["conversation_updated_at"]))
        return tuple(ProjectSidebarItem(record.project_id, record.name, record.description, tuple(grouped.get(record.project_id, ()))) for record in records.values())

    def workspace_for_conversation(self, conversation_id: str, user_id: str) -> str | None:
        statement = select(projects.c.workspace_id).join(conversations, conversations.c.project_id == projects.c.project_id).where(
            conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id, projects.c.user_id == user_id, projects.c.archived_at.is_(None)
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).scalar_one_or_none()

    def list_memories(self, project_id: str | None, user_id: str, *, scope: str | None = None, query: str = "", cursor: str | None = None, limit: int = 200) -> list[dict[str, object]] | dict[str, object]:
        # The legacy project route receives only two arguments and preserves its
        # compact list response. Managed Settings requests use the scoped path.
        managed = scope is not None
        actual_scope = scope or "project"
        if actual_scope not in {"user", "project"}:
            raise ValueError("unsupported memory scope")
        if actual_scope == "project" and (not project_id or self.get(project_id, user_id) is None):
            return {"items": [], "next_cursor": None} if managed else []
        predicates = [agent_memories.c.user_id == user_id, agent_memories.c.scope_type == actual_scope]
        predicates.append(agent_memories.c.project_id == project_id if actual_scope == "project" else agent_memories.c.project_id.is_(None))
        normalized = " ".join(query.split())
        if normalized:
            predicates.append(or_(agent_memories.c.fact.ilike(f"%{normalized}%"), agent_memories.c.tags.cast(__import__("sqlalchemy").String).ilike(f"%{normalized}%")))
        with self._engine.connect() as connection:
            rows = connection.execute(select(agent_memories).where(
                *predicates
            ).order_by(agent_memories.c.updated_at.desc()).limit(min(max(limit, 1), 100) + 1)).mappings().all()
        items = [{"memory_id": row["memory_id"], "fact": row["fact"], "tags": list(row["tags"] or []), "scope": actual_scope, "project_id": row["project_id"], "conversation_id": row["conversation_id"], "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()} for row in rows]
        if not managed:
            return items
        return {"items": items[:limit], "next_cursor": items[limit]["memory_id"] if len(items) > limit else None}

    def delete_memory(self, project_id: str | None, user_id: str, memory_id: str, *, scope: str = "project") -> bool:
        if scope not in {"user", "project"}:
            raise ValueError("unsupported memory scope")
        if scope == "project" and (not project_id or self.get(project_id, user_id) is None):
            return False
        with self._engine.begin() as connection:
            result = connection.execute(delete(agent_memories).where(
                agent_memories.c.memory_id == memory_id, agent_memories.c.user_id == user_id,
                agent_memories.c.scope_type == scope,
                agent_memories.c.project_id == project_id if scope == "project" else agent_memories.c.project_id.is_(None),
            ))
        return bool(result.rowcount)

    def update_memory(self, project_id: str | None, user_id: str, memory_id: str, fact: str, *, scope: str = "project") -> dict[str, object] | None:
        """Let the person rewrite a fact the agent got slightly wrong.

        Editing keeps the row's provenance: a memory the agent learned stays
        marked as such even after a human fixed its wording. What changes is
        the text and, because a person vouched for it now, its confidence.
        """
        if scope not in {"user", "project"}:
            raise ValueError("unsupported memory scope")
        if scope == "project" and (not project_id or self.get(project_id, user_id) is None):
            return None
        normalized = " ".join(str(fact).split())[:2000]
        if not normalized:
            raise ValueError("fact must be a non-blank string")
        predicates = [
            agent_memories.c.memory_id == memory_id,
            agent_memories.c.user_id == user_id,
            agent_memories.c.scope_type == scope,
            agent_memories.c.project_id == project_id if scope == "project" else agent_memories.c.project_id.is_(None),
        ]
        with self._engine.begin() as connection:
            result = connection.execute(update(agent_memories).where(*predicates).values(
                fact=normalized, confidence=1.0, updated_at=datetime.now(UTC),
            ))
            if not result.rowcount:
                return None
            row = connection.execute(select(agent_memories).where(agent_memories.c.memory_id == memory_id)).mappings().one()
        return {
            "memory_id": row["memory_id"], "fact": row["fact"], "tags": list(row["tags"] or []),
            "scope": scope, "project_id": row["project_id"], "conversation_id": row["conversation_id"],
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
        }


__all__ = ["PostgresProjectStore", "ProjectRecord", "ProjectSidebarItem", "ProjectSidebarChat"]
