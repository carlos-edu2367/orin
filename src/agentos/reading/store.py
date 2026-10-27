"""Durable override for which model performs visual reading on someone's behalf.

This backs the `/v1/settings/vision-model` routes only. The worker reads the
same `vision_model_selections` table through its own already-open engine
(`agentos.workers.chat.ChatWorker._vision_override`) rather than through this
store -- the two never share a connection, but they agree on the table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import media_model_selections, vision_model_selections
from agentos.provider_catalog.modalities import MEDIA_MODALITIES

VALID_MEDIA_MODALITIES: tuple[str, ...] = tuple(dict.fromkeys((*MEDIA_MODALITIES, "stt")))


@dataclass(frozen=True, slots=True)
class VisionModelSelection:
    provider: str
    model_id: str


@dataclass(frozen=True, slots=True)
class MediaModelSelection:
    modality: str
    provider: str
    model_id: str
    voice_id: str | None = None
    reference_audio_path: str | None = None


class PostgresVisionModelSettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, user_id: str) -> VisionModelSelection | None:
        statement = select(vision_model_selections.c.provider, vision_model_selections.c.model_id).where(
            vision_model_selections.c.user_id == user_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return VisionModelSelection(str(row["provider"]), str(row["model_id"])) if row else None

    def set(self, user_id: str, provider: str, model_id: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(vision_model_selections)
                .where(vision_model_selections.c.user_id == user_id)
                .values(provider=provider, model_id=model_id, updated_at=now)
            ).rowcount
            if not updated:
                connection.execute(
                    insert(vision_model_selections).values(user_id=user_id, provider=provider, model_id=model_id, updated_at=now)
                )

    def clear(self, user_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(vision_model_selections).where(vision_model_selections.c.user_id == user_id))


class PostgresMediaModelSettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        try:
            media_model_selections.create(self._engine, checkfirst=True)
        except Exception:
            pass

    def get_all(self, user_id: str) -> dict[str, MediaModelSelection]:
        statement = select(
            media_model_selections.c.modality,
            media_model_selections.c.provider,
            media_model_selections.c.model_id,
        ).where(media_model_selections.c.user_id == user_id)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        result: dict[str, MediaModelSelection] = {}
        for row in rows:
            mod = str(row["modality"]).strip().lower()
            if mod in VALID_MEDIA_MODALITIES:
                result[mod] = MediaModelSelection(
                    modality=mod,
                    provider=str(row["provider"]).strip().lower(),
                    model_id=str(row["model_id"]).strip(),
                )
        return result

    def get(self, user_id: str, modality: str) -> MediaModelSelection | None:
        norm_mod = str(modality or "").strip().lower()
        if norm_mod not in VALID_MEDIA_MODALITIES:
            raise ValueError(f"unsupported media modality: {modality!r}")
        return self.get_all(user_id).get(norm_mod)

    def set(
        self,
        user_id: str,
        modality: str,
        provider: str,
        model_id: str,
        *,
        voice_id: str | None = None,
        reference_audio_path: str | None = None,
    ) -> None:
        _ = (voice_id, reference_audio_path)
        norm_mod = str(modality or "").strip().lower()
        if norm_mod not in VALID_MEDIA_MODALITIES:
            raise ValueError(f"unsupported media modality: {modality!r}")
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(media_model_selections)
                .where(
                    media_model_selections.c.user_id == user_id,
                    media_model_selections.c.modality == norm_mod,
                )
                .values(provider=provider, model_id=model_id, updated_at=now)
            ).rowcount
            if not updated:
                connection.execute(
                    insert(media_model_selections).values(
                        user_id=user_id,
                        modality=norm_mod,
                        provider=provider,
                        model_id=model_id,
                        updated_at=now,
                    )
                )

    def clear(self, user_id: str, modality: str) -> None:
        norm_mod = str(modality or "").strip().lower()
        if norm_mod not in VALID_MEDIA_MODALITIES:
            raise ValueError(f"unsupported media modality: {modality!r}")
        with self._engine.begin() as connection:
            connection.execute(
                delete(media_model_selections).where(
                    media_model_selections.c.user_id == user_id,
                    media_model_selections.c.modality == norm_mod,
                )
            )
