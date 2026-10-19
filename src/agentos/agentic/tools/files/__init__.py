"""File tools: Read, Write, Edit, NotebookEdit (and Orin's ViewFile and TranscribePdf)."""

# Outputs leave out an original or written text longer than this (Claude Code drops a too-large original too).
ORIGINAL_FILE_LIMIT = 100_000

# W4-R10: appended to a Write or Edit result when the file it changed was too large to keep a copy of.
UNREWINDABLE_NOTE = "Note: this file cannot be rewound, because it was too large to keep a copy of it before this change."
