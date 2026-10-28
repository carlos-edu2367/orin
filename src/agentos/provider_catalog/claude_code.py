"""Claude Code CLI (`claude -p`) local subscription bridge and catalog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from agentos.agentic.provider_stream import (
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
    _tag_prefix_len,
)
from agentos.agentic.tools.public_names import PUBLIC_NAMES
from agentos.provider_catalog.opencode import (
    _ORIN_TOOL_CALL_RE,
    _build_bridge_system_prompt,
    _extract_tool_declarations,
    _format_bridge_conversation_transcript,
    _parse_orin_tool_calls,
)
from agentos.providers.models import (
    FinishReason,
    Measurement,
    ProviderUsage,
)


DEFAULT_CLAUDE_CLI_BRIDGE_URL = "http://127.0.0.1:18447"
ANTHROPIC_CLOUD_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_BASE_URL = ANTHROPIC_CLOUD_BASE_URL

_CLOUD_HOSTS = frozenset({"api.anthropic.com", "anthropic.com"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_FALLBACK_MODEL_RE = re.compile(r"^[A-Za-z0-9.,_:-]{1,128}$")
_VALID_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

_STRIPPED_ENV_EXACT = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    }
)

_STRIPPED_ENV_PREFIXES = (
    "ANTHROPIC_DEFAULT_",
    "ANTHROPIC_API_",
    "ANTHROPIC_AUTH_",
    "ANTHROPIC_BEDROCK_",
    "ANTHROPIC_VERTEX_",
    "ANTHROPIC_FOUNDRY_",
)

_INTERNAL_BY_PUBLIC = {public: internal for internal, public in PUBLIC_NAMES.items()}
_INTERNAL_BY_LOWER_PUBLIC = {public.lower(): internal for internal, public in PUBLIC_NAMES.items()}

_PUBLIC_ARG_RENAMES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path", "filePath": "path"},
    "write_file": {"file_path": "path", "filePath": "path"},
    "edit_file": {
        "file_path": "path",
        "filePath": "path",
        "old_string": "old_text",
        "new_string": "new_text",
    },
}

_SUBSCRIPTION_MODELS: tuple[tuple[str, str, int], ...] = (
    ("claude-opus-4-7", "Claude Opus 4.7 (Assinatura CLI)", 200_000),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Assinatura CLI)", 200_000),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5 (Assinatura CLI)", 200_000),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (Assinatura CLI)", 200_000),
)


class ProviderTransportError(ProviderKeyRejected):
    """Raised when the local Claude Code CLI bridge fails auth, security, or execution."""


@dataclass(frozen=True, slots=True)
class StreamUsage(ProviderUsage):
    """Extended usage snapshot preserving Anthropic prompt cache creation and read metrics."""

    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


# Alias matching opencode / prompt terminology
_extract_bridge_tool_calls = _parse_orin_tool_calls


def normalize_anthropic_base_url(value: str) -> str:
    """Validate and normalize an Anthropic Local CLI Bridge or Cloud API URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Anthropic base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Anthropic base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Anthropic base URL must not contain credentials, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Anthropic base URL has an invalid port") from exc

    host = (parsed.hostname or "").lower()
    is_cloud = host in _CLOUD_HOSTS
    is_loopback = host in _LOOPBACK_HOSTS
    if not is_cloud and not is_loopback:
        raise ValueError(
            "Anthropic base URL must be a local loopback address or api.anthropic.com"
        )
    if is_cloud:
        if parsed.scheme != "https":
            raise ValueError("Anthropic Cloud base URL must use HTTPS")
        return ANTHROPIC_CLOUD_BASE_URL

    path = parsed.path.rstrip("/")
    for suffix in ("/v1/models", "/v1/messages", "/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_claude_cli_bridge_url(base_url: str | None) -> bool:
    """Return True when the URL points to the local Claude Code CLI bridge."""
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    try:
        normalized = normalize_anthropic_base_url(base_url)
    except ValueError:
        return False
    host = (urlsplit(normalized).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def is_anthropic_cloud(base_url: str | None) -> bool:
    """Return True when the URL points to the hosted Anthropic Console API."""
    return not is_claude_cli_bridge_url(base_url)


def build_isolated_claude_env(
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a sanitized environment for `claude -p` that forces OAuth subscription auth."""
    source = os.environ if base_env is None else base_env
    isolated: dict[str, str] = {}
    for key, val in source.items():
        upper_key = str(key).upper()
        if upper_key in _STRIPPED_ENV_EXACT:
            continue
        if any(upper_key.startswith(prefix) for prefix in _STRIPPED_ENV_PREFIXES):
            continue
        isolated[str(key)] = str(val)

    isolated["PYTHONIOENCODING"] = "utf-8"
    isolated["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    isolated["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    return isolated


def _resolve_claude_binary(
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Locate the native `claude.exe` binary, prioritizing native PE executables over `.cmd` wrappers."""
    source_env = os.environ if env is None else env
    appdata = source_env.get("APPDATA")
    userprofile = source_env.get("USERPROFILE")
    localappdata = source_env.get("LOCALAPPDATA")
    home = source_env.get("HOME")

    exe_candidates: list[Path] = []
    if appdata:
        exe_candidates.append(
            Path(appdata)
            / "npm"
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
    if userprofile:
        exe_candidates.append(Path(userprofile) / ".local" / "bin" / "claude.exe")
    if localappdata:
        exe_candidates.append(Path(localappdata) / "Programs" / "claude" / "claude.exe")
    if home:
        exe_candidates.append(Path(home) / ".local" / "bin" / "claude.exe")

    for candidate in exe_candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("claude", path=source_env.get("PATH"))
    if found:
        found_path = Path(found)
        sibling_npm_exe = (
            found_path.parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
        if sibling_npm_exe.is_file():
            return str(sibling_npm_exe)
        if found_path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            direct_exe = found_path.with_suffix(".exe")
            if direct_exe.is_file():
                return str(direct_exe)
        return str(found_path)

    if appdata:
        cmd_candidate = Path(appdata) / "npm" / "claude.cmd"
        if cmd_candidate.is_file():
            return str(cmd_candidate)
    if home:
        posix_candidate = Path(home) / ".local" / "bin" / "claude"
        if posix_candidate.is_file():
            return str(posix_candidate)
    return None


def _build_claude_cli_command(
    *,
    binary: str,
    system_prompt_file: str | Path,
    model: str | None = None,
    effort: str | None = None,
    fallback_model: str | None = None,
) -> list[str]:
    """Construct the isolated `claude -p` argument list (never using `--bare`)."""
    if not isinstance(binary, str) or not binary.strip() or "\x00" in binary:
        raise ValueError("Valid Claude CLI binary path is required")

    prompt_file_str = str(system_prompt_file).strip()
    if (
        not prompt_file_str
        or prompt_file_str.startswith("-")
        or "\x00" in prompt_file_str
        or "\n" in prompt_file_str
        or "\r" in prompt_file_str
    ):
        raise ValueError("Invalid system_prompt_file path")

    cmd: list[str] = [
        binary.strip(),
        "-p",
        "--safe-mode",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-chrome",
        "--tools",
        "",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--max-turns",
        "1",
        "--system-prompt-file",
        prompt_file_str,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]

    if model is not None and str(model).strip():
        cleaned_model = str(model).strip()
        if "/" in cleaned_model:
            _, cleaned_model = cleaned_model.split("/", 1)
            cleaned_model = cleaned_model.strip()
        if cleaned_model.startswith("-") or not _SAFE_MODEL_RE.match(cleaned_model):
            raise ValueError(f"Invalid model identifier for Claude CLI: {model!r}")
        cmd.extend(["--model", cleaned_model])

    if effort is not None and str(effort).strip():
        cleaned_effort = str(effort).strip().lower()
        if cleaned_effort not in _VALID_EFFORT_LEVELS:
            raise ValueError(f"Invalid effort level for Claude CLI: {effort!r}")
        cmd.extend(["--effort", cleaned_effort])

    if fallback_model is not None and str(fallback_model).strip():
        cleaned_fallback = str(fallback_model).strip()
        if cleaned_fallback.startswith("-") or not _SAFE_FALLBACK_MODEL_RE.match(
            cleaned_fallback
        ):
            raise ValueError(f"Invalid fallback_model for Claude CLI: {fallback_model!r}")
        cmd.extend(["--fallback-model", cleaned_fallback])

    return cmd


def check_claude_cli_status(
    *,
    binary: str | None = None,
    timeout: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Verify that `claude` CLI is installed and authenticated via a `claude.ai` OAuth subscription."""
    resolved_binary = binary or _resolve_claude_binary(env)
    if not resolved_binary:
        raise ProviderTransportError(
            "Claude Code CLI executable ('claude') was not found on this machine. "
            "Install @anthropic-ai/claude-code and run 'claude auth login'."
        )

    isolated_env = build_isolated_claude_env(env)
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    try:
        completed = subprocess.run(
            [resolved_binary, "auth", "status"],
            env=isolated_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
             check=False,
        )
    except Exception as exc:
        raise ProviderTransportError(
            f"Failed to execute 'claude auth status': {exc}"
        ) from exc

    raw_output = (completed.stdout or "").strip()
    if not raw_output:
        stderr_hint = (completed.stderr or "").strip()
        raise ProviderTransportError(
            f"Claude Code CLI returned empty auth status. {stderr_hint}".strip()
        )

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ProviderTransportError(
            "Claude Code CLI returned invalid JSON for 'claude auth status'."
        ) from exc

    if not isinstance(payload, Mapping):
        raise ProviderTransportError(
            "Claude Code CLI returned an unexpected auth status payload."
        )

    logged_in = payload.get("loggedIn") is True
    auth_method = str(payload.get("authMethod") or "").strip().lower()
    if not logged_in:
        raise ProviderTransportError(
            "Claude Code CLI is not logged in. Run 'claude auth login' in your terminal."
        )
    if auth_method != "claude.ai":
        raise ProviderTransportError(
            f"Claude Code CLI is authenticated via '{auth_method}' instead of a 'claude.ai' OAuth subscription. "
            "Run 'claude auth login' to use your Pro/Max/Team subscription limits."
        )

    return dict(payload)


class ClaudeCodeModelCatalogClient:
    """Model catalog client for the local Claude Code CLI (`claude -p`) subscription."""

    def __init__(self, *, timeout: float = 8.0) -> None:
        self._timeout = timeout

    def fetch(self, api_key: str = "", *, base_url: str = "") -> list[dict[str, object]]:
        check_claude_cli_status(timeout=self._timeout)
        models: list[dict[str, object]] = []
        for model_id, display_name, context_window in _SUBSCRIPTION_MODELS:
            models.append(
                {
                    "id": model_id,
                    "name": display_name,
                    "display_name": display_name,
                    "context_length": context_window,
                    "max_input_tokens": context_window,
                    "supported_parameters": ["tools", "vision", "reasoning"],
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                    "capabilities": {
                        "image_input": {"supported": True},
                        "thinking": {"supported": True},
                        "reasoning": {"supported": True},
                        "tools": {"supported": True},
                        "vision": {"supported": True},
                        "pdf_input": {"supported": True},
                    },
                }
            )
        return models

    def list_models(self, api_key: str = "", *, base_url: str = "") -> list[dict[str, object]]:
        return self.fetch(api_key, base_url=base_url)


def _kill_process_tree(proc: Any) -> None:
    """Terminate the subprocess and any child processes without leaving orphans."""
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                creationflags=creationflags,
                check=False,
            )
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _resolve_native_tool_call(
    raw_name: str,
    raw_input: Mapping[str, Any],
    allowed_names: set[str],
) -> tuple[str, str] | None:
    """Map a native or MCP tool_use block to an authorized Orin tool name and JSON arguments."""
    candidate = raw_name.strip()
    if not candidate:
        return None

    # Strip `mcp__<server>__<tool>` prefix if present
    if candidate.startswith("mcp__"):
        parts = candidate.split("__", 2)
        if len(parts) == 3 and parts[2]:
            candidate = parts[2]

    mapped_name = ""
    if not allowed_names:
        mapped_name = _INTERNAL_BY_PUBLIC.get(
            candidate, _INTERNAL_BY_LOWER_PUBLIC.get(candidate.lower(), candidate)
        )
    elif candidate in allowed_names:
        mapped_name = candidate
    else:
        internal = _INTERNAL_BY_PUBLIC.get(
            candidate, _INTERNAL_BY_LOWER_PUBLIC.get(candidate.lower(), "")
        )
        if internal and internal in allowed_names:
            mapped_name = internal
        else:
            public = PUBLIC_NAMES.get(candidate, "")
            if public and public in allowed_names:
                mapped_name = public

    if not mapped_name:
        return None

    args_dict = dict(raw_input) if isinstance(raw_input, Mapping) else {}
    canonical_internal = _INTERNAL_BY_PUBLIC.get(mapped_name, mapped_name)
    renames = _PUBLIC_ARG_RENAMES.get(canonical_internal, {})
    for src_key, dst_key in renames.items():
        if src_key in args_dict and dst_key not in args_dict:
            args_dict[dst_key] = args_dict.pop(src_key)

    return mapped_name, json.dumps(args_dict, ensure_ascii=False)


def _format_reset_timestamp(resets_at: object) -> str | None:
    if not isinstance(resets_at, (int, float)) or isinstance(resets_at, bool) or resets_at <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(float(resets_at), tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return None


def _build_stream_usage(raw_usage: Mapping[str, Any] | None) -> StreamUsage:
    if not isinstance(raw_usage, Mapping):
        return StreamUsage(measurement=Measurement.CONFIRMED)

    def _int_val(key: str) -> int:
        val = raw_usage.get(key)
        return val if isinstance(val, int) and not isinstance(val, bool) and val >= 0 else 0

    uncached_in = _int_val("input_tokens")
    cache_creation = _int_val("cache_creation_input_tokens")
    cache_read = _int_val("cache_read_input_tokens")
    output_tokens = _int_val("output_tokens")

    total_input = uncached_in + cache_creation + cache_read
    total_tokens = total_input + output_tokens

    return StreamUsage(
        input_tokens=total_input,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cache_read,
        reasoning_tokens=None,
        measurement=Measurement.CONFIRMED,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )


def stream_claude_code_bridge(
    request: Mapping[str, Any] | None = None,
    *,
    base_url: str = DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    model: str = "",
    messages: Iterable[Mapping[str, Any]] = (),
    tools: Iterable[Mapping[str, Any]] = (),
    tool_choice: object = None,
    effort: str | None = None,
    timeout_seconds: float = 180.0,
    timeout: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    binary: str | None = None,
    popen_factory: Callable[..., Any] | None = None,
) -> Iterator[NormalizedStreamItem]:
    """Drive `claude -p` over stream-json and yield normalized Orin stream items."""
    if request is not None:
        model = str(model or request.get("model") or "")
        messages = request.get("messages") or messages  # type: ignore[assignment]
        tools = request.get("tools") or tools  # type: ignore[assignment]
        if tool_choice is None:
            tool_choice = request.get("tool_choice")
        if effort is None and request.get("effort") is not None:
            effort = str(request.get("effort"))

    normalize_anthropic_base_url(base_url or DEFAULT_CLAUDE_CLI_BRIDGE_URL)
    effective_timeout = float(timeout if timeout is not None else timeout_seconds)

    resolved_binary = binary or _resolve_claude_binary()
    if not resolved_binary:
        raise ProviderTransportError(
            "Claude Code CLI executable ('claude') was not found on this machine."
        )

    withheld = isinstance(tool_choice, str) and tool_choice.lower() == "none"
    declarations, allowed_names = (
        ([], set()) if withheld else _extract_tool_declarations(tools)
    )
    system_parts, prompt_text = _format_bridge_conversation_transcript(messages)
    system_prompt = _build_bridge_system_prompt(system_parts, declarations)

    prompt_file_path: Path | None = None
    proc: Any = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".prompt.txt",
            prefix="orin-claude-sys-",
            delete=False,
        ) as tmp_file:
            tmp_file.write(system_prompt or "You are a helpful assistant.")
            prompt_file_path = Path(tmp_file.name)

        effective_effort = (
            effort
            if effort and str(effort).strip().lower() in _VALID_EFFORT_LEVELS
            else None
        )
        cmd = _build_claude_cli_command(
            binary=resolved_binary,
            system_prompt_file=prompt_file_path,
            model=model or None,
            effort=effective_effort,
        )
        isolated_env = build_isolated_claude_env()
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )

        if popen_factory is not None:
            proc = popen_factory(
                cmd,
                env=isolated_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        else:
            proc = subprocess.Popen(
                cmd,
                env=isolated_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )

        # Immediately send the user turn via stream-json and close stdin so claude.exe
        # never waits 3,000ms on an open stdin pipe.
        user_envelope = {
            "type": "user",
            "message": {
                "role": "user",
                "content": prompt_text or "Continue.",
            },
        }
        if getattr(proc, "stdin", None) is not None:
            try:
                proc.stdin.write(json.dumps(user_envelope, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass

        line_queue: queue.Queue[str | None] = queue.Queue()
        stderr_chunks: list[str] = []

        def _read_stdout() -> None:
            try:
                stdout_stream = getattr(proc, "stdout", None)
                if stdout_stream is not None:
                    for raw_line in stdout_stream:
                        line_queue.put(str(raw_line))
            except Exception:
                pass
            finally:
                line_queue.put(None)

        def _read_stderr() -> None:
            try:
                stderr_stream = getattr(proc, "stderr", None)
                if stderr_stream is not None:
                    data = stderr_stream.read()
                    if data:
                        stderr_chunks.append(str(data))
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        sequence = 0
        saw_stream_thinking = False
        saw_stream_text = False
        saw_result_packet = False
        active_close_tag: str | None = None
        text_tag_buffer = ""
        emitted_tool_calls: list[tuple[str, str, str]] = []
        emitted_native_tool_ids: set[str] = set()
        pending_native_tools: dict[int, dict[str, Any]] = {}
        latest_usage_raw: dict[str, Any] = {}
        latest_rate_limit_info: dict[str, Any] = {}
        deadline = time.monotonic() + effective_timeout
        tool_tag_pairs = (
            ("<orin_tool_call>", "</orin_tool_call>"),
            ("<tool_call>", "</tool_call>"),
        )

        def _feed_streaming_text(chunk: str) -> Iterator[NormalizedStreamItem]:
            nonlocal sequence, active_close_tag, text_tag_buffer
            buf = text_tag_buffer + chunk
            text_tag_buffer = ""
            while buf:
                if active_close_tag is None:
                    matched_pair: tuple[int, str, str] | None = None
                    for open_tag, close_tag in tool_tag_pairs:
                        pos = buf.find(open_tag)
                        if pos != -1 and (matched_pair is None or pos < matched_pair[0]):
                            matched_pair = (pos, open_tag, close_tag)
                    if matched_pair is not None:
                        pos, open_tag, close_tag = matched_pair
                        before = buf[:pos]
                        after = buf[pos + len(open_tag) :]
                        if before:
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.TEXT, sequence, text=before
                            )
                        active_close_tag = close_tag
                        buf = after
                        continue
                    prefix_len = max(
                        _tag_prefix_len(buf, open_tag) for open_tag, _ in tool_tag_pairs
                    )
                    if prefix_len > 0:
                        emit_part = buf[:-prefix_len]
                        text_tag_buffer = buf[-prefix_len:]
                    else:
                        emit_part = buf
                    if emit_part:
                        sequence += 1
                        yield NormalizedStreamItem(
                            StreamKind.TEXT, sequence, text=emit_part
                        )
                    break
                else:
                    if active_close_tag in buf:
                        inner_xml, after = buf.split(active_close_tag, 1)
                        full_tag = f"<orin_tool_call>{inner_xml}</orin_tool_call>"
                        _, parsed_calls = _parse_orin_tool_calls(full_tag, allowed_names)
                        emitted_tool_calls.extend(parsed_calls)
                        active_close_tag = None
                        buf = after
                        continue
                    text_tag_buffer = buf
                    break

        def _flush_streaming_text_buffer() -> Iterator[NormalizedStreamItem]:
            nonlocal sequence, active_close_tag, text_tag_buffer
            if not text_tag_buffer:
                return
            leftover = text_tag_buffer
            text_tag_buffer = ""
            if active_close_tag is not None:
                active_close_tag = None
                _, parsed_calls = _parse_orin_tool_calls(
                    f"<orin_tool_call>{leftover}</orin_tool_call>", allowed_names
                )
                emitted_tool_calls.extend(parsed_calls)
            elif leftover.strip():
                sequence += 1
                yield NormalizedStreamItem(StreamKind.TEXT, sequence, text=leftover)

        def _flush_pending_native_tool(index: int) -> None:
            entry = pending_native_tools.pop(index, None)
            if not entry or withheld:
                return
            call_id = str(entry.get("id") or f"call_{uuid4().hex[:12]}")
            if call_id in emitted_native_tool_ids:
                return
            raw_name = str(entry.get("name") or "")
            json_parts: list[str] = entry.get("json_parts") or []
            raw_input: Mapping[str, Any] = {}
            if json_parts:
                joined_json = "".join(json_parts).strip()
                try:
                    parsed_input = json.loads(joined_json)
                    if isinstance(parsed_input, Mapping):
                        raw_input = parsed_input
                except json.JSONDecodeError:
                    raw_input = {}
            elif isinstance(entry.get("input"), Mapping):
                raw_input = entry["input"]

            resolved = _resolve_native_tool_call(raw_name, raw_input, allowed_names)
            if resolved is not None:
                mapped_name, args_json = resolved
                emitted_native_tool_ids.add(call_id)
                emitted_tool_calls.append((call_id, mapped_name, args_json))

        while True:
            if cancel_check is not None and cancel_check():
                _kill_process_tree(proc)
                return

            if time.monotonic() >= deadline:
                _kill_process_tree(proc)
                raise ProviderTransportError(
                    f"Claude Code CLI timed out after {effective_timeout:.1f}s"
                )

            try:
                raw_line = line_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if raw_line is None:
                break

            line = raw_line.strip()
            if not line:
                continue

            try:
                packet = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(packet, Mapping):
                continue

            pkt_type = str(packet.get("type") or "")

            if pkt_type == "system" and packet.get("subtype") == "init":
                api_key_source = str(packet.get("apiKeySource") or "none").strip()
                if api_key_source.lower() not in {"none", ""}:
                    _kill_process_tree(proc)
                    raise ProviderTransportError(
                        f"Security abort: Claude CLI reported apiKeySource={api_key_source!r} instead of 'none'. "
                        "Refusing to run to prevent unexpected API token charges; only OAuth subscription is permitted."
                    )
                continue

            if pkt_type == "error":
                err_obj = packet.get("error")
                err_msg = (
                    str(err_obj.get("message") or err_obj.get("type") or "")
                    if isinstance(err_obj, Mapping)
                    else str(err_obj or packet.get("message") or "Claude CLI error")
                )
                raise ProviderTransportError(err_msg or "Claude CLI error")

            if pkt_type == "rate_limit_event":
                info = packet.get("rate_limit_info")
                if isinstance(info, Mapping):
                    latest_rate_limit_info = dict(info)
                continue

            if pkt_type == "stream_event":
                event = packet.get("event")
                if not isinstance(event, Mapping):
                    continue
                ev_type = str(event.get("type") or "")
                if ev_type == "error":
                    err_obj = event.get("error")
                    err_msg = (
                        str(err_obj.get("message") or err_obj.get("type") or "")
                        if isinstance(err_obj, Mapping)
                        else str(err_obj or "Claude stream error")
                    )
                    raise ProviderTransportError(err_msg or "Claude stream error")
                if ev_type == "message_start":
                    msg_obj = event.get("message")
                    if isinstance(msg_obj, Mapping) and isinstance(
                        msg_obj.get("usage"), Mapping
                    ):
                        latest_usage_raw.update(msg_obj["usage"])
                elif ev_type == "message_delta":
                    if isinstance(event.get("usage"), Mapping):
                        latest_usage_raw.update(event["usage"])
                elif ev_type == "content_block_start":
                    idx = int(event.get("index", 0))
                    block = event.get("content_block")
                    if isinstance(block, Mapping):
                        b_type = str(block.get("type") or "")
                        if b_type == "tool_use":
                            pending_native_tools[idx] = {
                                "id": str(block.get("id") or f"call_{uuid4().hex[:12]}"),
                                "name": str(block.get("name") or ""),
                                "input": block.get("input")
                                if isinstance(block.get("input"), Mapping)
                                else {},
                                "json_parts": [],
                            }
                        elif b_type == "thinking":
                            initial_thinking = str(block.get("thinking") or "")
                            if initial_thinking:
                                saw_stream_thinking = True
                                sequence += 1
                                yield NormalizedStreamItem(
                                    StreamKind.THINKING,
                                    sequence,
                                    thinking=initial_thinking,
                                )
                elif ev_type == "content_block_delta":
                    idx = int(event.get("index", 0))
                    delta = event.get("delta")
                    if not isinstance(delta, Mapping):
                        continue
                    d_type = str(delta.get("type") or "")
                    if d_type == "thinking_delta":
                        thinking_piece = str(delta.get("thinking") or "")
                        if thinking_piece:
                            saw_stream_thinking = True
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.THINKING,
                                sequence,
                                thinking=thinking_piece,
                            )
                    elif d_type == "signature_delta":
                        sig_piece = str(delta.get("signature") or "")
                        if sig_piece:
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.THINKING,
                                sequence,
                                signature=sig_piece,
                            )
                    elif d_type == "text_delta":
                        text_piece = str(delta.get("text") or "")
                        if text_piece:
                            saw_stream_text = True
                            yield from _feed_streaming_text(text_piece)
                    elif d_type == "input_json_delta":
                        partial_json = str(delta.get("partial_json") or "")
                        if idx in pending_native_tools and partial_json:
                            pending_native_tools[idx]["json_parts"].append(partial_json)
                elif ev_type == "content_block_stop":
                    idx = int(event.get("index", 0))
                    if idx in pending_native_tools:
                        _flush_pending_native_tool(idx)
                continue

            if pkt_type == "assistant":
                for pending_idx in list(pending_native_tools.keys()):
                    _flush_pending_native_tool(pending_idx)
                msg = packet.get("message")
                if not isinstance(msg, Mapping):
                    continue
                if isinstance(msg.get("usage"), Mapping):
                    latest_usage_raw.update(msg["usage"])
                content_list = msg.get("content")
                if not isinstance(content_list, list):
                    continue
                for block in content_list:
                    if not isinstance(block, Mapping):
                        continue
                    b_type = str(block.get("type") or "")
                    if b_type == "thinking" and not saw_stream_thinking:
                        thinking_text = str(block.get("thinking") or "")
                        if thinking_text:
                            sequence += 1
                            yield NormalizedStreamItem(
                                StreamKind.THINKING,
                                sequence,
                                thinking=thinking_text,
                            )
                    elif b_type == "text" and not saw_stream_text:
                        raw_text = str(block.get("text") or "")
                        if raw_text:
                            normalized_raw_text = raw_text.replace(
                                "<tool_call>", "<orin_tool_call>"
                            ).replace("</tool_call>", "</orin_tool_call>")
                            cleaned_text, parsed_calls = _parse_orin_tool_calls(
                                normalized_raw_text, allowed_names
                            )
                            if cleaned_text:
                                sequence += 1
                                yield NormalizedStreamItem(
                                    StreamKind.TEXT,
                                    sequence,
                                    text=cleaned_text,
                                )
                            emitted_tool_calls.extend(parsed_calls)
                    elif b_type == "tool_use" and not withheld:
                        call_id = str(block.get("id") or f"call_{uuid4().hex[:12]}")
                        if call_id in emitted_native_tool_ids:
                            continue
                        raw_name = str(block.get("name") or "")
                        raw_input = (
                            block.get("input")
                            if isinstance(block.get("input"), Mapping)
                            else {}
                        )
                        resolved = _resolve_native_tool_call(
                            raw_name, raw_input, allowed_names
                        )
                        if resolved is not None:
                            mapped_name, args_json = resolved
                            emitted_native_tool_ids.add(call_id)
                            emitted_tool_calls.append((call_id, mapped_name, args_json))
                continue

            if pkt_type == "result":
                saw_result_packet = True
                if isinstance(packet.get("usage"), Mapping):
                    latest_usage_raw.update(packet["usage"])
                is_error = packet.get("is_error") is True or str(
                    packet.get("subtype") or ""
                ).startswith("error")
                if is_error:
                    err_msg = str(
                        packet.get("result")
                        or packet.get("error")
                        or "Claude Code CLI execution failed"
                    ).strip()
                    resets_at = latest_rate_limit_info.get("resetsAt")
                    if resets_at is None:
                        windows = latest_rate_limit_info.get("unifiedWindows")
                        if isinstance(windows, Mapping):
                            five_hr = windows.get("five_hour")
                            if isinstance(five_hr, Mapping):
                                resets_at = five_hr.get("resetsAt")
                    formatted_reset = _format_reset_timestamp(resets_at)
                    if formatted_reset and formatted_reset not in err_msg:
                        err_msg = f"{err_msg} (resets at {formatted_reset})"
                    raise ProviderTransportError(
                        err_msg,
                        status_code=429
                        if "limit" in err_msg.lower()
                        or latest_rate_limit_info.get("status") == "rejected"
                        else 500,
                        exhausted=False,
                    )
                break

        for pending_idx in list(pending_native_tools.keys()):
            _flush_pending_native_tool(pending_idx)
        yield from _flush_streaming_text_buffer()

        if cancel_check is not None and cancel_check():
            _kill_process_tree(proc)
            return

        if not saw_result_packet:
            stderr_thread.join(timeout=0.2)
            exit_code = proc.poll()
            stderr_text = "".join(stderr_chunks).strip()
            if (exit_code is not None and exit_code != 0) or (
                not saw_stream_text
                and not saw_stream_thinking
                and not emitted_tool_calls
                and stderr_text
            ):
                raise ProviderTransportError(
                    f"Claude Code CLI exited unexpectedly (code={exit_code}): {stderr_text or 'no output'}"
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

        final_usage = _build_stream_usage(latest_usage_raw)
        zero_cost = Decimal("0.0")
        sequence += 1
        yield NormalizedStreamItem(
            StreamKind.USAGE,
            sequence,
            usage=final_usage,
            cost=zero_cost,
        )

        sequence += 1
        finish_reason = (
            FinishReason.TOOL_CALLS if emitted_tool_calls else FinishReason.STOP
        )
        yield NormalizedStreamItem(
            StreamKind.FINISH,
            sequence,
            finish_reason=finish_reason,
            usage=final_usage,
            cost=zero_cost,
        )
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)
        if prompt_file_path is not None:
            try:
                prompt_file_path.unlink(missing_ok=True)
            except Exception:
                pass


__all__ = [
    "ANTHROPIC_CLOUD_BASE_URL",
    "ClaudeCodeModelCatalogClient",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_CLAUDE_CLI_BRIDGE_URL",
    "ProviderTransportError",
    "StreamUsage",
    "_build_bridge_system_prompt",
    "_build_claude_cli_command",
    "_extract_bridge_tool_calls",
    "_format_bridge_conversation_transcript",
    "_kill_process_tree",
    "_resolve_claude_binary",
    "build_isolated_claude_env",
    "check_claude_cli_status",
    "is_anthropic_cloud",
    "is_claude_cli_bridge_url",
    "normalize_anthropic_base_url",
    "stream_claude_code_bridge",
]
