"""W4: AgentToolset is split into per-family modules; the pipeline stays in agent_tools."""
from __future__ import annotations

import logging

from agentos.agentic import agent_tools


def test_shared_helpers_live_in_their_own_module() -> None:
    from agentos.agentic.tools import _shared

    for name in (
        "ToolDefinition", "ToolOutcome", "AgentToolError", "ReadFileRecord", "compute_structured_patch",
        "coerce_tool_arguments", "MAX_TOOL_RESULT_CHARS", "_schema", "_TEXT", "sanitize_page_text",
        "_terminate_process_tree", "_process_is_running",
    ):
        assert getattr(agent_tools, name) is getattr(_shared, name), name
    assert agent_tools.ToolOutcome.__module__ == "agentos.agentic.tools._shared"


def test_the_logger_keeps_its_name() -> None:
    from agentos.agentic.tools import _shared

    assert _shared.logger is logging.getLogger("agentos.agentic.agent_tools")
