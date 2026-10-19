"""PowerShell: Windows PowerShell 5.1 through the Bash runner (W4)."""
from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from agentos.agentic.tools._shared import _TEXT, _schema
from agentos.agentic.tools.shell.bash import BashOutput
from agentos.agentic.tools.spec import ToolInput, ToolSpec

# PowerShell 5.1 writes redirected output in the console code page; this makes it UTF-8 (Orin's own prefix,
# never analysed as part of the person's command). Progress records are off: they arrive as CLIXML noise.
_UTF8_PREFIX = (
    "$ProgressPreference = 'SilentlyContinue'; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
)


def powershell_executable() -> Path | None:
    """The system Windows PowerShell; never one found on PATH or in the workspace."""
    if os.name != "nt":
        return None
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return candidate if candidate.is_file() else None


def powershell_argv(command: str) -> list[str]:
    executable = powershell_executable()
    if executable is None:
        raise RuntimeError("Windows PowerShell is not available")
    encoded = base64.b64encode((_UTF8_PREFIX + command).encode("utf-16-le")).decode("ascii")
    return [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


_CLIXML_STRING = re.compile(r'<S S="(?:Error|Warning|Verbose|Debug|Information)">(.*?)</S>', re.DOTALL)
_CLIXML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def decode_clixml(text: str) -> str:
    """Windows PowerShell's serialized error stream (``#< CLIXML``) as plain text; other text unchanged."""
    if "#< CLIXML" not in text:
        return text
    head, _, xml = text.partition("#< CLIXML")
    pieces = [
        html.unescape(_CLIXML_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), item))
        for item in _CLIXML_STRING.findall(xml)
    ]
    decoded = "".join(pieces).strip()
    return f"{head.strip()}\n{decoded}".strip() if head.strip() else decoded


class PowerShellTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def powershell(
        self,
        command: str,
        background: bool = False,
        timeout_seconds: int | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        return self._run_shell(
            command, background, timeout_seconds, "", description,
            argv=powershell_argv(command) if isinstance(command, str) and command.strip() else None,
            tool_name="powershell",
            stderr_filter=decode_clixml,
        )


class PowerShellInput(ToolInput):
    command: str = Field(description="The PowerShell command to run.")
    description: str | None = Field(None, description="What the command does, in 5-10 words.")
    timeout: int | None = Field(None, ge=1000, le=600_000, description="Timeout in milliseconds.")
    run_in_background: bool | None = Field(None, description="Start it in the background and return immediately.")


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="powershell",
        description=(
            "Run one Windows PowerShell 5.1 command inside the conversation workspace and return its output. "
            "Use it for Windows-specific work (cmdlets, registry, services); use run_command for ordinary builds and scripts."
        ),
        legacy_schema=_schema({
            "command": {**_TEXT, "description": "A single PowerShell command, executed non-interactively."},
            "background": {"type": "boolean"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
            "description": _TEXT,
        }, ("command",)),
        handler="powershell",
        kind="terminal",
        input_model=PowerShellInput,
        public_description=(
            "Run a Windows PowerShell command in the workspace. Prefer the file tools for reading, searching and editing "
            "files. Give a short description. Use run_in_background for long-lived processes."
        ),
        output_model=BashOutput,
        policy_tags=("mutates",),
        available=lambda self: bool(self._enable_terminal) and powershell_executable() is not None,
        order=245,
    ),
)
