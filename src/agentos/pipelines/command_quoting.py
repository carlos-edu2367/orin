"""Where the ``${{ inputs.x }}`` references of a gate command sit: relative to the shell quotes and to the program.

The engine quotes every input value itself (``gates.render_command``). A reference the author already put inside
quotes (``'${{ inputs.x }}'``, ``"${{ inputs.x }}"``) or right after a backslash would receive a second layer of
quoting that closes the author's quote and lets the value through (``;``, ``$(...)``). Both the loader and the
run use this scan to refuse those templates (WS-C6 review, finding F1).

``classify_references`` is the ONE place that says what each reference is in the command (WS-C10 security review,
P1 and S2): the whole command, the program (the first word, where the shell looks for what to run) or an
argument. The gate renderer, the approval review and the rule "an agent may not pick the program" all read it, so
they cannot disagree again. Pure: no I/O, no engine imports.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .expressions import TemplatePart, TemplateRef

ROLE_WHOLE = "whole"  # the reference is the entire command: the value is split into words, the first is the program
ROLE_PROGRAM = "program"  # the reference sits in the program word (``${{ inputs.p }} --x``, ``./${{ inputs.p }}``)
ROLE_ARGUMENT = "argument"  # anywhere else: one quoted word that cannot change what runs

_PLACEHOLDER = "\ue001"  # stands for an earlier reference while the literal text before a reference is read
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GRAMMAR_WORDS = frozenset({"if", "then", "elif", "else", "do", "while", "until", "!", "{"})
_WRAPPERS = frozenset({"time", "exec", "command", "builtin", "nohup", "nice", "env", "sudo", "eval"})  # the next word is a program
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})  # ``sh -c <word>``: the word is a command line


def scan_quoting(parts: Sequence[TemplatePart]) -> tuple[list[TemplateRef], bool]:
    """``(references that sit inside quotes or after a backslash, whether the template leaves a quote open)``.

    Follows the POSIX shell rules that matter here: ``'...'`` takes everything literally, ``"..."`` and the free
    text take a backslash as an escape for the next character.
    """
    quoted: list[TemplateRef] = []
    state: str | None = None  # None (free text), "'" or '"'
    escaped = False
    for part in parts:
        if isinstance(part, str):
            for char in part:
                if escaped:
                    escaped = False
                elif state == "'":
                    if char == "'":
                        state = None
                elif char == "\\":
                    escaped = True
                elif state == '"':
                    if char == '"':
                        state = None
                elif char in "'\"":
                    state = char
            continue
        if state is not None or escaped:
            quoted.append(part)
        escaped = False
    return quoted, state is not None or escaped


def quoting_problem(parts: Sequence[TemplatePart]) -> str | None:
    """A pt-BR sentence naming the problem of a command template, or ``None`` when its quoting is safe."""
    quoted, open_quote = scan_quoting(parts)
    if quoted:
        names = ", ".join(f"`${{{{ {'.'.join(ref.path.segments)} }}}}`" for ref in quoted)
        return (
            f"a referência {names} está entre aspas ou depois de uma barra invertida; escreva-a sem aspas, "
            "porque o Orin já cita o valor sozinho e as aspas do modelo quebrariam essa citação"
        )
    if open_quote:
        return "o comando tem aspas ou barra invertida sem fechar"
    return None


@dataclass(frozen=True, slots=True)
class ReferenceRole:
    ref: TemplateRef
    role: str


def _tokenize(text: str) -> tuple[list[tuple[str, str]], bool]:
    """Shell-ish ``("word"|"op", text)`` tokens; the flag says the last word is still open (a reference follows it)."""
    tokens: list[tuple[str, str]] = []
    buf: list[str] = []
    has_word = False
    state: str | None = None
    escaped = False

    def flush() -> None:
        nonlocal has_word
        if has_word:
            tokens.append(("word", "".join(buf)))
        buf.clear()
        has_word = False

    for char in text:
        if escaped:
            buf.append(char)
            has_word, escaped = True, False
        elif state == "'":
            if char == "'":
                state = None
            else:
                buf.append(char)
        elif char == "\\":
            escaped, has_word = True, True
        elif state == '"':
            if char == '"':
                state = None
            else:
                buf.append(char)
        elif char in "'\"":
            state, has_word = char, True
        elif char.isspace() and char != "\n":
            flush()
        elif char in ";&|()<>\n":
            flush()
            tokens.append(("op", ";" if char == "\n" else char))
        else:
            buf.append(char)
            has_word = True
    open_word = has_word
    flush()
    return tokens, open_word


def _role_of_next(tokens: list[tuple[str, str]], open_word: bool) -> str:
    """The role a reference gets right after ``tokens`` (glued to the last word when ``open_word``)."""
    expect = True  # the next word is where the shell looks for the program
    skip = False  # the next word is the target of a redirection
    wrapper = False
    shell_program = False
    last_op = ""
    for index, (kind, value) in enumerate(tokens):
        if kind == "op":
            if value in "<>":
                skip = True
            elif not (value == "&" and last_op in {"<", ">"}):  # ``2>&1`` is not a new command
                expect, skip, wrapper, shell_program = True, False, False, False
            last_op = value
            continue
        last_op = ""
        role = ROLE_ARGUMENT
        if skip:
            skip = False
        elif expect:
            if wrapper and value.startswith("-"):
                pass  # an option of the wrapper (``sudo -n``, ``env -i``): the program is still ahead
            elif not (_ASSIGNMENT.match(value) or value in _GRAMMAR_WORDS):
                role = ROLE_PROGRAM
                base = value.rsplit("/", 1)[-1]
                wrapper, shell_program = base in _WRAPPERS, base in _SHELLS
                expect = wrapper
        elif shell_program and value.startswith("-") and not value.startswith("--") and "c" in value:
            expect, shell_program = True, False  # ``bash -c <command line>``
        if open_word and index == len(tokens) - 1:
            return role
    if skip:
        return ROLE_ARGUMENT
    return ROLE_PROGRAM if expect else ROLE_ARGUMENT


def classify_references(parts: Sequence[TemplatePart]) -> tuple[ReferenceRole, ...]:
    """The role of every reference of a command template, in order (see the module docstring).

    ``whole``: the only meaningful part is the reference (literal parts of just spaces or new lines do not count: a
    YAML block scalar leaves them around it). ``program``: the reference sits in the first word of a command, also
    after ``;``, ``&&``, ``|``, ``$(``, leading ``NAME=value`` assignments or a wrapper such as ``env``/``sudo``.
    Anything else is an ``argument``. Wrappers with options of their own (``xargs -n 1 PROG``) and interpreters that
    read code (``python -c``) are NOT followed: the person sees every command when approving the file.
    """
    refs = [part for part in parts if isinstance(part, TemplateRef)]
    meaningful = [part for part in parts if not (isinstance(part, str) and not part.strip())]
    if len(meaningful) == 1 and len(refs) == 1 and isinstance(meaningful[0], TemplateRef):
        return (ReferenceRole(refs[0], ROLE_WHOLE),)
    roles: list[ReferenceRole] = []
    before: list[str] = []
    for part in parts:
        if isinstance(part, str):
            before.append(part)
            continue
        literal = "".join(before).replace("\\\n", " ").replace("`", "(")
        tokens, open_word = _tokenize(literal)
        roles.append(ReferenceRole(part, _role_of_next(tokens, open_word)))
        before.append(_PLACEHOLDER)
    return tuple(roles)


__all__ = ["ROLE_ARGUMENT", "ROLE_PROGRAM", "ROLE_WHOLE", "ReferenceRole", "classify_references", "quoting_problem", "scan_quoting"]
