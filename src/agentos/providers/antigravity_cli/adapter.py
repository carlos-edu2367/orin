"""Google Antigravity CLI (`agy`) implementation of `CliAgentAdapter`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from agentos.providers.antigravity_cli.catalog import (
    context_window_for,
    discover_antigravity_models,
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


class AntigravityCliAdapter:
    """Adapter for Google's `agy` CLI running in isolated `--input-format stream-json --output-format stream-json` mode."""

    name: str = "antigravity"
    capabilities: CliCapabilities = CliCapabilities(
        native_tool_loop=True,
        tool_list_changed=False,
        images_in_tool_results=False,
        persistent_stdin=True,
        reports_tool_inventory=True,
        forbidden_item_kinds=FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    )

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        binary = resolve_antigravity_binary(env)
        if not binary:
            return None, None
        version = antigravity_version(binary, env=env)
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
        return discover_antigravity_models()

    def context_window(self, model_id: str | None) -> int:
        return context_window_for(model_id, self.models())


__all__ = [
    "AntigravityCliAdapter",
]
