"""Application-facing Skill library backed by the central registry.

The service deliberately returns compact metadata for collection reads and only
returns the Markdown body for an explicit detail read.  The in-process store
is the local bootstrap implementation; durable adapters can hydrate the same
``Skill`` values without leaking registry access to HTTP handlers.
"""
from __future__ import annotations

import re
from typing import Mapping

from .builtins import load_builtin_skills
from .models import Skill, SkillScope, SkillSource
from .registry import SkillNotFound, SkillRegistry
from .retrieval import RetrievalQuery


def _id(name: str) -> str:
    value = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")
    if not value:
        raise ValueError("skill name does not produce an identifier")
    return value


class SkillLibraryService:
    def __init__(self, *, builtins: tuple[Skill, ...] | None = None) -> None:
        self._builtins = builtins if builtins is not None else load_builtin_skills()
        self._custom: dict[str, list[Skill]] = {}
        self._agent_preferences: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}

    def _registry(self, user_id: str, agent_id: str | None = None) -> SkillRegistry:
        registry = SkillRegistry((*self._builtins, *self._custom.get(user_id, ())))
        preference = None if agent_id is None else self._agent_preferences.get((user_id, agent_id))
        if preference is None or preference[0] == "auto":
            return registry
        wanted: set[str] = set()
        def include(skill_id: str) -> None:
            if skill_id in wanted:
                return
            wanted.add(skill_id)
            for dependency in registry.resolve(skill_id).dependencies.skills:
                include(dependency)
        for skill_id in preference[1]:
            include(skill_id)
        return SkillRegistry(tuple(registry.resolve(skill_id) for skill_id in wanted))

    @staticmethod
    def _summary(metadata) -> dict[str, object]:
        return {"id": metadata.id, "name": metadata.name, "description": metadata.description,
                "version": metadata.version, "tags": list(metadata.tags), "source": metadata.source.value,
                "available": metadata.available}

    def list(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id = str(query["user_id"])
        registry = self._registry(user_id)
        text = str(query.get("query") or "")
        source = str(query.get("source") or "").lower()
        limit = max(1, min(int(query.get("limit") or 20), 100))
        available = ("read_file", "write_file", "list_files", "run_command", "fetch_url")
        if text:
            items = [item.metadata for item in registry.search(RetrievalQuery(text=text, available_tools=available, limit=100)).items]
        else:
            items = list(registry.list(limit=100, available_tools=available))
        if source:
            items = [item for item in items if item.source.value == source]
        return {"items": [self._summary(item) for item in items[:limit]], "next_cursor": None}

    def get(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(query["user_id"]), str(query["skill_id"])
        registry = self._registry(user_id)
        loaded = registry.load(skill_id, available_tools=("read_file", "write_file", "list_files", "run_command", "fetch_url"))
        meta = registry.metadata(skill_id, available_tools=("read_file", "write_file", "list_files", "run_command", "fetch_url"))
        return {**self._summary(meta), "instructions": loaded.instructions,
                "dependencies": [item.ref.id for item in loaded.dependencies],
                "requires_tools": list(loaded.skill.required_tools),
                "versions": [skill.version for skill in self._all_versions(user_id, skill_id)]}

    def create(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id = str(command["user_id"])
        candidate = Skill(id=_id(str(command["name"])), name=str(command["name"]), version=str(command.get("version") or "1.0.0"), description=str(command["description"]), instructions=str(command["instructions"]), tags=tuple(str(tag) for tag in command.get("tags") or ()), scope=SkillScope.USER, source=SkillSource.CUSTOM)
        registry = self._registry(user_id)
        try:
            registry.resolve(candidate.id, version=candidate.version, scope=SkillScope.USER)
        except SkillNotFound:
            self._custom.setdefault(user_id, []).append(candidate)
            return self._summary(candidate.metadata)
        raise ValueError("a Skill with this id and version already exists")

    def update(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(command["user_id"]), str(command["skill_id"])
        prior = self._registry(user_id).resolve(skill_id, scope=SkillScope.USER)
        version = str(command.get("version") or self._next_patch(prior.version))
        candidate = Skill(id=prior.id, name=str(command.get("name") or prior.name), version=version,
            description=str(command.get("description") or prior.description), instructions=str(command.get("instructions") or prior.instructions or ""),
            tags=tuple(str(tag) for tag in command.get("tags") or prior.tags), scope=SkillScope.USER, source=SkillSource.CUSTOM,
            dependencies=prior.dependencies, requires_tools=prior.requires_tools)
        self._custom.setdefault(user_id, []).append(candidate)
        return self.get({"user_id": user_id, "skill_id": skill_id})

    def agent_skills(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, agent_id = str(query["user_id"]), str(query["agent_id"])
        mode, ids = self._agent_preferences.get((user_id, agent_id), ("auto", ()))
        registry = self._registry(user_id)
        return {"mode": mode, "items": [self._summary(registry.metadata(skill_id)) for skill_id in ids]}

    def set_agent_skills(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id, agent_id, mode = str(command["user_id"]), str(command["agent_id"]), str(command["mode"])
        if mode not in {"auto", "pinned"}:
            raise ValueError("agent skill mode must be auto or pinned")
        ids = tuple(dict.fromkeys(str(item) for item in command.get("skill_ids") or ()))
        if mode == "pinned" and not ids:
            raise ValueError("a pinned agent needs at least one skill")
        registry = self._registry(user_id)
        for skill_id in ids:
            registry.resolve(skill_id)
        self._agent_preferences[(user_id, agent_id)] = (mode, ids if mode == "pinned" else ())
        return self.agent_skills({"user_id": user_id, "agent_id": agent_id})

    def agents_for_skill(self, query: Mapping[str, object]) -> dict[str, object]:
        user_id, skill_id = str(query["user_id"]), str(query["skill_id"])
        self._registry(user_id).resolve(skill_id)
        return {"items": [{"agent_id": agent_id, "mode": mode} for (owner, agent_id), (mode, ids) in self._agent_preferences.items() if owner == user_id and (mode == "auto" or skill_id in ids)]}

    def _all_versions(self, user_id: str, skill_id: str) -> list[Skill]:
        return sorted((skill for skill in self._custom.get(user_id, ()) if skill.id == skill_id), key=lambda item: item.version, reverse=True)

    @staticmethod
    def _next_patch(version: str) -> str:
        core = version.split("-", 1)[0].split("+", 1)[0]
        major, minor, patch = (int(part) for part in core.split("."))
        return f"{major}.{minor}.{patch + 1}"


__all__ = ["SkillLibraryService"]
