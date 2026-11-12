"""Per-user pending SKILL.md proposals with mandatory dry-run validation.

Proposals distilled by background consolidation (``auto-dream``) never activate
on their own: they are validated in dry-run mode using the same rules as
``create_skill`` and persisted with ``status="pending"`` under the user's isolated
data directory until the user explicitly approves, edits, or dismisses them.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any

from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.tools.orin.skills_admin import build_validated_skill_command
from agentos.agentic.tools.public_names import PUBLIC_NAMES, internal_name
from agentos.file_lock import file_lock
from agentos.installation.paths import orin_paths
from agentos.skills.models import SkillScope, SkillSource
from agentos.skills.registry import SkillNotFound
from agentos.skills.service import SKILL_RUNTIME_TOOLS, SkillValidationError, _id

ResourceNotFoundError = SkillNotFound

KNOWN_ORIN_TOOLS: frozenset[str] = (
    frozenset(SKILL_RUNTIME_TOOLS)
    | frozenset(PUBLIC_NAMES.keys())
    | frozenset(PUBLIC_NAMES.values())
)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|sk-[A-Za-z0-9]{10,}|-----BEGIN)"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|policy|prompts|rules)|"
    r"<\s*/?\s*system\s*>|system\s+prompt|developer\s+message)"
)
_PERMISSION_GRANT_RE = re.compile(
    r"(?ix)(?:grant\s+(?:all\s+)?permissions?|bypass[_\s-]*permissions?|"
    r"full[_\s-]*autonomy|allow\s+all\s+commands|sudo\s+nopasswd)"
)
_WORKFLOW_HEADING_RE = re.compile(r"(?im)^(?:#{1,6}\s*|\*\*|[-*]\s*\*\*)\s*workflow\b")
_VALIDATION_HEADING_RE = re.compile(r"(?im)^(?:#{1,6}\s*|\*\*|[-*]\s*\*\*)\s*validation\b")
_SAFE_USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCK = threading.Lock()
_LOCK_WAIT_SECONDS = 5.0


def _safe_user_segment(user_id: str) -> str:
    cleaned = str(user_id or "").strip()
    if not cleaned:
        raise ValueError("user_id is required")
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]
    prefix = re.sub(r"[^a-z0-9_-]+", "_", cleaned.lower()).strip("_")[:32] or "user"
    return f"{prefix}_{digest}"


def _clean_str_list(raw: object, *, field_name: str, require_non_empty: bool, max_items: int = 24) -> list[str]:
    if raw is None:
        items: list[str] = []
    elif isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]
    else:
        raise SkillValidationError(f"Field '{field_name}' must be a list of strings.")
    deduped = list(dict.fromkeys(items))[:max_items]
    if require_non_empty and not deduped:
        raise SkillValidationError(f"A high-quality Skill requires {field_name}.")
    return deduped


def _check_text_safety(text: str, *, field_name: str) -> None:
    if _SECRET_RE.search(text):
        raise SkillValidationError(f"Skill {field_name} contains secret or credential material.")
    if _PROMPT_INJECTION_RE.search(text):
        raise SkillValidationError(f"Skill {field_name} violates the prompt injection policy.")
    if _PERMISSION_GRANT_RE.search(text):
        raise SkillValidationError(f"Skill {field_name} cannot grant or escalate permissions.")


def _verify_custom_target_skill(user_id: str, target_skill_id: str, skill_library: object | None) -> None:
    if skill_library is None:
        raise SkillValidationError("Cannot validate edit proposal without a skill library.")
    clean_target = str(target_skill_id or "").strip()
    if not clean_target:
        raise SkillValidationError("Edit proposal requires a non-empty target_skill_id.")
    registry_for = getattr(skill_library, "registry_for", None)
    if callable(registry_for):
        try:
            resolved = registry_for(user_id).resolve(clean_target, scope=SkillScope.USER)
        except Exception as exc:
            raise SkillValidationError(f"Target custom skill '{clean_target}' was not found for user.") from exc
        if getattr(resolved, "source", None) not in {SkillSource.CUSTOM, "custom"}:
            raise SkillValidationError(f"Target skill '{clean_target}' is not a user custom skill.")
        return
    getter = getattr(skill_library, "get", None)
    if callable(getter):
        try:
            detail = getter({"user_id": user_id, "skill_id": clean_target})
        except Exception as exc:
            raise SkillValidationError(f"Target custom skill '{clean_target}' was not found for user.") from exc
        if not isinstance(detail, Mapping) or str(detail.get("source") or "") != "custom":
            raise SkillValidationError(f"Target skill '{clean_target}' is not a user custom skill.")
        return
    raise SkillValidationError("Skill library does not support target skill verification.")


def validate_skill_proposal(
    values: Mapping[str, Any],
    *,
    user_id: str,
    skill_library: object | None = None,
    available_tools: Collection[str] | None = None,
) -> dict[str, Any]:
    """Dry-run validate a candidate skill proposal using ``build_validated_skill_command`` and safety rules."""
    if not isinstance(values, Mapping):
        raise SkillValidationError("Skill proposal must be an object.")
    clean_user = str(user_id or "").strip()
    if not clean_user:
        raise SkillValidationError("user_id is required.")

    action = str(values.get("action") or "create").strip().lower()
    if action not in {"create", "edit"}:
        raise SkillValidationError(f"Unsupported skill proposal action '{action}'.")

    raw_target = values.get("target_skill_id")
    target_skill_id = str(raw_target).strip() if isinstance(raw_target, str) and raw_target.strip() else None
    if action == "edit":
        if not target_skill_id:
            raise SkillValidationError("Edit skill proposal requires target_skill_id.")
        _verify_custom_target_skill(clean_user, target_skill_id, skill_library)
    else:
        target_skill_id = None

    when_to_use = _clean_str_list(values.get("when_to_use"), field_name="when_to_use", require_non_empty=True)
    when_not_to_use = _clean_str_list(
        values.get("when_not_to_use"), field_name="when_not_to_use", require_non_empty=True
    )
    requires_tools_raw = _clean_str_list(
        values.get("requires_tools"), field_name="requires_tools", require_non_empty=False
    )
    tags = _clean_str_list(values.get("tags"), field_name="tags", require_non_empty=False)

    effective_tools: set[str] = set(KNOWN_ORIN_TOOLS)
    if available_tools is not None:
        effective_tools |= {str(t) for t in available_tools}

    candidate_input = {
        "name": str(values.get("name") or "").strip(),
        "description": str(values.get("description") or "").strip(),
        "instructions": str(values.get("instructions") or "").strip(),
        "when_to_use": when_to_use,
        "when_not_to_use": when_not_to_use,
        "requires_tools": requires_tools_raw,
        "tags": tags,
    }
    try:
        command = build_validated_skill_command(
            candidate_input,
            user_id=clean_user,
            available_tools=effective_tools,
            require_complete=True,
        )
    except AgentToolError as exc:
        raise SkillValidationError(str(exc)) from exc

    name = str(command["name"]).strip()
    description = str(command["description"]).strip()
    instructions = str(command["instructions"]).strip()
    if not _WORKFLOW_HEADING_RE.search(instructions) or not _VALIDATION_HEADING_RE.search(instructions):
        raise SkillValidationError("Skill instructions must include explicit Workflow and Validation sections.")
    if len(name) > 128:
        raise SkillValidationError("Skill name exceeds 128 characters.")
    if len(description) > 2000:
        raise SkillValidationError("Skill description exceeds 2000 characters.")
    if len(instructions) > 32000:
        raise SkillValidationError("Skill instructions exceed 32000 characters.")
    try:
        _id(name)
    except ValueError as exc:
        raise SkillValidationError(str(exc)) from exc

    _check_text_safety(name, field_name="name")
    _check_text_safety(description, field_name="description")
    _check_text_safety(instructions, field_name="instructions")
    for item in when_to_use:
        _check_text_safety(item, field_name="when_to_use")
    for item in when_not_to_use:
        _check_text_safety(item, field_name="when_not_to_use")
    for tag in tags:
        _check_text_safety(tag, field_name="tags")

    normalized_tools: list[str] = []
    for tool_name in requires_tools_raw:
        mapped = internal_name(tool_name)
        canonical = mapped if mapped in SKILL_RUNTIME_TOOLS else tool_name
        if canonical not in normalized_tools:
            normalized_tools.append(canonical)

    return {
        "action": action,
        "target_skill_id": target_skill_id,
        "name": name,
        "description": description,
        "when_to_use": when_to_use,
        "when_not_to_use": when_not_to_use,
        "requires_tools": normalized_tools,
        "tags": tags,
        "instructions": instructions,
    }


class SkillProposalStore:
    """Atomic per-user persistence for pending/approved/dismissed SKILL.md proposals."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None

    def _path_for(self, user_id: str) -> Path:
        root = self._base_dir if self._base_dir is not None else orin_paths().data
        return root / "users" / _safe_user_segment(user_id) / "skill_proposals.json"

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, Mapping)]

    @staticmethod
    def _write_file(path: Path, items: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def list_proposals(self, user_id: str, *, status: str | None = "pending") -> list[dict[str, Any]]:
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                return []
            items = self._read_file(path)
        clean_status = str(status or "").strip().lower()
        if clean_status and clean_status != "all":
            items = [item for item in items if str(item.get("status") or "").lower() == clean_status]
        return items

    def get_proposal(self, user_id: str, proposal_id: str) -> dict[str, Any]:
        target_id = str(proposal_id or "").strip()
        if not target_id:
            raise ResourceNotFoundError("skill proposal was not found")
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                raise ResourceNotFoundError("skill proposal was not found")
            items = self._read_file(path)
        for item in items:
            if str(item.get("proposal_id") or "") == target_id:
                return dict(item)
        raise ResourceNotFoundError(f"skill proposal '{target_id}' was not found")

    def propose(
        self,
        user_id: str,
        values: Mapping[str, Any],
        *,
        conversation_id: str | None = None,
        skill_library: object | None = None,
        available_tools: Collection[str] | None = None,
    ) -> dict[str, Any]:
        validated = validate_skill_proposal(
            values,
            user_id=user_id,
            skill_library=skill_library,
            available_tools=available_tools,
        )
        now_iso = datetime.now(UTC).isoformat()
        conv_id = (
            str(conversation_id or values.get("conversation_id") or "").strip() or None
        )
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                raise RuntimeError("Could not acquire lock for skill proposals store.")
            items = self._read_file(path)
            candidate_slug = _id(validated["name"])
            for existing in items:
                if (
                    str(existing.get("status") or "") == "pending"
                    and _id(str(existing.get("name") or "x")) == candidate_slug
                    and str(existing.get("action") or "create") == validated["action"]
                ):
                    existing.update(validated)
                    existing["updated_at"] = now_iso
                    if conv_id is not None:
                        existing["conversation_id"] = conv_id
                    self._write_file(path, items)
                    return dict(existing)

            record: dict[str, Any] = {
                "proposal_id": f"sprop_{uuid.uuid4().hex[:16]}",
                "action": validated["action"],
                "target_skill_id": validated["target_skill_id"],
                "name": validated["name"],
                "description": validated["description"],
                "when_to_use": validated["when_to_use"],
                "when_not_to_use": validated["when_not_to_use"],
                "requires_tools": validated["requires_tools"],
                "tags": validated["tags"],
                "instructions": validated["instructions"],
                "status": "pending",
                "created_at": now_iso,
                "updated_at": now_iso,
                "conversation_id": conv_id,
            }
            items.append(record)
            self._write_file(path, items)
            return dict(record)

    def propose_safe(
        self,
        user_id: str,
        values: Mapping[str, Any],
        *,
        conversation_id: str | None = None,
        skill_library: object | None = None,
        available_tools: Collection[str] | None = None,
    ) -> dict[str, Any] | None:
        try:
            return self.propose(
                user_id,
                values,
                conversation_id=conversation_id,
                skill_library=skill_library,
                available_tools=available_tools,
            )
        except Exception:  # noqa: BLE001 - invalid proposals are silently dropped
            return None

    def update_proposal(
        self,
        user_id: str,
        proposal_id: str,
        patch: Mapping[str, Any],
        *,
        skill_library: object | None = None,
        available_tools: Collection[str] | None = None,
    ) -> dict[str, Any]:
        target_id = str(proposal_id or "").strip()
        if not target_id:
            raise ResourceNotFoundError("skill proposal was not found")
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                raise ResourceNotFoundError("skill proposal was not found")
            items = self._read_file(path)
            target = next((item for item in items if str(item.get("proposal_id") or "") == target_id), None)
            if target is None:
                raise ResourceNotFoundError(f"skill proposal '{target_id}' was not found")

            merged: dict[str, Any] = dict(target)
            editable_fields = (
                "name",
                "description",
                "instructions",
                "when_to_use",
                "when_not_to_use",
                "requires_tools",
                "tags",
            )
            for field_name in editable_fields:
                if field_name in patch and patch[field_name] is not None:
                    merged[field_name] = patch[field_name]

            validated = validate_skill_proposal(
                merged,
                user_id=user_id,
                skill_library=skill_library,
                available_tools=available_tools,
            )
            target.update(validated)
            target["updated_at"] = datetime.now(UTC).isoformat()
            self._write_file(path, items)
            return dict(target)

    def approve_proposal(
        self,
        user_id: str,
        proposal_id: str,
        *,
        patch: Mapping[str, Any] | None = None,
        skill_library: object,
        available_tools: Collection[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        target_id = str(proposal_id or "").strip()
        if not target_id:
            raise ResourceNotFoundError("skill proposal was not found")
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                raise ResourceNotFoundError("skill proposal was not found")
            items = self._read_file(path)
            target = next((item for item in items if str(item.get("proposal_id") or "") == target_id), None)
            if target is None:
                raise ResourceNotFoundError(f"skill proposal '{target_id}' was not found")
            if str(target.get("status") or "") == "dismissed":
                raise SkillValidationError("Cannot approve a dismissed skill proposal.")

            merged: dict[str, Any] = dict(target)
            if patch:
                editable_fields = (
                    "name",
                    "description",
                    "instructions",
                    "when_to_use",
                    "when_not_to_use",
                    "requires_tools",
                    "tags",
                )
                for field_name in editable_fields:
                    if field_name in patch and patch[field_name] is not None:
                        merged[field_name] = patch[field_name]

            validated = validate_skill_proposal(
                merged,
                user_id=user_id,
                skill_library=skill_library,
                available_tools=available_tools,
            )

            if validated["action"] == "edit" and validated["target_skill_id"]:
                update_fn = getattr(skill_library, "update")
                published = update_fn(
                    {
                        "user_id": user_id,
                        "skill_id": validated["target_skill_id"],
                        "name": validated["name"],
                        "description": validated["description"],
                        "tags": validated["tags"],
                        "instructions": validated["instructions"],
                        "when_to_use": validated["when_to_use"],
                        "when_not_to_use": validated["when_not_to_use"],
                        "requires_tools": validated["requires_tools"],
                        "idempotency_key": idempotency_key,
                        "purpose": "skills.update",
                    }
                )
            else:
                create_fn = getattr(skill_library, "create")
                try:
                    published = create_fn(
                        {
                            "user_id": user_id,
                            "name": validated["name"],
                            "description": validated["description"],
                            "version": "1.0.0",
                            "tags": validated["tags"],
                            "instructions": validated["instructions"],
                            "when_to_use": validated["when_to_use"],
                            "when_not_to_use": validated["when_not_to_use"],
                            "requires_tools": validated["requires_tools"],
                            "idempotency_key": idempotency_key,
                            "purpose": "skills.create",
                        }
                    )
                except SkillNotFound as exc:
                    raise SkillValidationError(str(exc)) from exc

            target.update(validated)
            target["status"] = "approved"
            target["updated_at"] = datetime.now(UTC).isoformat()
            self._write_file(path, items)
            return {"proposal": dict(target), "skill": published}

    def dismiss_proposal(self, user_id: str, proposal_id: str) -> dict[str, Any]:
        target_id = str(proposal_id or "").strip()
        if not target_id:
            raise ResourceNotFoundError("skill proposal was not found")
        path = self._path_for(user_id)
        with _LOCK, file_lock(path, wait_seconds=_LOCK_WAIT_SECONDS) as locked:
            if not locked:
                raise ResourceNotFoundError("skill proposal was not found")
            items = self._read_file(path)
            target = next((item for item in items if str(item.get("proposal_id") or "") == target_id), None)
            if target is None:
                raise ResourceNotFoundError(f"skill proposal '{target_id}' was not found")
            target["status"] = "dismissed"
            target["updated_at"] = datetime.now(UTC).isoformat()
            self._write_file(path, items)
            return {"proposal_id": target_id, "status": "dismissed"}


__all__ = [
    "KNOWN_ORIN_TOOLS",
    "ResourceNotFoundError",
    "SkillProposalStore",
    "validate_skill_proposal",
]
