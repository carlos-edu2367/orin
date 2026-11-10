"""Google Antigravity CLI (`agy`) implementation of `CliAgentAdapter`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from agentos.providers.antigravity_cli import catalog as _catalog_mod
from agentos.providers.antigravity_cli import command as _command_mod
from agentos.providers.antigravity_cli.catalog import (
    FALLBACK_ANTIGRAVITY_MODELS,
    _infer_supported_efforts,
    context_window_for,
    discover_antigravity_models,
    get_cached_antigravity_models,
    set_cached_antigravity_models,
)
from agentos.providers.antigravity_cli.command import (
    DEFAULT_ANTIGRAVITY_MODEL,
    antigravity_version,
    build_antigravity_command,
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
    encode_antigravity_user_turn,
    is_antigravity_version_supported,
    redact_antigravity_argv,
    resolve_antigravity_binary,
    wire_antigravity_no_tools,
    wire_antigravity_tool_server,
)
from agentos.providers.antigravity_cli.errors import classify_antigravity_error
from agentos.providers.antigravity_cli.stream import (
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    AntigravityStreamTranslator,
)
from agentos.providers.cli_adapter import (
    CliCapabilities,
    CliErrorInfo,
    CliModelInfo,
    CliResult,
    CliStreamTranslator,
    CliTurnRequest,
    RateLimitState,
    ToolServerWiring,
)

# The functions as imported, before any test patches this module or the source modules: a name here that is
# still this object is not an override, so the source module's (possibly patched) function is the one to call.
_CANONICAL_RESOLVE_BINARY = resolve_antigravity_binary
_CANONICAL_DISCOVER_MODELS = discover_antigravity_models


class AntigravityCliAdapter:
    """Adapter for Google's `agy` CLI running in isolated `--input-format stream-json --output-format stream-json` mode."""

    name: str = "antigravity"
    capabilities: CliCapabilities = CliCapabilities(
        native_tool_loop=True,
        tool_list_changed=False,
        # agy forwards an image block of an MCP tool result to the model
        # (it offloads the bytes to a file and attaches them), so Orin's
        # view_file can hand Gemini and Claude the picture itself.
        images_in_tool_results=True,
        persistent_stdin=True,
        reports_tool_inventory=True,
        forbidden_item_kinds=FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
        # Calibrated from orin.db telemetry on gemini-4-argon-high (48 runs, 2603 iterations):
        # per-iteration silence p50=1.9s, p90=24.7s, p99=92.4s, max=194.7s (per-run max p50=63.1s,
        # p90=169.5s, p99=188.3s, max=194.7s; 4 runs died at the old 180s cap). 600s (~3x max,
        # plus one +50% waiting_model extension if alive) covers deep thinking on large contexts.
        idle_timeout_seconds=600,
        max_tool_call_seconds=180,
        # agy 1.5.x cuts a longer USER_INPUT at the end (re-checked on 1.5.4: 300 KB lost the question).
        max_user_message_bytes=190_791,
    )

    def __init__(
        self,
        *,
        binary: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._binary = binary
        self._env = dict(env) if env is not None else None
        self._last_binary: str | None = binary

    def _effective_env(
        self, env: Mapping[str, str] | None = None
    ) -> Mapping[str, str] | None:
        return env if env is not None else self._env

    def _resolve_binary_path(
        self, env: Mapping[str, str] | None = None
    ) -> str | None:
        if self._binary:
            return self._binary
        resolve_fn = (
            resolve_antigravity_binary
            if resolve_antigravity_binary is not _CANONICAL_RESOLVE_BINARY
            else _command_mod.resolve_antigravity_binary
        )
        return resolve_fn(self._effective_env(env))

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        effective_env = self._effective_env(env)
        binary = self._resolve_binary_path(effective_env)
        if not binary:
            return None, None
        version = antigravity_version(binary, env=effective_env)
        return binary, version

    def binary_version(
        self,
        binary: str,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, int, int] | None:
        return antigravity_version(binary, env=env)

    def version_supported(
        self, version: tuple[int, int, int] | None
    ) -> bool:
        return is_antigravity_version_supported(version)

    def wire_tool_server(
        self, handle: Any, directory: Path
    ) -> ToolServerWiring:
        server_name = getattr(handle, "server_name", "orin") or "orin"
        return wire_antigravity_tool_server(
            handle, directory, server_name=server_name
        )

    def wire_no_tools(
        self, directory: Path, *, system_prompt_file: Path | None = None
    ) -> ToolServerWiring:
        return wire_antigravity_no_tools(
            directory, system_prompt_file=system_prompt_file
        )

    def build_command(
        self,
        request: CliTurnRequest,
        wiring: ToolServerWiring | None = None,
    ) -> list[str]:
        supported_efforts: Sequence[str] | None = None
        cleaned_model = (
            clean_antigravity_model_id(request.model)
            or DEFAULT_ANTIGRAVITY_MODEL
        )
        for info in self.models():
            if info.id == cleaned_model:
                supported_efforts = info.supported_efforts
                break
        if supported_efforts is None:
            supported_efforts = _infer_supported_efforts(cleaned_model)
        return build_antigravity_command(
            request,
            wiring,
            supported_efforts=supported_efforts,
        )

    def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
        return encode_antigravity_user_turn(content)

    def loggable_command(self, argv: Sequence[str]) -> list[str]:
        return redact_antigravity_argv(argv)

    def environment(
        self,
        base: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None = None,
        tool_timeout_ms: int | None = None,
    ) -> dict[str, str]:
        return build_isolated_antigravity_env(base)

    def stream_translator(self) -> CliStreamTranslator:
        return AntigravityStreamTranslator()

    def classify_error(
        self,
        result: CliResult,
        rate_limit: RateLimitState | None = None,
    ) -> CliErrorInfo:
        return classify_antigravity_error(result, rate_limit)

    def models(self) -> Sequence[CliModelInfo]:
        binary = self._resolve_binary_path() or self._last_binary
        if binary:
            cached = get_cached_antigravity_models(binary)
            if cached is not None:
                return cached
        return FALLBACK_ANTIGRAVITY_MODELS

    def refresh_models(
        self, env: Mapping[str, str] | None = None
    ) -> Sequence[CliModelInfo]:
        effective_env = self._effective_env(env)
        binary = self._resolve_binary_path(effective_env)
        if not binary:
            return FALLBACK_ANTIGRAVITY_MODELS

        self._last_binary = binary
        discover_fn = (
            discover_antigravity_models
            if discover_antigravity_models is not _CANONICAL_DISCOVER_MODELS
            else _catalog_mod.discover_antigravity_models
        )
        try:
            discovered = discover_fn(
                binary,
                run_models_cmd=True,
                timeout=15.0,
                env=effective_env,
            )
        except Exception:
            return FALLBACK_ANTIGRAVITY_MODELS

        if discovered and discovered is not FALLBACK_ANTIGRAVITY_MODELS:
            return set_cached_antigravity_models(binary, discovered)

        return FALLBACK_ANTIGRAVITY_MODELS

    def context_window(self, model_id: str | None) -> int:
        return context_window_for(model_id, self.models())


__all__ = [
    "AntigravityCliAdapter",
]
