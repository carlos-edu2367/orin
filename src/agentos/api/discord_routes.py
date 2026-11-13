"""REST routes for Discord (Orin Friend) integration configuration and connection testing."""
from __future__ import annotations

from collections.abc import Mapping
import inspect
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentos.api.friend_routes import engine_from_services
from agentos.discord.bridge_service import DiscordBridgeService
from agentos.discord.gateway_client import DiscordRuntimeStatus
from agentos.discord.rest_client import DiscordApiError, DiscordRestClient
from agentos.discord.store import (
    DiscordBotConfig,
    PostgresDiscordStore,
    normalize_optional_snowflake_id,
    normalize_snowflake_ids,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher

DISCORD_TEST_CONFIRMATION_MESSAGE = (
    "Orin conectado a este PC e pronto para comandos via Orin Friend."
)


class UpdateDiscordConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    bot_token: str | None = Field(default=None, max_length=512)
    allowed_discord_user_ids: list[Any] | str = Field(default_factory=list)
    allowed_channel_ids: list[Any] | str = Field(default_factory=list)
    default_notify_channel_id: str | None = Field(default=None, max_length=64)


class TestDiscordConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token: str | None = Field(default=None, max_length=512)
    discord_user_id: str | None = Field(default=None, max_length=64)
    send_dm: bool = True


def _is_available_service(service: Any) -> bool:
    if service is None:
        return False
    return not service.__class__.__name__.startswith("_Unavailable")


def _discord_store_for(services: Any) -> PostgresDiscordStore:
    store = getattr(services, "discord_store", None)
    if _is_available_service(store):
        return store  # type: ignore[return-value]
    engine = engine_from_services(services)
    if engine is None:
        raise RuntimeError("discord_store service is unavailable")
    cipher = ProviderSecretCipher.from_environment(
        required=not bool(os.getenv("AGENTOS_TEST_POSTGRES_DSN"))
    )
    created = PostgresDiscordStore(engine, cipher)
    services.discord_store = created
    return created


def _discord_bridge_for(services: Any) -> DiscordBridgeService | None:
    bridge = getattr(services, "discord_bridge", None)
    if _is_available_service(bridge):
        return bridge  # type: ignore[return-value]
    try:
        store = _discord_store_for(services)
    except Exception:
        return None
    conv_app = getattr(services, "conversation_application", None)
    if not _is_available_service(conv_app):
        return None
    created = DiscordBridgeService(
        discord_store=store,
        chat_application=conv_app,
        uploads=getattr(services, "uploads", None),
        local_workspaces=getattr(services, "local_workspaces", None),
        project_store=getattr(services, "projects", None),
        provider_catalog=getattr(services, "provider_model_catalog", None),
    )
    services.discord_bridge = created
    return created


def _discord_error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    category: str = "VALIDATION",
    retryable: bool = False,
) -> JSONResponse:
    correlation_id = f"corr_{uuid4().hex}"
    return JSONResponse(
        {
            "detail": message,
            "message": message,
            "error": {
                "code": code,
                "category": category,
                "message_key": code,
                "message": message,
                "correlation_id": correlation_id,
                "retryable": retryable,
                "retry_after": None,
                "details": {
                    "message": message,
                    "problems": [message],
                },
            },
        },
        status_code=status_code,
    )


def _format_bot_username(bot_info: Mapping[str, Any]) -> str | None:
    username = str(bot_info.get("username") or "").strip()
    if not username:
        return None
    discriminator = str(bot_info.get("discriminator") or "").strip()
    if discriminator and discriminator != "0":
        return f"{username}#{discriminator}"
    return username


def _build_discord_state_payload(
    config: DiscordBotConfig | None,
    runtime_status: DiscordRuntimeStatus,
) -> dict[str, Any]:
    bot_application_id = (
        config.bot_application_id
        if config and config.bot_application_id
        else runtime_status.bot_application_id
    )
    bot_user_id = (
        config.bot_user_id
        if config and config.bot_user_id
        else runtime_status.bot_user_id
    )
    bot_username = (
        config.bot_username
        if config and config.bot_username
        else runtime_status.bot_username
    )
    return {
        "enabled": bool(config.enabled) if config else False,
        "has_bot_token": bool(config.has_bot_token) if config else False,
        "masked_token_hint": config.masked_token_hint if config else None,
        "allowed_discord_user_ids": list(config.allowed_discord_user_ids) if config else [],
        "allowed_channel_ids": list(config.allowed_channel_ids) if config else [],
        "default_notify_channel_id": config.default_notify_channel_id if config else None,
        "bot_application_id": bot_application_id,
        "bot_user_id": bot_user_id,
        "bot_username": bot_username,
        "runtime": {
            "status": runtime_status.status,
            "last_connected_at": (
                runtime_status.last_connected_at.isoformat()
                if runtime_status.last_connected_at is not None
                else None
            ),
            "last_error": runtime_status.last_error,
            "bot_user_id": runtime_status.bot_user_id or bot_user_id,
            "bot_username": runtime_status.bot_username or bot_username,
            "latency_ms": runtime_status.latency_ms,
        },
    }


def _runtime_status_for(
    bridge: DiscordBridgeService | None,
    config: DiscordBotConfig | None,
    user_id: str,
) -> DiscordRuntimeStatus:
    if bridge is not None:
        try:
            return bridge.get_user_status(user_id)
        except Exception:
            pass
    return DiscordRuntimeStatus(
        status="disconnected",
        bot_user_id=config.bot_user_id if config else None,
        bot_username=config.bot_username if config else None,
        bot_application_id=config.bot_application_id if config else None,
    )


def _create_rest_client(bridge: DiscordBridgeService | None, token: str) -> Any:
    if bridge is not None:
        factory = getattr(bridge, "_rest_client_factory", None)
        if callable(factory) and DiscordRestClient is _ORIGINAL_DISCORD_REST_CLIENT:
            return factory(token)
    return DiscordRestClient(token)


async def _close_rest_client(client: Any) -> None:
    aclose = getattr(client, "aclose", None)
    if callable(aclose):
        try:
            res = aclose()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass


_ORIGINAL_DISCORD_REST_CLIENT = DiscordRestClient


def register_discord_routes(app: FastAPI, services: Any, principal_for: Any) -> None:
    @app.get("/v1/integrations/discord")
    async def get_discord_integration(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=False)
        services.security.authorize(
            principal,
            action="conversation.read",
            resource_id=principal.user_id,
            purpose="discord.config.read",
        )
        store = _discord_store_for(services)
        bridge = _discord_bridge_for(services)
        config = await run_in_threadpool(store.get_config, principal.user_id)
        runtime_status = _runtime_status_for(bridge, config, principal.user_id)
        return JSONResponse(_build_discord_state_payload(config, runtime_status), status_code=200)

    @app.put("/v1/integrations/discord")
    async def update_discord_integration(
        payload: UpdateDiscordConfigRequest,
        request: Request,
    ) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=principal.user_id,
            purpose="discord.config.write",
        )
        store = _discord_store_for(services)
        bridge = _discord_bridge_for(services)

        try:
            normalized_user_ids = normalize_snowflake_ids(
                payload.allowed_discord_user_ids,
                field_name="allowed_discord_user_ids",
            )
        except ValueError as exc:
            return _discord_error_response(
                400,
                "invalid_discord_user_id",
                f"ID de usuário do Discord inválido na allowlist: informe apenas números de 17 a 20 dígitos (Snowflake). ({exc})",
            )

        try:
            normalized_channel_ids = normalize_snowflake_ids(
                payload.allowed_channel_ids,
                field_name="allowed_channel_ids",
            )
        except ValueError as exc:
            return _discord_error_response(
                400,
                "invalid_discord_channel_id",
                f"ID de canal do Discord inválido: informe apenas números de 17 a 20 dígitos (Snowflake). ({exc})",
            )

        clean_token: str | None = None
        if payload.bot_token is not None and payload.bot_token.strip():
            clean_token = payload.bot_token.strip()
            if len(clean_token) < 8:
                return _discord_error_response(
                    400,
                    "invalid_bot_token",
                    "O Bot Token informado é inválido ou curto demais.",
                )

        existing = await run_in_threadpool(store.get_config, principal.user_id)
        has_token_after_save = bool(clean_token) or bool(existing and existing.has_bot_token)

        if payload.enabled:
            if not has_token_after_save:
                return _discord_error_response(
                    400,
                    "bot_token_required",
                    "Para ativar o bot do Discord, informe um Bot Token válido (modo fail-closed).",
                )
            if not normalized_user_ids:
                return _discord_error_response(
                    400,
                    "allowlist_required",
                    "Para ativar o bot do Discord, informe ao menos um Discord User ID autorizado na allowlist (modo fail-closed).",
                )

        save_kwargs: dict[str, Any] = {
            "enabled": payload.enabled,
            "bot_token": clean_token,
            "allowed_discord_user_ids": normalized_user_ids,
            "allowed_channel_ids": normalized_channel_ids,
        }
        if payload.default_notify_channel_id is not None:
            try:
                save_kwargs["default_notify_channel_id"] = normalize_optional_snowflake_id(
                    payload.default_notify_channel_id,
                    field_name="default_notify_channel_id",
                )
            except ValueError as exc:
                return _discord_error_response(
                    400,
                    "invalid_notify_channel_id",
                    f"ID de canal padrão de notificação inválido: {exc}",
                )

        try:
            config = await run_in_threadpool(
                lambda: store.save_config(principal.user_id, **save_kwargs)
            )
        except ValueError as exc:
            return _discord_error_response(400, "invalid_discord_config", str(exc))

        if bridge is not None:
            if payload.enabled:
                await bridge.reload_user(principal.user_id)
            else:
                await bridge.stop_user(principal.user_id)
                await bridge.reload_user(principal.user_id)

        config = await run_in_threadpool(store.get_config, principal.user_id)
        runtime_status = _runtime_status_for(bridge, config, principal.user_id)
        return JSONResponse(_build_discord_state_payload(config, runtime_status), status_code=200)

    @app.post("/v1/integrations/discord/test")
    async def test_discord_integration(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.create",
            resource_id=principal.user_id,
            purpose="discord.config.test",
        )
        raw_body = await request.body()
        if raw_body and raw_body.strip():
            try:
                payload = TestDiscordConfigRequest.model_validate_json(raw_body)
            except ValidationError:
                return _discord_error_response(
                    422,
                    "invalid_request",
                    "Corpo da requisição de teste do Discord inválido.",
                )
        else:
            payload = TestDiscordConfigRequest()

        store = _discord_store_for(services)
        bridge = _discord_bridge_for(services)

        provided_token = (
            payload.bot_token.strip()
            if isinstance(payload.bot_token, str) and payload.bot_token.strip()
            else None
        )
        saved_token = await run_in_threadpool(store.get_decrypted_bot_token, principal.user_id)
        token_to_test = provided_token or saved_token

        if not token_to_test:
            return _discord_error_response(
                400,
                "bot_token_missing",
                "Nenhum Bot Token configurado para testar.",
            )
        if len(token_to_test) < 8:
            return _discord_error_response(
                400,
                "invalid_bot_token",
                "O Bot Token informado é inválido ou curto demais.",
            )

        explicit_discord_user_id: str | None = None
        if payload.discord_user_id is not None and payload.discord_user_id.strip():
            try:
                explicit_discord_user_id = normalize_optional_snowflake_id(
                    payload.discord_user_id,
                    field_name="discord_user_id",
                )
            except ValueError:
                return _discord_error_response(
                    400,
                    "invalid_discord_user_id",
                    "Discord User ID inválido para teste: informe um Snowflake numérico de 17 a 20 dígitos.",
                )

        rest_client = _create_rest_client(bridge, token_to_test)
        try:
            try:
                bot_info = await rest_client.get_current_bot()
            except DiscordApiError as exc:
                if exc.status_code in {401, 403}:
                    return _discord_error_response(
                        400,
                        "discord_bot_token_rejected",
                        "Bot Token inválido ou sem acesso no Discord (401 Unauthorized). Verifique o token no Discord Developer Portal.",
                    )
                return _discord_error_response(
                    400,
                    "discord_api_error",
                    f"Falha ao validar o Bot Token no Discord (HTTP {exc.status_code}): {exc}",
                )
            except Exception as exc:
                return _discord_error_response(
                    400,
                    "discord_connection_failed",
                    f"Não foi possível conectar à API do Discord para validar o Bot Token: {exc}",
                )

            if not isinstance(bot_info, Mapping) or not bot_info.get("id"):
                return _discord_error_response(
                    400,
                    "discord_invalid_bot_response",
                    "Resposta inesperada do Discord ao consultar os dados do bot (/users/@me).",
                )

            bot_user_id = str(bot_info.get("id") or "").strip() or None
            bot_username = _format_bot_username(bot_info)
            bot_application_id = (
                str(bot_info.get("application_id") or bot_info.get("id") or "").strip() or None
            )

            is_saved_token = bool(saved_token and token_to_test == saved_token)
            if is_saved_token:
                await run_in_threadpool(
                    lambda: store.update_bot_identity(
                        principal.user_id,
                        application_id=bot_application_id,
                        bot_user_id=bot_user_id,
                        bot_username=bot_username,
                    )
                )

            config = await run_in_threadpool(store.get_config, principal.user_id)
            target_user_id = explicit_discord_user_id
            if not target_user_id and config and config.allowed_discord_user_ids:
                target_user_id = config.allowed_discord_user_ids[0]

            dm_sent = False
            dm_channel_id: str | None = None
            warning_message: str | None = None
            status_message = "Bot Token validado com sucesso no Discord."

            if payload.send_dm and target_user_id:
                try:
                    dm_channel = await rest_client.open_dm(target_user_id)
                    raw_channel_id = (
                        str(dm_channel.get("id") or "").strip()
                        if isinstance(dm_channel, Mapping)
                        else ""
                    )
                    if not raw_channel_id:
                        raise DiscordApiError(
                            "Canal de DM não retornado pelo Discord.",
                            status_code=400,
                        )
                    await rest_client.send_message(
                        raw_channel_id,
                        content=DISCORD_TEST_CONFIRMATION_MESSAGE,
                    )
                    dm_sent = True
                    dm_channel_id = raw_channel_id
                    status_message = DISCORD_TEST_CONFIRMATION_MESSAGE
                    if is_saved_token and config is not None:
                        try:
                            await run_in_threadpool(
                                store.update_default_notify_channel,
                                principal.user_id,
                                dm_channel_id,
                            )
                        except ValueError:
                            pass
                except DiscordApiError as exc:
                    if exc.status_code == 403 or str(exc.error_code) == "50007":
                        warning_message = (
                            "Bot autenticado com sucesso, mas o Discord bloqueou o envio de DM (403 Cannot send messages to this user). "
                            "Adicione o bot a um servidor privado em comum com você e habilite 'Permitir mensagens diretas de membros do servidor' no Discord."
                        )
                        status_message = warning_message
                    else:
                        warning_message = (
                            f"Bot autenticado (@{bot_username or bot_user_id}), mas não foi possível enviar a DM de teste (HTTP {exc.status_code}): {exc}"
                        )
                        status_message = warning_message
                except Exception as exc:
                    warning_message = (
                        f"Bot autenticado (@{bot_username or bot_user_id}), mas ocorreu um erro ao enviar a DM de teste: {exc}"
                    )
                    status_message = warning_message
        finally:
            await _close_rest_client(rest_client)

        config = await run_in_threadpool(store.get_config, principal.user_id)
        runtime_status = _runtime_status_for(bridge, config, principal.user_id)
        state_payload = _build_discord_state_payload(config, runtime_status)
        if not state_payload["bot_user_id"] and bot_user_id:
            state_payload["bot_user_id"] = bot_user_id
        if not state_payload["bot_username"] and bot_username:
            state_payload["bot_username"] = bot_username
        if not state_payload["bot_application_id"] and bot_application_id:
            state_payload["bot_application_id"] = bot_application_id

        response_body: dict[str, Any] = {
            **state_payload,
            "ok": True,
            "connected": True,
            "dm_sent": dm_sent,
            "dm_channel_id": dm_channel_id,
            "recipient_discord_user_id": target_user_id,
            "bot_user_id": bot_user_id,
            "bot_username": bot_username,
            "bot_application_id": bot_application_id,
            "message": status_message,
            "warning": warning_message,
        }
        return JSONResponse(response_body, status_code=200)

    @app.delete("/v1/integrations/discord")
    async def delete_discord_integration(request: Request) -> JSONResponse:
        principal = principal_for(request, mutable=True)
        services.security.authorize(
            principal,
            action="conversation.delete",
            resource_id=principal.user_id,
            purpose="discord.config.delete",
        )
        store = _discord_store_for(services)
        bridge = _discord_bridge_for(services)

        if bridge is not None:
            await bridge.stop_user(principal.user_id)
            disconnected_map = getattr(bridge, "_disconnected_statuses", None)
            if isinstance(disconnected_map, dict):
                disconnected_map.pop(principal.user_id, None)

        await run_in_threadpool(store.delete_config, principal.user_id)
        config = await run_in_threadpool(store.get_config, principal.user_id)
        runtime_status = _runtime_status_for(bridge, config, principal.user_id)
        return JSONResponse(
            {
                "deleted": True,
                **_build_discord_state_payload(config, runtime_status),
            },
            status_code=200,
        )


__all__ = [
    "DISCORD_TEST_CONFIRMATION_MESSAGE",
    "TestDiscordConfigRequest",
    "UpdateDiscordConfigRequest",
    "register_discord_routes",
]
