"""Small, model-independent rules for Code mode.

The model can propose a plan, but it never decides whether a request is
authorized to write, publish, or deploy.  Those decisions are represented by
these typed values and enforced by the trusted runtime/tool boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class CodePermissionMode(StrEnum):
    ASK_PERMISSIONS = "ask_permissions"
    ACCEPT_EDITS = "accept_edits"
    PLAN_MODE = "plan_mode"
    FULL_AUTONOMY = "full_autonomy"

    @classmethod
    def _missing_(cls, value: object) -> CodePermissionMode | None:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "approval_required":
                return cls.ASK_PERMISSIONS
            if normalized == "code_autonomy":
                return cls.ACCEPT_EDITS
        return None


class CodeAutonomy(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    CODE_AUTONOMY = "code_autonomy"
    PLAN_MODE = "plan_mode"
    FULL_AUTONOMY = "full_autonomy"

    @classmethod
    def _missing_(cls, value: object) -> CodeAutonomy | None:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "ask_permissions":
                return cls.APPROVAL_REQUIRED
            if normalized == "accept_edits":
                return cls.CODE_AUTONOMY
        return None


def normalize_permission_mode(value: str | CodePermissionMode | CodeAutonomy | None) -> CodePermissionMode:
    if isinstance(value, CodePermissionMode):
        return value
    if isinstance(value, CodeAutonomy):
        return CodePermissionMode(value.value)
    if isinstance(value, str) and value.strip():
        try:
            return CodePermissionMode(value.strip())
        except ValueError:
            pass
    return CodePermissionMode.ASK_PERMISSIONS


def normalize_code_autonomy(value: str | CodePermissionMode | CodeAutonomy | None) -> CodeAutonomy:
    if isinstance(value, CodeAutonomy):
        return value
    if isinstance(value, CodePermissionMode):
        return CodeAutonomy(value.value)
    if isinstance(value, str) and value.strip():
        try:
            return CodeAutonomy(value.strip())
        except ValueError:
            pass
    return CodeAutonomy.APPROVAL_REQUIRED


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
    def permission_mode(self) -> CodePermissionMode:
        return CodePermissionMode(self.autonomy.value)

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
    r"build|deploy|commit|pull request|pr|git\s+push)\b",
    re.IGNORECASE,
)
_REVIEW_PATTERN = re.compile(r"\b(revise|review|audite|audit)\b", re.IGNORECASE)
_INVESTIGATION_PATTERN = re.compile(r"\b(investigue|diagnostique|descubra\s+por que|why .*fail)\b", re.IGNORECASE)
_REFACTOR_PATTERN = re.compile(r"\b(refator\w*|refactor\w*)", re.IGNORECASE)
_BUGFIX_PATTERN = re.compile(r"\b(corrija|conserte|bug|erro|falha|fix)\b", re.IGNORECASE)
_PUSH_NEGATION_PATTERN = re.compile(r"\b(?:n[aã]o|sem|bloque(?:ie|ar))\b.{0,48}\bgit\s+push\b|\bgit\s+push\b.{0,48}\b(?:n[aã]o|sem|bloque(?:ie|ar))\b", re.IGNORECASE)
_PUSH_AUTHORIZATION_PATTERN = re.compile(
    r"\b(?:pode|autoriz(?:o|e|ado|ada|ar)|permit(?:o|a|ido|ida|ir)|liber(?:o|e|ado|ada|ar)|"
    r"fa[çc]a|execute|rode|mande|realize|prossiga|continue|can|may|please)\b.{0,80}\bgit\s+push\b|"
    r"\bgit\s+push\b.{0,80}\b(?:autoriz(?:ado|ada)|permit(?:ido|ida)|liber(?:ado|ada)|pode)\b",
    re.IGNORECASE,
)


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


def explicitly_authorizes_git_push(message: str) -> bool:
    """Return true only for a direct, affirmative authorization to push.

    This is intentionally narrower than generic Code autonomy: a person can
    authorize the concrete ``git push`` they named without also authorizing a
    pull request, deploy, or release.
    """
    if not isinstance(message, str) or not re.search(r"\bgit\s+push\b", message, re.IGNORECASE):
        return False
    if _PUSH_NEGATION_PATTERN.search(message):
        return False
    return _PUSH_AUTHORIZATION_PATTERN.search(message) is not None
