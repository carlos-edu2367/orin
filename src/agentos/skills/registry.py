"""Single in-memory source of truth for resolution and loading of Skills."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Collection, Iterable

from .models import LoadedSkill, Skill, SkillMetadata, SkillScope, semver_key
from .retrieval import RetrievalQuery, RetrievalResult, SkillRetriever


_SCOPE_ORDER = {SkillScope.AGENT: 4, SkillScope.WORKSPACE: 3, SkillScope.USER: 2, SkillScope.SYSTEM: 1}


class SkillError(RuntimeError):
    pass


class SkillNotFound(SkillError):
    pass


class SkillUnavailable(SkillError):
    pass


class SkillDependencyCycle(SkillError):
    pass


class SkillDependencyDepthExceeded(SkillError):
    pass


class SkillRegistry:
    """Owns all resolution, prerequisite checks, and lazy content loading."""

    def __init__(self, skills: Iterable[Skill] = (), *, retriever: SkillRetriever | None = None, maximum_dependency_depth: int = 12) -> None:
        if maximum_dependency_depth < 1:
            raise ValueError("maximum_dependency_depth must be positive")
        self._skills: dict[tuple[str, str, SkillScope], Skill] = {}
        self._retriever = retriever or SkillRetriever()
        self._maximum_dependency_depth = maximum_dependency_depth
        for skill in skills:
            self.register(skill)

    def register(self, skill: Skill) -> Skill:
        if not isinstance(skill, Skill):
            raise ValueError("registry accepts Skill values")
        key = (skill.id, skill.version, skill.scope)
        if key in self._skills:
            raise ValueError(f"skill '{skill.id}@{skill.version}' is already registered for {skill.scope.value}")
        self._skills[key] = skill
        return skill

    def update(self, skill: Skill) -> Skill:
        """Publish a new immutable version; never overwrite an existing version."""
        return self.register(skill)

    def remove(self, skill_id: str, *, version: str | None = None, scope: SkillScope | None = None) -> int:
        targets = [key for key in self._skills if key[0] == skill_id and (version is None or key[1] == version) and (scope is None or key[2] is SkillScope(scope))]
        for key in targets:
            del self._skills[key]
        return len(targets)

    def disable(self, skill_id: str, *, version: str | None = None, scope: SkillScope | None = None, reason: str = "skill is disabled") -> Skill:
        skill = self.resolve(skill_id, version=version, scope=scope)
        disabled = replace(skill, enabled=False)
        self._skills[(skill.id, skill.version, skill.scope)] = disabled
        return disabled

    def resolve(self, skill_id: str, *, version: str | None = None, scope: SkillScope | None = None) -> Skill:
        candidates = [skill for skill in self._skills.values() if skill.id == skill_id and (version is None or skill.version == version) and (scope is None or skill.scope is SkillScope(scope))]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise SkillNotFound(f"skill '{skill_id}{suffix}' was not found")
        candidates.sort(key=lambda item: (_SCOPE_ORDER[item.scope], self._version_key(item.version)), reverse=True)
        return candidates[0]

    def metadata(self, skill_id: str, *, version: str | None = None, scope: SkillScope | None = None, available_tools: Collection[str] = ()) -> SkillMetadata:
        skill = self.resolve(skill_id, version=version, scope=scope)
        reason = self._unavailable_reason(skill, frozenset(available_tools))
        return replace(skill.metadata, available=reason is None, unavailable_reason=reason)

    def list(self, *, tags: Collection[str] = (), limit: int = 20, available_tools: Collection[str] = ()) -> tuple[SkillMetadata, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        wanted = {str(tag).lower() for tag in tags}
        items = []
        for skill in self._current_versions():
            if wanted and not wanted.issubset({tag.lower() for tag in skill.tags}):
                continue
            items.append(self.metadata(skill.id, version=skill.version, scope=skill.scope, available_tools=available_tools))
        return tuple(items[:limit])

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        skills = tuple(skill for skill in self._current_versions() if self._unavailable_reason(skill, query.available_tools) is None)
        return self._retriever.retrieve(skills, query)

    def search(self, query: RetrievalQuery) -> RetrievalResult:
        return self.retrieve(query)

    def load(self, skill_id: str, *, version: str | None = None, available_tools: Collection[str] = (), scope: SkillScope | None = None) -> LoadedSkill:
        tools = frozenset(available_tools)
        return self._load(self.resolve(skill_id, version=version, scope=scope), tools, (), 0)

    def read_instructions(self, skill_id: str, *, version: str | None = None, scope: SkillScope | None = None) -> str:
        """Read a skill body without requiring it to be executable now.

        Detail views and diagnostics must remain available for a skill whose
        required tools are not present in the current runtime.  ``load`` keeps
        its strict availability checks for execution; this method only applies
        the normal package-integrity and lazy-content checks.
        """
        return self._with_instructions(self.resolve(skill_id, version=version, scope=scope)).instructions

    def read_resource(self, skill_id: str, resource_path: str, *, version: str | None = None, available_tools: Collection[str] = (), scope: SkillScope | None = None) -> str:
        """Read a package resource only after ordinary availability checks."""
        loaded = self.load(skill_id, version=version, available_tools=available_tools, scope=scope)
        package = loaded.skill.package_path
        if package is None:
            raise SkillUnavailable(f"skill '{skill_id}' has no package resources")
        relative = Path(resource_path)
        allowed_roots = {"resources", "references", "examples", "templates"}
        if not resource_path or relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] not in allowed_roots:
            raise SkillUnavailable("skill resource path must stay within a readable package resource directory")
        root = Path(package).parent.resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise SkillNotFound(f"skill resource '{resource_path}' was not found")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise SkillUnavailable(f"skill resource '{resource_path}' is not UTF-8 text") from error

    def _load(self, skill: Skill, available_tools: frozenset[str], chain: tuple[str, ...], depth: int) -> LoadedSkill:
        if depth > self._maximum_dependency_depth:
            raise SkillDependencyDepthExceeded(f"skill dependency depth exceeds {self._maximum_dependency_depth}")
        if skill.id in chain:
            raise SkillDependencyCycle("skill dependency cycle: " + " -> ".join((*chain, skill.id)))
        reason = self._basic_unavailable_reason(skill, available_tools)
        if reason is not None:
            raise SkillUnavailable(f"skill '{skill.id}' is unavailable: {reason}")
        dependencies = tuple(self._load(self.resolve(dependency_id), available_tools, (*chain, skill.id), depth + 1) for dependency_id in skill.dependencies.skills)
        return LoadedSkill(self._with_instructions(skill), dependencies)

    def _unavailable_reason(self, skill: Skill, available_tools: frozenset[str]) -> str | None:
        return self._availability_reason(skill, available_tools, (), 0)

    def _availability_reason(self, skill: Skill, available_tools: frozenset[str], chain: tuple[str, ...], depth: int) -> str | None:
        if depth > self._maximum_dependency_depth:
            return f"dependency depth exceeds {self._maximum_dependency_depth}"
        if skill.id in chain:
            return "dependency cycle: " + " -> ".join((*chain, skill.id))
        reason = self._basic_unavailable_reason(skill, available_tools)
        if reason is not None:
            return reason
        for dependency_id in skill.dependencies.skills:
            dependency = self.resolve(dependency_id)
            dependency_reason = self._availability_reason(dependency, available_tools, (*chain, skill.id), depth + 1)
            if dependency_reason is not None:
                return f"required skill {dependency_id} is unavailable: {dependency_reason}"
        return None

    def _basic_unavailable_reason(self, skill: Skill, available_tools: frozenset[str]) -> str | None:
        if not skill.enabled:
            return "skill is disabled"
        missing = sorted(set(skill.required_tools) - available_tools)
        if missing:
            return f"required tool {missing[0]} is unavailable"
        for dependency_id in skill.dependencies.skills:
            try:
                self.resolve(dependency_id)
            except SkillNotFound:
                return f"required skill {dependency_id} is unavailable"
        return None

    def _with_instructions(self, skill: Skill) -> Skill:
        if skill.instructions is not None:
            return skill
        if skill.package_path is None:
            raise SkillUnavailable(f"skill '{skill.id}' has no instructions")
        from .parser import parse_skill_file

        parsed = parse_skill_file(Path(skill.package_path), include_instructions=True, source=skill.source, scope=skill.scope)
        if parsed.id != skill.id or parsed.version != skill.version:
            raise SkillUnavailable(f"skill package metadata does not match '{skill.id}@{skill.version}'")
        if parsed.digest != skill.digest or parsed.package_digest != skill.package_digest:
            raise SkillUnavailable(f"skill package content changed after '{skill.id}@{skill.version}' was registered")
        return parsed

    def _current_versions(self) -> tuple[Skill, ...]:
        current: dict[tuple[str, SkillScope], Skill] = {}
        for skill in self._skills.values():
            key = (skill.id, skill.scope)
            existing = current.get(key)
            if existing is None or self._version_key(skill.version) > self._version_key(existing.version):
                current[key] = skill
        chosen: dict[str, Skill] = {}
        for skill in current.values():
            existing = chosen.get(skill.id)
            if existing is None or _SCOPE_ORDER[skill.scope] > _SCOPE_ORDER[existing.scope]:
                chosen[skill.id] = skill
        return tuple(sorted(chosen.values(), key=lambda item: item.id))

    @staticmethod
    def _version_key(version: str) -> tuple[object, ...]:
        # Build metadata has no SemVer precedence.  The remaining values are
        # only deterministic tie-breakers, preferring the canonical release.
        return (*semver_key(version), 1 if "+" not in version else 0, version)


__all__ = ["SkillDependencyCycle", "SkillDependencyDepthExceeded", "SkillError", "SkillNotFound", "SkillRegistry", "SkillUnavailable"]
