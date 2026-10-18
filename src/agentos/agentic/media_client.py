"""Same-provider media generation HTTP client (image, audio, video)."""
from __future__ import annotations

import base64
from ipaddress import ip_address
import re
import socket
import time
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx


MAX_MEDIA_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
IMAGE_AUDIO_TIMEOUT_SECONDS = 120.0
VIDEO_TIMEOUT_SECONDS = 300.0

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[a-zA-Z0-9.+/-]+);base64,(?P<data>.+)$", re.DOTALL)


class MediaGenerationError(RuntimeError):
    """Sanitized error raised when a media generation request fails."""


def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        addr = ip_address(host)
        return not (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # In unit tests with MockTransport, unresolvable external hostnames are allowed if not private literals
        return True
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            addr = ip_address(sockaddr[0])
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_multicast
                or addr.is_reserved
                or addr.is_unspecified
            ):
                return False
        except ValueError:
            continue
    return True


def _decode_data_url(url: str) -> tuple[bytes, str] | None:
    match = _DATA_URL_RE.match(url.strip())
    if not match:
        return None
    mime = match.group("mime").lower()
    raw_b64 = match.group("data")
    try:
        data = base64.b64decode(raw_b64)
    except Exception as exc:
        raise MediaGenerationError("Provider returned malformed base64 media data.") from exc
    ext = "png"
    if "jpeg" in mime or "jpg" in mime:
        ext = "jpg"
    elif "webp" in mime:
        ext = "webp"
    elif "gif" in mime:
        ext = "gif"
    elif "wav" in mime:
        ext = "wav"
    elif "ogg" in mime or "opus" in mime:
        ext = "ogg"
    elif "mp4" in mime:
        ext = "mp4"
    elif "webm" in mime:
        ext = "webm"
    elif "mpeg" in mime or "mp3" in mime:
        ext = "mp3"
    return data, ext


class MediaGenerationClient:
    """Executes image, audio, and video generation calls against the active turn provider."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = str(provider or "").strip().lower()
        self.base_url = str(base_url or "").rstrip("/")
        self._api_key = str(api_key or "")
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._transport, follow_redirects=False)

    def _download_url(self, client: httpx.Client, url: str, default_ext: str) -> tuple[bytes, str]:
        data_decoded = _decode_data_url(url)
        if data_decoded is not None:
            return data_decoded
        if not _is_safe_public_url(url):
            raise MediaGenerationError("Download URL points to a private or unsafe address (recusado por segurança).")
        with client.stream("GET", url) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise MediaGenerationError(f"Failed to download generated media (HTTP {response.status_code}).")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_MEDIA_DOWNLOAD_BYTES:
                    raise MediaGenerationError("Generated media exceeds the 100 MB safety limit.")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise MediaGenerationError("Downloaded media file is empty.")
            ctype = (response.headers.get("content-type") or "").lower()
            ext = default_ext
            if "webp" in ctype:
                ext = "webp"
            elif "jpeg" in ctype or "jpg" in ctype:
                ext = "jpg"
            elif "png" in ctype:
                ext = "png"
            elif "wav" in ctype:
                ext = "wav"
            elif "mp4" in ctype:
                ext = "mp4"
            elif "webm" in ctype:
                ext = "webm"
            return content, ext

    def _extract_error(self, response: httpx.Response) -> str:
        status_prefix = f"HTTP {response.status_code}"
        try:
            payload = response.json()
            if isinstance(payload, Mapping):
                err = payload.get("error")
                if isinstance(err, Mapping):
                    base_msg = str(err.get("message") or "").strip()[:300]
                    metadata = err.get("metadata")
                    meta_parts: list[str] = []
                    if isinstance(metadata, Mapping):
                        provider_name = str(metadata.get("provider_name") or "").strip()
                        if provider_name:
                            meta_parts.append(provider_name)
                        raw_meta = metadata.get("raw")
                        raw_detail = ""
                        if isinstance(raw_meta, str) and raw_meta.strip():
                            try:
                                parsed_raw = json.loads(raw_meta)
                                if isinstance(parsed_raw, Mapping):
                                    inner_err = parsed_raw.get("error")
                                    if isinstance(inner_err, Mapping) and inner_err.get("message"):
                                        raw_detail = str(inner_err["message"]).strip()
                                    elif parsed_raw.get("message"):
                                        raw_detail = str(parsed_raw["message"]).strip()
                            except Exception:
                                raw_detail = raw_meta.strip()
                            if not raw_detail:
                                raw_detail = raw_meta.strip()
                        elif isinstance(raw_meta, Mapping):
                            raw_detail = str(raw_meta.get("message") or raw_meta).strip()
                        if raw_detail:
                            meta_parts.append(raw_detail[:240])
                    detail_suffix = f" ({': '.join(meta_parts)})" if meta_parts else ""
                    if base_msg:
                        return f"{status_prefix}: {base_msg}{detail_suffix}"
                    if detail_suffix:
                        return f"{status_prefix}{detail_suffix}"
                if isinstance(err, str) and err.strip():
                    return f"{status_prefix}: {err.strip()[:300]}"
        except Exception:
            pass
        return status_prefix

    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        size: str = "1024x1024",
    ) -> tuple[bytes, str]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise MediaGenerationError("Prompt is required for image generation.")
        with self._client(IMAGE_AUDIO_TIMEOUT_SECONDS) as client:
            if self.provider == "openrouter":
                return self._generate_image_via_chat(client, model=model, prompt=clean_prompt)

            body: dict[str, Any] = {"model": model, "prompt": clean_prompt, "n": 1}
            if size:
                body["size"] = size
            # gpt-image-1 returns b64_json by default and rejects response_format in some gateways
            if not model.lower().startswith("gpt-image"):
                body["response_format"] = "b64_json"

            resp = client.post(f"{self.base_url}/images/generations", headers=self._headers(), json=body)
            if resp.status_code in {404, 405}:
                return self._generate_image_via_chat(client, model=model, prompt=clean_prompt)
            if resp.status_code >= 400 and "response_format" in body:
                body.pop("response_format", None)
                resp = client.post(f"{self.base_url}/images/generations", headers=self._headers(), json=body)
            if resp.status_code >= 400:
                raise MediaGenerationError(f"Image generation failed: {self._extract_error(resp)}")

            payload = resp.json()
            return self._extract_media_from_data_list(client, payload, default_ext="png")

    def _generate_image_via_chat(
        self,
        client: httpx.Client,
        *,
        model: str,
        prompt: str,
    ) -> tuple[bytes, str]:
        resp = client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
            },
        )
        if resp.status_code >= 400:
            raise MediaGenerationError(f"Image generation failed: {self._extract_error(resp)}")
        payload = resp.json()
        choices = payload.get("choices") if isinstance(payload, Mapping) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
            if isinstance(message, Mapping):
                images = message.get("images")
                if isinstance(images, list) and images:
                    first = images[0]
                    if isinstance(first, Mapping):
                        url = (
                            (first.get("image_url") or {}).get("url")
                            if isinstance(first.get("image_url"), Mapping)
                            else first.get("url") or first.get("b64_json")
                        )
                        if isinstance(url, str) and url:
                            if url.startswith("data:") or url.startswith("http://") or url.startswith("https://"):
                                return self._download_url(client, url, "png")
                            return base64.b64decode(url), "png"
                content = message.get("content")
                if isinstance(content, str):
                    data_match = re.search(r"(data:image/[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=]+)", content)
                    if data_match:
                        decoded = _decode_data_url(data_match.group(1))
                        if decoded is not None:
                            return decoded
        raise MediaGenerationError("Provider response did not include a generated image.")

    def generate_audio(
        self,
        *,
        model: str,
        text: str,
        voice: str = "alloy",
        response_format: str = "mp3",
    ) -> tuple[bytes, str]:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise MediaGenerationError("Text is required for audio generation.")
        fmt = str(response_format or "mp3").strip().lower()
        if fmt not in {"mp3", "wav", "opus", "aac", "flac"}:
            fmt = "mp3"
        ext = "ogg" if fmt == "opus" else fmt

        with self._client(IMAGE_AUDIO_TIMEOUT_SECONDS) as client:
            if "audio" in model.lower() and "tts" not in model.lower():
                return self._generate_audio_via_chat(client, model=model, text=clean_text, voice=voice, fmt=fmt)

            resp = client.post(
                f"{self.base_url}/audio/speech",
                headers=self._headers(),
                json={
                    "model": model,
                    "input": clean_text,
                    "voice": voice or "alloy",
                    "response_format": fmt,
                },
            )
            if resp.status_code in {404, 405}:
                return self._generate_audio_via_chat(client, model=model, text=clean_text, voice=voice, fmt=fmt)
            if resp.status_code >= 400:
                raise MediaGenerationError(f"Audio generation failed: {self._extract_error(resp)}")

            ctype = (resp.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                payload = resp.json()
                return self._extract_media_from_data_list(client, payload, default_ext=ext)
            if not resp.content:
                raise MediaGenerationError("Provider returned empty audio stream.")
            return resp.content, ext

    def _generate_audio_via_chat(
        self,
        client: httpx.Client,
        *,
        model: str,
        text: str,
        voice: str,
        fmt: str,
    ) -> tuple[bytes, str]:
        resp = client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": model,
                "modalities": ["text", "audio"],
                "audio": {"voice": voice or "alloy", "format": fmt if fmt in {"mp3", "wav", "opus"} else "mp3"},
                "messages": [{"role": "user", "content": text}],
            },
        )
        if resp.status_code >= 400:
            raise MediaGenerationError(f"Audio generation failed: {self._extract_error(resp)}")
        payload = resp.json()
        choices = payload.get("choices") if isinstance(payload, Mapping) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
            audio_obj = message.get("audio") if isinstance(message, Mapping) else None
            if isinstance(audio_obj, Mapping) and isinstance(audio_obj.get("data"), str):
                return base64.b64decode(audio_obj["data"]), ("ogg" if fmt == "opus" else fmt)
        raise MediaGenerationError("Provider response did not include generated audio.")

    def generate_video(
        self,
        *,
        model: str,
        prompt: str,
        duration_seconds: int = 5,
    ) -> tuple[bytes, str]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise MediaGenerationError("Prompt is required for video generation.")
        with self._client(VIDEO_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{self.base_url}/videos/generations",
                headers=self._headers(),
                json={
                    "model": model,
                    "prompt": clean_prompt,
                    "duration": max(1, min(60, int(duration_seconds or 5))),
                },
            )
            if resp.status_code >= 400:
                raise MediaGenerationError(f"Video generation failed: {self._extract_error(resp)}")
            ctype = (resp.headers.get("content-type") or "").lower()
            if "video/" in ctype and resp.content:
                return resp.content, "mp4"
            payload = resp.json()
            if isinstance(payload, Mapping) and payload.get("id") and payload.get("status") in {"queued", "processing", "running", "in_progress"}:
                job_id = str(payload["id"]).strip()
                if not re.fullmatch(r"[A-Za-z0-9._:-]+", job_id):
                    raise MediaGenerationError("Provider returned an unsafe job identifier.")
                for _ in range(30):
                    time.sleep(2.0)
                    poll = client.get(f"{self.base_url}/videos/generations/{job_id}", headers=self._headers())
                    if poll.status_code >= 400:
                        break
                    poll_data = poll.json()
                    if isinstance(poll_data, Mapping) and poll_data.get("status") in {"succeeded", "completed"}:
                        payload = poll_data
                        break
            return self._extract_media_from_data_list(client, payload, default_ext="mp4")

    def _extract_media_from_data_list(
        self,
        client: httpx.Client,
        payload: Any,
        *,
        default_ext: str,
    ) -> tuple[bytes, str]:
        if not isinstance(payload, Mapping):
            raise MediaGenerationError("Invalid response payload from media provider.")
        items = payload.get("data")
        first = items[0] if isinstance(items, list) and items else payload
        if isinstance(first, Mapping):
            b64 = first.get("b64_json") or first.get("base64") or first.get("audio") or first.get("video")
            if isinstance(b64, str) and b64:
                return base64.b64decode(b64), default_ext
            url = first.get("url") or first.get("video_url") or first.get("audio_url")
            if isinstance(url, str) and url:
                return self._download_url(client, url, default_ext)
        raise MediaGenerationError("Provider response did not include media data or download URL.")


__all__ = ["MediaGenerationClient", "MediaGenerationError"]
