"""PostgreSQL repository for sanitized, credential-scoped model catalogs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.provider_catalog.models import PricingSummary, ProviderCatalogContext, ProviderModelRecord

from .schema import provider_configurations, provider_model_catalog, provider_model_favorites
from agentos.persistence.provider_secrets import ProviderSecretCipher


class PostgresProviderCatalogRepository:
    def __init__(self, engine: Engine, *, cipher: ProviderSecretCipher | None = None) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()

    def credential(self, context: ProviderCatalogContext, provider: str) -> dict[str, object] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(provider_configurations.c.enabled, provider_configurations.c.api_key, provider_configurations.c.api_key_ciphertext, provider_configurations.c.base_url).where(
                    provider_configurations.c.user_id == context.user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
        if row is None:
            return None
        value = dict(row)
        encrypted = value.get("api_key_ciphertext") or value.get("api_key")
        value["api_key"] = self._cipher.decrypt(str(encrypted))
        return value

    def replace(self, context: ProviderCatalogContext, provider: str, records: list[ProviderModelRecord], refreshed_at: datetime) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            configuration = connection.execute(select(
                provider_configurations.c.enabled, provider_configurations.c.base_url,
            ).where(
                provider_configurations.c.user_id == context.user_id,
                provider_configurations.c.provider == provider,
            )).mappings().first()
            if configuration is None or configuration["enabled"] is not True:
                raise LookupError("provider is not enabled")
            base_url = configuration["base_url"]
            custom_ids = set(connection.execute(select(provider_model_catalog.c.model_id).where(
                provider_model_catalog.c.user_id == context.user_id,
                provider_model_catalog.c.provider == provider,
                provider_model_catalog.c.is_custom.is_(True),
            )).scalars())
            connection.execute(delete(provider_model_catalog).where(
                provider_model_catalog.c.user_id == context.user_id,
                provider_model_catalog.c.provider == provider,
                provider_model_catalog.c.is_custom.is_(False),
            ))
            records = [record for record in records if record.model_id not in custom_ids]
            if records:
                connection.execute(insert(provider_model_catalog), [
                    {
                        "user_id": context.user_id,
                        "provider": provider,
                        "model_id": record.model_id,
                        "catalog_base_url": base_url,
                        "display_name": record.display_name,
                        "context_window": record.context_window,
                        "capabilities": list(record.capabilities),
                        "input_modalities": list(record.input_modalities),
                        "output_modalities": list(record.output_modalities),
                        "input_per_million": record.pricing.input_per_million if record.pricing else None,
                        "output_per_million": record.pricing.output_per_million if record.pricing else None,
                        "route_kind": record.route_kind,
                        "is_custom": False,
                        "refreshed_at": record.refreshed_at,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for record in records
                ])
            connection.execute(update(provider_configurations).where(
                provider_configurations.c.user_id == context.user_id,
                provider_configurations.c.provider == provider,
            ).values(catalog_refreshed_at=refreshed_at, updated_at=now))

    def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False) -> list[ProviderModelRecord]:
        statement = select(
            provider_model_catalog,
            provider_model_favorites.c.id.label("favorite_id"),
        ).join(
            provider_configurations,
            (provider_configurations.c.user_id == provider_model_catalog.c.user_id)
            & (provider_configurations.c.provider == provider_model_catalog.c.provider),
        ).outerjoin(
            provider_model_favorites,
            (provider_model_favorites.c.user_id == provider_model_catalog.c.user_id)
            & (provider_model_favorites.c.provider == provider_model_catalog.c.provider)
            & (provider_model_favorites.c.model_id == provider_model_catalog.c.model_id),
        ).where(
            provider_model_catalog.c.user_id == context.user_id,
            provider_model_catalog.c.provider == provider,
            provider_configurations.c.enabled.is_(True),
            (
                provider_model_catalog.c.is_custom.is_(True)
                | provider_configurations.c.catalog_refreshed_at.is_not(None)
            ),
            (
                (provider_model_catalog.c.catalog_base_url == provider_configurations.c.base_url)
                | (
                    provider_model_catalog.c.catalog_base_url.is_(None)
                    & provider_configurations.c.base_url.is_(None)
                )
            ),
        )
        if favorites_only:
            statement = statement.where(provider_model_favorites.c.id.is_not(None))
        statement = statement.order_by(provider_model_favorites.c.id.is_(None), provider_model_catalog.c.display_name, provider_model_catalog.c.model_id)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            ProviderModelRecord(
                provider=str(row["provider"]), model_id=str(row["model_id"]), display_name=str(row["display_name"]),
                context_window=row["context_window"], capabilities=tuple(row["capabilities"]),
                input_modalities=tuple(row["input_modalities"]), output_modalities=tuple(row["output_modalities"]),
                pricing=PricingSummary(row["input_per_million"], row["output_per_million"])
                if row["input_per_million"] is not None or row["output_per_million"] is not None else None,
                # SQLite stores ``DateTime(timezone=True)`` values without the
                # offset.  Catalog records are always written in UTC, so put
                # that invariant back at this persistence boundary before the
                # domain model validates it. PostgreSQL values stay untouched.
                refreshed_at=_utc_datetime(row["refreshed_at"]), is_favorite=row["favorite_id"] is not None,
                route_kind=str(row.get("route_kind") or "model"),
                is_custom=bool(row.get("is_custom")),
            )
            for row in rows
        ]

    def set_favorite(self, context: ProviderCatalogContext, provider: str, model_id: str, favorite: bool) -> ProviderModelRecord:
        with self._engine.begin() as connection:
            known = connection.execute(select(provider_model_catalog.c.id).where(
                provider_model_catalog.c.user_id == context.user_id,
                provider_model_catalog.c.provider == provider,
                provider_model_catalog.c.model_id == model_id,
            )).scalar_one_or_none()
            if known is None:
                raise LookupError(model_id)
            predicate = (
                (provider_model_favorites.c.user_id == context.user_id)
                & (provider_model_favorites.c.provider == provider)
                & (provider_model_favorites.c.model_id == model_id)
            )
            if favorite:
                exists = connection.execute(select(provider_model_favorites.c.id).where(predicate)).scalar_one_or_none()
                if exists is None:
                    connection.execute(insert(provider_model_favorites).values(
                        user_id=context.user_id, provider=provider, model_id=model_id, created_at=datetime.now(UTC),
                    ))
            else:
                connection.execute(delete(provider_model_favorites).where(predicate))
        return next(item for item in self.list(context, provider) if item.model_id == model_id)

    def add_custom(self, context: ProviderCatalogContext, provider: str, record: ProviderModelRecord) -> ProviderModelRecord:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            configuration = connection.execute(select(
                provider_configurations.c.enabled, provider_configurations.c.base_url,
            ).where(
                provider_configurations.c.user_id == context.user_id,
                provider_configurations.c.provider == provider,
            )).mappings().first()
            if configuration is None or configuration["enabled"] is not True:
                raise LookupError("provider is not enabled")
            base_url = configuration["base_url"]
            existing = connection.execute(select(provider_model_catalog.c.id).where(
                provider_model_catalog.c.user_id == context.user_id,
                provider_model_catalog.c.provider == provider,
                provider_model_catalog.c.model_id == record.model_id,
            )).scalar_one_or_none()
            values = {
                "catalog_base_url": base_url,
                "display_name": record.display_name,
                "context_window": record.context_window,
                "capabilities": list(record.capabilities),
                "input_modalities": list(record.input_modalities),
                "output_modalities": list(record.output_modalities),
                "input_per_million": None,
                "output_per_million": None,
                "route_kind": record.route_kind,
                "is_custom": True,
                "refreshed_at": record.refreshed_at,
                "updated_at": now,
            }
            if existing is None:
                connection.execute(insert(provider_model_catalog).values(
                    user_id=context.user_id, provider=provider, model_id=record.model_id,
                    created_at=now, **values,
                ))
            else:
                connection.execute(update(provider_model_catalog).where(
                    provider_model_catalog.c.id == existing,
                ).values(**values))
        return next(item for item in self.list(context, provider) if item.model_id == record.model_id)

    def remove_custom(self, context: ProviderCatalogContext, provider: str, model_id: str) -> None:
        with self._engine.begin() as connection:
            known = connection.execute(select(provider_model_catalog.c.id).where(
                provider_model_catalog.c.user_id == context.user_id,
                provider_model_catalog.c.provider == provider,
                provider_model_catalog.c.model_id == model_id,
                provider_model_catalog.c.is_custom.is_(True),
            )).scalar_one_or_none()
            if known is None:
                raise LookupError(model_id)
            connection.execute(delete(provider_model_favorites).where(
                provider_model_favorites.c.user_id == context.user_id,
                provider_model_favorites.c.provider == provider,
                provider_model_favorites.c.model_id == model_id,
            ))
            connection.execute(delete(provider_model_catalog).where(provider_model_catalog.c.id == known))


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("catalog refreshed_at is invalid")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = ["PostgresProviderCatalogRepository"]
