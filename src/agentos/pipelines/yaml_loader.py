"""Restricted YAML reader for pipeline files (schema v1).

Untrusted input: a project file can come from a repository. The reader builds only
plain ``dict``/``list``/``str``/``int``/``float``/``bool``/``None`` values and refuses
anchors, aliases, tags, merge keys, duplicate keys and several documents. It keeps
the line and column of every node so the validator can point at the problem.

Only a restricted ``SafeLoader`` subclass is used, through ``yaml.compose``: no
object construction beyond scalars, no ``yaml.load`` with a full loader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .errors import PipelineError, PipelineLoadError

MAX_BYTES = 256 * 1024
MAX_DEPTH = 16

_TAG_PREFIX = "tag:yaml.org,2002:"
_ALLOWED_TAGS = frozenset(
    _TAG_PREFIX + name for name in ("str", "int", "float", "bool", "null", "seq", "map")
)
_MERGE_TAG = _TAG_PREFIX + "merge"
_BOOL_TAG = _TAG_PREFIX + "bool"
_TIMESTAMP_TAG = _TAG_PREFIX + "timestamp"


class _Forbidden(Exception):
    def __init__(self, message: str, mark: Any) -> None:
        super().__init__(message)
        self.message = message
        self.mark = mark


class _RestrictedLoader(yaml.SafeLoader):
    """SafeLoader that keeps booleans to ``true``/``false``, keeps dates as text and refuses anchors."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:  # type: ignore[override]
        event = self.peek_event()
        if getattr(event, "anchor", None):
            raise _Forbidden(
                "âncoras e aliases (&x, *x) não são permitidos em pipelines", event.start_mark
            )
        nested = isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent))
        if nested:
            self._depth += 1
            if self._depth > MAX_DEPTH:
                raise _Forbidden(f"aninhamento acima de {MAX_DEPTH} níveis", event.start_mark)
        try:
            return super().compose_node(parent, index)
        finally:
            if nested:
                self._depth -= 1


def _restrict_resolvers() -> None:
    table: dict[Any, list[tuple[str, Any]]] = {}
    for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items():
        kept = [(tag, regexp) for tag, regexp in entries if tag not in (_BOOL_TAG, _TIMESTAMP_TAG)]
        if kept:
            table[first] = kept
    _RestrictedLoader.yaml_implicit_resolvers = table
    _RestrictedLoader.add_implicit_resolver(_BOOL_TAG, re.compile(r"^(?:true|false)$"), list("tf"))


_restrict_resolvers()


@dataclass(slots=True)
class LoadedYaml:
    """Plain data plus the (line, column) of every value and key, 1-based."""

    data: Any
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)
    key_positions: dict[str, tuple[int, int]] = field(default_factory=dict)

    def where(self, path: str) -> tuple[int | None, int | None]:
        """Position of the value at ``path``; falls back to the nearest known parent."""
        return _lookup(self.positions, path)

    def key_where(self, path: str) -> tuple[int | None, int | None]:
        """Position of the key that holds ``path``; falls back to the value, then to a parent."""
        if path in self.key_positions:
            return self.key_positions[path]
        return _lookup(self.positions, path)


def _lookup(table: dict[str, tuple[int, int]], path: str) -> tuple[int | None, int | None]:
    current = path
    while True:
        if current in table:
            return table[current]
        if not current:
            return (None, None)
        cut = max(current.rfind("."), current.rfind("["))
        current = current[:cut] if cut > 0 else ""


def _error(code: str, message: str, mark: Any = None, path: str = "") -> PipelineLoadError:
    line = column = None
    if mark is not None:
        line, column = mark.line + 1, mark.column + 1
    return PipelineLoadError(PipelineError(code, message, line, column, path))


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def load_yaml(text: str) -> LoadedYaml:
    """Parse one YAML document into plain data, or raise ``PipelineLoadError``."""
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise _error("E_TOO_LARGE", f"o arquivo passa de {MAX_BYTES // 1024} KB")
    loader = _RestrictedLoader(text)
    try:
        try:
            root = loader.get_single_node()
        except _Forbidden as exc:
            raise _error("E_YAML_FORBIDDEN", exc.message, exc.mark) from None
        except yaml.composer.ComposerError as exc:
            mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
            if "single document" in str(getattr(exc, "problem", "")) or "single document" in str(exc):
                raise _error("E_YAML_FORBIDDEN", "mais de um documento no arquivo", mark) from None
            raise _error("E_YAML_SYNTAX", _problem(exc), mark) from None
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            raise _error("E_YAML_SYNTAX", _problem(exc), mark) from None
        if root is None:
            raise _error("E_YAML_SYNTAX", "o arquivo está vazio")
        positions: dict[str, tuple[int, int]] = {}
        key_positions: dict[str, tuple[int, int]] = {}
        data = _build(loader, root, "", positions, key_positions)
        return LoadedYaml(data, positions, key_positions)
    finally:
        loader.dispose()


def _problem(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc)
    return f"YAML inválido: {str(problem).strip()}"


def _build(
    loader: _RestrictedLoader,
    node: Node,
    path: str,
    positions: dict[str, tuple[int, int]],
    key_positions: dict[str, tuple[int, int]],
) -> Any:
    positions[path] = (node.start_mark.line + 1, node.start_mark.column + 1)
    if node.tag not in _ALLOWED_TAGS:
        raise _error("E_YAML_FORBIDDEN", f"tag YAML não permitida ({node.tag})", node.start_mark, path)
    if isinstance(node, ScalarNode):
        return loader.construct_object(node, deep=True)
    if isinstance(node, SequenceNode):
        return [
            _build(loader, child, f"{path}[{index}]", positions, key_positions)
            for index, child in enumerate(node.value)
        ]
    if isinstance(node, MappingNode):
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                raise _error("E_YAML_FORBIDDEN", "chave de mesclagem (<<) não é permitida", key_node.start_mark, path)
            if not isinstance(key_node, ScalarNode) or key_node.tag != _TAG_PREFIX + "str":
                raise _error("E_TYPE", "toda chave precisa ser texto", key_node.start_mark, path)
            key = str(key_node.value)
            if key in result:
                raise _error("E_YAML_FORBIDDEN", f"chave duplicada `{key}`", key_node.start_mark, _join(path, key))
            child_path = _join(path, key)
            key_positions[child_path] = (key_node.start_mark.line + 1, key_node.start_mark.column + 1)
            result[key] = _build(loader, value_node, child_path, positions, key_positions)
        return result
    raise _error("E_YAML_FORBIDDEN", "construção YAML não permitida", node.start_mark, path)


__all__ = ["LoadedYaml", "MAX_BYTES", "MAX_DEPTH", "load_yaml"]
