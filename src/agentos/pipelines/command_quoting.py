"""Where the ``${{ inputs.x }}`` references of a gate command sit relative to the shell quotes of the template.

The engine quotes every input value itself (``gates.render_command``). A reference the author already put inside
quotes (``'${{ inputs.x }}'``, ``"${{ inputs.x }}"``) or right after a backslash would receive a second layer of
quoting that closes the author's quote and lets the value through (``;``, ``$(...)``). Both the loader and the
run use this scan to refuse those templates (WS-C6 review, finding F1). Pure: no I/O, no engine imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from .expressions import TemplatePart, TemplateRef


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


__all__ = ["quoting_problem", "scan_quoting"]
