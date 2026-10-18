"""Conservative shell command analysis for permission matching.

The analyzer only needs to answer two questions: which simple commands does
this line run, and does it contain anything we do not fully understand? It
errs on the side of ``complex``: a complex command can still be approved by a
person, but never silently by an allow rule.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

MAX_COMMAND_CHARS = 20_000
_SAFE_REDIRECTS = {
    "posix": {"2>&1", "1>&2", ">&2", ">/dev/null", "1>/dev/null", "2>/dev/null", "&>/dev/null"},
    "cmd": {"2>&1", "1>&2", ">nul", "1>nul", "2>nul", ">NUL", "1>NUL", "2>NUL"},
}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WORD_END = " \t;&|<>\r\n"


@dataclass(frozen=True, slots=True)
class ShellAnalysis:
    subcommands: tuple[str, ...]
    complex: bool
    reasons: tuple[str, ...]
    # Words that a non-trivial redirection writes to or reads from (e.g. ".git/hooks/x" in "> .git/hooks/x").
    redirect_targets: tuple[str, ...] = ()


def default_shell() -> str:
    return "cmd" if os.name == "nt" else "posix"


def analyze_command(command: str, *, shell: str | None = None) -> ShellAnalysis:
    dialect = shell or default_shell()
    if dialect not in _SAFE_REDIRECTS:
        dialect = "posix"
    if not isinstance(command, str):
        return ShellAnalysis((), True, ("not a string",))
    reasons: list[str] = []
    redirect_targets: list[str] = []
    if len(command) > MAX_COMMAND_CHARS:
        return ShellAnalysis((), True, ("command too long",))
    if "\x00" in command:
        return ShellAnalysis((), True, ("NUL byte",))
    subs: list[str] = []
    current: list[str] = []

    # Index just past the last escaped character. An escaped blank is part of the
    # word for /bin/sh, so a "#" right after it is not a comment. Misjudging here
    # only keeps text (fail-closed); it never drops text the shell would run.
    escaped_end = -1

    def flush() -> None:
        nonlocal escaped_end
        text = "".join(current).strip()
        current.clear()
        escaped_end = -1
        if text:
            subs.append(" ".join(text.split()))

    def at_word_start() -> bool:
        return not current or (current[-1] in " \t" and len(current) != escaped_end)

    i = 0
    n = len(command)
    quote: str | None = None
    while i < n:
        ch = command[i]
        if quote == "'":
            current.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if dialect == "posix":
                if ch in "$`":
                    reasons.append("expansion inside double quotes")
                if ch == "\\" and i + 1 < n:
                    current.append(ch)
                    current.append(command[i + 1])
                    i += 2
                    continue
            elif ch in "%!":
                reasons.append("variable expansion")
            current.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch == "'" and dialect == "posix":
            quote = "'"
            current.append(ch)
            i += 1
            continue
        if ch == '"':
            quote = '"'
            current.append(ch)
            i += 1
            continue
        if ch in "\r\n":
            flush()
            i += 1
            continue
        two = command[i:i + 2]
        if two in ("&&", "||"):
            flush()
            i += 2
            continue
        if dialect == "posix" and two == "|&":
            flush()
            i += 2
            continue
        redirect_start = ch in "<>" or (ch == "&" and i + 1 < n and command[i + 1] == ">")
        if redirect_start:
            prefix = ""
            if ch != "&" and current and current[-1] in "0123456789" and (len(current) == 1 or current[-2] in " \t"):
                prefix = current.pop()
            j = i
            while j < n and command[j] in "<>&":
                j += 1
            operator = command[i:j]
            if "<<" in operator:
                reasons.append("here-document")
            k = j
            while k < n and command[k] in " \t":
                k += 1
            m = k
            while m < n and command[m] not in _WORD_END:
                m += 1
            token = prefix + operator + command[k:m]
            if token not in _SAFE_REDIRECTS[dialect]:
                reasons.append(f"redirection {token}")
                current.append(f" {token} ")
                if command[k:m]:
                    redirect_targets.append(command[k:m])
            i = m
            continue
        if ch in ";|&":
            flush()
            i += 1
            continue
        if dialect == "posix":
            if ch == "`":
                reasons.append("command substitution")
            elif ch == "$":
                reasons.append("expansion")
            elif ch in "()":
                reasons.append("subshell")
            elif ch in "{}":
                reasons.append("brace group")
            elif ch == "\\":
                if i + 1 < n and command[i + 1] in ";&|<>()$`\n\r'\"{} \t":
                    reasons.append("escaped operator")
                current.append(ch)
                if i + 1 < n:
                    current.append(command[i + 1])
                    escaped_end = len(current)
                i += 2
                continue
            elif ch == "#" and at_word_start():
                while i < n and command[i] not in "\r\n":
                    i += 1
                continue
        elif ch in "^%!()":
            reasons.append(f"cmd metacharacter {ch}")
        current.append(ch)
        i += 1
    if quote is not None:
        reasons.append("unterminated quote")
    flush()
    for sub in subs:
        first = sub.split(" ", 1)[0]
        if _ASSIGNMENT.match(first):
            reasons.append("environment assignment")
    unique = tuple(dict.fromkeys(reasons))
    return ShellAnalysis(tuple(subs), bool(unique), unique, tuple(redirect_targets))
