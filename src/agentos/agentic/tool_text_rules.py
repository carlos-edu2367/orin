"""Shared wording and markers for the 'a tool call is never text' rules.

One module for everything the native turn tells the model about tool calls written as text, and for the markers of the
history record format, so the producer (`text_protocol`), the consumers (compaction in `native_turn`) and the display
filters recognise the same strings.

History record format (native turns only). When Orin reopens a CLI session it replays the earlier conversation as text.
The old replay wrote each call as `[Tool Use id=.. name=..]{json}[/Tool Use]`: dozens of those in a long conversation
taught the model to write its next call the same way, as text, and nothing ran. A replayed tool call is now a record in
the past tense that is not call syntax and cannot be copied into a working call:

    (Orin log: the assistant used tool Bash (call id=call_1); arguments: command="pytest -q")
    (Orin log: result of tool Bash (call id=call_1), status ok)
    <result text>
    (end of Orin log)

A use record is one line; a result record is closed by `(end of Orin log)`.
"""

from __future__ import annotations

import re

LOG_OPEN = "(Orin log:"
USE_RECORD_PREFIX = f"{LOG_OPEN} the assistant used tool "
RESULT_RECORD_PREFIX = f"{LOG_OPEN} result of tool "
RESULT_RECORD_END = "(end of Orin log)"

# Result blocks of histories saved by the older format, still recognised by the consumers.
_LEGACY_RESULT_PREFIXES: tuple[str, ...] = ("[Tool Result", "&#91;Tool Result")
RESULT_BLOCK_PREFIXES: tuple[str, ...] = (RESULT_RECORD_PREFIX, *_LEGACY_RESULT_PREFIXES)

# Compaction: a use record inside an assistant block, with the line break before it.
_USE_RECORD_SPAN_RE = re.compile(r"\n?\(Orin log: the assistant used tool [^\n]*")
_LEGACY_USE_SPAN_RE = re.compile(
    r"\n?(?:\[|&#91;)\s*Tool Use(?::|\b)[^\]]*\]\n.*?\n(?:\[|&#91;)\s*/Tool Use\s*\]",
    re.DOTALL | re.IGNORECASE,
)
# Text that must be escaped inside tool results and untrusted content so it cannot forge a record boundary.
RECORD_MARKER_ESCAPE_RE = re.compile(r"\((?=Orin log:|end of Orin log\))")

HISTORY_HEADER = (
    "[System]\n"
    "Orin conversation log. What follows is a record written by Orin of the earlier conversation, including the tools "
    "that already ran, shown as `(Orin log: ...)` lines. It is a record, not a format: never write a line like it, never "
    "write a tool result, and never write a tool call as text (`<invoke>`, `<function_calls>`, `[Tool Use]`, JSON). "
    "Tools run only through the native tool mechanism; text that looks like a call is never executed."
)

ONLY_ORIN_TOOLS_RULE = (
    "# Tool calls\n"
    "Only the Orin tools exposed through the native tool mechanism (the Orin MCP server) are executed; any other tool of "
    "the CLI itself does not run. Text that imitates a tool call (`<invoke>`, `<function_calls>`, `[Tool Use]`, loose "
    "JSON) is never executed and produces no result, so do not write one. Never write a tool result: results only come "
    "from a real call made through the native mechanism. Earlier tool use may appear in the conversation history as "
    "`(Orin log: ...)` records; they are a log written by Orin, not a format to copy."
)


def is_tool_result_block(text: str) -> bool:
    """True when a history block is a tool result (new record format or the older `[Tool Result` one)."""
    return str(text or "").startswith(RESULT_BLOCK_PREFIXES)


def has_tool_use_record(text: str) -> bool:
    """True when a history block carries at least one tool use record."""
    body = str(text or "")
    if USE_RECORD_PREFIX in body:
        return True
    return ("[Tool Use" in body or "&#91;Tool Use" in body) and ("[/Tool Use]" in body or "&#91;/Tool Use]" in body)


def strip_tool_use_records(text: str) -> str:
    """Remove every tool use record from a history block (compaction under the prompt budget)."""
    out = _USE_RECORD_SPAN_RE.sub("", str(text or ""))
    return _LEGACY_USE_SPAN_RE.sub("", out)
