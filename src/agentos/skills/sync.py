"""Automatic discovery and idempotent sync of disk-installed skills (``npx skills add``, Claude, Codex, Cursor, ...)."""
from __future__ import annotations

from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import re
from typing import Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import agent_skills, skill_versions, skills

from .disk import DiskSkill, DiskSkillCatalog, load_disk_skills, skill_roots
from .models import Skill, SkillDependencies, SkillScope, SkillSource, semver_key

logger = logging.getLogger(__name__)

_VALID_SKILL_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?::[a-z][a-z0-9]*(?:-[a-z0-9]+)*)?$")


def _normalize_skill_id(raw_name: str) -> str | None:
    parts = [part.strip().lower() for part in str(raw_name or "").split(":") if part.strip()]
    if not parts or len(parts) > 2:
        return None
    normalized: list[str] = []
    for part in parts:
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", part)).strip("-")
        if not slug or not slug[0].isalpha():
            return None
        normalized.append(slug)
    candidate = ":".join(normalized)
    return candidate if _VALID_SKILL_ID.fullmatch(candidate) else None


def _valid_semver(raw: str | None) -> str:
    if raw:
        try:
            semver_key(raw)
            return raw
        except ValueError:
            pass
    return "1.0.0"


def _next_patch(version: str) -> str:
    core = version.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = (int(part) for part in core.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _clean_unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value or "").split())[:256]
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return tuple(result)


def disk_skill_to_library_skill(disk_skill: DiskSkill) -> Skill | None:
    """Convert a disk ``SKILL.md`` into an immutable ``Skill`` compatible with ``SkillRegistry`` / SQL storage."""
    if disk_skill.kind != "skill":
        return None
    skill_id = _normalize_skill_id(disk_skill.name)
    if skill_id is None:
        return None
    name = " ".join(disk_skill.name.split())[:128] or skill_id
    description = " ".join(disk_skill.description.split())[:2_000] or name
    scope = SkillScope.WORKSPACE if disk_skill.origin == "project" else SkillScope.USER
    author = " ".join(disk_skill.author.split())[:256] if disk_skill.author else None
    try:
        return Skill(
            id=skill_id,
            name=name,
            version=_valid_semver(disk_skill.version),
            description=description,
            instructions=disk_skill.body or "",
            tags=_clean_unique_strings(disk_skill.tags),
            capabilities=_clean_unique_strings(disk_skill.capabilities),
            when_to_use=_clean_unique_strings(disk_skill.when_to_use),
            when_not_to_use=_clean_unique_strings(disk_skill.when_not_to_use),
            dependencies=SkillDependencies(),
            requires_tools=(),
            scope=scope,
            source=SkillSource.IMPORTED,
            enabled=True,
            package_path=disk_skill.path,
            author=author,
        )
    except ValueError as error:
        logger.debug("disk skill %s skipped during library sync: %s", disk_skill.path, error)
        return None


def disk_roots_fingerprint(
    *,
    workspace_root: Path | None = None,
    trusted: bool = False,
    personal: bool = True,
) -> tuple[tuple[str, int, int], ...]:
    """Cheap mtime+size snapshot across skill roots so unchanged directories skip DB sync work."""
    entries: list[tuple[str, int, int]] = []
    for _origin, kind, folder in skill_roots(workspace_root=workspace_root, trusted=trusted, personal=personal):
        if kind != "skill":
            continue
        try:
            root_stat = folder.stat()
        except OSError:
            continue
        entries.append((os.path.normcase(str(folder)), root_stat.st_mtime_ns, 0))
        try:
            children = sorted(folder.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            target = child / "SKILL.md"
            try:
                file_stat = target.stat()
            except OSError:
                continue
            entries.append((os.path.normcase(str(target)), file_stat.st_mtime_ns, file_stat.st_size))
    return tuple(entries)


def _skill_metadata_payload(skill: Skill, *, disk_origin: str, model_invocable: bool) -> dict[str, object]:
    return {
        "tags": list(skill.tags),
        "capabilities": list(skill.capabilities),
        "when_to_use": list(skill.when_to_use),
        "when_not_to_use": list(skill.when_not_to_use),
        "dependencies": list(skill.dependencies.skills),
        "dependency_tools": list(skill.dependencies.tools),
        "requires_tools": list(skill.requires_tools),
        "author": skill.author,
        "package_path": str(skill.package_path) if skill.package_path else None,
        "disk_origin": disk_origin,
        "model_invocable": model_invocable,
    }


def sync_disk_skills_to_db(
    engine: Engine,
    *,
    user_id: str,
    workspace_root: Path | None = None,
    trusted: bool = False,
    personal: bool = True,
    catalog: DiskSkillCatalog | None = None,
) -> dict[str, int]:
    """Idempotently mirror discovered user/managed disk skills into the ``skills`` SQL tables as ``source='imported'``.

    Custom and plugin skills owned by the user always take precedence and are never overwritten.
    Project-scoped skills stay scoped to their workspace turn (via ``load_disk_skills``) and are not
    persisted globally under ``user_id``, preventing cross-project leakage.
    """
    loaded = catalog if catalog is not None else load_disk_skills(
        workspace_root=workspace_root, trusted=trusted, personal=personal,
    )
    discovered: dict[str, tuple[Skill, DiskSkill]] = {}
    for disk_skill in loaded.skills.values():
        if disk_skill.kind != "skill" or disk_skill.origin == "project":
            continue
        converted = disk_skill_to_library_skill(disk_skill)
        if converted is None or converted.id in discovered:
            continue
        discovered[converted.id] = (converted, disk_skill)

    now = datetime.now(UTC)
    added = 0
    updated = 0
    removed = 0

    with engine.begin() as connection:
        existing_rows = connection.execute(
            select(skills.c.id, skills.c.skill_id, skills.c.source, skills.c.enabled)
            .where((skills.c.user_id == user_id) & (skills.c.scope == SkillScope.USER.value))
        ).mappings().all()

        protected_ids = {
            str(row["skill_id"])
            for row in existing_rows
            if str(row["source"]) != SkillSource.IMPORTED.value
        }
        imported_by_id: dict[str, Mapping[str, object]] = {
            str(row["skill_id"]): row
            for row in existing_rows
            if str(row["source"]) == SkillSource.IMPORTED.value
        }

        imported_record_ids = [int(row["id"]) for row in imported_by_id.values()]
        latest_versions: dict[int, Mapping[str, object]] = {}
        if imported_record_ids:
            version_rows = connection.execute(
                select(skill_versions)
                .where(skill_versions.c.skill_record_id.in_(imported_record_ids))
            ).mappings().all()
            by_record: dict[int, list[Mapping[str, object]]] = {}
            for vrow in version_rows:
                by_record.setdefault(int(vrow["skill_record_id"]), []).append(vrow)
            for rec_id, vrows in by_record.items():
                latest_versions[rec_id] = max(vrows, key=lambda item: semver_key(str(item["version"])))

        for skill_id, (skill, disk_skill) in discovered.items():
            if skill_id in protected_ids:
                continue
            existing = imported_by_id.get(skill_id)
            meta_payload = _skill_metadata_payload(
                skill, disk_origin=disk_skill.origin, model_invocable=disk_skill.model_invocable,
            )
            if existing is None:
                record_id = connection.execute(
                    insert(skills).values(
                        skill_id=skill.id,
                        user_id=user_id,
                        workspace_id=None,
                        scope=SkillScope.USER.value,
                        source=SkillSource.IMPORTED.value,
                        plugin_id=None,
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    )
                ).inserted_primary_key[0]
                connection.execute(
                    insert(skill_versions).values(
                        skill_record_id=record_id,
                        version=skill.version,
                        name=skill.name,
                        description=skill.description,
                        metadata=meta_payload,
                        instructions=skill.instructions or "",
                        content_digest=skill.digest or "",
                        published_at=now,
                    )
                )
                added += 1
                continue

            rec_id = int(existing["id"])
            latest = latest_versions.get(rec_id)
            latest_meta = dict(latest["metadata"] or {}) if latest is not None else {}
            changed = (
                latest is None
                or str(latest["content_digest"]) != str(skill.digest or "")
                or str(latest["name"]) != skill.name
                or str(latest["description"]) != skill.description
                or str(latest_meta.get("package_path") or "") != str(skill.package_path or "")
                or bool(latest_meta.get("model_invocable", True)) != disk_skill.model_invocable
            )
            if changed:
                next_ver = skill.version if latest is None else _next_patch(str(latest["version"]))
                connection.execute(
                    insert(skill_versions).values(
                        skill_record_id=rec_id,
                        version=next_ver,
                        name=skill.name,
                        description=skill.description,
                        metadata=meta_payload,
                        instructions=skill.instructions or "",
                        content_digest=skill.digest or "",
                        published_at=now,
                    )
                )
                connection.execute(
                    update(skills).where(skills.c.id == rec_id).values(enabled=True, updated_at=now)
                )
                updated += 1
            elif not bool(existing["enabled"]):
                connection.execute(
                    update(skills).where(skills.c.id == rec_id).values(enabled=True, updated_at=now)
                )
                updated += 1

        # Clean up imported skills that were removed from disk (e.g. ``npx skills remove``).
        stale_ids = [sid for sid in imported_by_id if sid not in discovered]
        if stale_ids:
            for sid in stale_ids:
                rec_id = int(imported_by_id[sid]["id"])
                version_ids = connection.execute(
                    select(skill_versions.c.id).where(skill_versions.c.skill_record_id == rec_id)
                ).scalars().all()
                pinned = (
                    connection.execute(
                        select(agent_skills.c.id)
                        .where(agent_skills.c.skill_version_id.in_(version_ids))
                        .limit(1)
                    ).scalar_one_or_none()
                    if version_ids
                    else None
                )
                if pinned is not None:
                    if bool(imported_by_id[sid]["enabled"]):
                        connection.execute(
                            update(skills).where(skills.c.id == rec_id).values(enabled=False, updated_at=now)
                        )
                        removed += 1
                else:
                    connection.execute(delete(skill_versions).where(skill_versions.c.skill_record_id == rec_id))
                    connection.execute(delete(skills).where(skills.c.id == rec_id))
                    removed += 1

    return {"added": added, "updated": updated, "removed": removed, "total": len(discovered)}


__all__ = [
    "disk_roots_fingerprint",
    "disk_skill_to_library_skill",
    "sync_disk_skills_to_db",
]
