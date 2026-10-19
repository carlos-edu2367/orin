"""Preview tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class PreviewTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def preview_start(
        self,
        name: str | None = None,
        wait_timeout: float = 10.0,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.start_server(name=name, wait_timeout=float(wait_timeout))
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Preview server iniciado: {result.get('name')} ({result.get('url')})"
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": result},
        )

    def preview_stop(
        self,
        name: str | None = None,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.stop_server(name=name)
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Preview server encerrado: {result.get('name') or 'default'}"
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": result},
        )

    def preview_logs(
        self,
        name: str | None = None,
        tail_lines: int = 100,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.read_logs(name=name, tail_lines=int(tail_lines))
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Logs do preview server: {result.get('name') or 'default'}"
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": {k: v for k, v in result.items() if k != "logs"}},
        )


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="preview_start",
        description="Start a local development/preview server from .orin/launch.json (or auto-detected from package.json / pyproject.toml), wait for its port, and navigate the browser if available.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional configuration name from launch.json. Uses the first configuration when omitted."},
            "wait_timeout": {"type": "number", "minimum": 0.1, "maximum": 60.0, "description": "Seconds to wait for the server port to open. Defaults to 10."},
        }),
        handler="preview_start",
        kind="terminal",
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=300,
    ),
    ToolSpec(
        key="preview_stop",
        description="Stop a running local preview server started with preview_start.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional server configuration name to stop. Stops all tracked preview servers when omitted."},
        }),
        handler="preview_stop",
        kind="terminal",
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=310,
    ),
    ToolSpec(
        key="preview_logs",
        description="Read recent stdout/stderr logs from a local preview server started with preview_start.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional server configuration name."},
            "tail_lines": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Number of trailing log lines to return. Defaults to 100."},
        }),
        handler="preview_logs",
        kind="terminal",
        read_only=True,
        available=lambda self: (self._enable_terminal),
        order=320,
    ),
)
