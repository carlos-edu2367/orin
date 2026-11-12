"""Personalized PageRank over the project's file and symbol dependency graph.

Pure NumPy implementation (no SciPy or NetworkX) designed for sub-5ms execution
on repositories up to 10k files.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re

import numpy as np


DEFAULT_ALPHA = 0.85
DEFAULT_MAX_ITER = 20
DEFAULT_TOL = 1e-6

FOCUS_PATH_BONUS = 2.0
QUERY_PATH_BONUS = 1.5
QUERY_IDENTIFIER_BONUS = 1.0

FOCUS_EDGE_MULTIPLIER = 50.0
QUERY_IDENTIFIER_EDGE_MULTIPLIER = 10.0

COMPOUND_SYMBOL_MULTIPLIER = 10.0
LOW_SIGNAL_SYMBOL_MULTIPLIER = 0.1
UBIQUITOUS_DEFINER_THRESHOLD = 5

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_TRANSITION_RE = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2,}[a-z]")


def extract_identifiers(text: str) -> tuple[str, ...]:
    """Return unique identifiers in order of appearance."""
    if not text:
        return ()
    seen: dict[str, None] = {}
    for match in _IDENTIFIER_RE.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen[token] = None
    return tuple(seen)


def is_compound_identifier(symbol: str) -> bool:
    """True for non-private snake_case or CamelCase identifiers with length >= 8."""
    if not symbol or len(symbol) < 8 or symbol.startswith("_"):
        return False
    stripped = symbol.strip("_")
    if "_" in stripped:
        return True
    return bool(_CAMEL_TRANSITION_RE.search(symbol))


def symbol_edge_multiplier(symbol: str, *, num_definers: int) -> float:
    """Edge weight multiplier for a symbol reference."""
    if symbol.startswith("_") or num_definers > UBIQUITOUS_DEFINER_THRESHOLD:
        return LOW_SIGNAL_SYMBOL_MULTIPLIER
    if is_compound_identifier(symbol):
        return COMPOUND_SYMBOL_MULTIPLIER
    return 1.0


def _normalize_rel_path(path: str) -> str:
    norm = path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def personalized_pagerank(
    paths: Sequence[str],
    edges: Sequence[tuple[str, str, float, tuple[str, ...]]],
    *,
    file_symbols: Mapping[str, Sequence[str]] | None = None,
    focus_paths: Sequence[str] = (),
    query: str = "",
    alpha: float = DEFAULT_ALPHA,
    max_iter: int = DEFAULT_MAX_ITER,
    tol: float = DEFAULT_TOL,
) -> dict[str, float]:
    """Compute Personalized PageRank score for every path in ``paths``."""
    nodes = sorted({p for p in paths if p})
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: 1.0}

    node_to_idx = {path: idx for idx, path in enumerate(nodes)}
    lower_node_to_path = {path.lower(): path for path in nodes}
    nodes_by_len_desc = sorted(nodes, key=len, reverse=True)

    focus_set: set[str] = set()
    for raw_focus in focus_paths:
        if not isinstance(raw_focus, str) or not raw_focus.strip():
            continue
        norm = _normalize_rel_path(raw_focus)
        if norm in node_to_idx:
            focus_set.add(norm)
        elif norm.lower() in lower_node_to_path:
            focus_set.add(lower_node_to_path[norm.lower()])
        else:
            norm_lower = norm.lower()
            suffix_lower = f"/{norm_lower}"
            matched_suffix = False
            for candidate in nodes:
                if candidate.lower().endswith(suffix_lower):
                    focus_set.add(candidate)
                    matched_suffix = True
            if not matched_suffix:
                for candidate in nodes_by_len_desc:
                    if norm_lower.endswith(f"/{candidate.lower()}"):
                        focus_set.add(candidate)
                        break

    query_clean = (query or "").strip()
    query_lower = query_clean.lower()
    query_identifiers = frozenset(extract_identifiers(query_clean)) if query_clean else frozenset()
    query_identifiers_lower = frozenset(ident.lower() for ident in query_identifiers)

    query_path_matches: set[str] = set()
    query_sym_matches: set[str] = set()

    if query_clean:
        for path in nodes:
            basename = path.rsplit("/", 1)[-1]
            stem = basename.rsplit(".", 1)[0] if "." in basename else basename
            if (
                path.lower() in query_lower
                or (basename and basename.lower() in query_lower)
                or (len(stem) >= 4 and stem.lower() in query_identifiers_lower)
            ):
                query_path_matches.add(path)
            if file_symbols is not None and query_identifiers:
                defined = file_symbols.get(path, ())
                if any(sym in query_identifiers or sym.lower() in query_identifiers_lower for sym in defined):
                    query_sym_matches.add(path)

    has_signals = bool(focus_set or query_path_matches or query_sym_matches)

    if not has_signals:
        p = np.full(n, 1.0 / n, dtype=np.float64)
    else:
        p = np.full(n, 1e-4 / n, dtype=np.float64)
        for idx, path in enumerate(nodes):
            if path in focus_set:
                p[idx] += FOCUS_PATH_BONUS
            if path in query_path_matches:
                p[idx] += QUERY_PATH_BONUS
            if path in query_sym_matches:
                p[idx] += QUERY_IDENTIFIER_BONUS
        p /= float(p.sum())

    edge_weights: dict[tuple[int, int], float] = {}
    for src_path, dst_path, base_weight, matched_symbols in edges:
        if (
            src_path == dst_path
            or not isinstance(base_weight, (int, float))
            or not math.isfinite(base_weight)
            or base_weight <= 0.0
        ):
            continue
        u = node_to_idx.get(src_path)
        v = node_to_idx.get(dst_path)
        if u is None or v is None:
            continue

        has_query_sym = bool(
            query_identifiers
            and any(sym in query_identifiers or sym.lower() in query_identifiers_lower for sym in matched_symbols)
        )

        # Forward edge: src_path -> dst_path (importer/caller -> defined target)
        w_fwd = float(base_weight)
        if src_path in focus_set:
            w_fwd *= FOCUS_EDGE_MULTIPLIER
        if has_query_sym:
            w_fwd *= QUERY_IDENTIFIER_EDGE_MULTIPLIER
        edge_weights[(u, v)] = edge_weights.get((u, v), 0.0) + w_fwd

        # Reverse coupling when personalization signals are active so callers of
        # focused files or queried symbols also receive PageRank flow.
        if has_signals:
            w_rev = 0.0
            if dst_path in focus_set:
                w_rev = float(base_weight) * FOCUS_EDGE_MULTIPLIER
                if has_query_sym:
                    w_rev *= QUERY_IDENTIFIER_EDGE_MULTIPLIER
            elif has_query_sym:
                w_rev = float(base_weight) * QUERY_IDENTIFIER_EDGE_MULTIPLIER
            elif focus_set:
                w_rev = float(base_weight) * 0.15
            if w_rev > 0.0:
                edge_weights[(v, u)] = edge_weights.get((v, u), 0.0) + w_rev

    if edge_weights:
        pairs = list(edge_weights.keys())
        src_idx = np.fromiter((pair[0] for pair in pairs), dtype=np.int64, count=len(pairs))
        dst_idx = np.fromiter((pair[1] for pair in pairs), dtype=np.int64, count=len(pairs))
        weights = np.fromiter(edge_weights.values(), dtype=np.float64, count=len(pairs))
    else:
        src_idx = np.empty(0, dtype=np.int64)
        dst_idx = np.empty(0, dtype=np.int64)
        weights = np.empty(0, dtype=np.float64)

    out_strength = np.zeros(n, dtype=np.float64)
    if src_idx.size > 0:
        np.add.at(out_strength, src_idx, weights)

    dangling_mask = out_strength <= 0.0
    norm_weights = np.zeros_like(weights, dtype=np.float64)
    if src_idx.size > 0:
        valid = out_strength[src_idx] > 0.0
        norm_weights[valid] = weights[valid] / out_strength[src_idx[valid]]

    rank = p.copy()
    for _ in range(max(1, int(max_iter))):
        dangling_mass = float(rank[dangling_mask].sum())
        next_rank = ((1.0 - alpha) + alpha * dangling_mass) * p
        if src_idx.size > 0:
            contributions = alpha * rank[src_idx] * norm_weights
            np.add.at(next_rank, dst_idx, contributions)
        total = float(next_rank.sum())
        if total > 0.0:
            next_rank /= total
        if float(np.abs(next_rank - rank).sum()) < tol:
            rank = next_rank
            break
        rank = next_rank

    return {nodes[idx]: float(rank[idx]) for idx in range(n)}


__all__ = [
    "COMPOUND_SYMBOL_MULTIPLIER",
    "DEFAULT_ALPHA",
    "DEFAULT_MAX_ITER",
    "DEFAULT_TOL",
    "FOCUS_EDGE_MULTIPLIER",
    "FOCUS_PATH_BONUS",
    "LOW_SIGNAL_SYMBOL_MULTIPLIER",
    "QUERY_IDENTIFIER_BONUS",
    "QUERY_IDENTIFIER_EDGE_MULTIPLIER",
    "QUERY_PATH_BONUS",
    "UBIQUITOUS_DEFINER_THRESHOLD",
    "extract_identifiers",
    "is_compound_identifier",
    "personalized_pagerank",
    "symbol_edge_multiplier",
]
