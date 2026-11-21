"""Settings API for registered orchestration, effort, context and quota keys (WS-E0)."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.settings.loader import EffectiveSettings, load_effective_settings
from agentos.settings.paths import SettingsPaths
from agentos.settings.registry import REGISTRY, resolve, validate_change
from agentos.settings.writer import update_settings_file

from .security import AuthenticatedPrincipal, AuthorizationError
from .settings_routes import SettingsRejected

_MISSING = object()


class OrchestrationSettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=200)
    project_root: str | None = Field(default=None, max_length=4096)
    group: str | None = Field(default=None, max_length=120)
    prefix: str | None = Field(default=None, max_length=120)


def _get_nested(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not part:
            continue
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [part for part in dotted.split(".") if part]
    current: dict[str, Any] = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _remove_nested(data: dict[str, Any], dotted: str) -> None:
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        return
    parents: list[tuple[dict[str, Any], str]] = []
    current: Any = data
    for part in parts[:-1]:
        nxt = current.get(part) if isinstance(current, dict) else None
        if not isinstance(nxt, dict):
            return
        parents.append((current, part))
        current = nxt
    if isinstance(current, dict) and parts[-1] in current:
        current.pop(parts[-1], None)
        for parent, key in reversed(parents):
            child = parent.get(key)
            if isinstance(child, dict) and not child:
                parent.pop(key, None)
            else:
                break


def _user_override_for(settings: EffectiveSettings, path: str) -> tuple[bool, Any]:
    found = False
    value: Any = None
    for layer in settings.layers:
        if layer.name != "user":
            continue
        raw = _get_nested(layer.data, path)
        if raw is not _MISSING and raw is not None:
            found = True
            value = raw
    return found, value


def orchestration_report(
    root: Path | None,
    trusted: bool,
    *,
    group: str | None = None,
    prefix: str | None = None,
) -> dict[str, Any]:
    settings = load_effective_settings(root, trusted=trusted)
    allowed_groups = {item.strip() for item in group.split(",") if item.strip()} if group else None
    clean_prefix = prefix.strip() if prefix else None

    items: list[dict[str, Any]] = []
    for key in REGISTRY.values():
        if allowed_groups is not None and key.group not in allowed_groups:
            continue
        if clean_prefix and not key.path.startswith(clean_prefix):
            continue
        resolved = resolve(settings, key.path)
        has_user_override, user_value = _user_override_for(settings, key.path)
        items.append(
            {
                "path": key.path,
                "type": key.type,
                "group": key.group,
                "label": key.label,
                "description": key.description,
                "scope": key.scope.value,
                "default": copy.deepcopy(key.default),
                "value": copy.deepcopy(resolved.value),
                "layer": resolved.layer,
                "user_value": copy.deepcopy(user_value) if has_user_override else None,
                "has_user_override": has_user_override,
                "minimum": key.minimum,
                "maximum": key.maximum,
                "choices": list(key.choices) if key.choices is not None else None,
                "item_type": key.item_type,
                "value_minimum": key.value_minimum,
                "value_maximum": key.value_maximum,
                "locked": resolved.layer == "managed",
            }
        )

    return {
        "project_root": str(root) if root is not None else None,
        "trusted": trusted,
        "items": items,
    }


def apply_orchestration_changes(root: Path | None, changes: Mapping[str, Any]) -> None:
    validated: list[tuple[str, Any]] = []
    problems: list[str] = []

    for path, raw_value in changes.items():
        if path not in REGISTRY:
            problems.append(f"Configuração desconhecida: {path}")
            continue
        if raw_value is None:
            validated.append((path, None))
            continue
        try:
            validated.append((path, validate_change(path, raw_value)))
        except ValueError as error:
            problems.append(str(error))

    if problems:
        raise SettingsRejected("invalid_setting", problems)
    if not validated:
        return

    target = SettingsPaths(root).writable_file("user")

    def mutate(data: dict[str, Any]) -> None:
        for path, value in validated:
            if value is None:
                _remove_nested(data, path)
                continue
            if path.startswith("effort.") and isinstance(data.get("effort"), str):
                if data.get("effortLevel") is None:
                    data["effortLevel"] = data["effort"]
                data["effort"] = {}
            _set_nested(data, path, copy.deepcopy(value))

    update_settings_file(target, mutate)


def register_orchestration_settings_routes(
    app: FastAPI,
    services,
    principal_for: Callable[..., AuthenticatedPrincipal],
    conversation_root: Callable[[str, AuthenticatedPrincipal], Path],
) -> None:
    def owner(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        services.capabilities.require("host_folders")
        current = principal_for(request, mutable=mutable)
        services.security.authorize(
            current,
            action="settings.write" if mutable else "settings.read",
            resource_id=None,
            purpose=purpose,
        )
        if mutable and current.credential_kind == "cli_token":
            raise AuthorizationError("orin code does not change settings through the interface's API")
        return current

    def folder_of(
        principal: AuthenticatedPrincipal,
        conversation_id: str | None,
        project_root: str | None,
    ) -> tuple[Path | None, bool]:
        from agentos.permissions.trust import WorkspaceTrustStore

        if conversation_id:
            root = Path(conversation_root(conversation_id, principal))
            return root, WorkspaceTrustStore().is_trusted(principal.user_id, root)
        if project_root and project_root.strip():
            candidate = Path(project_root.strip())
            if candidate.is_dir():
                return candidate, WorkspaceTrustStore().is_trusted(principal.user_id, candidate)
        return None, False

    @app.get("/v1/settings/orchestration")
    async def get_orchestration_settings(
        request: Request,
        conversation_id: str | None = None,
        project_root: str | None = None,
        group: str | None = None,
        prefix: str | None = None,
    ) -> JSONResponse:
        def _sync() -> dict[str, object]:
            principal = owner(request, mutable=False, purpose="settings.orchestration")
            root, trusted = folder_of(principal, conversation_id, project_root)
            return orchestration_report(root, trusted, group=group, prefix=prefix)

        return JSONResponse(await run_in_threadpool(_sync))

    @app.put("/v1/settings/orchestration")
    async def put_orchestration_settings(
        payload: OrchestrationSettingsChange,
        request: Request,
    ) -> JSONResponse:
        def _sync() -> dict[str, object]:
            principal = owner(request, mutable=True, purpose="settings.orchestration")
            root, trusted = folder_of(principal, payload.conversation_id, payload.project_root)
            apply_orchestration_changes(root, payload.changes)
            return orchestration_report(root, trusted, group=payload.group, prefix=payload.prefix)

        return JSONResponse(await run_in_threadpool(_sync))


__all__ = [
    "OrchestrationSettingsChange",
    "apply_orchestration_changes",
    "orchestration_report",
    "register_orchestration_settings_routes",
]
