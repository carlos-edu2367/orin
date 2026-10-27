"""REST routes for Orin Friend: singleton conversation ensure, Glass-Box persona & SOUL.md sync, and affective memories."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.api.contracts import ApplicationNotFoundError, ApplicationValidationError
from agentos.memory.friend_persona import (
    DEFAULT_TRAITS,
    VALID_BOND_STAGES,
    FriendPersonaStore,
    validate_style_note,
)
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore

_BOND_STAGE_ALIASES: dict[str, str] = {
    "acquaintance": "acquaintance",
    "buddy": "buddy",
    "close_friend": "close_friend",
    "confidant": "confidant",
    "conhecido": "acquaintance",
    "parceiro": "buddy",
    "amigo_proximo": "close_friend",
    "confidente": "confidant",
}


class UpdateFriendPersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traits: dict[str, float] | None = None
    bond_stage: str | None = Field(default=None, max_length=64)
    affinity_score: float | None = Field(default=None, ge=0.0, le=100.0)
    current_mood: str | None = Field(default=None, max_length=120)
    style_notes: list[str] | str | None = None


def _engine_from_services(services: Any) -> Any:
    conv = getattr(services, "conversation_application", None)
    if conv is None:
        return None
    engine = getattr(conv, "_engine", None)
    if engine is not None:
        return engine
    store = getattr(conv, "store", None)
    return getattr(store, "_engine", None)


def _persona_store_for(services: Any) -> FriendPersonaStore:
    store = getattr(services, "friend_persona", None)
    if store is not None:
        return store
    engine = _engine_from_services(services)
    if engine is None:
        raise RuntimeError("friend_persona service is unavailable")
    created = FriendPersonaStore(engine)
    services.friend_persona = created
    return created


def _memory_store_for(services: Any, user_id: str) -> PostgresAgentMemoryStore:
    factory = getattr(services, "friend_memory_factory", None)
    if callable(factory):
        return factory(user_id)
    engine = _engine_from_services(services)
    if engine is None:
        raise RuntimeError("friend_memory_factory service is unavailable")
    return PostgresAgentMemoryStore(engine, user_id=user_id)


def _normalize_bond_stage(raw_stage: str | None) -> str | None:
    if raw_stage is None:
        return None
    cleaned = str(raw_stage).strip().lower()
    if not cleaned:
        raise ApplicationValidationError("bond_stage cannot be blank")
    resolved = _BOND_STAGE_ALIASES.get(cleaned)
    if resolved is None or resolved not in VALID_BOND_STAGES:
        raise ApplicationValidationError(f"unsupported bond_stage: {raw_stage!r}")
    return resolved


def _normalize_style_notes(raw_notes: Sequence[str] | str | None) -> list[str] | None:
    if raw_notes is None:
        return None
    if isinstance(raw_notes, str):
        lines = [line.strip().lstrip("- ").strip() for line in raw_notes.splitlines() if line.strip()]
        if not lines:
            return []
        raw_items: Sequence[str] = lines
    elif isinstance(raw_notes, Sequence):
        raw_items = [str(item) for item in raw_notes if str(item).strip()]
    else:
        raise ApplicationValidationError("style_notes must be a list of strings or a string")

    validated: list[str] = []
    for item in raw_items:
        try:
            cleaned = validate_style_note(item)
        except ValueError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if cleaned not in validated:
            validated.append(cleaned)
    return validated


def _normalize_traits_payload(raw_traits: Mapping[str, float] | None) -> dict[str, float] | None:
    if raw_traits is None:
        return None
    if not isinstance(raw_traits, Mapping):
        raise ApplicationValidationError("traits must be an object")
    cleaned: dict[str, float] = {}
    for key, val in raw_traits.items():
        if key not in DEFAULT_TRAITS:
            raise ApplicationValidationError(f"unknown trait: {key!r}")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ApplicationValidationError(f"trait {key!r} must be numeric")
        cleaned[str(key)] = float(val)
    return cleaned


def register_friend_routes(app: FastAPI, services: Any, principal_for: Any) -> None:
    @app.post("/v1/conversations/friend/ensure")
    async def ensure_friend_conversation(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=None,
            purpose="conversation.friend.ensure",
        )
        conv_app = getattr(services, "conversation_application", None)
        if conv_app is None or not hasattr(conv_app, "ensure_friend_conversation"):
            raise RuntimeError("conversation_application with ensure_friend_conversation is unavailable")
        persona_store = _persona_store_for(services)

        def _ensure() -> dict[str, object]:
            conv = conv_app.ensure_friend_conversation(principal.user_id)
            persona = persona_store.sync_from_disk_if_modified(principal.user_id)
            soul_path = str(persona_store.soul_file_path(principal.user_id))
            persona_dict = {**persona.to_dict(), "soul_markdown_path": soul_path}
            conv_dict = dict(conv) if isinstance(conv, Mapping) else {}
            return {**conv_dict, "conversation": conv, "persona": persona_dict}

        result = await run_in_threadpool(_ensure)
        return JSONResponse(result, status_code=200)

    def _serialize_friend_memories(raw_memories: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for entry in raw_memories:
            fact = str(entry.get("fact") or entry.get("content") or "")
            salience = float(entry.get("salience") if entry.get("salience") is not None else 0.5)
            items.append(
                {
                    **dict(entry),
                    "id": str(entry.get("id") or entry.get("memory_id") or ""),
                    "memory_id": str(entry.get("id") or entry.get("memory_id") or ""),
                    "kind": str(entry.get("kind") or "episodic"),
                    "fact": fact,
                    "content": fact,
                    "salience": salience,
                    "importance": salience,
                    "emotion_tag": entry.get("emotion_tag"),
                    "created_at": str(entry.get("updated_at") or entry.get("created_at") or ""),
                }
            )
        return items

    @app.get("/v1/friend/persona")
    async def get_friend_persona(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(
            principal,
            action="conversation.list",
            resource_id=None,
            purpose="friend.persona.read",
        )
        persona_store = _persona_store_for(services)
        memory_store = _memory_store_for(services, principal.user_id)

        def _load() -> dict[str, object]:
            persona = persona_store.sync_from_disk_if_modified(principal.user_id)
            soul_path = str(persona_store.soul_file_path(principal.user_id))
            persona_dict = {**persona.to_dict(), "soul_markdown_path": soul_path}
            memories = _serialize_friend_memories(memory_store.list_friend_memories(limit=50))
            return {**persona_dict, "persona": persona_dict, "memories": memories}

        result = await run_in_threadpool(_load)
        return JSONResponse(result, status_code=200)

    @app.post("/v1/friend/soul/sync")
    async def sync_friend_soul(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=None,
            purpose="friend.persona.sync",
        )
        persona_store = _persona_store_for(services)

        def _sync() -> dict[str, object]:
            persona = persona_store.sync_from_disk_if_modified(principal.user_id)
            soul_path = str(persona_store.soul_file_path(principal.user_id))
            persona_dict = {**persona.to_dict(), "soul_markdown_path": soul_path}
            return {**persona_dict, "persona": persona_dict}

        result = await run_in_threadpool(_sync)
        return JSONResponse(result, status_code=200)

    @app.get("/v1/friend/memories")
    async def list_friend_memories(request: Request) -> JSONResponse:
        principal = principal_for(request)
        services.security.authorize(
            principal,
            action="conversation.list",
            resource_id=None,
            purpose="friend.memories.list",
        )
        memory_store = _memory_store_for(services, principal.user_id)
        raw = await run_in_threadpool(lambda: memory_store.list_friend_memories(limit=50))
        items = _serialize_friend_memories(raw)
        return JSONResponse({"items": items, "memories": items}, status_code=200)

    @app.put("/v1/friend/persona")
    async def update_friend_persona(
        payload: UpdateFriendPersonaRequest,
        request: Request,
    ) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=None,
            purpose="friend.persona.update",
        )
        traits = _normalize_traits_payload(payload.traits)
        bond_stage = _normalize_bond_stage(payload.bond_stage)
        style_notes = _normalize_style_notes(payload.style_notes)
        mood = payload.current_mood
        if mood is not None:
            cleaned_mood = str(mood).strip()
            if not cleaned_mood:
                raise ApplicationValidationError("current_mood cannot be blank")
            try:
                validate_style_note(cleaned_mood)
            except ValueError as exc:
                raise ApplicationValidationError(str(exc)) from exc
            mood = cleaned_mood

        persona_store = _persona_store_for(services)

        def _update() -> dict[str, object]:
            try:
                updated = persona_store.update_persona(
                    principal.user_id,
                    traits=traits,
                    style_notes=style_notes,
                    current_mood=mood,
                    bond_stage=bond_stage,
                    affinity_score=payload.affinity_score,
                )
            except ValueError as exc:
                raise ApplicationValidationError(str(exc)) from exc
            soul_path = str(persona_store.soul_file_path(principal.user_id))
            updated_dict = {**updated.to_dict(), "soul_markdown_path": soul_path}
            return {**updated_dict, "persona": updated_dict}

        result = await run_in_threadpool(_update)
        return JSONResponse(result, status_code=200)

    @app.delete("/v1/friend/memories/{memory_id}")
    async def delete_friend_memory(memory_id: str, request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=memory_id,
            purpose="friend.memory.delete",
        )
        clean_id = str(memory_id or "").strip()
        if not clean_id or len(clean_id) > 128:
            raise ApplicationNotFoundError(memory_id)
        memory_store = _memory_store_for(services, principal.user_id)
        deleted = await run_in_threadpool(memory_store.forget, clean_id)
        if not deleted:
            raise ApplicationNotFoundError(clean_id)
        return JSONResponse({"memory_id": clean_id, "deleted": True}, status_code=200)


__all__ = ["UpdateFriendPersonaRequest", "register_friend_routes"]
