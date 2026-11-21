"""Infrastructure runtime adapters for instruction-vs-memory contradiction lint (WS-D7).

Keeps SQLAlchemy queries and concrete provider transport wiring outside ``agentos.memory``
so the domain memory package stays free of concrete infrastructure imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentos.memory.instruction_lint import (
    InstructionLinter,
    ModelCaller,
    ProviderResolver,
    _choose_lightweight_model,
    _wrap_provider_as_caller,
)
from agentos.persistence.postgres.schema import (
    agent_memories,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
)


def load_active_memories_for_lint(
    engine: Engine,
    user_id: str,
    *,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query active non-friend memories for ``user_id`` (and optional ``project_id``)."""
    predicate = [
        agent_memories.c.user_id == user_id,
        agent_memories.c.status == "active",
        agent_memories.c.superseded_by.is_(None),
        agent_memories.c.scope_type != "friend",
    ]
    if project_id:
        predicate.append(
            (agent_memories.c.scope_type == "user")
            | (agent_memories.c.project_id == project_id)
        )
    else:
        predicate.append(agent_memories.c.scope_type == "user")
    stmt = (
        select(
            agent_memories.c.memory_id,
            agent_memories.c.fact,
            agent_memories.c.kind,
            agent_memories.c.scope_type,
            agent_memories.c.project_id,
            agent_memories.c.status,
            agent_memories.c.superseded_by,
        )
        .where(*predicate)
        .order_by(agent_memories.c.updated_at.desc())
    )
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(stmt).mappings().all()]


def resolve_lightweight_model_caller(engine: Engine | None, user_id: str) -> ModelCaller | None:
    """Resolve a lightweight configured provider/model caller for ``user_id``, or ``None`` if unavailable."""
    if engine is None or not str(user_id or "").strip():
        return None
    try:
        with engine.connect() as connection:
            configs = connection.execute(
                select(
                    provider_configurations.c.provider,
                    provider_configurations.c.base_url,
                    provider_configurations.c.enabled,
                    provider_configurations.c.key_cooldown_seconds,
                    provider_configurations.c.risk_acknowledged_at,
                ).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.enabled.is_(True),
                )
            ).mappings().all()
            if not configs:
                return None

            config_by_provider: dict[str, Mapping[str, Any]] = {}
            for cfg in configs:
                prov = str(cfg.get("provider") or "").strip()
                if not prov:
                    continue
                if prov == "google-antigravity" and not cfg.get("risk_acknowledged_at"):
                    continue
                config_by_provider[prov] = cfg
            if not config_by_provider:
                return None

            enabled_providers = sorted(config_by_provider.keys())
            fav_rows = connection.execute(
                select(
                    provider_model_favorites.c.provider,
                    provider_model_favorites.c.model_id,
                ).where(
                    provider_model_favorites.c.user_id == user_id,
                    provider_model_favorites.c.provider.in_(enabled_providers),
                )
            ).all()
            candidates: list[tuple[str, str]] = [
                (str(r.provider), str(r.model_id))
                for r in fav_rows
                if r.provider and r.model_id and not str(r.model_id).lower().endswith(":batch")
            ]
            if not candidates:
                cat_rows = connection.execute(
                    select(
                        provider_model_catalog.c.provider,
                        provider_model_catalog.c.model_id,
                    )
                    .where(
                        provider_model_catalog.c.user_id == user_id,
                        provider_model_catalog.c.provider.in_(enabled_providers),
                    )
                    .limit(50)
                ).all()
                candidates = [
                    (str(r.provider), str(r.model_id))
                    for r in cat_rows
                    if r.provider and r.model_id and not str(r.model_id).lower().endswith(":batch")
                ]
    except Exception:  # noqa: BLE001
        return None

    chosen = _choose_lightweight_model(candidates)
    if chosen is None:
        return None
    provider_name, model_id = chosen
    cfg = config_by_provider[provider_name]

    try:
        from agentos.agentic.provider_stream import (
            DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            HTTPProviderStreamTransport,
            MultiKeyProviderStreamTransport,
            PROVIDERS_WITH_BASE_URL,
            is_claude_cli_bridge_url,
        )
        from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
        from agentos.provider_credentials.crypto import ProviderSecretCipher

        if provider_name in {"openai-codex", "google-antigravity"}:
            from agentos.providers.claude_cli.provider import ClaudeCliProvider
            from agentos.providers.cli_registry import cli_adapter_for

            cli_adapter = cli_adapter_for(
                provider_name,
                str(cfg.get("base_url") or "") or None,
                risk_acknowledged_at=cfg.get("risk_acknowledged_at")
                if provider_name == "google-antigravity"
                else None,
            )
            if cli_adapter is None:
                return None
            transport = ClaudeCliProvider(
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                model=model_id,
                adapter=cli_adapter,
            )
            return _wrap_provider_as_caller(transport, provider_name, model_id)

        base_url = str(cfg.get("base_url") or "").strip()
        if provider_name == "anthropic" and is_claude_cli_bridge_url(base_url):
            from agentos.providers.claude_cli.provider import ClaudeCliProvider

            transport = ClaudeCliProvider(base_url=base_url, model=model_id)
            return _wrap_provider_as_caller(transport, provider_name, model_id)

        keys = PostgresProviderApiKeyAdapter(
            engine,
            cipher=ProviderSecretCipher.from_environment(required=False),
        )
        first_key = keys.next_available_key(user_id, provider_name)
        if first_key is None:
            if provider_name not in PROVIDERS_WITH_BASE_URL or (
                provider_name == "anthropic" and not is_claude_cli_bridge_url(base_url)
            ):
                return None
            transport = HTTPProviderStreamTransport(
                provider=provider_name,
                base_url=base_url,
                api_key="",
                model=model_id,
            )
            return _wrap_provider_as_caller(transport, provider_name, model_id)

        def _build(api_key: str) -> HTTPProviderStreamTransport:
            return HTTPProviderStreamTransport(
                provider=provider_name,
                base_url=base_url,
                api_key=api_key,
                model=model_id,
            )

        transport = MultiKeyProviderStreamTransport(
            key_pool=keys,
            user_id=user_id,
            provider=provider_name,
            cooldown_seconds=int(cfg.get("key_cooldown_seconds") or 60),
            transport_factory=_build,
            model=model_id,
            base_url=base_url,
        )
        return _wrap_provider_as_caller(transport, provider_name, model_id)
    except Exception:  # noqa: BLE001
        return None


def build_instruction_linter(
    engine: Engine | None,
    *,
    memory_store: Any | None = None,
    model_caller: ModelCaller | None = None,
    provider: Any | None = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    provider_resolver: ProviderResolver | None = None,
    walk_up: bool = True,
) -> InstructionLinter:
    """Build an ``InstructionLinter`` wired with the SQL memory loader and provider resolver."""
    memory_loader = (
        (lambda uid, pid: load_active_memories_for_lint(engine, uid, project_id=pid))
        if engine is not None
        else None
    )
    resolved_provider_resolver = (
        provider_resolver
        if provider_resolver is not None
        else (lambda uid: resolve_lightweight_model_caller(engine, uid))
    )
    return InstructionLinter(
        memory_loader=memory_loader,
        memory_store=memory_store,
        model_caller=model_caller,
        provider=provider,
        provider_name=provider_name,
        model_id=model_id,
        provider_resolver=resolved_provider_resolver,
        walk_up=walk_up,
    )


__all__ = [
    "build_instruction_linter",
    "load_active_memories_for_lint",
    "resolve_lightweight_model_caller",
]
