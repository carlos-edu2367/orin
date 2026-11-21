"""Deterministic write-time conflict detection for agent memories (WS-D3 / D-RF6).

Applies the fixed rule order defined in the 0.13 specification:
1. Exact normalized hash active in the same scope -> ``noop``.
2. User memory protection: existing ``source_kind == "user"`` can only be
   superseded by another ``source_kind == "user"``; agent contradicting or
   attempting to replace user memory -> ``conflict`` (``status="proposed"``).
3. High similarity (semantic >= 0.98 or Jaccard >= 0.88 or short pt-BR
   same-subject/predicate value replacement) without polarity inversion ->
   ``supersede``.
4. Polarity inversion with compatible origin & confidence >= existing ->
   ``supersede`` (``polarity_supersede``); otherwise falls into conflict.
5. Ambiguous band (``0.85 <= sim < 0.98``) or unresolved conflict: consults
   ``ConflictAdjudicator`` only when ``memory.conflicts.llmAdjudication == True``;
   otherwise returns ``conflict`` (``status="proposed"``).
6. Otherwise -> ``add`` (``status="active"``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from .conflict_adjudicator import ConflictAdjudicator
from .models_write import (
    MemoryWriteContext,
    WriteDecision,
    compute_text_hash,
    normalize_memory_text,
)

# Public aliases matching the D-RF6 plan contract.
normalize = normalize_memory_text
text_hash = compute_text_hash

MAX_CONFLICT_CANDIDATES = 5
SEMANTIC_SUPERSEDE_THRESHOLD = 0.98
SEMANTIC_AMBIGUOUS_LOW = 0.85
JACCARD_SUPERSEDE_THRESHOLD = 0.88

_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_SLOT_TOKEN_RE = re.compile(r"[\wÀ-ÿ]+(?:[./+-][\wÀ-ÿ]+)*", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})
_ARTICLES = frozenset({"o", "a", "os", "as", "um", "uma", "uns", "umas", "the", "an"})

_NEGATION_RE = re.compile(
    r"\b(?:"
    r"não|nao|nunca|jamais|evite|evitar|proibido|proibida|sem|"
    r"never|not|dont|don't|cannot|can't|won't|wont|shouldn't|shouldnt|mustn't|mustnt|"
    r"neither|nor|avoid|without|no\s+longer|no\s+more"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_NEG_CLAUSE_SPLIT_RE = re.compile(r"(?:[,;.]|\b(?:e|mas|porém|porem|and|but)\b)", re.IGNORECASE | re.UNICODE)
_NEG_TARGET_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
_NEG_FILLER_WORDS = frozenset({
    *_STOPWORDS,
    "de", "do", "da", "em", "no", "na", "nos", "nas", "ao", "aos",
    "um", "uma", "uns", "umas", "os", "as", "ou", "se", "neste", "nesta", "nesse", "nessa",
    "sempre", "always", "the", "an", "to", "in", "on", "of", "at", "by", "or", "is", "be",
})
_NEGATION_SINGLE_TOKENS = frozenset({
    "não", "nao", "nunca", "jamais", "evite", "evitar", "proibido", "proibida", "sem",
    "never", "not", "dont", "don't", "cannot", "can't", "won't", "wont", "shouldn't",
    "shouldnt", "mustn't", "mustnt", "neither", "nor", "avoid", "without",
})


def _terms(text_val: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text_val or "")} - _STOPWORDS


def _negated_targets(text_val: str) -> set[tuple[str, tuple[str, ...]]]:
    targets: set[tuple[str, tuple[str, ...]]] = set()
    raw = text_val or ""
    for match in _NEGATION_RE.finditer(raw):
        tail = raw[match.end() :]
        clause = _NEG_CLAUSE_SPLIT_RE.split(tail, maxsplit=1)[0]
        tokens = [
            tok.lower()
            for tok in _NEG_TARGET_WORD_RE.findall(clause)
            if tok.lower() not in _NEG_FILLER_WORDS
        ]
        targets.add(("neg", tuple(tokens[:3])))
    return targets


def _has_polarity_inversion(text_a: str, text_b: str) -> bool:
    neg_a = [m.group(0).lower() for m in _NEGATION_RE.finditer(text_a or "")]
    neg_b = [m.group(0).lower() for m in _NEGATION_RE.finditer(text_b or "")]
    if (bool(neg_a) != bool(neg_b)) or (len(neg_a) != len(neg_b)):
        return True
    if not neg_a and not neg_b:
        return False
    return _negated_targets(text_a) != _negated_targets(text_b)


def compute_jaccard(text_a: str, text_b: str) -> float:
    terms_a = _terms(text_a)
    terms_b = _terms(text_b)
    if not terms_a or not terms_b:
        return 0.0
    union_len = len(terms_a | terms_b)
    if union_len == 0:
        return 0.0
    return len(terms_a & terms_b) / union_len


def _content_terms_without_negation(text_val: str) -> set[str]:
    cleaned = _NEGATION_RE.sub(" ", text_val or "")
    return {
        tok.lower()
        for tok in _WORD.findall(cleaned)
        if tok.lower() not in _STOPWORDS and tok.lower() not in _NEGATION_SINGLE_TOKENS
    }


def _same_topic_for_polarity(
    text_a: str,
    text_b: str,
    semantic_similarity: float | None = None,
) -> bool:
    if semantic_similarity is not None and semantic_similarity >= SEMANTIC_AMBIGUOUS_LOW:
        return True
    ca = _content_terms_without_negation(text_a)
    cb = _content_terms_without_negation(text_b)
    if not ca or not cb:
        return False
    inter = len(ca & cb)
    if inter == 0:
        return False
    jaccard_no_neg = inter / len(ca | cb)
    overlap_min = inter / min(len(ca), len(cb))
    return jaccard_no_neg >= 0.60 or (inter >= 2 and overlap_min >= 0.75)


def _is_short_slot_replacement(text_a: str, text_b: str) -> bool:
    """Detect short subject+predicate statements differing in a single value token.

    Handles short Portuguese/English factual updates where word-level Jaccard drops
    below 0.88 due to sentence brevity (e.g. ``"o projeto usa pnpm"`` ->
    ``"o projeto usa npm"``), while rejecting subject changes (``"Carlos prefere café"``
    vs ``"Maria prefere café"``) and multi-word complementary facts.
    """
    norm_a = normalize_memory_text(text_a)
    norm_b = normalize_memory_text(text_b)
    if not norm_a or not norm_b or norm_a == norm_b:
        return False

    tokens_a = [tok for tok in _SLOT_TOKEN_RE.findall(norm_a) if tok not in _ARTICLES]
    tokens_b = [tok for tok in _SLOT_TOKEN_RE.findall(norm_b) if tok not in _ARTICLES]
    if len(tokens_a) != len(tokens_b):
        return False
    n = len(tokens_a)
    if n < 3 or n > 10:
        return False

    diffs = [idx for idx in range(n) if tokens_a[idx] != tokens_b[idx]]
    if len(diffs) != 1:
        return False

    diff_idx = diffs[0]
    # Keep at least two leading tokens (subject + predicate) identical so distinct
    # subjects ("Carlos prefere café" vs "Maria prefere café") do not collide.
    if diff_idx < 2:
        return False

    tok_a = tokens_a[diff_idx]
    tok_b = tokens_b[diff_idx]
    if tok_a in _NEGATION_SINGLE_TOKENS or tok_b in _NEGATION_SINGLE_TOKENS:
        return False
    return True


@dataclass(frozen=True, slots=True)
class ExistingMemoryCandidate:
    memory_id: str
    fact: str
    text_hash: str = ""
    kind: str = "fact"
    scope_type: str = "user"
    scope_id: str = ""
    project_id: str | None = None
    agent_key: str | None = None
    source_kind: str = "agent"
    trust: int = 1
    confidence: float = 1.0
    semantic_similarity: float | None = None
    status: str = "active"

    @classmethod
    def from_any(
        cls,
        raw: Any,
        *,
        semantic_similarity: float | None = None,
    ) -> ExistingMemoryCandidate:
        if isinstance(raw, cls):
            if semantic_similarity is not None and raw.semantic_similarity is None:
                return cls(
                    memory_id=raw.memory_id,
                    fact=raw.fact,
                    text_hash=raw.text_hash,
                    kind=raw.kind,
                    scope_type=raw.scope_type,
                    scope_id=raw.scope_id,
                    project_id=raw.project_id,
                    agent_key=raw.agent_key,
                    source_kind=raw.source_kind,
                    trust=raw.trust,
                    confidence=raw.confidence,
                    semantic_similarity=semantic_similarity,
                    status=raw.status,
                )
            return raw
        if isinstance(raw, Mapping):
            fact = str(raw.get("fact") or "")
            raw_hash = str(raw.get("text_hash") or "").strip() or compute_text_hash(fact)
            sim = semantic_similarity if semantic_similarity is not None else raw.get("semantic_similarity")
            return cls(
                memory_id=str(raw.get("memory_id") or raw.get("id") or ""),
                fact=fact,
                text_hash=raw_hash,
                kind=str(raw.get("kind") or "fact"),
                scope_type=str(raw.get("scope_type") or "user"),
                scope_id=str(raw.get("scope_id") or ""),
                project_id=str(raw["project_id"]) if raw.get("project_id") is not None else None,
                agent_key=str(raw["agent_key"]) if raw.get("agent_key") is not None else None,
                source_kind=str(raw.get("source_kind") or "agent"),
                trust=int(raw["trust"]) if raw.get("trust") is not None else 1,
                confidence=float(raw["confidence"]) if raw.get("confidence") is not None else 1.0,
                semantic_similarity=float(sim) if sim is not None else None,
                status=str(raw.get("status") or "active"),
            )
        fact = str(getattr(raw, "fact", "") or "")
        raw_hash = str(getattr(raw, "text_hash", "") or "").strip() or compute_text_hash(fact)
        sim = semantic_similarity if semantic_similarity is not None else getattr(raw, "semantic_similarity", None)
        return cls(
            memory_id=str(getattr(raw, "memory_id", "") or getattr(raw, "id", "") or ""),
            fact=fact,
            text_hash=raw_hash,
            kind=str(getattr(raw, "kind", "fact") or "fact"),
            scope_type=str(getattr(raw, "scope_type", "user") or "user"),
            scope_id=str(getattr(raw, "scope_id", "") or ""),
            project_id=getattr(raw, "project_id", None),
            agent_key=getattr(raw, "agent_key", None),
            source_kind=str(getattr(raw, "source_kind", "agent") or "agent"),
            trust=int(getattr(raw, "trust", 1)),
            confidence=float(getattr(raw, "confidence", 1.0)),
            semantic_similarity=float(sim) if sim is not None else None,
            status=str(getattr(raw, "status", "active") or "active"),
        )


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    existing: ExistingMemoryCandidate
    jaccard: float
    semantic_similarity: float | None
    polarity_inversion: bool
    short_replacement: bool
    high_similarity: bool
    ambiguous_similarity: bool

    @property
    def sort_score(self) -> float:
        sem = self.semantic_similarity if self.semantic_similarity is not None else 0.0
        bonus = 0.90 if self.short_replacement else 0.0
        return max(sem, self.jaccard, bonus)


class ConflictResolver:
    """Deterministic conflict resolver plugged into ``WritePolicyChain``."""

    def __init__(
        self,
        *,
        adjudicator: ConflictAdjudicator | None = None,
        llm_adjudication_enabled: bool | Callable[[], bool] | None = None,
    ) -> None:
        self._adjudicator = adjudicator
        self._llm_adjudication_enabled = llm_adjudication_enabled

    def _is_llm_adjudication_enabled(
        self,
        context: MemoryWriteContext,
        override: bool | None = None,
    ) -> bool:
        if override is not None:
            return bool(override)
        if context.llm_adjudication is not None:
            return bool(context.llm_adjudication)
        if callable(self._llm_adjudication_enabled):
            try:
                return bool(self._llm_adjudication_enabled())
            except Exception:  # noqa: BLE001
                return False
        if self._llm_adjudication_enabled is not None:
            return bool(self._llm_adjudication_enabled)
        return False

    def decide(self, candidate: Any, context: MemoryWriteContext) -> WriteDecision:
        return self.resolve(candidate, context)

    def resolve(
        self,
        candidate: Any,
        context: MemoryWriteContext,
        *,
        existing_candidates: Sequence[Any] | None = None,
        llm_adjudication: bool | None = None,
    ) -> WriteDecision:
        raw_fact = (
            str(candidate.get("fact") or "")
            if isinstance(candidate, Mapping)
            else str(getattr(candidate, "fact", "") or "")
        )
        raw_hash = (
            str(candidate.get("text_hash") or "").strip()
            if isinstance(candidate, Mapping)
            else str(getattr(candidate, "text_hash", "") or "").strip()
        )
        cand_hash = raw_hash or compute_text_hash(raw_fact)
        cand_confidence = (
            float(candidate.get("confidence", 1.0))
            if isinstance(candidate, Mapping)
            else float(getattr(candidate, "confidence", 1.0))
        )
        cand_supersedes_id = (
            candidate.get("supersedes_id")
            if isinstance(candidate, Mapping)
            else getattr(candidate, "supersedes_id", None)
        )
        cand_source_kind = str(context.source_kind or "agent").strip().lower()
        cand_trust = int(context.trust)

        raw_pool = existing_candidates if existing_candidates is not None else context.existing_candidates
        active_candidates: list[ExistingMemoryCandidate] = []
        for item in raw_pool or ():
            converted = ExistingMemoryCandidate.from_any(item)
            if not converted.memory_id or converted.status != "active":
                continue
            active_candidates.append(converted)
            if len(active_candidates) >= MAX_CONFLICT_CANDIDATES:
                break

        if not active_candidates:
            return WriteDecision(action="add", target_id=None, status="active", reason="distinct")

        # Rule 1: exact normalized hash active in the same scope -> noop.
        for existing in active_candidates:
            existing_hash = existing.text_hash or compute_text_hash(existing.fact)
            if existing_hash == cand_hash:
                return WriteDecision(
                    action="noop",
                    target_id=existing.memory_id,
                    status="active",
                    reason="exact_hash",
                )

        # Respect explicit surgical supersession requested by caller (e.g. replaces_memory_id).
        if cand_supersedes_id and str(cand_supersedes_id).strip():
            return WriteDecision(
                action="add",
                target_id=None,
                status="active",
                reason="explicit_supersede",
            )

        evaluated: list[_EvaluatedCandidate] = []
        for existing in active_candidates:
            jaccard = compute_jaccard(raw_fact, existing.fact)
            sem = existing.semantic_similarity
            pol_inv = _has_polarity_inversion(raw_fact, existing.fact) and _same_topic_for_polarity(
                raw_fact, existing.fact, sem
            )
            short_rep = (not pol_inv) and _is_short_slot_replacement(raw_fact, existing.fact)
            high_sim = (
                (sem is not None and sem >= SEMANTIC_SUPERSEDE_THRESHOLD)
                or jaccard >= JACCARD_SUPERSEDE_THRESHOLD
                or short_rep
            )
            ambiguous_sim = (
                sem is not None and SEMANTIC_AMBIGUOUS_LOW <= sem < SEMANTIC_SUPERSEDE_THRESHOLD
            )
            evaluated.append(
                _EvaluatedCandidate(
                    existing=existing,
                    jaccard=jaccard,
                    semantic_similarity=sem,
                    polarity_inversion=pol_inv,
                    short_replacement=short_rep,
                    high_similarity=high_sim,
                    ambiguous_similarity=ambiguous_sim,
                )
            )

        # Rule 2: user memory can only be superseded by user; agent contradicting or
        # replacing user memory -> conflict (status="proposed", reason="agent_vs_user").
        if cand_source_kind != "user":
            user_conflicts = [
                ev
                for ev in evaluated
                if ev.existing.source_kind == "user"
                and (ev.polarity_inversion or ev.short_replacement or ev.high_similarity or ev.ambiguous_similarity)
            ]
            if user_conflicts:
                best_user = max(user_conflicts, key=lambda ev: ev.sort_score)
                return WriteDecision(
                    action="conflict",
                    target_id=best_user.existing.memory_id,
                    status="proposed",
                    reason="agent_vs_user",
                )

        # Rule 3: high similarity (>= 0.98 semantic, >= 0.88 Jaccard, or short pt-BR
        # same-subject/predicate replacement) without polarity inversion -> supersede.
        high_matches = [ev for ev in evaluated if ev.high_similarity and not ev.polarity_inversion]
        if high_matches:
            best_high = max(high_matches, key=lambda ev: ev.sort_score)
            if best_high.short_replacement and cand_trust < best_high.existing.trust:
                # Lower-trust source replacing a higher-trust fact -> treat as ambiguous conflict.
                return self._handle_ambiguous(
                    raw_fact,
                    active_candidates,
                    best_high.existing.memory_id,
                    cand_source_kind=cand_source_kind,
                    context=context,
                    llm_override=llm_adjudication,
                )
            return WriteDecision(
                action="supersede",
                target_id=best_high.existing.memory_id,
                status="active",
                reason="near_duplicate_or_replacement",
            )

        # Rule 4: polarity inversion with same origin (or trust >= existing and not agent_vs_user)
        # and confidence >= existing -> supersede; otherwise fall into ambiguous/conflict.
        polarity_matches = [ev for ev in evaluated if ev.polarity_inversion]
        if polarity_matches:
            best_pol = max(polarity_matches, key=lambda ev: ev.sort_score)
            same_origin = cand_source_kind == best_pol.existing.source_kind
            trusted_override = (
                cand_trust >= best_pol.existing.trust
                and not (best_pol.existing.source_kind == "user" and cand_source_kind != "user")
            )
            if (same_origin or trusted_override) and cand_confidence >= best_pol.existing.confidence:
                return WriteDecision(
                    action="supersede",
                    target_id=best_pol.existing.memory_id,
                    status="active",
                    reason="polarity_supersede",
                )
            return self._handle_ambiguous(
                raw_fact,
                active_candidates,
                best_pol.existing.memory_id,
                cand_source_kind=cand_source_kind,
                context=context,
                llm_override=llm_adjudication,
            )

        # Rule 5: ambiguous similarity band (0.85 <= sim < 0.98).
        ambiguous_matches = [ev for ev in evaluated if ev.ambiguous_similarity]
        if ambiguous_matches:
            best_amb = max(ambiguous_matches, key=lambda ev: ev.sort_score)
            return self._handle_ambiguous(
                raw_fact,
                active_candidates,
                best_amb.existing.memory_id,
                cand_source_kind=cand_source_kind,
                context=context,
                llm_override=llm_adjudication,
            )

        # Rule 6: distinct non-conflicting memory -> add.
        return WriteDecision(action="add", target_id=None, status="active", reason="distinct")

    def _handle_ambiguous(
        self,
        fact: str,
        active_candidates: Sequence[ExistingMemoryCandidate],
        target_id: str,
        *,
        cand_source_kind: str,
        context: MemoryWriteContext,
        llm_override: bool | None,
    ) -> WriteDecision:
        if self._is_llm_adjudication_enabled(context, override=llm_override) and self._adjudicator is not None:
            return self._adjudicator.adjudicate(
                fact,
                active_candidates,
                llm_adjudication_enabled=True,
                candidate_source_kind=cand_source_kind,
                default_target_id=target_id,
            )
        return WriteDecision(
            action="conflict",
            target_id=target_id,
            status="proposed",
            reason="ambiguous_conflict",
        )


__all__ = [
    "ConflictResolver",
    "ExistingMemoryCandidate",
    "JACCARD_SUPERSEDE_THRESHOLD",
    "MAX_CONFLICT_CANDIDATES",
    "SEMANTIC_AMBIGUOUS_LOW",
    "SEMANTIC_SUPERSEDE_THRESHOLD",
    "_has_polarity_inversion",
    "_is_short_slot_replacement",
    "_terms",
    "compute_jaccard",
    "normalize",
    "text_hash",
]
