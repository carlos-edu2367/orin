"""Application-facing Skill library backed by the central registry.

The service deliberately returns compact metadata for collection reads and only
returns the Markdown body for an explicit detail read.  The in-process store
is the local bootstrap implementation; durable adapters can hydrate the same
``Skill`` values without leaking registry access to HTTP handlers.
"""
from __future__ import annotations

import re
from typing import Collection, Mapping

from .builtins import load_builtin_skills
from .models import Skill, SkillDependencies, SkillScope, SkillSource, semver_key
from .registry import SkillNotFound, SkillRegistry
from .retrieval import RetrievalQuery


SKILL_RUNTIME_TOOLS = ("read_file", "write_file", "list_files", "run_command", "fetch_url")


class SkillValidationError(ValueError):
    """Raised before publishing a Skill that cannot be used by the runtime."""


def validate_skill_definition(skill: Skill, registry: SkillRegistry, *, available_tools: Collection[str] = SKILL_RUNTIME_TOOLS) -> None:
    """Reject a Skill unless its runtime prerequisites are valid."""
    missing_tools = sorted(set(skill.required_tools) - set(available_tools))
    if missing_tools:
        raise SkillValidationError(
            f"Skill requires unavailable tool '{missing_tools[0]}'. Available tools: {', '.join(available_tools)}."
        )

    for dependency_id in skill.dependencies.skills:
        if dependency_id == skill.id:
            raise SkillValidationError(f"Skill cannot depend on itself ('{skill.id}').")
        try:
            registry.resolve(dependency_id)
        except SkillNotFound as error:
            raise SkillValidationError(f"Skill dependency '{dependency_id}' is not installed.") from error
        dependency = registry.metadata(dependency_id, available_tools=available_tools)
        if not dependency.available:
            reason = dependency.unavailable_reason or "it cannot be used by the runtime"
            raise SkillValidationError(f"Skill dependency '{dependency_id}' is unavailable: {reason}.")


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

    def registry_for(self, user_id: str, agent_id: str | None = None) -> SkillRegistry:
        """Expose the same runtime registry contract as the durable adapter."""
        return self._registry(user_id, agent_id)

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
        available = SKILL_RUNTIME_TOOLS
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
        meta = registry.metadata(skill_id, available_tools=SKILL_RUNTIME_TOOLS)
        skill = registry.resolve(skill_id)
        return {**self._summary(meta), "instructions": registry.read_instructions(skill_id),
                "dependencies": list(skill.dependencies.skills),
                "requires_tools": list(skill.required_tools),
                "versions": [skill.version for skill in self._all_versions(user_id, skill_id)]}

    def create(self, command: Mapping[str, object]) -> dict[str, object]:
        user_id = str(command["user_id"])
        candidate = self._custom_skill(command, skill_id=_id(str(command["name"])))
        registry = self._registry(user_id)
        validate_skill_definition(candidate, registry)
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
        candidate = self._custom_skill(values, skill_id=prior.id)
        validate_skill_definition(candidate, self._registry(user_id))
        self._custom.setdefault(user_id, []).append(candidate)
        return self.get({"user_id": user_id, "skill_id": skill_id})

    def remove_version(self, command: Mapping[str, object]) -> dict[str, object]:
        """Remove one old user-owned version while keeping the active version."""
        user_id, skill_id, version = str(command["user_id"]), str(command["skill_id"]), str(command["version"])
        versions = self._all_versions(user_id, skill_id)
        target = next((skill for skill in versions if skill.version == version), None)
        if target is None:
            raise ValueError("skill version was not found in this user scope")
        current = max(versions, key=lambda skill: semver_key(skill.version))
        if target.version == current.version:
            raise ValueError("the current skill version cannot be uninstalled")
        self._custom[user_id] = [
            skill for skill in self._custom.get(user_id, ())
            if not (skill.id == skill_id and skill.version == version)
        ]
        return self.get({"user_id": user_id, "skill_id": skill_id})

    @staticmethod
    def _custom_skill(command: Mapping[str, object], *, skill_id: str) -> Skill:
        dependencies = command.get("dependencies") or {}
        if not isinstance(dependencies, Mapping):
            raise ValueError("dependencies must be an object")
        return Skill(
            id=skill_id, name=str(command["name"]), version=str(command.get("version") or "1.0.0"),
            description=str(command["description"]), instructions=str(command["instructions"]),
            tags=tuple(str(tag) for tag in command.get("tags") or ()),
            capabilities=tuple(str(capability) for capability in command.get("capabilities") or ()),
            when_to_use=tuple(str(item) for item in command.get("when_to_use") or ()),
            when_not_to_use=tuple(str(item) for item in command.get("when_not_to_use") or ()),
            dependencies=SkillDependencies(
                tuple(str(item) for item in dependencies.get("skills") or ()),
                tuple(str(item) for item in dependencies.get("tools") or ()),
            ),
            requires_tools=tuple(str(item) for item in command.get("requires_tools") or ()),
            scope=SkillScope.USER, source=SkillSource.CUSTOM,
        )

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
        return sorted((skill for skill in self._custom.get(user_id, ()) if skill.id == skill_id), key=lambda item: semver_key(item.version), reverse=True)

    @staticmethod
    def _next_patch(version: str) -> str:
        core = version.split("-", 1)[0].split("+", 1)[0]
        major, minor, patch = (int(part) for part in core.split("."))
        return f"{major}.{minor}.{patch + 1}"


__all__ = ["SKILL_RUNTIME_TOOLS", "SkillLibraryService", "SkillValidationError", "validate_skill_definition"]
