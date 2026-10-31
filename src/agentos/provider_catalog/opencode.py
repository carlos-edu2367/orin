"""OpenCode provider edge: Local Bridge (`opencode serve`) and Cloud Zen catalog/stream."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from agentos.agentic.provider_stream import (
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
)
from agentos.agentic.retry import parse_retry_after
from agentos.agentic.tools.public_names import internal_name, public_name
from agentos.providers.models import (
    FinishReason,
    ProviderError,
    ProviderErrorCategory,
    ProviderUsage,
    Retryability,
)


DEFAULT_OPENCODE_LOCAL_BASE_URL = "http://127.0.0.1:18446"
OPENCODE_CLOUD_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_OPENCODE_BASE_URL = OPENCODE_CLOUD_BASE_URL

_CLOUD_HOST = "opencode.ai"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# OpenCode's free tier (`big-pickle`, `mimo-v2.6-flash-free`, etc.) rejects
# requests where all built-in tools are disabled with 403 FreeTierError.
# Leaving `"read": True` satisfies the upstream gate while our system prompt
# directs all tool calls through Orin's `<orin_tool_call>` protocol.
_OPENCODE_BRIDGE_TOOLS_GATE: dict[str, bool] = {
    "read": True,
    "bash": False,
    "edit": False,
    "glob": False,
    "grep": False,
    "skill": False,
    "task": False,
    "todowrite": False,
    "webfetch": False,
    "websearch": False,
    "write": False,
    "question": False,
    "apply_patch": False,
}

_ORIN_TOOL_CALL_RE = re.compile(
    r"<orin_tool_call>\s*(.*?)\s*</orin_tool_call>",
    re.DOTALL,
)


def normalize_opencode_base_url(value: str) -> str:
    """Validate and normalize an OpenCode Local Bridge or Cloud Zen URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OpenCode base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OpenCode base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "OpenCode base URL must not contain credentials, query, or fragment"
        )
    host = (parsed.hostname or "").lower()
    is_cloud = host == _CLOUD_HOST or host.endswith(f".{_CLOUD_HOST}")
    is_loopback = host in _LOOPBACK_HOSTS
    if not is_cloud and not is_loopback:
        raise ValueError(
            "OpenCode base URL must be a local loopback address or opencode.ai"
        )
    if is_cloud and parsed.scheme != "https":
        raise ValueError("OpenCode Cloud base URL must use HTTPS")

    path = parsed.path.rstrip("/")
    if is_cloud:
        if path.endswith("/models"):
            path = path[: -len("/models")]
        if not path:
            path = "/zen/v1"
    else:
        for suffix in ("/v1", "/api"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_opencode_cloud(base_url: str) -> bool:
    """Return True when the URL points to the hosted OpenCode Zen API."""
    host = (urlsplit(base_url).hostname or "").lower()
    return host == _CLOUD_HOST or host.endswith(f".{_CLOUD_HOST}")


def _locate_opencode_binary() -> str | None:
    """Locate the installed OpenCode CLI binary without invoking a shell."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        exe_candidate = (
            Path(appdata)
            / "npm"
            / "node_modules"
            / "opencode-ai"
            / "bin"
            / "opencode.exe"
        )
        if exe_candidate.is_file():
            return str(exe_candidate)
    found = shutil.which("opencode")
    if found:
        sibling_exe = (
            Path(found).parent
            / "node_modules"
            / "opencode-ai"
            / "bin"
            / "opencode.exe"
        )
        if sibling_exe.is_file():
            return str(sibling_exe)
        return found
    if appdata:
        cmd_candidate = Path(appdata) / "npm" / "opencode.cmd"
        if cmd_candidate.is_file():
            return str(cmd_candidate)
    return None


class OpenCodeBridgeProcessManager:
    """Manage the local `opencode serve` daemon on a loopback port."""

    def __init__(self, *, startup_timeout: float = 12.0) -> None:
        self._startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen[bytes]] = {}

    def ensure_running(self, base_url: str) -> None:
        normalized = normalize_opencode_base_url(base_url)
        if is_opencode_cloud(normalized):
            return
        if self._is_healthy(normalized):
            return
        with self._lock:
            if self._is_healthy(normalized):
                return
            parsed = urlsplit(normalized)
            port = parsed.port or 18446
            binary = _locate_opencode_binary()
            if not binary:
                raise RuntimeError(
                    "OpenCode CLI executable not found on this machine"
                )
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            )
            sandbox_dir = Path(tempfile.gettempdir()) / "orin-opencode-bridge"
            sandbox_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [
                    binary,
                    "serve",
                    "--port",
                    str(port),
                    "--hostname",
                    "127.0.0.1",
                ],
                cwd=str(sandbox_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._processes[port] = proc
            deadline = time.monotonic() + self._startup_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if self._is_healthy(normalized):
                    return
                time.sleep(0.25)
            raise RuntimeError("OpenCode local bridge could not be started")

    @staticmethod
    def _is_healthy(base_url: str) -> bool:
        try:
            response = httpx.get(
                f"{base_url}/config/providers",
                timeout=1.5,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False


_DEFAULT_BRIDGE_MANAGER = OpenCodeBridgeProcessManager()


class OpenCodeModelCatalogClient:
    """Server-side OpenCode model catalog client for both Local Bridge and Cloud Zen."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OPENCODE_BASE_URL,
        timeout: float = 15.0,
        bridge_manager: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._bridge_manager = bridge_manager or _DEFAULT_BRIDGE_MANAGER

    def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
        if base_url.strip():
            target = normalize_opencode_base_url(base_url)
        elif api_key.strip():
            target = normalize_opencode_base_url(self._base_url)
        else:
            target = DEFAULT_OPENCODE_LOCAL_BASE_URL

        if is_opencode_cloud(target):
            return self._fetch_cloud(api_key, target)
        return self._fetch_local_bridge(target)

    def _fetch_cloud(
        self, api_key: str, target: str
    ) -> list[dict[str, object]]:
        response = httpx.get(
            f"{target}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise ValueError("OpenCode returned an invalid catalog")
        return data

    def _fetch_local_bridge(self, target: str) -> list[dict[str, object]]:
        try:
            self._bridge_manager.ensure_running(target)
            response = httpx.get(
                f"{target}/config/providers",
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except Exception as error:
            raise RuntimeError("OpenCode bridge connection failed") from error

        providers_list: list[Any] | None = None
        if isinstance(payload, dict) and isinstance(payload.get("providers"), list):
            providers_list = payload["providers"]
        elif isinstance(payload, list):
            providers_list = payload

        if not isinstance(providers_list, list):
            raise RuntimeError("OpenCode bridge returned an invalid catalog")

        models: list[dict[str, object]] = []
        for provider_entry in providers_list:
            if not isinstance(provider_entry, Mapping):
                continue
            if str(provider_entry.get("id") or "") != "opencode":
                continue
            raw_models = provider_entry.get("models")
            if not isinstance(raw_models, Mapping):
                continue
            for model_key, item in raw_models.items():
                if not isinstance(item, Mapping):
                    continue
                model_id = str(item.get("id") or model_key).strip()
                if not model_id:
                    continue
                display_name = str(item.get("name") or model_id).strip() or model_id
                limit = (
                    item.get("limit")
                    if isinstance(item.get("limit"), Mapping)
                    else {}
                )
                context_len = limit.get("context")
                if not isinstance(context_len, int) or isinstance(context_len, bool):
                    context_len = None

                caps = (
                    item.get("capabilities")
                    if isinstance(item.get("capabilities"), Mapping)
                    else {}
                )
                supported_params: list[str] = []
                if caps.get("toolcall") is True:
                    supported_params.append("tools")
                if caps.get("reasoning") is True:
                    supported_params.append("reasoning")

                input_mods: list[str] = []
                raw_in = (
                    caps.get("input")
                    if isinstance(caps.get("input"), Mapping)
                    else {}
                )
                for mod in ("text", "image", "audio", "video", "pdf"):
                    if raw_in.get(mod) is True:
                        input_mods.append(mod)
                if not input_mods:
                    input_mods.append("text")

                output_mods: list[str] = []
                raw_out = (
                    caps.get("output")
                    if isinstance(caps.get("output"), Mapping)
                    else {}
                )
                for mod in ("text", "image", "audio", "video"):
                    if raw_out.get(mod) is True:
                        output_mods.append(mod)
                if not output_mods:
                    output_mods.append("text")

                cost = (
                    item.get("cost")
                    if isinstance(item.get("cost"), Mapping)
                    else {}
                )
                pricing: dict[str, str] = {}
                if isinstance(cost.get("input"), (int, float)) and not isinstance(
                    cost.get("input"), bool
                ):
                    pricing["prompt"] = str(float(cost["input"]) / 1_000_000)
                if isinstance(cost.get("output"), (int, float)) and not isinstance(
                    cost.get("output"), bool
                ):
                    pricing["completion"] = str(float(cost["output"]) / 1_000_000)

                models.append(
                    {
                        "id": model_id,
                        "name": display_name,
                        "context_length": context_len,
                        "supported_parameters": supported_params,
                        "input_modalities": input_mods,
                        "output_modalities": output_mods,
                        **({"pricing": pricing} if pricing else {}),
                    }
                )
        return models


def _extract_tool_declarations(
    tools: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, object]], set[str]]:
    declarations: list[dict[str, object]] = []
    allowed_names: set[str] = set()
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        fn = (
            item.get("function")
            if isinstance(item.get("function"), Mapping)
            else item
        )
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        allowed_names.add(name)
        declarations.append(
            {
                "name": name,
                "description": str(fn.get("description") or ""),
                "parameters": fn.get("parameters")
                or fn.get("input_schema")
                or {"type": "object", "properties": {}},
            }
        )
    return declarations, allowed_names


def _build_bridge_system_prompt(
    system_parts: list[str],
    declarations: list[dict[str, object]],
) -> str:
    base_system = "\n\n".join(part for part in system_parts if part.strip())
    if not declarations:
        return base_system
    tools_json = json.dumps(declarations, ensure_ascii=False)
    bridge_instructions = (
        "\n\n<orin-tool-bridge>\n"
        "IMPORTANT TOOL EXECUTION PROTOCOL:\n"
        "You are running inside Orin's agentic harness via the OpenCode Bridge.\n"
        "1. NEVER invoke any native OpenCode tool (`read`, `bash`, `edit`, `write`, `glob`, `grep`, etc.) directly.\n"
        "2. To call a tool, you MUST emit an `<orin_tool_call>` XML block in your text response containing a JSON object with `\"name\"` and `\"arguments\"`:\n"
        "<orin_tool_call>{\"name\": \"tool_name\", \"arguments\": {\"key\": \"value\"}}</orin_tool_call>\n"
        "3. Only call tools from the following authorized Orin tool declarations:\n"
        f"{tools_json}\n"
        "4. After emitting `<orin_tool_call>...</orin_tool_call>`, stop your response and wait for the `[Tool Result]` in the next turn.\n"
        "</orin-tool-bridge>"
    )
    return f"{base_system}{bridge_instructions}" if base_system else bridge_instructions.lstrip()


def _extract_tool_result_text(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        chunks: list[str] = []
        for item in raw_content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, Mapping):
                if isinstance(item.get("text"), str):
                    chunks.append(item["text"])
                elif isinstance(item.get("content"), str):
                    chunks.append(item["content"])
        return "\n".join(chunks)
    if raw_content is None:
        return ""
    return str(raw_content)


def _format_tool_call_xml(name: str, raw_args: Any) -> str:
    tc_args = raw_args
    if isinstance(tc_args, str):
        try:
            tc_args = json.loads(tc_args)
        except json.JSONDecodeError:
            tc_args = {}
    if not isinstance(tc_args, Mapping):
        tc_args = {}
    return (
        "<orin_tool_call>"
        + json.dumps(
            {"name": name, "arguments": dict(tc_args)},
            ensure_ascii=False,
        )
        + "</orin_tool_call>"
    )


def _format_bridge_conversation_transcript(
    messages: Iterable[Mapping[str, Any]],
) -> tuple[list[str], str]:
    system_parts: list[str] = []
    turns_text: list[str] = []
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "")
        content = msg.get("content")
        text_content = ""
        anthropic_tool_uses: list[Mapping[str, Any]] = []
        anthropic_tool_results: list[Mapping[str, Any]] = []
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                b_type = str(block.get("type") or "")
                if b_type == "tool_use":
                    anthropic_tool_uses.append(block)
                elif b_type == "tool_result":
                    anthropic_tool_results.append(block)
                elif b_type in ("", "text") and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
            text_content = "\n".join(chunks)

        if role == "system":
            if text_content.strip():
                system_parts.append(text_content.strip())
                if turns_text:
                    turns_text.append(f"[System]\n{text_content.strip()}")
            continue
        if role == "user":
            if anthropic_tool_results:
                for tr in anthropic_tool_results:
                    call_id = str(
                        tr.get("tool_use_id")
                        or tr.get("tool_call_id")
                        or tr.get("id")
                        or ""
                    )
                    tr_text = _extract_tool_result_text(tr.get("content"))
                    turns_text.append(f"[Tool Result id={call_id}]\n{tr_text}")
                if text_content.strip():
                    turns_text.append(f"[User]\n{text_content}")
            else:
                turns_text.append(f"[User]\n{text_content}")
            continue
        if role == "assistant":
            parts: list[str] = []
            if text_content.strip():
                parts.append(text_content.strip())
            for tu in anthropic_tool_uses:
                tc_name = str(tu.get("name") or "").strip()
                tc_args = tu.get("input") if "input" in tu else tu.get("arguments")
                if tc_name:
                    parts.append(_format_tool_call_xml(tc_name, tc_args))
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, Mapping):
                        continue
                    fn = (
                        tc.get("function")
                        if isinstance(tc.get("function"), Mapping)
                        else {}
                    )
                    tc_name = str(fn.get("name") or "").strip()
                    tc_args = fn.get("arguments")
                    if tc_name:
                        parts.append(_format_tool_call_xml(tc_name, tc_args))
            if parts:
                turns_text.append("[Assistant]\n" + "\n".join(parts))
            continue
        if role == "tool":
            if anthropic_tool_results:
                for tr in anthropic_tool_results:
                    call_id = str(
                        tr.get("tool_use_id")
                        or tr.get("tool_call_id")
                        or tr.get("id")
                        or msg.get("tool_call_id")
                        or ""
                    )
                    tr_text = _extract_tool_result_text(tr.get("content"))
                    turns_text.append(f"[Tool Result id={call_id}]\n{tr_text}")
            else:
                call_id = str(msg.get("tool_call_id") or "")
                turns_text.append(f"[Tool Result id={call_id}]\n{text_content}")

    if len(turns_text) == 1 and turns_text[0].startswith("[User]\n"):
        return system_parts, turns_text[0][len("[User]\n") :]
    return system_parts, "\n\n".join(turns_text) or "Continue."


def _resolve_allowed_tool_name(raw_name: str, allowed_names: set[str]) -> str:
    candidate = raw_name.strip()
    if not candidate or not allowed_names:
        return ""
    if candidate in allowed_names:
        return candidate
    if candidate.startswith("mcp__"):
        parts = candidate.split("__", 2)
        if len(parts) == 3 and parts[2]:
            candidate = parts[2]
            if candidate in allowed_names:
                return candidate
    lower_map = {item.lower(): item for item in allowed_names}
    if candidate.lower() in lower_map:
        return lower_map[candidate.lower()]
    internal = internal_name(candidate)
    if internal in allowed_names:
        return internal
    if internal.lower() in lower_map:
        return lower_map[internal.lower()]
    public = public_name(internal)
    if public in allowed_names:
        return public
    if public.lower() in lower_map:
        return lower_map[public.lower()]
    return ""


def _parse_orin_tool_calls(
    text: str,
    allowed_names: set[str],
) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract validated `<orin_tool_call>` blocks and strip all `<orin_tool_call>` tags from visible text."""
    calls: list[tuple[str, str, str]] = []
    if not _ORIN_TOOL_CALL_RE.search(text):
        return text, calls

    for match in _ORIN_TOOL_CALL_RE.finditer(text):
        raw_json = match.group(1).strip()
        if raw_json.startswith("```"):
            raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.IGNORECASE)
            raw_json = re.sub(r"\s*```$", "", raw_json).strip()
        if not allowed_names:
            continue
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, Mapping):
            continue
        raw_name = str(parsed.get("name") or "").strip()
        name = _resolve_allowed_tool_name(raw_name, allowed_names)
        if not name:
            continue
        arguments = parsed.get("arguments")
        if arguments is None or (arguments == {} and "input" in parsed):
            arguments = parsed.get("input")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, Mapping):
            arguments = {}
        call_id = str(parsed.get("id") or f"call_{uuid4().hex[:12]}")
        calls.append((call_id, name, json.dumps(dict(arguments), ensure_ascii=False)))

    cleaned = _ORIN_TOOL_CALL_RE.sub("", text).strip()
    return cleaned, calls


def _resolve_model_tuple(model: str) -> tuple[str, str]:
    cleaned = model.strip()
    if "/" in cleaned:
        provider_id, model_id = cleaned.split("/", 1)
        return provider_id.strip() or "opencode", model_id.strip()
    return "opencode", cleaned


def stream_opencode_bridge(
    *,
    base_url: str,
    model: str,
    messages: Iterable[Mapping[str, Any]],
    tools: Iterable[Mapping[str, Any]] = (),
    tool_choice: object = None,
    client: httpx.Client | None = None,
    bridge_manager: Any | None = None,
    timeout: float = 180.0,
) -> Iterator[NormalizedStreamItem]:
    """Drive a local `opencode serve` instance and yield normalized Orin stream items."""
    normalized_base = normalize_opencode_base_url(base_url)
    manager = bridge_manager or _DEFAULT_BRIDGE_MANAGER
    try:
        manager.ensure_running(normalized_base)
    except Exception as error:
        raise ProviderKeyRejected(str(error)) from error

    withheld = isinstance(tool_choice, str) and tool_choice.lower() == "none"
    declarations, allowed_names = (
        ([], set()) if withheld else _extract_tool_declarations(tools)
    )
    system_parts, prompt_text = _format_bridge_conversation_transcript(messages)
    system_prompt = _build_bridge_system_prompt(system_parts, declarations)
    provider_id, model_id = _resolve_model_tuple(model)

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        try:
            session_resp = http.post(f"{normalized_base}/session", json={})
            session_resp.raise_for_status()
            session_data = session_resp.json()
            session_id = (
                str(session_data.get("id") or "")
                if isinstance(session_data, Mapping)
                else ""
            )
            if not session_id:
                raise ProviderKeyRejected("OpenCode bridge did not return a session ID")

            payload: dict[str, object] = {
                "model": {"providerID": provider_id, "modelID": model_id},
                "tools": dict(_OPENCODE_BRIDGE_TOOLS_GATE),
                "parts": [{"type": "text", "text": prompt_text}],
            }
            if system_prompt:
                payload["system"] = system_prompt

            msg_resp = http.post(
                f"{normalized_base}/session/{session_id}/message",
                json=payload,
            )
            msg_resp.raise_for_status()
            body = msg_resp.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403, 429):
                rejected = ProviderKeyRejected(str(error))
                rejected.status_code = error.response.status_code
                rejected.retry_after_seconds = parse_retry_after(
                    error.response.headers
                )
                raise rejected from error
            raise
        except httpx.TransportError as error:
            raise ProviderKeyRejected(str(error)) from error

        if not isinstance(body, Mapping):
            yield NormalizedStreamItem(
                StreamKind.ERROR,
                1,
                error=ProviderError(
                    ProviderErrorCategory.INVALID_RESPONSE,
                    "INVALID_BRIDGE_RESPONSE",
                    "provider stream failed",
                ),
            )
            return

        info = body.get("info") if isinstance(body.get("info"), Mapping) else {}
        upstream_error = info.get("error")
        if isinstance(upstream_error, Mapping) and upstream_error:
            err_name = str(upstream_error.get("name") or "PROVIDER_ERROR")
            err_data = (
                upstream_error.get("data")
                if isinstance(upstream_error.get("data"), Mapping)
                else {}
            )
            status_code = err_data.get("statusCode")
            if status_code in (401, 403, 429) or "FreeTier" in err_name or "Auth" in err_name:
                rejected = ProviderKeyRejected(err_name)
                rejected.status_code = int(status_code) if isinstance(status_code, int) else 403
                raise rejected
            yield NormalizedStreamItem(
                StreamKind.ERROR,
                1,
                error=ProviderError(
                    ProviderErrorCategory.PROVIDER_INTERNAL,
                    "PROVIDER_ERROR",
                    "provider stream failed",
                    Retryability.NEVER,
                ),
            )
            return

        sequence = 0
        parts = body.get("parts") if isinstance(body.get("parts"), list) else []
        emitted_tool_calls: list[tuple[str, str, str]] = []

        for part in parts:
            if not isinstance(part, Mapping):
                continue
            part_type = str(part.get("type") or "")
            if part_type == "reasoning":
                reasoning_text = str(part.get("text") or "")
                if reasoning_text:
                    sequence += 1
                    yield NormalizedStreamItem(
                        StreamKind.THINKING, sequence, thinking=reasoning_text
                    )
            elif part_type == "text":
                raw_text = str(part.get("text") or "")
                if not raw_text:
                    continue
                cleaned_text, parsed_calls = _parse_orin_tool_calls(
                    raw_text, allowed_names
                )
                if cleaned_text:
                    sequence += 1
                    yield NormalizedStreamItem(
                        StreamKind.TEXT, sequence, text=cleaned_text
                    )
                emitted_tool_calls.extend(parsed_calls)
            elif part_type == "tool" and not withheld:
                # Fallback if the model invoked the gated native `read` tool directly
                native_tool = str(part.get("tool") or "")
                state = (
                    part.get("state")
                    if isinstance(part.get("state"), Mapping)
                    else {}
                )
                raw_input = (
                    state.get("input")
                    if isinstance(state.get("input"), Mapping)
                    else {}
                )
                mapped_name = (
                    "read_file"
                    if native_tool == "read" and "read_file" in allowed_names
                    else (native_tool if native_tool in allowed_names else "")
                )
                if mapped_name:
                    mapped_args = dict(raw_input)
                    if "filePath" in mapped_args and "path" not in mapped_args:
                        mapped_args["path"] = mapped_args.pop("filePath")
                    call_id = str(part.get("callID") or f"call_{uuid4().hex[:12]}")
                    emitted_tool_calls.append(
                        (
                            call_id,
                            mapped_name,
                            json.dumps(mapped_args, ensure_ascii=False),
                        )
                    )

        for call_id, tool_name, args_json in emitted_tool_calls:
            sequence += 1
            yield NormalizedStreamItem(
                StreamKind.TOOL_CALL,
                sequence,
                tool_call_id=call_id,
                tool_name=tool_name,
                arguments_delta=args_json,
            )

        tokens = info.get("tokens")
        if isinstance(tokens, Mapping):
            in_tok = tokens.get("input")
            out_tok = tokens.get("output")
            input_tokens = (
                in_tok
                if isinstance(in_tok, int) and not isinstance(in_tok, bool) and in_tok >= 0
                else None
            )
            output_tokens = (
                out_tok
                if isinstance(out_tok, int) and not isinstance(out_tok, bool) and out_tok >= 0
                else None
            )
            if input_tokens is not None or output_tokens is not None:
                total = (input_tokens or 0) + (output_tokens or 0)
                sequence += 1
                yield NormalizedStreamItem(
                    StreamKind.USAGE,
                    sequence,
                    usage=ProviderUsage(
                        input_tokens,
                        output_tokens,
                        total,
                        None,
                        measurement="CONFIRMED",
                    ),
                )

        sequence += 1
        finish = (
            FinishReason.TOOL_CALLS
            if emitted_tool_calls
            else FinishReason.STOP
        )
        yield NormalizedStreamItem(
            StreamKind.FINISH, sequence, finish_reason=finish
        )
    finally:
        if owns_client:
            http.close()


__all__ = [
    "DEFAULT_OPENCODE_BASE_URL",
    "DEFAULT_OPENCODE_LOCAL_BASE_URL",
    "OPENCODE_CLOUD_BASE_URL",
    "OpenCodeBridgeProcessManager",
    "OpenCodeModelCatalogClient",
    "is_opencode_cloud",
    "normalize_opencode_base_url",
    "stream_opencode_bridge",
]
