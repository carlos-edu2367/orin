"""The one door for assistant text on its way out of the model: sanitize it before it is saved or sent anywhere.

A model sometimes writes a tool call as text (`<invoke name="Bash">`, `<function_calls>`, `</function_results>`,
`[Tool Use ...]`, an `(Orin log: ...)` record) instead of calling the tool. Nothing ran, and the raw markup would
show in the conversation, the Discord channel and any export. The filters live in
`providers/claude_cli/text_protocol.py`; this module is the neutral name every writer imports, so a new place that
stores or sends assistant text has one function to call and one contract test (`tests/unit/agentic/
test_text_sanitize_contract.py`) that lists the places that already do.

Markup inside fenced code blocks or inline code (single or double backticks) is kept: it is a quotation, as in a
report that cites a tag. An opening tag that never closes is only removed up to the end when it starts a line; in the
middle of a sentence it is prose and stays. Ordinary `<result>` / `<output>` tags are kept: they only count next to a
`</function_results>`.
"""

from __future__ import annotations

from agentos.providers.claude_cli.text_protocol import (
    ResidualToolTextStreamFilter,
    strip_residual_tool_protocol_blocks,
)

__all__ = [
    "ResidualToolTextStreamFilter",
    "new_assistant_text_stream",
    "sanitize_assistant_text",
    "strip_residual_tool_protocol_blocks",
]


def sanitize_assistant_text(text: str) -> str:
    """The assistant's text without tool calls or results written as text. Whitespace is left as it was."""
    if not isinstance(text, str) or not text:
        return ""
    return strip_residual_tool_protocol_blocks(text)


def new_assistant_text_stream() -> ResidualToolTextStreamFilter:
    """The same rule for text that arrives in chunks: `feed(chunk)` returns what may be shown, `finish()` the tail.

    `saw_text_tool_call` turns True once the model wrote a call as text, so the caller can tell it nothing ran.
    """
    return ResidualToolTextStreamFilter()
