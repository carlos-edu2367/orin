"""Claude Code CLI (`claude -p`) implementation of `CliAgentAdapter`."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from agentos.providers.claude_cli import catalog as catalog_mod
from agentos.providers.claude_cli import command as cmd_mod
from agentos.providers.claude_cli.errors import classify_result_error_info
from agentos.providers.claude_cli.stream import StreamJsonParser
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

if TYPE_CHECKING:
    from agentos.providers.cli_tools_mcp.server import TurnToolServerHandle

NATIVE_LOOP_VALIDATED: bool = True
PERSISTENT_STDIN_OK: bool = True


class ClaudeCliAdapter:
    """Adapter for Anthropic's `claude` CLI running in isolated `-p` headless mode."""

    name: str = "claude"
    capabilities: CliCapabilities = CliCapabilities(
        native_tool_loop=NATIVE_LOOP_VALIDATED,
        tool_list_changed=True,
        images_in_tool_results=True,
        persistent_stdin=PERSISTENT_STDIN_OK,
        reports_tool_inventory=True,
        forbidden_item_kinds=frozenset(),
    )

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        binary = cmd_mod._resolve_binary_compat(env)
        if not binary:
            return None, None
        version = cmd_mod.claude_cli_version(binary, env=env)
        return binary, version

    def version_supported(
        self, version: tuple[int, int, int] | None
    ) -> bool:
        return cmd_mod.is_version_validated(version)

    def build_command(
        self,
        request: CliTurnRequest,
        wiring: ToolServerWiring | None = None,
    ) -> list[str]:
        eff_wiring = wiring or request.tool_server_wiring or request.wiring
        mcp_config_file = request.tool_server_config_file
        if mcp_config_file is None and eff_wiring is not None and eff_wiring.files:
            mcp_config_file = eff_wiring.files[0]
        return cmd_mod.build_claude_command(
            binary=request.binary,
            system_prompt_file=request.system_prompt_file,
            model=request.model,
            effort=request.effort,
            fallback_model=request.fallback_model,
            max_turns=request.max_turns,
            mcp_config_file=mcp_config_file,
            mcp_server_name=request.tool_server_name,
            autocompact=request.autocompact,
            session_id=request.session_id,
            resume_session_id=request.resume_session_id,
            persist_session=request.persist_session,
        )

    def write_tool_server_config(
        self, handle: TurnToolServerHandle, directory: Path
    ) -> Path:
        return cmd_mod.write_owner_only_temp(
            handle.mcp_config_json(),
            prefix="orin-claude-mcp-",
            suffix=".json",
            directory=directory,
        )

    def wire_tool_server(
        self, handle: TurnToolServerHandle, directory: Path
    ) -> ToolServerWiring:
        cfg = self.write_tool_server_config(handle, directory)
        return ToolServerWiring(
            argv=("--mcp-config", str(cfg)),
            env={},
            files=(cfg,),
        )

    def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
        return cmd_mod.user_event_line(content).encode("utf-8")

    def loggable_command(self, argv: Sequence[str]) -> list[str]:
        return cmd_mod.redact_argv(argv)

    def environment(
        self,
        base: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None,
        tool_timeout_ms: int | None,
    ) -> dict[str, str]:
        return cmd_mod.build_isolated_claude_env(
            base,
            max_output_tokens=max_output_tokens,
            mcp_tool_timeout_ms=tool_timeout_ms,
        )

    def stream_translator(self) -> CliStreamTranslator:
        return StreamJsonParser()

    def classify_error(
        self, result: CliResult, rate_limit: RateLimitState | None
    ) -> CliErrorInfo:
        return classify_result_error_info(result, rate_limit)

    def models(self) -> Sequence[CliModelInfo]:
        cached = catalog_mod.discover_cached_models()
        if cached:
            return tuple(
                CliModelInfo(
                    id=m.id,
                    display_name=m.name,
                    context_window=m.context_window,
                )
                for m in cached
            )
        return tuple(
            CliModelInfo(id=mid, display_name=name, context_window=win)
            for mid, name, win in catalog_mod._SUBSCRIPTION_MODELS
        )

    def context_window(self, model_id: str | None) -> int:
        return catalog_mod.context_window_for(
            model_id, catalog_mod.discover_cached_models()
        )


__all__ = [
    "ClaudeCliAdapter",
    "NATIVE_LOOP_VALIDATED",
    "PERSISTENT_STDIN_OK",
]
