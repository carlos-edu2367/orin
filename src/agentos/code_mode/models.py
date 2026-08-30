"""Small, model-independent rules for Code mode.

The model can propose a plan, but it never decides whether a request is
authorized to write, publish, or deploy.  Those decisions are represented by
these typed values and enforced by the trusted runtime/tool boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class CodeAutonomy(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    CODE_AUTONOMY = "code_autonomy"
    FULL_AUTONOMY = "full_autonomy"


class CodeWorkKind(StrEnum):
    IMPLEMENTATION = "implementation"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    INVESTIGATION = "investigation"
    REVIEW = "review"


class CodeStage(StrEnum):
    DISCOVERING = "discovering"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    FIXING = "fixing"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    COMPLETED_WITH_CAVEATS = "completed_with_caveats"
    WAITING_DECISION = "waiting_decision"
    BLOCKED = "blocked"


class CodeCompletionKind(StrEnum):
    VERIFIED = "verified"
    WITH_CAVEATS = "with_caveats"


@dataclass(frozen=True, slots=True)
class CodeModeSettings:
    autonomy: CodeAutonomy = CodeAutonomy.APPROVAL_REQUIRED
    system_notifications: bool = False
    monitoring_enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "autonomy", CodeAutonomy(self.autonomy))
        if not isinstance(self.system_notifications, bool) or not isinstance(self.monitoring_enabled, bool):
            raise ValueError("code mode boolean settings are invalid")

    @property
    def requires_plan_approval(self) -> bool:
        return self.autonomy is CodeAutonomy.APPROVAL_REQUIRED

    @property
    def permits_push(self) -> bool:
        return self.autonomy is CodeAutonomy.FULL_AUTONOMY

    def as_dict(self) -> dict[str, object]:
        return {
            "autonomy": self.autonomy.value,
            "system_notifications": self.system_notifications,
            "monitoring_enabled": self.monitoring_enabled,
        }


_CODE_PATTERN = re.compile(
    r"\b(implemente|implement|corrija|conserte|refator|crie\s+(?:uma\s+)?(?:api|tela|feature|teste)|"
    r"bug|erro|falha|teste|testes|frontend|backend|c[oó]digo|code|typescript|python|react|api|endpoint|"
    r"build|deploy|commit|pull request|pr)\b",
    re.IGNORECASE,
)
_REVIEW_PATTERN = re.compile(r"\b(revise|review|audite|audit)\b", re.IGNORECASE)
_INVESTIGATION_PATTERN = re.compile(r"\b(investigue|diagnostique|descubra\s+por que|why .*fail)\b", re.IGNORECASE)
_REFACTOR_PATTERN = re.compile(r"\b(refator\w*|refactor\w*)", re.IGNORECASE)
_BUGFIX_PATTERN = re.compile(r"\b(corrija|conserte|bug|erro|falha|fix)\b", re.IGNORECASE)


def detect_code_request(message: str) -> CodeWorkKind | None:
    """Conservative local detection; a false negative merely leaves manual Code available."""
    if not isinstance(message, str) or not _CODE_PATTERN.search(message):
        return None
    if _REVIEW_PATTERN.search(message):
        return CodeWorkKind.REVIEW
    if _INVESTIGATION_PATTERN.search(message):
        return CodeWorkKind.INVESTIGATION
    if _REFACTOR_PATTERN.search(message):
        return CodeWorkKind.REFACTOR
    if _BUGFIX_PATTERN.search(message):
        return CodeWorkKind.BUGFIX
    return CodeWorkKind.IMPLEMENTATION
