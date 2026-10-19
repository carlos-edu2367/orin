"""Preview tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolOutcome,
)


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
