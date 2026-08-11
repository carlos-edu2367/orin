"""Bounded, dependency-free Skill retrieval with attachment signals."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Collection, Iterable, Protocol

from .models import Skill, SkillMetadata


_WORDS = re.compile(r"[\wÀ-ÿ]{2,}", re.UNICODE)
_ATTACHMENT_TERMS = {
    "pdf": {"pdf"},
    "doc": {"document", "docx", "word"},
    "docx": {"document", "docx", "word"},
    "xls": {"spreadsheet", "excel", "xlsx"},
    "xlsx": {"spreadsheet", "excel", "xlsx"},
    "csv": {"spreadsheet", "csv"},
    "ppt": {"presentation", "slides", "powerpoint"},
    "pptx": {"presentation", "slides", "powerpoint"},
}


def terms(value: str) -> set[str]:
    return {item.lower() for item in _WORDS.findall(value or "")}


class SemanticScorer(Protocol):
    def __call__(self, query: str, skill: Skill) -> float: ...


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str = ""
    attachments: tuple[str, ...] = ()
    available_tools: Collection[str] = ()
    tags: tuple[str, ...] = ()
    capability: str | None = None
    limit: int = 5
    minimum_score: float = 0.01

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("retrieval text must be a string")
        if not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("retrieval limit must be between 1 and 100")
        if self.minimum_score < 0:
            raise ValueError("minimum_score must be non-negative")
        object.__setattr__(self, "attachments", tuple(str(item) for item in self.attachments))
        object.__setattr__(self, "available_tools", frozenset(str(item) for item in self.available_tools))
        object.__setattr__(self, "tags", tuple(str(item).lower() for item in self.tags))
        if self.capability is not None:
            object.__setattr__(self, "capability", str(self.capability))


@dataclass(frozen=True, slots=True)
class RetrievalItem:
    metadata: SkillMetadata
    score: float

    @property
    def id(self) -> str:
        return self.metadata.id

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def version(self) -> str:
        return self.metadata.version

    @property
    def tags(self) -> tuple[str, ...]:
        return self.metadata.tags


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    items: tuple[RetrievalItem, ...]


class SkillRetriever:
    """Ranks metadata locally; a failed semantic scorer never blocks a turn."""

    def __init__(self, semantic_scorer: SemanticScorer | None = None) -> None:
        self._semantic_scorer = semantic_scorer

    def retrieve(self, skills: Iterable[Skill], query: RetrievalQuery) -> RetrievalResult:
        wanted = terms(query.text)
        attachment_terms = self._attachment_terms(query.attachments)
        ranked: list[RetrievalItem] = []
        for skill in skills:
            if not skill.enabled or not set(skill.required_tools).issubset(query.available_tools):
                continue
            if query.tags and not set(query.tags).issubset({tag.lower() for tag in skill.tags}):
                continue
            if query.capability and query.capability not in skill.capabilities:
                continue
            score = self._lexical_score(skill, wanted, attachment_terms)
            if self._semantic_scorer is not None and query.text.strip():
                try:
                    score += max(0.0, float(self._semantic_scorer(query.text, skill)))
                except Exception:  # Optional enrichment must not block lexical fallback.
                    pass
            if score >= query.minimum_score:
                ranked.append(RetrievalItem(skill.metadata, round(score, 6)))
        ranked.sort(key=lambda item: (-item.score, item.id, item.version))
        return RetrievalResult(tuple(ranked[:query.limit]))

    @staticmethod
    def _attachment_terms(attachments: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for attachment in attachments:
            lowered = attachment.lower().rsplit("?", 1)[0]
            suffix = lowered.rsplit(".", 1)[-1] if "." in lowered else ""
            found.update(_ATTACHMENT_TERMS.get(suffix, ()))
            found.update(terms(lowered.split("/")[-1]))
        return found

    @staticmethod
    def _lexical_score(skill: Skill, wanted: set[str], attachment_terms: set[str]) -> float:
        name = terms(skill.name) | terms(skill.id)
        description = terms(skill.description)
        tags = {tag.lower() for tag in skill.tags}
        capabilities = set(skill.capabilities)
        use_cases = terms(" ".join(skill.when_to_use))
        score = (
            6 * len(wanted & name)
            + 3 * len(wanted & description)
            + 8 * len(wanted & tags)
            + 5 * len(wanted & capabilities)
            + 3 * len(wanted & use_cases)
        )
        metadata_terms = name | description | tags | capabilities | use_cases
        score += 30 * len(attachment_terms & metadata_terms)
        return float(score)


__all__ = ["RetrievalItem", "RetrievalQuery", "RetrievalResult", "SemanticScorer", "SkillRetriever", "terms"]
