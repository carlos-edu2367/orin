"""Discord Gateway v10 WebSocket client with Heartbeat, ACK zombie detection, Resume, and fatal close handling."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import random
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed

_LOGGER = logging.getLogger("agentos.discord.gateway")

DEFAULT_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_MESSAGE_CONTENT = 1 << 15
DEFAULT_GATEWAY_INTENTS = (
    INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT
)

# Discord Gateway Opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Close codes that must never trigger reconnect loops
FATAL_GATEWAY_CLOSE_CODES: dict[int, str] = {
    4004: (
        "Token do bot Discord inválido ou revogado (código 4004). "
        "Verifique o Bot Token informado nas configurações do Orin."
    ),
    4010: "Shard inválido enviado ao Discord Gateway (código 4010).",
    4011: "O bot exige sharding no Discord Gateway (código 4011).",
    4012: "Versão de API inválida no Discord Gateway (código 4012).",
    4013: (
        "Intents inválidas enviadas ao Discord Gateway (código 4013). "
        "Verifique a configuração do bot."
    ),
    4014: (
        "Intent privilegiada não habilitada no Discord Developer Portal (código 4014). "
        "Acesse Discord Developer Portal -> Bot -> Privileged Gateway Intents e ative 'Message Content Intent'."
    ),
}

# Close codes that invalidate the saved session_id/seq and require a fresh Identify
SESSION_RESET_CLOSE_CODES: frozenset[int] = frozenset({4003, 4007, 4009})


def normalize_gateway_url(raw_url: str | None) -> str:
    """Normalize a Discord Gateway or `resume_gateway_url` to `wss://.../?v=10&encoding=json`."""
    if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
        return DEFAULT_GATEWAY_URL
    cleaned = raw_url.strip()
    parsed = urlsplit(cleaned)
    scheme = (parsed.scheme or "wss").lower()
    if scheme not in {"wss", "ws"}:
        scheme = "wss"
    netloc = parsed.netloc
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = f"{path}/"
    query = parsed.query
    if not query:
        query = "v=10&encoding=json"
    elif "v=" not in query:
        query = f"{query}&v=10&encoding=json"
    return urlunsplit((scheme, netloc, path, query, ""))


def _extract_ws_close_code(exc: BaseException | None, ws: Any = None) -> int | None:
    if exc is not None:
        rcvd = getattr(exc, "rcvd", None)
        if rcvd is not None and getattr(rcvd, "code", None) is not None:
            try:
                return int(rcvd.code)
            except (TypeError, ValueError):
                pass
        raw_code = getattr(exc, "code", None)
        if raw_code is not None:
            try:
                return int(raw_code)
            except (TypeError, ValueError):
                pass
    if ws is not None:
        ws_code = getattr(ws, "close_code", None)
        if ws_code is not None:
            try:
                return int(ws_code)
            except (TypeError, ValueError):
                pass
    return None


@dataclass(frozen=True, slots=True)
class DiscordRuntimeStatus:
    status: str = "disconnected"
    last_connected_at: datetime | None = None
    last_error: str | None = None
    bot_user_id: str | None = None
    bot_username: str | None = None
    latency_ms: float | None = None
    bot_application_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "last_error": self.last_error,
            "bot_user_id": self.bot_user_id,
            "bot_username": self.bot_username,
            "latency_ms": self.latency_ms,
            "bot_application_id": self.bot_application_id,
        }


class DiscordGatewayClient:
    """Managed outbound Discord Gateway v10 WebSocket connection for a single bot token."""

    def __init__(
        self,
        bot_token: str,
        *,
        on_message: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        on_interaction: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        on_ready: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        on_status_change: Callable[[DiscordRuntimeStatus], Awaitable[None] | None] | None = None,
        intents: int = DEFAULT_GATEWAY_INTENTS,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        ws_connect: Callable[..., Any] | None = None,
        jitter_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        invalid_session_delay_seconds: float = 0.2,
    ) -> None:
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ValueError("bot_token must be a non-empty string")
        self._bot_token = bot_token.strip()
        self._on_message = on_message
        self._on_interaction = on_interaction
        self._on_ready = on_ready
        self._on_status_change = on_status_change
        self._intents = int(intents)
        self._default_gateway_url = normalize_gateway_url(gateway_url)
        self._ws_connect = ws_connect or websockets.connect
        self._jitter_fn = jitter_fn or (lambda: random.uniform(0.1, 0.5))
        self._sleep = sleep_fn
        self._initial_backoff = max(0.01, float(initial_backoff_seconds))
        self._max_backoff = max(self._initial_backoff, float(max_backoff_seconds))
        self._invalid_session_delay = max(0.0, float(invalid_session_delay_seconds))

        # Session resume state
        self._session_id: str | None = None
        self._seq: int | None = None
        self._resume_gateway_url: str | None = None

        # Runtime status snapshot
        self._status = DiscordRuntimeStatus(status="disconnected")
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None
        self._active_ws: Any = None

        # Heartbeat state
        self._awaiting_heartbeat_ack: bool = False
        self._last_heartbeat_sent_at: float | None = None

    @property
    def status_snapshot(self) -> DiscordRuntimeStatus:
        return self._status

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def sequence(self) -> int | None:
        return self._seq

    @property
    def resume_gateway_url(self) -> str | None:
        return self._resume_gateway_url

    async def _set_status(
        self,
        status: str,
        *,
        last_connected_at: Any = ...,
        last_error: Any = ...,
        bot_user_id: Any = ...,
        bot_username: Any = ...,
        latency_ms: Any = ...,
        bot_application_id: Any = ...,
    ) -> DiscordRuntimeStatus:
        cur = self._status
        updated = DiscordRuntimeStatus(
            status=status,
            last_connected_at=cur.last_connected_at if last_connected_at is ... else last_connected_at,
            last_error=cur.last_error if last_error is ... else last_error,
            bot_user_id=cur.bot_user_id if bot_user_id is ... else bot_user_id,
            bot_username=cur.bot_username if bot_username is ... else bot_username,
            latency_ms=cur.latency_ms if latency_ms is ... else latency_ms,
            bot_application_id=cur.bot_application_id if bot_application_id is ... else bot_application_id,
        )
        self._status = updated
        if self._on_status_change is not None:
            try:
                res = self._on_status_change(updated)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                _LOGGER.debug("Error in DiscordGatewayClient on_status_change callback", exc_info=True)
        return updated

    def _clear_session(self) -> None:
        self._session_id = None
        self._seq = None
        self._resume_gateway_url = None

    async def start(self) -> None:
        """Start the Gateway supervision loop in background if not already running."""
        if self._runner_task is not None and not self._runner_task.done():
            return
        self._stop_event.clear()
        self._runner_task = asyncio.create_task(self.run_forever(), name="discord-gateway-client")

    async def stop(self) -> None:
        """Gracefully close the active WebSocket and stop the Gateway loop."""
        self._stop_event.set()
        ws = self._active_ws
        if ws is not None:
            try:
                await ws.close(code=1000)
            except Exception:
                pass
        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except (asyncio.CancelledError, Exception):
                pass
            self._runner_task = None
        if self._status.status != "error":
            await self._set_status("disconnected")

    async def _send_json(self, ws: Any, payload: Mapping[str, Any]) -> None:
        raw = json.dumps(dict(payload), ensure_ascii=False)
        await ws.send(raw)

    async def _send_heartbeat(self, ws: Any) -> None:
        self._awaiting_heartbeat_ack = True
        self._last_heartbeat_sent_at = time.monotonic()
        await self._send_json(ws, {"op": OP_HEARTBEAT, "d": self._seq})

    async def _send_identify(self, ws: Any) -> None:
        await self._send_json(
            ws,
            {
                "op": OP_IDENTIFY,
                "d": {
                    "token": self._bot_token,
                    "intents": self._intents,
                    "properties": {
                        "os": sys.platform,
                        "browser": "orin",
                        "device": "orin",
                    },
                },
            },
        )

    async def _send_resume(self, ws: Any) -> None:
        await self._send_json(
            ws,
            {
                "op": OP_RESUME,
                "d": {
                    "token": self._bot_token,
                    "session_id": self._session_id,
                    "seq": self._seq,
                },
            },
        )

    async def _heartbeat_loop(self, ws: Any, interval_ms: float) -> None:
        interval_sec = max(0.05, float(interval_ms) / 1000.0)
        jitter = max(0.0, min(1.0, float(self._jitter_fn())))
        first_wait = max(0.01, interval_sec * jitter)
        await self._sleep(first_wait)

        while not self._stop_event.is_set():
            if self._awaiting_heartbeat_ack:
                _LOGGER.warning(
                    "Discord Gateway Heartbeat ACK missing (zombie connection detected); closing for Resume."
                )
                try:
                    await ws.close(code=4000, reason="Heartbeat ACK timeout")
                except Exception:
                    pass
                return

            try:
                await self._send_heartbeat(ws)
            except Exception:
                return

            await self._sleep(interval_sec)

    async def _invoke_callback(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        payload: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        try:
            res = callback(payload)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            _LOGGER.exception("Unhandled error in Discord Gateway event handler")

    async def _handle_dispatch(self, event_name: str, data: dict[str, Any]) -> None:
        if event_name == "READY":
            session_id = str(data.get("session_id") or "").strip()
            if session_id:
                self._session_id = session_id
            resume_url = str(data.get("resume_gateway_url") or "").strip()
            if resume_url:
                self._resume_gateway_url = normalize_gateway_url(resume_url)

            user_obj = data.get("user") if isinstance(data.get("user"), Mapping) else {}
            app_obj = data.get("application") if isinstance(data.get("application"), Mapping) else {}
            bot_uid = str(user_obj.get("id") or "").strip() or None
            bot_uname = str(user_obj.get("username") or "").strip() or None
            app_id = str(app_obj.get("id") or "").strip() or bot_uid

            await self._set_status(
                "connected",
                last_connected_at=datetime.now(UTC),
                last_error=None,
                bot_user_id=bot_uid,
                bot_username=bot_uname,
                bot_application_id=app_id,
            )
            await self._invoke_callback(
                self._on_ready,
                {
                    **data,
                    "bot_user_id": bot_uid,
                    "bot_username": bot_uname,
                    "bot_application_id": app_id,
                },
            )
            return

        if event_name == "RESUMED":
            await self._set_status(
                "connected",
                last_connected_at=datetime.now(UTC),
                last_error=None,
            )
            return

        if event_name == "MESSAGE_CREATE":
            await self._invoke_callback(self._on_message, data)
            return

        if event_name == "INTERACTION_CREATE":
            await self._invoke_callback(self._on_interaction, data)
            return

    async def _run_single_connection(self, ws: Any) -> str:
        """Drive one WebSocket session until it closes or requests reconnect.

        Returns one of: ``"stopped"``, ``"reconnect"``, ``"fatal"``.
        """
        self._awaiting_heartbeat_ack = False
        self._last_heartbeat_sent_at = None
        heartbeat_task: asyncio.Task[None] | None = None
        attempted_resume = False
        session_established = False

        try:
            async for raw_message in ws:
                if self._stop_event.is_set():
                    return "stopped"
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8", errors="replace")
                try:
                    packet = json.loads(raw_message)
                except Exception:
                    continue
                if not isinstance(packet, Mapping):
                    continue

                op = packet.get("op")
                data = packet.get("d")
                seq = packet.get("s")
                event_name = str(packet.get("t") or "")

                if isinstance(seq, int) and not isinstance(seq, bool):
                    self._seq = seq

                if op == OP_HELLO:
                    interval_ms = 41250.0
                    if isinstance(data, Mapping) and isinstance(data.get("heartbeat_interval"), (int, float)):
                        interval_ms = float(data["heartbeat_interval"])
                    if heartbeat_task is not None:
                        heartbeat_task.cancel()
                    heartbeat_task = asyncio.create_task(
                        self._heartbeat_loop(ws, interval_ms),
                        name="discord-gateway-heartbeat",
                    )
                    if self._session_id and self._seq is not None:
                        attempted_resume = True
                        await self._send_resume(ws)
                    else:
                        attempted_resume = False
                        await self._send_identify(ws)

                elif op == OP_HEARTBEAT_ACK:
                    self._awaiting_heartbeat_ack = False
                    if self._last_heartbeat_sent_at is not None:
                        rtt_ms = round((time.monotonic() - self._last_heartbeat_sent_at) * 1000.0, 1)
                        await self._set_status(self._status.status, latency_ms=max(0.0, rtt_ms))

                elif op == OP_HEARTBEAT:
                    await self._send_heartbeat(ws)

                elif op == OP_DISPATCH:
                    if event_name in {"READY", "RESUMED"}:
                        session_established = True
                    payload_dict = dict(data) if isinstance(data, Mapping) else {}
                    await self._handle_dispatch(event_name, payload_dict)

                elif op == OP_RECONNECT:
                    _LOGGER.info("Discord Gateway requested Op 7 Reconnect; closing for Resume.")
                    try:
                        await ws.close(code=4000, reason="Op 7 Reconnect")
                    except Exception:
                        pass
                    return "reconnect"

                elif op == OP_INVALID_SESSION:
                    resumable = bool(data)
                    if not resumable:
                        self._clear_session()
                    if self._invalid_session_delay > 0:
                        await self._sleep(self._invalid_session_delay)
                    if resumable and self._session_id and self._seq is not None:
                        attempted_resume = True
                        await self._send_resume(ws)
                    else:
                        attempted_resume = False
                        await self._send_identify(ws)

        finally:
            if attempted_resume and not session_established and not self._stop_event.is_set():
                self._clear_session()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

        return "stopped" if self._stop_event.is_set() else "reconnect"

    async def run_forever(self) -> None:
        """Connect and maintain the Gateway session with exponential backoff and fatal close guards."""
        backoff = self._initial_backoff
        await self._set_status("connecting", last_error=None)

        while not self._stop_event.is_set():
            target_url = (
                self._resume_gateway_url
                if (self._session_id and self._resume_gateway_url)
                else self._default_gateway_url
            )
            ws_instance: Any = None
            close_exc: BaseException | None = None

            try:
                async with self._ws_connect(target_url, max_size=16 * 1024 * 1024) as ws:
                    ws_instance = ws
                    self._active_ws = ws
                    was_connected_before = self._status.last_connected_at
                    outcome = await self._run_single_connection(ws)
                    if self._status.last_connected_at != was_connected_before:
                        backoff = self._initial_backoff
                    if outcome == "stopped":
                        break
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                close_exc = exc
            except Exception as exc:
                close_exc = exc
                _LOGGER.warning("Discord Gateway connection error: %s", exc)
            finally:
                self._active_ws = None

            if self._stop_event.is_set():
                break

            close_code = _extract_ws_close_code(close_exc, ws_instance)
            if close_code in FATAL_GATEWAY_CLOSE_CODES:
                fatal_message = FATAL_GATEWAY_CLOSE_CODES[close_code]
                _LOGGER.error("Discord Gateway fatal close (%d): %s", close_code, fatal_message)
                self._clear_session()
                await self._set_status("error", last_error=fatal_message)
                self._stop_event.set()
                return

            if close_code in SESSION_RESET_CLOSE_CODES:
                self._clear_session()

            err_summary = (
                f"Conexão encerrada (código {close_code})"
                if close_code is not None
                else (str(close_exc) if close_exc else "Reconectando ao Discord Gateway")
            )
            await self._set_status("reconnecting", last_error=err_summary)
            jitter = max(0.0, min(1.0, float(self._jitter_fn()))) if close_exc is None else 0.0
            await self._sleep(backoff + jitter)
            backoff = min(self._max_backoff, backoff * 2.0)

        if self._status.status != "error":
            await self._set_status("disconnected")


__all__ = [
    "DEFAULT_GATEWAY_INTENTS",
    "DEFAULT_GATEWAY_URL",
    "DiscordGatewayClient",
    "DiscordRuntimeStatus",
    "FATAL_GATEWAY_CLOSE_CODES",
    "INTENT_DIRECT_MESSAGES",
    "INTENT_GUILDS",
    "INTENT_GUILD_MESSAGES",
    "INTENT_MESSAGE_CONTENT",
    "OP_DISPATCH",
    "OP_HEARTBEAT",
    "OP_HEARTBEAT_ACK",
    "OP_HELLO",
    "OP_IDENTIFY",
    "OP_INVALID_SESSION",
    "OP_RECONNECT",
    "OP_RESUME",
    "normalize_gateway_url",
]
