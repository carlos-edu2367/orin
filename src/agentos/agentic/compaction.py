"""What a compaction summary looks like, and the message that replaces the history it folds.

One set of headings for Chat and Code mode, in English for every model, with the summary
itself written in the conversation's language.
"""

from __future__ import annotations

COMPACTION_SECTIONS = (
    "Primary Request and Intent",
    "Key Technical Concepts",
    "Files and Code Sections",
    "Errors and fixes",
    "Problem Solving",
    "All user messages",
    "Pending Tasks",
    "Current Work",
    "Optional Next Step",
)
COMPACTION_HEADING = "## Compacted context"
COMPACTION_HEADER = (
    f"{COMPACTION_HEADING}\n"
    "[This conversation continues from an earlier part that no longer fits in the context window. "
    "The summary below is authoritative for the paths, decisions and values it records; "
    "read a file again only when you expect it to have changed since then.]"
)
COMPACTION_INSTRUCTION = (
    "Summarize the conversation below so that another agent can continue the work without redoing it.\n"
    "First think through the conversation in order inside <analysis>...</analysis>; that part is discarded.\n"
    "Then, outside the analysis, write exactly these nine markdown sections in this order, each as a "
    "'### ' heading, writing '- none' under a section that has nothing:\n"
    + "\n".join(f"### {section}" for section in COMPACTION_SECTIONS)
    + "\nUnder 'Files and Code Sections' give every file read or changed with its exact path and what matters about it. "
    "Under 'Errors and fixes' give each error and how it was resolved. Under 'All user messages' list every request "
    "the person made, in order. Under 'Pending Tasks' and 'Current Work' say exactly what is left and what was being "
    "done right before this summary. Keep exact paths, identifiers and figures, and invent nothing. "
    "Write the summary in the same language the person uses in the conversation."
)
MICROCOMPACT_MARKER = "[microcompacted: an older tool result was cleared to save context; run the tool again if you need it]"


def compaction_message(body: str) -> dict[str, object]:
    """The message that stands in for everything a compaction folded."""
    return {"role": "system", "content": f"{COMPACTION_HEADER}\n{body}"}


__all__ = [
    "COMPACTION_HEADER", "COMPACTION_HEADING", "COMPACTION_INSTRUCTION", "COMPACTION_SECTIONS",
    "MICROCOMPACT_MARKER", "compaction_message",
]
