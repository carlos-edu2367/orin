"""``orin code``: Orin Code from a terminal or an SDK host.

A thin client of the local Orin API (spec §9, decision D6): it never runs the agent loop itself. It makes sure the
local runtime is up, creates or resumes a conversation through the API, follows the conversation's event stream and
speaks Claude Code's headless protocol (text, json and stream-json, with control requests) on stdin and stdout.
"""

from .args import CliUsageError, CodeOptions, parse_code_arguments

__all__ = ["CliUsageError", "CodeOptions", "parse_code_arguments"]
