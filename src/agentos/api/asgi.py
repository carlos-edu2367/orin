"""Production ASGI entry point composed with durable adapters."""

from sqlalchemy import create_engine

from agentos.bootstrap.production import ProductionSettings, compose_production_services, create_production_app

settings = ProductionSettings()
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
app = create_production_app(
    settings,
    services=compose_production_services(
        engine,
        localhost_trust_enabled=settings.LOCALHOST_TRUST_ENABLED,
        activity_cursor_secret=settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None,
    ),
)
