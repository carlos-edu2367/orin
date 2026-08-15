"""Strict parser for portable SKILL.md packages.

The supported frontmatter is deliberately small so a package never smuggles
arbitrary object graphs into the runtime.  It is enough for the Agent Skills
directory convention and can be expanded explicitly when needed.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any

from .models import Skill, SkillDependencies, SkillScope, SkillSource


class SkillParseError(ValueError):
    pass


_ALLOWED = frozenset({"id", "name", "description", "version", "tags", "capabilities", "when_to_use", "when_not_to_use", "dependencies", "requires_tools", "author"})


_DEFAULT_REQUIRED: frozenset[str] = frozenset({"name", "description", "version"})


def parse_skill_file(
    path: Path | str,
    *,
    include_instructions: bool = True,
    source: SkillSource = SkillSource.BUILTIN,
    scope: SkillScope = SkillScope.SYSTEM,
    required_fields: frozenset[str] = _DEFAULT_REQUIRED,
    default_version: str | None = None,
) -> Skill:
    target = Path(path)
    if target.is_dir():
        target = target / "SKILL.md"
    try:
        document = target.read_text(encoding="utf-8")
    except OSError as error:
        raise SkillParseError(f"cannot read skill package '{target}'") from error
    skill = parse_skill_markdown(
        document,
        include_instructions=include_instructions,
        source=source,
        scope=scope,
        required_fields=required_fields,
        default_version=default_version,
    )
    return replace(skill, package_path=target)


def parse_skill_markdown(
    document: str,
    *,
    include_instructions: bool = True,
    source: SkillSource = SkillSource.BUILTIN,
    scope: SkillScope = SkillScope.SYSTEM,
    required_fields: frozenset[str] = _DEFAULT_REQUIRED,
    default_version: str | None = None,
) -> Skill:
    """Parse a SKILL.md document.

    ``required_fields``/``default_version`` exist for the plugin inspector: real-world
    plugin skills (e.g. the ecosystem's own `superpowers`) declare only name/description
    per skill, tracking version once at the plugin level instead. Orin's own skill
    service keeps the strict default (version required, no substitute) because its
    skills are versioned individually via `skill_versions`.
    """
    if not isinstance(document, str) or not document.startswith("---\n"):
        raise SkillParseError("SKILL.md must begin with YAML frontmatter")
    try:
        header, body = document[4:].split("\n---\n", 1)
    except ValueError as error:
        raise SkillParseError("SKILL.md frontmatter is not closed") from error
    values = _parse_frontmatter(header)
    unknown = set(values) - _ALLOWED
    if unknown:
        raise SkillParseError(f"unsupported skill frontmatter field: {sorted(unknown)[0]}")
    missing = required_fields - set(values)
    if missing:
        raise SkillParseError(f"missing required skill frontmatter field: {sorted(missing)[0]}")
    if "version" not in values:
        if default_version is None:
            raise SkillParseError("missing required skill frontmatter field: version")
        values["version"] = default_version
    skill_id = str(values.get("id") or _slug(str(values["name"])))
    dependencies = values.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise SkillParseError("dependencies must be a mapping")
    unknown_dependencies = set(dependencies) - {"skills", "tools"}
    if unknown_dependencies:
        raise SkillParseError(f"unsupported dependencies frontmatter field: {sorted(unknown_dependencies)[0]}")
    return Skill(
        id=skill_id,
        name=_scalar(values["name"], "name"),
        version=_scalar(values["version"], "version"),
        description=_scalar(values["description"], "description"),
        instructions=body.strip() if include_instructions else None,
        tags=_list(values.get("tags", ()), "tags"),
        capabilities=_list(values.get("capabilities", ()), "capabilities"),
        when_to_use=_list(values.get("when_to_use", ()), "when_to_use"),
        when_not_to_use=_list(values.get("when_not_to_use", ()), "when_not_to_use"),
        dependencies=SkillDependencies(_list(dependencies.get("skills", ()), "dependencies.skills"), _list(dependencies.get("tools", ()), "dependencies.tools")),
        requires_tools=_list(values.get("requires_tools", ()), "requires_tools"),
        source=source,
        scope=scope,
        author=None if values.get("author") is None else _scalar(values["author"], "author"),
        content_digest=_digest(body),
        package_digest=_digest_document(document),
    )


def _parse_frontmatter(header: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    active_list: list[str] | None = None
    active_mapping: str | None = None
    for number, raw_line in enumerate(header.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0:
            active_list = None
            active_mapping = None
            if ":" not in line:
                raise SkillParseError(f"invalid frontmatter line {number}")
            key, raw = (part.strip() for part in line.split(":", 1))
            if not key:
                raise SkillParseError(f"invalid frontmatter line {number}")
            if key in values:
                raise SkillParseError(f"duplicate frontmatter field: {key}")
            if raw:
                values[key] = _value(raw, number)
            else:
                values[key] = [] if key in {"tags", "capabilities", "when_to_use", "when_not_to_use", "requires_tools"} else {}
                active_list = values[key] if isinstance(values[key], list) else None
                active_mapping = key if isinstance(values[key], dict) else None
            continue
        if line.startswith("- ") and active_list is not None:
            active_list.append(_scalar(line[2:], f"frontmatter line {number}"))
            continue
        if active_mapping is not None and ":" in line:
            key, raw = (part.strip() for part in line.split(":", 1))
            if not key:
                raise SkillParseError(f"invalid frontmatter line {number}")
            if key in values[active_mapping]:
                raise SkillParseError(f"duplicate {active_mapping} frontmatter field: {key}")
            if not raw:
                values[active_mapping][key] = []
                active_list = values[active_mapping][key]
            else:
                values[active_mapping][key] = _value(raw, number)
                active_list = None
            continue
        raise SkillParseError(f"invalid frontmatter indentation on line {number}")
    return values


def _value(raw: str, line: int) -> Any:
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise SkillParseError(f"invalid list on frontmatter line {line}")
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(item, f"frontmatter line {line}") for item in inner.split(",")]
    return _scalar(raw, f"frontmatter line {line}")


def _scalar(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise SkillParseError(f"{field} must be a string")
    clean = value.strip().strip('"').strip("'")
    if not clean:
        raise SkillParseError(f"{field} must not be blank")
    return clean


def _list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise SkillParseError(f"{field} must be a list")
    return tuple(_scalar(item, field) for item in value)


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def _digest(body: str) -> str:
    from hashlib import sha256

    return sha256(body.strip().encode("utf-8")).hexdigest()


def _digest_document(document: str) -> str:
    from hashlib import sha256

    return sha256(document.encode("utf-8")).hexdigest()


__all__ = ["SkillParseError", "parse_skill_file", "parse_skill_markdown"]
