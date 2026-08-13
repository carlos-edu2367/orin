"""PostgreSQL-backed Skill library with immutable version rows."""
from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.persistence.postgres.schema import agent_skills, execution_skills, skill_versions, skills
from agentos.skills.builtins import load_builtin_skills
from agentos.skills.models import Skill, SkillDependencies, SkillScope, SkillSource
from agentos.skills.registry import SkillNotFound, SkillRegistry
from agentos.skills.service import _id


def _now() -> datetime:
    return datetime.now(UTC)


class PostgresSkillLibraryService:
    """Hydrates the same registry contract used by the runtime from SQL rows."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._seed_builtins()

    def _seed_builtins(self) -> None:
        for skill in load_builtin_skills(include_instructions=True):
            try:
                self._insert(skill, user_id=None)
            except IntegrityError:
                continue

    @staticmethod
    def _metadata(skill: Skill) -> dict[str, object]:
        return {"tags": list(skill.tags), "capabilities": list(skill.capabilities), "when_to_use": list(skill.when_to_use),
                "when_not_to_use": list(skill.when_not_to_use), "dependencies": list(skill.dependencies.skills),
                "dependency_tools": list(skill.dependencies.tools), "requires_tools": list(skill.requires_tools), "author": skill.author}

    def _insert(self, skill: Skill, *, user_id: str | None) -> None:
        now = _now()
        with self.engine.begin() as connection:
            identity = (skills.c.skill_id == skill.id) & (skills.c.scope == skill.scope.value)
            identity = identity & (skills.c.user_id.is_(None) if user_id is None else skills.c.user_id == user_id)
            record_id = connection.execute(select(skills.c.id).where(identity)).scalar_one_or_none()
            if record_id is None:
                record_id = connection.execute(insert(skills).values(skill_id=skill.id, user_id=user_id, workspace_id=None, scope=skill.scope.value, source=skill.source.value, enabled=skill.enabled, created_at=now, updated_at=now)).inserted_primary_key[0]
            connection.execute(insert(skill_versions).values(skill_record_id=record_id, version=skill.version, name=skill.name, description=skill.description, metadata=self._metadata(skill), instructions=skill.instructions or "", content_digest=skill.digest or "", published_at=now))

    def _skills_for(self, user_id: str) -> tuple[Skill, ...]:
        statement = select(skills, skill_versions).join(skill_versions, skills.c.id == skill_versions.c.skill_record_id).where((skills.c.user_id.is_(None)) | (skills.c.user_id == user_id))
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        result: list[Skill] = []
        for row in rows:
            metadata = dict(row["metadata"] or {})
            result.append(Skill(id=str(row["skill_id"]), name=str(row["name"]), version=str(row["version"]), description=str(row["description"]), instructions=str(row["instructions"]), tags=tuple(metadata.get("tags") or ()), capabilities=tuple(metadata.get("capabilities") or ()), when_to_use=tuple(metadata.get("when_to_use") or ()), when_not_to_use=tuple(metadata.get("when_not_to_use") or ()), dependencies=SkillDependencies(tuple(metadata.get("dependencies") or ()), tuple(metadata.get("dependency_tools") or ())), requires_tools=tuple(metadata.get("requires_tools") or ()), scope=SkillScope(str(row["scope"])), source=SkillSource(str(row["source"])), enabled=bool(row["enabled"]), author=metadata.get("author")))
        return tuple(result)

    def registry_for(self, user_id: str, agent_id: str | None = None) -> SkillRegistry:
        registry = SkillRegistry(self._skills_for(user_id))
        if agent_id is None:
            return registry
        current = self.agent_skills({"user_id": user_id, "agent_id": agent_id})
        if current["mode"] == "auto":
            return registry
        wanted: set[str] = set()
        def include(skill_id: str) -> None:
            if skill_id in wanted:
                return
            wanted.add(skill_id)
            for dependency in registry.resolve(skill_id).dependencies.skills:
                include(dependency)
        for item in current["items"]:
            include(str(item["id"]))
        return SkillRegistry(tuple(registry.resolve(skill_id) for skill_id in wanted))

    @staticmethod
    def _summary(metadata) -> dict[str, object]:
        return {"id": metadata.id, "name": metadata.name, "description": metadata.description, "version": metadata.version, "tags": list(metadata.tags), "source": metadata.source.value, "available": metadata.available}

    def list(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, text = str(query["user_id"]), str(query.get("query") or "")
        registry = self.registry_for(user_id)
        available = ("read_file", "write_file", "list_files", "run_command", "fetch_url")
        items = registry.retrieve(__import__("agentos.skills.retrieval", fromlist=["RetrievalQuery"]).RetrievalQuery(text=text, available_tools=available, limit=100)).items if text else registry.list(limit=100, available_tools=available)
        source = str(query.get("source") or "")
        metadata = [item.metadata if hasattr(item, "metadata") else item for item in items]
        if source:
            metadata = [item for item in metadata if item.source.value == source]
        return {"items": [self._summary(item) for item in metadata[:max(1, min(int(query.get("limit") or 20), 100))]], "next_cursor": None}

    def get(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(query["user_id"]), str(query["skill_id"])
        registry = self.registry_for(user_id)
        available = ("read_file", "write_file", "list_files", "run_command", "fetch_url")
        loaded, metadata = registry.load(skill_id, available_tools=available), registry.metadata(skill_id, available_tools=available)
        versions = [skill.version for skill in self._skills_for(user_id) if skill.id == skill_id]
        return {**self._summary(metadata), "instructions": loaded.instructions, "dependencies": [item.ref.id for item in loaded.dependencies], "requires_tools": list(loaded.skill.required_tools), "versions": sorted(set(versions), reverse=True)}

    def create(self, command: Mapping[str, object]) -> dict[str, object]:
        skill = self._custom_skill(command, skill_id=_id(str(command["name"])))
        try:
            self._insert(skill, user_id=str(command["user_id"]))
        except IntegrityError as error:
            raise ValueError("a Skill with this id and version already exists") from error
        return self._summary(skill.metadata)

    def update(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(command["user_id"]), str(command["skill_id"])
        prior = self.registry_for(user_id).resolve(skill_id, scope=SkillScope.USER)
        core = prior.version.split("-", 1)[0].split("+", 1)[0]
        version = str(command.get("version") or f"{core.rsplit('.', 1)[0]}.{int(core.rsplit('.', 1)[1]) + 1}")
        values: dict[str, object] = {
            "name": command.get("name", prior.name), "version": version,
            "description": command.get("description", prior.description),
            "instructions": command.get("instructions", prior.instructions),
            "tags": command.get("tags", prior.tags), "capabilities": command.get("capabilities", prior.capabilities),
            "when_to_use": command.get("when_to_use", prior.when_to_use),
            "when_not_to_use": command.get("when_not_to_use", prior.when_not_to_use),
            "requires_tools": command.get("requires_tools", prior.requires_tools),
            "dependencies": command.get("dependencies", {"skills": prior.dependencies.skills, "tools": prior.dependencies.tools}),
        }
        skill = self._custom_skill(values, skill_id=prior.id)
        self._insert(skill, user_id=user_id)
        return self.get({"user_id": user_id, "skill_id": skill_id})

    @staticmethod
    def _custom_skill(command: Mapping[str, object], *, skill_id: str) -> Skill:
        dependencies = command.get("dependencies") or {}
        if not isinstance(dependencies, Mapping):
            raise ValueError("dependencies must be an object")
        return Skill(
            id=skill_id, name=str(command["name"]), version=str(command.get("version") or "1.0.0"),
            description=str(command["description"]), instructions=str(command["instructions"]),
            tags=tuple(str(value) for value in command.get("tags") or ()),
            capabilities=tuple(str(value) for value in command.get("capabilities") or ()),
            when_to_use=tuple(str(value) for value in command.get("when_to_use") or ()),
            when_not_to_use=tuple(str(value) for value in command.get("when_not_to_use") or ()),
            dependencies=SkillDependencies(
                tuple(str(value) for value in dependencies.get("skills") or ()),
                tuple(str(value) for value in dependencies.get("tools") or ()),
            ),
            requires_tools=tuple(str(value) for value in command.get("requires_tools") or ()),
            scope=SkillScope.USER, source=SkillSource.CUSTOM,
        )

    def agent_skills(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, agent_id = str(query["user_id"]), str(query["agent_id"])
        statement = select(agent_skills.c.mode, skills, skill_versions).join(skill_versions, agent_skills.c.skill_version_id == skill_versions.c.id).join(skills, skill_versions.c.skill_record_id == skills.c.id).where((agent_skills.c.user_id == user_id) & (agent_skills.c.agent_id == agent_id))
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        if not rows:
            return {"mode": "auto", "items": []}
        items = [{"id": row["skill_id"], "name": row["name"], "description": row["description"], "version": row["version"], "tags": list((row["metadata"] or {}).get("tags") or ()), "source": row["source"], "available": bool(row["enabled"])} for row in rows]
        return {"mode": str(rows[0]["mode"]), "items": items}

    def set_agent_skills(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id, agent_id, mode = str(command["user_id"]), str(command["agent_id"]), str(command["mode"])
        if mode not in {"auto", "pinned"}:
            raise ValueError("agent skill mode must be auto or pinned")
        ids = tuple(dict.fromkeys(str(item) for item in command.get("skill_ids") or ()))
        if mode == "pinned" and not ids:
            raise ValueError("a pinned agent needs at least one skill")
        registry = self.registry_for(user_id)
        resolved = [registry.resolve(skill_id) for skill_id in ids] if mode == "pinned" else []
        with self.engine.begin() as connection:
            connection.execute(delete(agent_skills).where((agent_skills.c.user_id == user_id) & (agent_skills.c.agent_id == agent_id)))
            for skill in resolved:
                identity = (skills.c.skill_id == skill.id) & (skills.c.scope == skill.scope.value)
                identity = identity & (skills.c.user_id.is_(None) if skill.scope is SkillScope.SYSTEM else skills.c.user_id == user_id)
                version_id = connection.execute(select(skill_versions.c.id).join(skills, skills.c.id == skill_versions.c.skill_record_id).where(identity & (skill_versions.c.version == skill.version))).scalar_one()
                connection.execute(insert(agent_skills).values(user_id=user_id, agent_id=agent_id, skill_version_id=version_id, mode=mode, created_at=_now()))
        return self.agent_skills({"user_id": user_id, "agent_id": agent_id})

    def agents_for_skill(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(query["user_id"]), str(query["skill_id"])
        statement = select(agent_skills.c.agent_id, agent_skills.c.mode).join(skill_versions, agent_skills.c.skill_version_id == skill_versions.c.id).join(skills, skill_versions.c.skill_record_id == skills.c.id).where((agent_skills.c.user_id == user_id) & (skills.c.skill_id == skill_id))
        with self.engine.connect() as connection:
            items = [{"agent_id": str(row["agent_id"]), "mode": str(row["mode"])} for row in connection.execute(statement).mappings()]
        return {"items": items}

    def record_load(self, *, user_id: str, execution_id: str, agent_id: str, loaded) -> None:
        """Capture the exact body consumed by an execution, not a live reference."""
        with self.engine.begin() as connection:
            condition = (execution_skills.c.execution_id == execution_id) & (execution_skills.c.skill_id == loaded.ref.id) & (execution_skills.c.version == loaded.ref.version)
            existing = connection.execute(select(execution_skills.c.id, execution_skills.c.load_count).where(condition)).mappings().first()
            if existing is not None:
                connection.execute(update(execution_skills).where(execution_skills.c.id == existing["id"]).values(load_count=int(existing["load_count"]) + 1))
                return
            connection.execute(insert(execution_skills).values(user_id=user_id, execution_id=execution_id, agent_id=agent_id, skill_id=loaded.ref.id, version=loaded.ref.version, content_digest=loaded.digest, content_snapshot=loaded.instructions, loaded_at=_now(), load_count=1))

    def loads_for_execution(self, *, user_id: str, execution_id: str) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(execution_skills).where((execution_skills.c.user_id == user_id) & (execution_skills.c.execution_id == execution_id)).order_by(execution_skills.c.loaded_at)).mappings()]


__all__ = ["PostgresSkillLibraryService"]
