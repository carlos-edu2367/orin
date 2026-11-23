"""Tiny expression language for pipeline files (schema v1).

No dynamic evaluation of any kind: a hand-written scanner and a recursive-descent
parser build a tree of frozen dataclasses, and a small function walks it. The only
things an expression can do are read a path in the context, compare with ``==`` and
``!=``, combine with ``and``/``or``/``not`` and take ``len()`` of a path.

Grammar (single source, copied to ``docs/PIPELINES.md``)::

    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := atom (("==" | "!=") atom)?
    atom    := literal | path | "len" "(" path ")" | "(" expr ")"
    path    := ident ("." ident)*        # at most 8 segments; ident = [a-z][a-z0-9_]*
    literal := STRING | INT | "true" | "false" | "null"
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Union

MAX_EXPRESSION_CHARS = 500
MAX_PATH_SEGMENTS = 8
TEMPLATE_OPEN = "${{"
TEMPLATE_CLOSE = "}}"

_KEYWORDS = frozenset({"and", "or", "not", "true", "false", "null", "len"})
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
_IDENT_REST = _LOWER + _DIGITS + "_"
_WHITESPACE = " \t\r\n"


class ExpressionError(ValueError):
    """Syntax or shape error; ``offset`` is a 0-based character offset in the scanned text."""

    def __init__(self, message: str, offset: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.offset = offset


@dataclass(frozen=True, slots=True)
class Literal:
    value: object


@dataclass(frozen=True, slots=True)
class PathRef:
    segments: tuple[str, ...]
    offset: int = 0

    @property
    def dotted(self) -> str:
        return ".".join(self.segments)


@dataclass(frozen=True, slots=True)
class Len:
    path: PathRef


@dataclass(frozen=True, slots=True)
class Not:
    operand: "Expr"


@dataclass(frozen=True, slots=True)
class And:
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True, slots=True)
class Or:
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True, slots=True)
class Compare:
    op: str
    left: "Expr"
    right: "Expr"


Expr = Union[Literal, PathRef, Len, Not, And, Or, Compare]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # ident | string | int | op | end
    value: str
    offset: int


def _scan(text: str) -> list[_Token]:
    if len(text) > MAX_EXPRESSION_CHARS:
        raise ExpressionError(f"expressão com mais de {MAX_EXPRESSION_CHARS} caracteres", MAX_EXPRESSION_CHARS)
    for index, char in enumerate(text):
        if (ord(char) < 32 and char not in _WHITESPACE) or ord(char) == 127:
            raise ExpressionError("caractere de controle na expressão", index)
    tokens: list[_Token] = []
    pos = 0
    size = len(text)
    while pos < size:
        char = text[pos]
        if char in _WHITESPACE:
            pos += 1
        elif char in _LOWER:
            end = pos + 1
            while end < size and text[end] in _IDENT_REST:
                end += 1
            tokens.append(_Token("ident", text[pos:end], pos))
            pos = end
        elif char in _DIGITS or (char == "-" and pos + 1 < size and text[pos + 1] in _DIGITS):
            end = pos + 1
            while end < size and text[end] in _DIGITS:
                end += 1
            tokens.append(_Token("int", text[pos:end], pos))
            pos = end
        elif char in "'\"":
            end = pos + 1
            chars: list[str] = []
            while end < size and text[end] != char:
                if text[end] == "\\" and end + 1 < size and text[end + 1] in (char, "\\"):
                    chars.append(text[end + 1])
                    end += 2
                else:
                    chars.append(text[end])
                    end += 1
            if end >= size:
                raise ExpressionError("texto sem aspas de fechamento", pos)
            tokens.append(_Token("string", "".join(chars), pos))
            pos = end + 1
        elif text.startswith("==", pos) or text.startswith("!=", pos):
            tokens.append(_Token("op", text[pos:pos + 2], pos))
            pos += 2
        elif char in "().":
            tokens.append(_Token("op", char, pos))
            pos += 1
        else:
            raise ExpressionError(f"caractere não permitido `{char}`", pos)
    tokens.append(_Token("end", "", size))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _next(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _is(self, kind: str, value: str | None = None) -> bool:
        token = self._peek()
        return token.kind == kind and (value is None or token.value == value)

    def parse(self) -> Expr:
        if self._is("end"):
            raise ExpressionError("expressão vazia", 0)
        expr = self._or()
        token = self._peek()
        if token.kind != "end":
            raise ExpressionError(f"trecho inesperado `{token.value}`", token.offset)
        return expr

    def _or(self) -> Expr:
        left = self._and()
        while self._is("ident", "or"):
            self._next()
            left = Or(left, self._and())
        return left

    def _and(self) -> Expr:
        left = self._not()
        while self._is("ident", "and"):
            self._next()
            left = And(left, self._not())
        return left

    def _not(self) -> Expr:
        if self._is("ident", "not"):
            self._next()
            return Not(self._not())
        return self._cmp()

    def _cmp(self) -> Expr:
        left = self._atom()
        if self._is("op", "==") or self._is("op", "!="):
            op = self._next().value
            return Compare(op, left, self._atom())
        return left

    def _atom(self) -> Expr:
        token = self._next()
        if token.kind == "string":
            return Literal(token.value)
        if token.kind == "int":
            return Literal(int(token.value))
        if token.kind == "op" and token.value == "(":
            inner = self._or()
            closing = self._next()
            if not (closing.kind == "op" and closing.value == ")"):
                raise ExpressionError("faltou fechar o parêntese", closing.offset)
            return inner
        if token.kind == "ident":
            if token.value == "true":
                return Literal(True)
            if token.value == "false":
                return Literal(False)
            if token.value == "null":
                return Literal(None)
            if token.value == "len":
                return self._len(token)
            if token.value in {"and", "or", "not"}:
                raise ExpressionError(f"`{token.value}` no lugar errado", token.offset)
            return self._path(token)
        if token.kind == "end":
            raise ExpressionError("a expressão termina antes do esperado", token.offset)
        raise ExpressionError(f"trecho inesperado `{token.value}`", token.offset)

    def _len(self, token: _Token) -> Expr:
        opening = self._next()
        if not (opening.kind == "op" and opening.value == "("):
            raise ExpressionError("`len` precisa de parênteses: len(caminho)", opening.offset)
        first = self._next()
        if first.kind != "ident" or first.value in _KEYWORDS:
            raise ExpressionError("`len()` aceita só um caminho", first.offset)
        path = self._path(first)
        closing = self._next()
        if not (closing.kind == "op" and closing.value == ")"):
            raise ExpressionError("`len()` aceita só um caminho", closing.offset)
        return Len(path)

    def _path(self, first: _Token) -> PathRef:
        segments = [first.value]
        while self._is("op", "."):
            self._next()
            following = self._next()
            if following.kind != "ident":
                raise ExpressionError("depois do ponto vem o nome de um campo", following.offset)
            segments.append(following.value)
        if len(segments) > MAX_PATH_SEGMENTS:
            raise ExpressionError(f"caminho com mais de {MAX_PATH_SEGMENTS} segmentos", first.offset)
        return PathRef(tuple(segments), first.offset)


def parse_expression(text: str) -> Expr:
    """Parse a bare expression (no ``${{ }}`` wrapper)."""
    return _Parser(_scan(str(text))).parse()


def unwrap_expression(text: str) -> str:
    """The inside of a whole-value ``${{ ... }}``; a bare expression is returned stripped."""
    stripped = str(text).strip()
    if stripped.startswith(TEMPLATE_OPEN) and stripped.endswith(TEMPLATE_CLOSE) and len(stripped) >= 5:
        return stripped[len(TEMPLATE_OPEN):-len(TEMPLATE_CLOSE)].strip()
    return stripped


def collect_paths(expr: Expr) -> tuple[PathRef, ...]:
    """Every path an expression reads, in source order."""
    if isinstance(expr, PathRef):
        return (expr,)
    if isinstance(expr, Len):
        return (expr.path,)
    if isinstance(expr, Not):
        return collect_paths(expr.operand)
    if isinstance(expr, (And, Or, Compare)):
        return collect_paths(expr.left) + collect_paths(expr.right)
    return ()


Resolver = Callable[[tuple[str, ...]], object]


def resolve_path(context: Mapping[str, object], segments: tuple[str, ...]) -> object:
    """Walk nested mappings; anything missing is ``None``."""
    current: object = context
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def evaluate(expr: Expr, resolver: Resolver) -> object:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, PathRef):
        return resolver(expr.segments)
    if isinstance(expr, Len):
        value = resolver(expr.path.segments)
        if isinstance(value, (str, list, tuple, dict)):
            return len(value)
        return 0
    if isinstance(expr, Not):
        return not bool(evaluate(expr.operand, resolver))
    if isinstance(expr, And):
        return bool(evaluate(expr.left, resolver)) and bool(evaluate(expr.right, resolver))
    if isinstance(expr, Or):
        return bool(evaluate(expr.left, resolver)) or bool(evaluate(expr.right, resolver))
    if isinstance(expr, Compare):
        left = evaluate(expr.left, resolver)
        right = evaluate(expr.right, resolver)
        same = type(left) is type(right) and left == right if isinstance(left, bool) or isinstance(right, bool) else left == right
        return same if expr.op == "==" else not same
    raise ExpressionError("expressão desconhecida", 0)


@dataclass(frozen=True, slots=True)
class TemplateRef:
    """One ``${{ path }}`` found inside a longer text."""

    path: PathRef
    start: int
    end: int


TemplatePart = Union[str, TemplateRef]


def split_template(text: str) -> tuple[TemplatePart, ...]:
    """Split a text into literal pieces and ``${{ path }}`` references (paths only)."""
    parts: list[TemplatePart] = []
    pos = 0
    size = len(text)
    while pos < size:
        start = text.find(TEMPLATE_OPEN, pos)
        if start < 0:
            parts.append(text[pos:])
            break
        if start > pos:
            parts.append(text[pos:start])
        inner_start = start + len(TEMPLATE_OPEN)
        close = text.find(TEMPLATE_CLOSE, inner_start)
        if close < 0:
            raise ExpressionError("`${{` sem `}}` de fechamento", start)
        inner = text[inner_start:close]
        if TEMPLATE_OPEN in inner:
            raise ExpressionError("`${{ }}` não pode ficar dentro de outro `${{ }}`", inner_start + inner.find(TEMPLATE_OPEN))
        if not inner.strip():
            raise ExpressionError("`${{ }}` vazio", start)
        try:
            expr = parse_expression(inner)
        except ExpressionError as exc:
            raise ExpressionError(exc.message, inner_start + exc.offset) from None
        if not isinstance(expr, PathRef):
            raise ExpressionError(
                "dentro de um texto, `${{ }}` aceita só um caminho (use `when` para condições)",
                inner_start + (len(inner) - len(inner.lstrip())),
            )
        parts.append(TemplateRef(PathRef(expr.segments, inner_start + expr.offset), start, close + len(TEMPLATE_CLOSE)))
        pos = close + len(TEMPLATE_CLOSE)
    return tuple(parts)


def format_value(value: object) -> str:
    """How a resolved value appears inside a text: scalars as text, composites as compact JSON."""
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def render_template(text: str, resolver: Resolver) -> str:
    """Replace every ``${{ path }}`` in ``text``."""
    out: list[str] = []
    for part in split_template(text):
        out.append(part if isinstance(part, str) else format_value(resolver(part.path.segments)))
    return "".join(out)


__all__ = [
    "And",
    "Compare",
    "Expr",
    "ExpressionError",
    "Len",
    "Literal",
    "MAX_EXPRESSION_CHARS",
    "MAX_PATH_SEGMENTS",
    "Not",
    "Or",
    "PathRef",
    "TemplatePart",
    "TemplateRef",
    "collect_paths",
    "evaluate",
    "format_value",
    "parse_expression",
    "render_template",
    "resolve_path",
    "split_template",
    "unwrap_expression",
]
