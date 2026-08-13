"""Production ASGI entry point composed with durable adapters."""

from agentos.bootstrap.production import ProductionSettings, compose_production_services, create_production_app
from agentos.persistence.sqlite import create_local_engine

settings = ProductionSettings()
engine = create_local_engine(settings.DATABASE_URL)
app = create_production_app(
    settings,
    services=compose_production_services(
        engine,
        localhost_trust_enabled=settings.LOCALHOST_TRUST_ENABLED,
        activity_cursor_secret=settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None,
    ),
)
