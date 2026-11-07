"""OpenAI Codex CLI (`codex exec --json`) implementation of `CliAgentAdapter`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

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
from agentos.providers.codex_cli.catalog import (
    context_window_for,
    discover_codex_models,
)
from agentos.providers.codex_cli.command import (
    DEFAULT_CODEX_MODEL,
    build_codex_command,
    build_isolated_codex_env,
    clean_codex_model_id,
    codex_version,
    encode_codex_user_turn,
    is_codex_version_supported,
    redact_codex_argv,
    resolve_codex_binary,
    wire_codex_tool_server,
)
from agentos.providers.codex_cli.errors import classify_codex_error
from agentos.providers.codex_cli.stream import CodexJsonlTranslator


class CodexCliAdapter:
    """Adapter for OpenAI's `codex` CLI running in isolated `exec --json` mode."""

    name: str = "codex"
    capabilities: CliCapabilities = CliCapabilities(
        native_tool_loop=True,
        tool_list_changed=False,
        images_in_tool_results=True,
        persistent_stdin=False,
        reports_tool_inventory=False,
        forbidden_item_kinds=frozenset(
            {"command_execution", "file_change", "web_search"}
        ),
    )

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        binary = resolve_codex_binary(env)
        if not binary:
            return None, None
        version = codex_version(binary, env=env)
        return binary, version

    def binary_version(
        self,
        binary: str,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, int, int] | None:
        return codex_version(binary, env=env)

    def version_supported(
        self, version: tuple[int, int, int] | None
    ) -> bool:
        return is_codex_version_supported(version)

    def wire_tool_server(
        self, handle: Any, directory: Path
    ) -> ToolServerWiring:
        server_name = getattr(handle, "server_name", "orin") or "orin"
        return wire_codex_tool_server(handle, server_name=server_name)

    def build_command(
        self,
        request: CliTurnRequest,
        wiring: ToolServerWiring | None = None,
    ) -> list[str]:
        supported_efforts: Sequence[str] | None = None
        cleaned_model = clean_codex_model_id(request.model) or DEFAULT_CODEX_MODEL
        for info in self.models():
            if info.id == cleaned_model:
                supported_efforts = info.supported_efforts
                break
        return build_codex_command(
            request,
            wiring,
            supported_efforts=supported_efforts,
        )

    def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
        return encode_codex_user_turn(content)

    def loggable_command(self, argv: Sequence[str]) -> list[str]:
        return redact_codex_argv(argv)

    def environment(
        self,
        base: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None = None,
        tool_timeout_ms: int | None = None,
    ) -> dict[str, str]:
        return build_isolated_codex_env(base)

    def stream_translator(self) -> CliStreamTranslator:
        return CodexJsonlTranslator()

    def classify_error(
        self,
        result: CliResult,
        rate_limit: RateLimitState | None = None,
    ) -> CliErrorInfo:
        return classify_codex_error(result, rate_limit)

    def models(self) -> Sequence[CliModelInfo]:
        return discover_codex_models()

    def context_window(self, model_id: str | None) -> int:
        return context_window_for(model_id, self.models())


__all__ = [
    "CodexCliAdapter",
]
