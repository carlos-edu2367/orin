"""What ``orin code`` attaches to a turn (W6, spec §9): its CLI settings layer and headless switches.

The API accepts these only from a caller authenticated with this run's CLI token, stores them with the turn
(``conversation_turns.cli_options``) and the worker's session applies them. Everything here can only narrow what the
person's own settings allow, except what a CLI flag legitimately adds as the "cli" settings layer (which sits above
the person's and the repository's layers and below managed policy, as in Claude Code).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CLI_TEXT = 65536
MAX_CLI_SETTINGS_BYTES = 65536
MAX_CLI_TOOL_NAME = 128


class CliOptionsError(ValueError):
    """A turn's stored CLI options do not validate: the turn must not run without them."""


class CliTurnOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The "cli" settings layer: --settings, --allowedTools/--disallowedTools, --add-dir, --max-turns, ... merged.
    settings: dict[str, Any] = Field(default_factory=dict)
    # --setting-sources: which file layers load (managed and cli always do).
    setting_sources: list[Literal["user", "project", "local"]] | None = Field(default=None, max_length=3)
    # --system-prompt replaces Orin's base Code prompt; --append-system-prompt adds to the turn's context.
    system_prompt: str | None = Field(default=None, max_length=MAX_CLI_TEXT)
    append_system_prompt: str | None = Field(default=None, max_length=MAX_CLI_TEXT)
    # --tools: the only tools offered (Claude Code names); None means Orin's normal set.
    tools: list[str] | None = Field(default=None, max_length=256)
    # --permission-prompts none: nobody can answer a prompt, so whatever would ask is refused (W1-R9 / W3-R24).
    permission_prompts: Literal["host", "none"] = "host"
    # The tool inputs and results the protocol needs travel on the turn's tool events.
    transcript: bool = True
    # A headless run answers the host as Claude Code would: no "Code mode wants a verification" second pass.
    headless: bool = True
    # --trust: the API trusts the workspace folder before it queues the turn (the session itself ignores this).
    trust_workspace: bool = False

    @field_validator("settings")
    @classmethod
    def _settings_are_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("cli settings must be plain JSON") from error
        if len(encoded.encode("utf-8")) > MAX_CLI_SETTINGS_BYTES:
            raise ValueError("cli settings are too large")
        return value

    @field_validator("tools")
    @classmethod
    def _tool_names(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [str(item).strip() for item in value]
        if any(not item or len(item) > MAX_CLI_TOOL_NAME for item in cleaned):
            raise ValueError("tool names must be non-blank and short")
        return cleaned

    def dumps(self) -> str:
        return self.model_dump_json()


def load_cli_options(raw: object) -> CliTurnOptions | None:
    """A turn's stored options; None when the CLI did not start the turn. A value that no longer validates raises."""
    if raw is None or raw == "" or raw == b"":
        return None
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        return CliTurnOptions.model_validate(data)
    except Exception as error:  # noqa: BLE001 - any shape problem is the same refusal
        raise CliOptionsError("the turn's CLI options are invalid") from error


__all__ = ["CliOptionsError", "CliTurnOptions", "MAX_CLI_SETTINGS_BYTES", "MAX_CLI_TEXT", "load_cli_options"]
