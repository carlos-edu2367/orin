"""Conservative PowerShell command analysis for permission matching (W4).

Same contract as ``analyze_command`` for POSIX/cmd: which simple commands does this line run, and does
it contain anything we do not fully understand? Variables, subexpressions, script blocks, the call and
dot-source operators, backtick escapes, here-strings and dynamic-evaluation cmdlets all make a command
``complex``: a person may still approve it, an allow rule never does. Redirection targets are recorded
so protected paths are caught. Cmdlet aliases are listed so a deny rule written for ``Remove-Item`` also
stops ``rm``, ``del`` and ``ri`` (over-matching a deny only blocks more).
"""
from __future__ import annotations

import re

from agentos.permissions.shell import MAX_COMMAND_CHARS, ShellAnalysis, analyze_command

# alias (lower case) -> cmdlet (lower case); Windows PowerShell 5.1 built-ins
POWERSHELL_ALIASES: dict[str, str] = {
    "rm": "remove-item", "del": "remove-item", "erase": "remove-item", "rd": "remove-item", "rmdir": "remove-item",
    "ri": "remove-item", "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem", "cat": "get-content",
    "gc": "get-content", "type": "get-content", "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item",
    "mv": "move-item", "move": "move-item", "mi": "move-item", "echo": "write-output", "write": "write-output",
    "sc": "set-content", "ac": "add-content", "ni": "new-item", "iwr": "invoke-webrequest", "curl": "invoke-webrequest",
    "wget": "invoke-webrequest", "irm": "invoke-restmethod", "iex": "invoke-expression", "icm": "invoke-command",
    "saps": "start-process", "start": "start-process", "kill": "stop-process", "spps": "stop-process",
    "cd": "set-location", "sl": "set-location", "chdir": "set-location", "pwd": "get-location", "gl": "get-location",
    "sls": "select-string", "select": "select-object", "where": "where-object", "?": "where-object",
    "%": "foreach-object", "foreach": "foreach-object", "ren": "rename-item", "rni": "rename-item",
    "sp": "set-itemproperty", "clc": "clear-content",
}
# Programs that run other code from a string or start another interpreter: never simple.
_DYNAMIC = frozenset({
    "invoke-expression", "invoke-command", "start-process", "start-job", "add-type", "new-object",
    "powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe", "bash", "bash.exe", "wsl", "wsl.exe",
    "set-alias", "new-alias", "set-item", "invoke-item", "ii",
})
_SAFE_REDIRECTS = frozenset({">$null", "1>$null", "2>$null", "3>$null", "4>$null", "5>$null", "6>$null", "*>$null"})
_STREAM_MERGE = re.compile(r"[0-9*]?>&[0-9]")
_WORD_END = frozenset(" \t;|&<>\r\n")
_SEPARATORS = frozenset("\r\n;")
_GROUPING = frozenset("(){}[]")
_TYPOGRAPHIC_QUOTES = frozenset("‘’‚‛“”„")
# -Name:value binds a value without a space; anything but a plain word (a path, a pattern) is not simple (W4-R2).
_BOUND_VALUE = re.compile(r"(?:^|\s)-[A-Za-z]\w*:(?![A-Za-z0-9_-]+(?:\s|$))")


def canonical_cmdlet(word: str) -> str:
    """The cmdlet a word names (an alias resolved), lower case."""
    lowered = word.strip("\"'").lower()
    return POWERSHELL_ALIASES.get(lowered, lowered)


def analyze_powershell(command: str) -> ShellAnalysis:
    if not isinstance(command, str):
        return ShellAnalysis((), True, ("not a string",))
    if len(command) > MAX_COMMAND_CHARS:
        return ShellAnalysis((), True, ("command too long",))
    if "\x00" in command:
        return ShellAnalysis((), True, ("NUL byte",))
    reasons: list[str] = []
    redirect_targets: list[str] = []
    subs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "".join(current).strip()
        current.clear()
        if text:
            subs.append(" ".join(text.split()))

    i, n = 0, len(command)
    quote: str | None = None
    while i < n:
        ch = command[i]
        if quote == "'":
            current.append(ch)
            if ch == "'":
                if i + 1 < n and command[i + 1] == "'":  # '' is an escaped quote inside single quotes
                    current.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch in "$`":
                reasons.append("expansion inside double quotes")
            current.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in _TYPOGRAPHIC_QUOTES:
            # PowerShell also delimits strings with curly quotes; never guess how they pair.
            reasons.append("typographic quote")
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == "@" and i + 1 < n and command[i + 1] in ("'", '"', "(", "{"):
            reasons.append("here-string or array/hash expression")
        if ch in _SEPARATORS:
            flush()
            i += 1
            continue
        if command[i:i + 2] in ("&&", "||"):
            flush()
            i += 2
            continue
        if ch == "|":
            flush()
            i += 1
            continue
        starts_word = not current or current[-1] in (" ", "\t")
        if ch in ("<", ">") or (ch in "0123456789*" and starts_word and i + 1 < n and command[i + 1] == ">"):
            j = i
            while j < n and command[j] in "0123456789*<>&":
                j += 1
            operator = command[i:j]
            if _STREAM_MERGE.fullmatch(operator):
                current.append(f" {operator} ")  # 2>&1 and the like write no file
                i = j
                continue
            k = j
            while k < n and command[k] in (" ", "\t"):
                k += 1
            m = k
            while m < n and command[m] not in _WORD_END:
                m += 1
            target = command[k:m]
            token = operator + target
            if token.lower() not in _SAFE_REDIRECTS:
                reasons.append(f"redirection {operator}")
                if target:
                    redirect_targets.append(target.strip("\"'"))
            current.append(f" {token} ")
            i = m
            continue
        if ch == "&":
            reasons.append("call operator")
            i += 1
            continue
        if ch == "`":
            reasons.append("backtick escape")
        elif ch == "$":
            reasons.append("variable or subexpression")
        elif ch in _GROUPING:
            reasons.append("grouping, script block or type literal")
        elif ch == "#" and starts_word:
            while i < n and command[i] not in ("\r", "\n"):
                i += 1
            continue
        current.append(ch)
        i += 1
    if quote is not None:
        reasons.append("unterminated quote")
    flush()
    for sub in subs:
        first = sub.split(" ", 1)[0]
        if first == ".":
            reasons.append("dot-sourcing")
        if canonical_cmdlet(first) in _DYNAMIC:
            reasons.append(f"runs dynamic code ({first})")
        if _BOUND_VALUE.search(sub):
            reasons.append("parameter bound to a path or pattern")
    unique = tuple(dict.fromkeys(reasons))
    return ShellAnalysis(tuple(subs), bool(unique), unique, tuple(redirect_targets))


def analyze_for(command: str, shell: str) -> ShellAnalysis:
    """The analysis for one dialect: ``powershell``, or ``posix``/``cmd`` through ``analyze_command``."""
    if shell == "powershell":
        return analyze_powershell(command)
    return analyze_command(command, shell=shell)


__all__ = ["POWERSHELL_ALIASES", "analyze_for", "analyze_powershell", "canonical_cmdlet"]
