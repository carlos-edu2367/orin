"""Asynchronous Discord REST v10 client with rate-limit handling and anti-SSRF attachment downloads."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import logging
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from agentos.uploads.media import MAX_UPLOAD_BYTES, UploadRejected

_LOGGER = logging.getLogger("agentos.discord.rest")

DISCORD_API_BASE_URL = "https://discord.com/api/v10"
ALLOWED_DISCORD_ATTACHMENT_HOSTS: frozenset[str] = frozenset({
    "cdn.discordapp.com",
    "media.discordapp.net",
})
SAFE_ALLOWED_MENTIONS: dict[str, list[str]] = {"parse": []}
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_SLEEP_SECONDS = 10.0
MAX_ATTACHMENT_REDIRECTS = 3


class DiscordApiError(RuntimeError):
    """Raised when the Discord REST API returns a non-success HTTP status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: int | str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.error_code = error_code
        self.response_body = response_body


class DiscordAttachmentError(UploadRejected):
    """Raised when an attachment URL or payload violates Discord CDN security or size limits."""


@dataclass(frozen=True, slots=True)
class DiscordFileAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


def validate_discord_attachment_url(url: str) -> str:
    """Strictly validate that an attachment URL uses HTTPS and points to Discord's official CDN hosts."""
    if not isinstance(url, str) or not url.strip():
        raise DiscordAttachmentError("URL de anexo vazia ou inválida.")
    cleaned = url.strip()
    try:
        parsed = urlsplit(cleaned)
    except Exception as exc:
        raise DiscordAttachmentError(f"URL de anexo malformada: {cleaned!r}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme != "https":
        raise DiscordAttachmentError(
            f"Esquema de URL não permitido para anexo do Discord ({scheme!r}); apenas 'https' é aceito."
        )
    if parsed.username is not None or parsed.password is not None:
        raise DiscordAttachmentError("Credenciais embutidas na URL do anexo não são permitidas.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DiscordAttachmentError("Porta inválida na URL do anexo.") from exc
    if port not in (None, 443):
        raise DiscordAttachmentError(f"Porta não permitida na URL do anexo: {port}")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname not in ALLOWED_DISCORD_ATTACHMENT_HOSTS:
        raise DiscordAttachmentError(
            f"Host de anexo não autorizado ({hostname!r}); permitido apenas {sorted(ALLOWED_DISCORD_ATTACHMENT_HOSTS)}."
        )
    if not parsed.path or parsed.path == "/":
        raise DiscordAttachmentError("Caminho de anexo inválido na CDN do Discord.")
    return cleaned


def _normalize_outgoing_files(
    files: Sequence[DiscordFileAttachment | Mapping[str, Any] | tuple[Any, ...]] | None,
) -> list[DiscordFileAttachment]:
    if not files:
        return []
    normalized: list[DiscordFileAttachment] = []
    for item in files:
        if isinstance(item, DiscordFileAttachment):
            normalized.append(item)
        elif isinstance(item, Mapping):
            filename = str(item.get("filename") or item.get("name") or "arquivo").strip() or "arquivo"
            raw_bytes = item.get("content") if "content" in item else item.get("bytes")
            if isinstance(raw_bytes, str):
                data = raw_bytes.encode("utf-8")
            elif isinstance(raw_bytes, (bytes, bytearray, memoryview)):
                data = bytes(raw_bytes)
            else:
                raise ValueError(f"Invalid file content for {filename!r}")
            content_type = str(item.get("content_type") or item.get("media_type") or "application/octet-stream")
            normalized.append(DiscordFileAttachment(filename=filename, content=data, content_type=content_type))
        elif isinstance(item, tuple) and len(item) >= 2:
            filename = str(item[0] or "arquivo").strip() or "arquivo"
            raw_bytes = item[1]
            data = raw_bytes.encode("utf-8") if isinstance(raw_bytes, str) else bytes(raw_bytes)
            content_type = str(item[2]) if len(item) >= 3 and item[2] else "application/octet-stream"
            normalized.append(DiscordFileAttachment(filename=filename, content=data, content_type=content_type))
        else:
            raise ValueError(f"Unsupported outgoing file descriptor: {item!r}")
    return normalized


class DiscordRestClient:
    """Async wrapper over Discord REST API v10 with 429 retry, anti-SSRF CDN download, and safe mentions."""

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str = DISCORD_API_BASE_URL,
        timeout: float = 25.0,
        client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ValueError("bot_token must be a non-empty string")
        self._bot_token = bot_token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._external_client = client
        self._client: httpx.AsyncClient | None = client
        self._sleep = sleep_fn

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._external_client is None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> DiscordRestClient:
        self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    def _auth_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "User-Agent": "DiscordBot (https://orin.local, 1.0)",
        }
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _extract_retry_after(response: httpx.Response, parsed_body: Any) -> float:
        if isinstance(parsed_body, Mapping):
            raw_retry = parsed_body.get("retry_after")
            if isinstance(raw_retry, (int, float)) and raw_retry >= 0:
                return float(raw_retry)
            if isinstance(raw_retry, str):
                try:
                    return max(0.0, float(raw_retry.strip()))
                except ValueError:
                    pass
        header_val = response.headers.get("Retry-After") or response.headers.get("X-RateLimit-Reset-After")
        if header_val:
            try:
                return max(0.0, float(header_val.strip()))
            except ValueError:
                pass
        return 1.0

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
    ) -> Any:
        client = self._ensure_client()
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = self._auth_headers()

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            request_kwargs: dict[str, Any] = {"headers": dict(headers)}
            if files is not None:
                request_kwargs["data"] = dict(data or {})
                request_kwargs["files"] = files
            elif json_body is not None:
                request_kwargs["json"] = dict(json_body)

            response = await client.request(method.upper(), url, **request_kwargs)

            if response.status_code == 204 or not response.content:
                if 200 <= response.status_code < 300:
                    return {}
            parsed: Any = None
            if response.content:
                try:
                    parsed = response.json()
                except Exception:
                    parsed = response.text

            if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                retry_after = self._extract_retry_after(response, parsed)
                bounded_wait = min(max(retry_after, 0.01), MAX_RATE_LIMIT_SLEEP_SECONDS)
                _LOGGER.warning(
                    "Discord REST rate limit (429) on %s %s; retrying in %.2fs (attempt %d/%d)",
                    method,
                    path,
                    bounded_wait,
                    attempt + 1,
                    MAX_RATE_LIMIT_RETRIES,
                )
                await self._sleep(bounded_wait)
                continue

            if 200 <= response.status_code < 300:
                return parsed if parsed is not None else {}

            err_msg = f"Discord API error {response.status_code} on {method.upper()} {path}"
            err_code: int | str | None = None
            if isinstance(parsed, Mapping):
                if parsed.get("message"):
                    err_msg = f"{err_msg}: {parsed['message']}"
                err_code = parsed.get("code")
            raise DiscordApiError(
                err_msg,
                status_code=response.status_code,
                error_code=err_code,
                response_body=parsed,
            )

        raise DiscordApiError(
            f"Discord API rate limit retries exhausted on {method.upper()} {path}",
            status_code=429,
        )

    async def get_current_bot(self) -> dict[str, Any]:
        """Fetch the authenticated bot user (`GET /users/@me`)."""
        result = await self._request("GET", "/users/@me")
        return dict(result) if isinstance(result, Mapping) else {}

    async def open_dm(self, discord_user_id: str) -> dict[str, Any]:
        """Open or retrieve a direct-message channel with `discord_user_id` (`POST /users/@me/channels`)."""
        clean_uid = str(discord_user_id or "").strip()
        if not clean_uid:
            raise ValueError("discord_user_id must be a non-empty string")
        result = await self._request(
            "POST",
            "/users/@me/channels",
            json_body={"recipient_id": clean_uid},
        )
        return dict(result) if isinstance(result, Mapping) else {}

    async def trigger_typing(self, channel_id: str) -> None:
        """Trigger typing indicator in `channel_id` (`POST /channels/{channel_id}/typing`)."""
        clean_cid = str(channel_id or "").strip()
        if not clean_cid:
            raise ValueError("channel_id must be a non-empty string")
        await self._request("POST", f"/channels/{clean_cid}/typing")

    async def send_message(
        self,
        channel_id: str,
        *,
        content: str | None = None,
        embeds: Sequence[Mapping[str, Any]] | None = None,
        components: Sequence[Mapping[str, Any]] | None = None,
        files: Sequence[DiscordFileAttachment | Mapping[str, Any] | tuple[Any, ...]] | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to `channel_id`, always enforcing `allowed_mentions={"parse": []}`."""
        clean_cid = str(channel_id or "").strip()
        if not clean_cid:
            raise ValueError("channel_id must be a non-empty string")

        payload: dict[str, Any] = {
            "allowed_mentions": dict(SAFE_ALLOWED_MENTIONS),
        }
        if content is not None:
            payload["content"] = str(content)
        if embeds is not None:
            payload["embeds"] = [dict(e) for e in embeds]
        if components is not None:
            payload["components"] = [dict(c) for c in components]
        if reply_to_message_id is not None and str(reply_to_message_id).strip():
            payload["message_reference"] = {
                "message_id": str(reply_to_message_id).strip(),
                "fail_if_not_exists": False,
            }

        normalized_files = _normalize_outgoing_files(files)
        if not normalized_files:
            result = await self._request("POST", f"/channels/{clean_cid}/messages", json_body=payload)
            return dict(result) if isinstance(result, Mapping) else {}

        payload["attachments"] = [
            {"id": idx, "filename": f.filename}
            for idx, f in enumerate(normalized_files)
        ]
        multipart_files = [
            (f"files[{idx}]", (f.filename, f.content, f.content_type))
            for idx, f in enumerate(normalized_files)
        ]
        result = await self._request(
            "POST",
            f"/channels/{clean_cid}/messages",
            data={"payload_json": json.dumps(payload, ensure_ascii=False)},
            files=multipart_files,
        )
        return dict(result) if isinstance(result, Mapping) else {}

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: str | None = None,
        embeds: Sequence[Mapping[str, Any]] | None = None,
        components: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message in `channel_id`, always enforcing `allowed_mentions={"parse": []}`."""
        clean_cid = str(channel_id or "").strip()
        clean_mid = str(message_id or "").strip()
        if not clean_cid or not clean_mid:
            raise ValueError("channel_id and message_id must be non-empty strings")

        payload: dict[str, Any] = {
            "allowed_mentions": dict(SAFE_ALLOWED_MENTIONS),
        }
        if content is not None:
            payload["content"] = str(content)
        if embeds is not None:
            payload["embeds"] = [dict(e) for e in embeds]
        if components is not None:
            payload["components"] = [dict(c) for c in components]

        result = await self._request(
            "PATCH",
            f"/channels/{clean_cid}/messages/{clean_mid}",
            json_body=payload,
        )
        return dict(result) if isinstance(result, Mapping) else {}

    async def ack_interaction(
        self,
        interaction_id: str,
        interaction_token: str,
        *,
        response_type: int = 6,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """Acknowledge a Discord component interaction (`POST /interactions/{id}/{token}/callback`)."""
        clean_iid = str(interaction_id or "").strip()
        clean_tok = str(interaction_token or "").strip()
        if not clean_iid or not clean_tok:
            raise ValueError("interaction_id and interaction_token must be non-empty strings")

        body: dict[str, Any] = {"type": int(response_type)}
        if data is not None:
            callback_data = dict(data)
            callback_data["allowed_mentions"] = dict(SAFE_ALLOWED_MENTIONS)
            body["data"] = callback_data

        await self._request(
            "POST",
            f"/interactions/{clean_iid}/{clean_tok}/callback",
            json_body=body,
        )

    async def edit_interaction_original(
        self,
        application_id: str,
        interaction_token: str,
        *,
        content: str | None = None,
        embeds: Sequence[Mapping[str, Any]] | None = None,
        components: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit the original interaction response message (`PATCH /webhooks/{app_id}/{token}/messages/@original`)."""
        clean_app = str(application_id or "").strip()
        clean_tok = str(interaction_token or "").strip()
        if not clean_app or not clean_tok:
            raise ValueError("application_id and interaction_token must be non-empty strings")

        payload: dict[str, Any] = {
            "allowed_mentions": dict(SAFE_ALLOWED_MENTIONS),
        }
        if content is not None:
            payload["content"] = str(content)
        if embeds is not None:
            payload["embeds"] = [dict(e) for e in embeds]
        if components is not None:
            payload["components"] = [dict(c) for c in components]

        result = await self._request(
            "PATCH",
            f"/webhooks/{clean_app}/{clean_tok}/messages/@original",
            json_body=payload,
        )
        return dict(result) if isinstance(result, Mapping) else {}

    async def download_attachment(
        self,
        url: str,
        *,
        max_bytes: int = MAX_UPLOAD_BYTES,
    ) -> bytes:
        """Download a Discord attachment with strict anti-SSRF host validation and streaming size cap."""
        if isinstance(max_bytes, bool) or int(max_bytes) <= 0:
            raise ValueError("max_bytes must be a positive integer")
        limit = int(max_bytes)
        current_url = validate_discord_attachment_url(url)
        client = self._ensure_client()

        for _redirect_hop in range(MAX_ATTACHMENT_REDIRECTS + 1):
            async with client.stream(
                "GET",
                current_url,
                headers={"User-Agent": "DiscordBot (https://orin.local, 1.0)"},
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise DiscordAttachmentError("Redirecionamento de anexo sem cabeçalho Location.")
                    next_url = urljoin(current_url, location)
                    current_url = validate_discord_attachment_url(next_url)
                    continue

                if response.status_code != 200:
                    raise DiscordAttachmentError(
                        f"Falha ao baixar anexo da CDN do Discord (HTTP {response.status_code})."
                    )

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length.strip())
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > limit:
                        raise DiscordAttachmentError(
                            f"Anexo excede o limite de {limit} bytes (Content-Length: {declared_size})."
                        )

                buffer = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if not chunk:
                        continue
                    if len(buffer) + len(chunk) > limit:
                        raise DiscordAttachmentError(
                            f"Anexo excede o limite máximo permitido de {limit} bytes."
                        )
                    buffer.extend(chunk)

                if not buffer:
                    raise DiscordAttachmentError("O anexo baixado está vazio.")
                return bytes(buffer)

        raise DiscordAttachmentError("Número máximo de redirecionamentos excedido ao baixar anexo.")


__all__ = [
    "ALLOWED_DISCORD_ATTACHMENT_HOSTS",
    "DISCORD_API_BASE_URL",
    "DiscordApiError",
    "DiscordAttachmentError",
    "DiscordFileAttachment",
    "DiscordRestClient",
    "SAFE_ALLOWED_MENTIONS",
    "validate_discord_attachment_url",
]
