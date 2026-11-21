"""Structured verification findings with mandatory evidence (0.13 WS-A4, Spec 5.5).

Every finding produced by a verifier must carry concrete evidence:
- ``failing_test`` or ``command`` requires a non-empty ``command`` and an integer ``exit_code``.
- ``code_ref`` requires a non-empty ``file``, an integer ``line >= 1``, and a non-empty ``reason``.

Findings without valid evidence are automatically downgraded to ``severity = "unproven"``
(preserving ``original_severity``) and never block or require ``resolve_findings``.
A ``pass`` verdict with any proven ``blocker`` finding is rejected.
Legacy ``{passed: bool, findings: str}`` reports are accepted and normalized cleanly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Verdict = Literal["pass", "fail", "inconclusive"]

VALID_VERDICTS: frozenset[str] = frozenset({"pass", "fail", "inconclusive"})
VALID_SEVERITIES: frozenset[str] = frozenset({"blocker", "major", "minor", "nit", "unproven"})
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"blocker", "major"})
VALID_CATEGORIES: frozenset[str] = frozenset({
    "bug",
    "regression",
    "security",
    "requirement",
    "test",
    "performance",
    "consistency",
})
VALID_EVIDENCE_KINDS: frozenset[str] = frozenset({"failing_test", "command", "code_ref"})
VALID_RESOLUTION_STATUSES: frozenset[str] = frozenset({"confirmed", "rejected", "fixed"})

MAX_SUMMARY_CHARS = 2000


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_str(value: Any, *, max_chars: int = 2000) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_chars]


@dataclass(frozen=True, slots=True)
class CheckedCommand:
    command: str
    exit_code: int
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    command: str | None = None
    exit_code: int | None = None
    output_excerpt: str | None = None
    file: str | None = None
    line: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.command is not None:
            data["command"] = self.command
        if self.exit_code is not None:
            data["exit_code"] = self.exit_code
        if self.output_excerpt is not None:
            data["output_excerpt"] = self.output_excerpt
        if self.file is not None:
            data["file"] = self.file
        if self.line is not None:
            data["line"] = self.line
        if self.reason is not None:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    severity: str
    original_severity: str
    category: str
    claim: str
    evidence: Evidence | None
    has_evidence: bool
    suggested_fix: str | None = None

    @property
    def is_blocking(self) -> bool:
        return self.has_evidence and self.severity in BLOCKING_SEVERITIES

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity,
            "original_severity": self.original_severity,
            "category": self.category,
            "claim": self.claim,
            "has_evidence": self.has_evidence,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }
        if self.suggested_fix is not None:
            data["suggested_fix"] = self.suggested_fix
        return data


@dataclass(frozen=True, slots=True)
class FindingResolution:
    id: str
    status: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "status": self.status}
        if self.reason:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True, slots=True)
class NormalizedReport:
    verdict: str
    passed: bool
    checked: tuple[CheckedCommand, ...]
    findings: tuple[Finding, ...]
    unverified: tuple[str, ...]
    summary_text: str

    @property
    def blocking_findings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.is_blocking)

    @property
    def requires_resolution(self) -> bool:
        return bool(self.blocking_findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "checked": [item.to_dict() for item in self.checked],
            "findings": [item.to_dict() for item in self.findings],
            "blocking_findings": [item.to_dict() for item in self.blocking_findings],
            "unverified": list(self.unverified),
            "summary_text": self.summary_text,
        }


def validate_evidence(raw: Any) -> tuple[Evidence | None, bool]:
    """Validate one evidence block according to Spec 5.5.

    Returns ``(evidence, True)`` when all required fields for the kind are present and valid;
    otherwise ``(None, False)``.
    """
    if not isinstance(raw, Mapping):
        return None, False
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VALID_EVIDENCE_KINDS:
        return None, False

    command = _non_empty_str(raw.get("command"), max_chars=1000)
    raw_exit = raw.get("exit_code")
    exit_code = raw_exit if _is_strict_int(raw_exit) else None
    output_excerpt = _non_empty_str(raw.get("output_excerpt"), max_chars=2000)
    file_path = _non_empty_str(raw.get("file"), max_chars=500)
    raw_line = raw.get("line")
    line = raw_line if (_is_strict_int(raw_line) and raw_line >= 1) else None
    reason = _non_empty_str(raw.get("reason"), max_chars=1000)

    if kind in {"failing_test", "command"}:
        if command is None or exit_code is None:
            return None, False
    elif kind == "code_ref":
        if file_path is None or line is None or reason is None:
            return None, False

    evidence = Evidence(
        kind=kind,
        command=command,
        exit_code=exit_code,
        output_excerpt=output_excerpt,
        file=file_path,
        line=line,
        reason=reason,
    )
    return evidence, True


def _parse_checked(raw: Any) -> tuple[CheckedCommand, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("'checked' must be a list of command objects")
    items: list[CheckedCommand] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("each entry in 'checked' must be an object with 'command' and 'exit_code'")
        command = _non_empty_str(entry.get("command"), max_chars=1000)
        exit_code = entry.get("exit_code")
        if command is None or not _is_strict_int(exit_code):
            raise ValueError("each entry in 'checked' requires non-empty 'command' and integer 'exit_code'")
        summary = _non_empty_str(entry.get("summary"), max_chars=500) or ""
        items.append(CheckedCommand(command=command, exit_code=exit_code, summary=summary))
    return tuple(items)


def _parse_unverified(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("'unverified' must be a list of strings")
    items: list[str] = []
    for entry in raw:
        text = _non_empty_str(entry, max_chars=1000)
        if text is not None:
            items.append(text)
    return tuple(items)


def _parse_structured_findings(raw: Sequence[Any]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    used_ids: set[str] = set()
    auto_seq = 1
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("each item in 'findings' must be an object")
        explicit_id = _non_empty_str(entry.get("id"), max_chars=64)
        if explicit_id is not None:
            if explicit_id in used_ids:
                raise ValueError(f"duplicate finding id '{explicit_id}'")
            finding_id = explicit_id
        else:
            while f"F{auto_seq}" in used_ids:
                auto_seq += 1
            finding_id = f"F{auto_seq}"
            auto_seq += 1
        used_ids.add(finding_id)

        claim = (
            _non_empty_str(entry.get("claim"), max_chars=2000)
            or _non_empty_str(entry.get("description"), max_chars=2000)
            or _non_empty_str(entry.get("message"), max_chars=2000)
        )
        if claim is None:
            raise ValueError(f"finding '{finding_id}' requires a non-empty 'claim'")

        raw_severity = str(entry.get("severity") or "minor").strip().lower()
        original_severity = raw_severity if raw_severity in VALID_SEVERITIES else "unproven"
        raw_category = str(entry.get("category") or "bug").strip().lower()
        category = raw_category if raw_category in VALID_CATEGORIES else "bug"

        evidence, has_evidence = validate_evidence(entry.get("evidence"))
        severity = original_severity if has_evidence else "unproven"
        suggested_fix = _non_empty_str(entry.get("suggested_fix"), max_chars=1000)

        findings.append(
            Finding(
                id=finding_id,
                severity=severity,
                original_severity=original_severity,
                category=category,
                claim=claim,
                evidence=evidence,
                has_evidence=has_evidence,
                suggested_fix=suggested_fix,
            )
        )
    return tuple(findings)


def _render_summary(
    *,
    explicit_summary: str | None,
    findings: tuple[Finding, ...],
    checked: tuple[CheckedCommand, ...],
    unverified: tuple[str, ...],
) -> str:
    if explicit_summary:
        return explicit_summary[:MAX_SUMMARY_CHARS]
    lines: list[str] = []
    for item in findings:
        proof = ""
        if item.evidence is not None:
            if item.evidence.kind == "code_ref":
                proof = f" ({item.evidence.file}:{item.evidence.line})"
            elif item.evidence.command is not None:
                proof = f" (`{item.evidence.command}` exit={item.evidence.exit_code})"
        lines.append(f"[{item.id}] ({item.severity}/{item.category}) {item.claim}{proof}")
    if not lines and checked:
        for cmd in checked:
            tail = f" — {cmd.summary}" if cmd.summary else ""
            lines.append(f"`{cmd.command}` (exit={cmd.exit_code}){tail}")
    if unverified:
        lines.append("Não verificado: " + "; ".join(unverified))
    return "\n".join(lines)[:MAX_SUMMARY_CHARS]


def validate_report(payload: Mapping[str, Any]) -> NormalizedReport:
    """Normalize and validate a structured or legacy verification report."""
    if not isinstance(payload, Mapping):
        raise ValueError("verification report payload must be an object")

    raw_verdict = payload.get("verdict")
    raw_passed = payload.get("passed")
    if raw_verdict is not None and str(raw_verdict).strip() != "":
        verdict = str(raw_verdict).strip().lower()
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"invalid verdict '{verdict}'; expected one of: pass, fail, inconclusive")
        passed = verdict == "pass"
    elif raw_passed is not None:
        passed = bool(raw_passed)
        verdict = "pass" if passed else "fail"
    else:
        raise ValueError("verification report requires 'verdict' ('pass' | 'fail' | 'inconclusive') or 'passed' (boolean)")

    checked = _parse_checked(payload.get("checked"))
    unverified = _parse_unverified(payload.get("unverified"))
    explicit_summary = _non_empty_str(payload.get("summary"), max_chars=MAX_SUMMARY_CHARS)

    raw_findings = payload.get("findings")
    findings: tuple[Finding, ...]
    if raw_findings is None or raw_findings == "" or raw_findings == []:
        findings = ()
    elif isinstance(raw_findings, str):
        legacy_text = raw_findings.strip()[:MAX_SUMMARY_CHARS]
        if explicit_summary is None and legacy_text:
            explicit_summary = legacy_text
        if legacy_text and verdict != "pass":
            findings = (
                Finding(
                    id="F1",
                    severity="unproven",
                    original_severity="unproven",
                    category="bug",
                    claim=legacy_text,
                    evidence=None,
                    has_evidence=False,
                    suggested_fix=None,
                ),
            )
        else:
            findings = ()
    elif isinstance(raw_findings, (list, tuple)):
        findings = _parse_structured_findings(raw_findings)
    else:
        raise ValueError("'findings' must be a list of finding objects or a legacy text string")

    if verdict == "pass" and any(item.has_evidence and item.severity == "blocker" for item in findings):
        raise ValueError("verdict 'pass' is incompatible with a proven blocker finding")

    summary_text = _render_summary(
        explicit_summary=explicit_summary,
        findings=findings,
        checked=checked,
        unverified=unverified,
    )
    return NormalizedReport(
        verdict=verdict,
        passed=passed,
        checked=checked,
        findings=findings,
        unverified=unverified,
        summary_text=summary_text,
    )


def validate_resolutions(
    items: Any,
    *,
    open_finding_ids: frozenset[str] | set[str],
) -> tuple[FindingResolution, ...]:
    """Validate ``resolve_findings`` items against the set of open blocking finding ids."""
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError("'items' must be a non-empty list of finding resolutions")
    if not open_finding_ids:
        raise ValueError("there are no open blocking verification findings to resolve")

    resolutions: list[FindingResolution] = []
    seen_ids: set[str] = set()
    for entry in items:
        if not isinstance(entry, Mapping):
            raise ValueError("each resolution item must be an object with 'id' and 'status'")
        finding_id = _non_empty_str(entry.get("id"), max_chars=64)
        if finding_id is None:
            raise ValueError("each resolution item requires a non-empty 'id'")
        if finding_id not in open_finding_ids:
            known = ", ".join(sorted(open_finding_ids))
            raise ValueError(f"unknown or non-blocking finding id '{finding_id}'; open blocking ids: {known}")
        if finding_id in seen_ids:
            raise ValueError(f"duplicate resolution for finding id '{finding_id}'")
        seen_ids.add(finding_id)

        status = str(entry.get("status") or "").strip().lower()
        if status not in VALID_RESOLUTION_STATUSES:
            raise ValueError(f"invalid status '{status}' for finding '{finding_id}'; expected confirmed, rejected, or fixed")

        reason = _non_empty_str(entry.get("reason"), max_chars=1000) or ""
        if status == "rejected" and not reason:
            raise ValueError(f"finding '{finding_id}' with status 'rejected' requires a non-empty 'reason'")

        resolutions.append(FindingResolution(id=finding_id, status=status, reason=reason))
    return tuple(resolutions)


__all__ = [
    "BLOCKING_SEVERITIES",
    "CheckedCommand",
    "Evidence",
    "Finding",
    "FindingResolution",
    "NormalizedReport",
    "VALID_CATEGORIES",
    "VALID_EVIDENCE_KINDS",
    "VALID_RESOLUTION_STATUSES",
    "VALID_SEVERITIES",
    "VALID_VERDICTS",
    "Verdict",
    "validate_evidence",
    "validate_report",
    "validate_resolutions",
]
