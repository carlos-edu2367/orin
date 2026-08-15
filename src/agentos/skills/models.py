"""Immutable value objects for declarative AgentOS skills.

Skills carry operational content, never authority.  Permission checks remain
with the runtime tools; these objects only describe procedures and their
requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
import re


_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?::[a-z][a-z0-9]*(?:-[a-z0-9]+)*)?$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def _text(value: object, field: str, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} must be a bounded non-blank string")
    return value.strip()


def _strings(values: tuple[str, ...] | list[str], field: str) -> tuple[str, ...]:
    result = tuple(_text(value, field, maximum=256) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} values must be unique")
    return result


def semver_key(version: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    """Return the SemVer precedence key; build metadata intentionally sorts equal."""
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise ValueError("skill version must be a semantic version")
    release, _, _build = version.partition("+")
    core, separator, prerelease = release.partition("-")
    identifiers = () if not separator else tuple(prerelease.split("."))
    if any(not item or (item.isdigit() and len(item) > 1 and item.startswith("0")) for item in identifiers):
        raise ValueError("skill version must be a semantic version")
    prerelease_key = tuple((0, int(item)) if item.isdigit() else (1, item) for item in identifiers)
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, 1 if not identifiers else 0, prerelease_key


class SkillScope(StrEnum):
    SYSTEM = "system"
    USER = "user"
    WORKSPACE = "workspace"
    AGENT = "agent"


class SkillSource(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"
    IMPORTED = "imported"
    PLUGIN = "plugin"


@dataclass(frozen=True, slots=True)
class SkillRef:
    id: str
    version: str
    scope: SkillScope = SkillScope.SYSTEM

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID.fullmatch(self.id):
            raise ValueError("skill id must be a lowercase kebab-case identifier")
        semver_key(self.version)
        object.__setattr__(self, "scope", SkillScope(self.scope))


@dataclass(frozen=True, slots=True)
class SkillDependencies:
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "skills", _strings(self.skills, "skill dependency"))
        object.__setattr__(self, "tools", _strings(self.tools, "tool dependency"))
        for skill_id in self.skills:
            if not _ID.fullmatch(skill_id):
                raise ValueError("skill dependency must be a skill id")


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    ref: SkillRef
    name: str
    description: str
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    when_to_use: tuple[str, ...] = ()
    when_not_to_use: tuple[str, ...] = ()
    dependencies: SkillDependencies = SkillDependencies()
    source: SkillSource = SkillSource.BUILTIN
    available: bool = True
    unavailable_reason: str | None = None

    @property
    def id(self) -> str:
        return self.ref.id

    @property
    def version(self) -> str:
        return self.ref.version

    def __post_init__(self) -> None:
        _text(self.name, "skill name", maximum=128)
        _text(self.description, "skill description", maximum=2_000)
        object.__setattr__(self, "tags", _strings(self.tags, "tag"))
        object.__setattr__(self, "capabilities", _strings(self.capabilities, "capability"))
        object.__setattr__(self, "when_to_use", _strings(self.when_to_use, "when_to_use"))
        object.__setattr__(self, "when_not_to_use", _strings(self.when_not_to_use, "when_not_to_use"))
        if not isinstance(self.dependencies, SkillDependencies):
            raise ValueError("dependencies must be typed")
        object.__setattr__(self, "source", SkillSource(self.source))
        if self.unavailable_reason is not None:
            _text(self.unavailable_reason, "unavailable_reason", maximum=256)


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    name: str
    version: str
    description: str
    instructions: str | None = None
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    when_to_use: tuple[str, ...] = ()
    when_not_to_use: tuple[str, ...] = ()
    dependencies: SkillDependencies = SkillDependencies()
    requires_tools: tuple[str, ...] = ()
    scope: SkillScope = SkillScope.SYSTEM
    source: SkillSource = SkillSource.BUILTIN
    enabled: bool = True
    package_path: Path | None = None
    author: str | None = None
    content_digest: str | None = None
    package_digest: str | None = None

    def __post_init__(self) -> None:
        ref = SkillRef(self.id, self.version, self.scope)
        _text(self.name, "skill name", maximum=128)
        _text(self.description, "skill description", maximum=2_000)
        if self.instructions is not None and not isinstance(self.instructions, str):
            raise ValueError("instructions must be markdown text or None")
        if self.content_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", self.content_digest):
            raise ValueError("content_digest must be a SHA-256 hex digest")
        if self.package_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", self.package_digest):
            raise ValueError("package_digest must be a SHA-256 hex digest")
        if self.instructions is not None:
            digest = sha256(self.instructions.encode("utf-8")).hexdigest()
            if self.content_digest is not None and self.content_digest != digest:
                raise ValueError("content_digest does not match instructions")
            object.__setattr__(self, "content_digest", digest)
        object.__setattr__(self, "tags", _strings(self.tags, "tag"))
        object.__setattr__(self, "capabilities", _strings(self.capabilities, "capability"))
        object.__setattr__(self, "when_to_use", _strings(self.when_to_use, "when_to_use"))
        object.__setattr__(self, "when_not_to_use", _strings(self.when_not_to_use, "when_not_to_use"))
        if not isinstance(self.dependencies, SkillDependencies):
            raise ValueError("dependencies must be typed")
        tools = _strings(self.requires_tools, "required tool")
        object.__setattr__(self, "requires_tools", tools)
        object.__setattr__(self, "scope", ref.scope)
        object.__setattr__(self, "source", SkillSource(self.source))
        if self.package_path is not None:
            object.__setattr__(self, "package_path", Path(self.package_path))
        if self.author is not None:
            _text(self.author, "author", maximum=256)

    @property
    def ref(self) -> SkillRef:
        return SkillRef(self.id, self.version, self.scope)

    @property
    def digest(self) -> str | None:
        return self.content_digest

    @property
    def required_tools(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.dependencies.tools, *self.requires_tools)))

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            ref=self.ref,
            name=self.name,
            description=self.description,
            tags=self.tags,
            capabilities=self.capabilities,
            when_to_use=self.when_to_use,
            when_not_to_use=self.when_not_to_use,
            dependencies=SkillDependencies(self.dependencies.skills, self.required_tools),
            source=self.source,
            available=self.enabled,
            unavailable_reason=None if self.enabled else "skill is disabled",
        )


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    skill: Skill
    dependencies: tuple["LoadedSkill", ...] = ()

    @property
    def ref(self) -> SkillRef:
        return self.skill.ref

    @property
    def instructions(self) -> str:
        if self.skill.instructions is None:
            raise ValueError(f"skill '{self.skill.id}' has no loaded instructions")
        return self.skill.instructions

    @property
    def digest(self) -> str:
        digest = self.skill.digest
        if digest is None:
            raise ValueError(f"skill '{self.skill.id}' has no loaded instructions")
        return digest


__all__ = ["LoadedSkill", "Skill", "SkillDependencies", "SkillMetadata", "SkillRef", "SkillScope", "SkillSource", "semver_key"]
