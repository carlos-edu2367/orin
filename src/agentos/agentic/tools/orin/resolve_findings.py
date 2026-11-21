"""Resolve verification findings tool: implementer confirms, rejects, or fixes proven findings (0.13 WS-A4)."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.agentic.verification_findings import (
    Finding,
    FindingResolution,
    NormalizedReport,
    VALID_RESOLUTION_STATUSES,
    validate_report,
    validate_resolutions,
)


def _extract_blocking_findings(
    findings: NormalizedReport | Sequence[Finding | Mapping[str, Any]],
) -> tuple[Finding, ...]:
    if isinstance(findings, NormalizedReport):
        return findings.blocking_findings
    items = list(findings or ())
    if not items:
        return ()
    if all(isinstance(item, Finding) for item in items):
        return tuple(item for item in items if isinstance(item, Finding) and item.is_blocking)
    raw_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Finding):
            raw_items.append(item.to_dict())
        elif isinstance(item, Mapping):
            raw_items.append(dict(item))
        else:
            raise ValueError("open verification findings must be Finding instances or mappings")
    return validate_report({"verdict": "fail", "findings": raw_items}).blocking_findings


class ResolveFindingsTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def set_open_verification_findings(
        self,
        findings: NormalizedReport | Sequence[Finding | Mapping[str, Any]],
        *,
        on_resolve: Callable[[tuple[FindingResolution, ...]], None] | None = None,
    ) -> tuple[Finding, ...]:
        """Register proven blocker/major findings awaiting resolution by the implementer."""
        blocking = _extract_blocking_findings(findings)
        self._open_verification_findings: dict[str, Finding] = {item.id: item for item in blocking}
        self._verification_resolutions: dict[str, FindingResolution] = {}
        self._on_resolve_findings = on_resolve
        self.refresh_definitions()
        return blocking

    def clear_open_verification_findings(self) -> None:
        """Clear pending verification findings and hide ``resolve_findings`` again."""
        self._open_verification_findings = {}
        self._on_resolve_findings = None
        self.refresh_definitions()

    def open_verification_findings(self) -> tuple[Finding, ...]:
        """Return currently open proven blocking findings."""
        open_map: Mapping[str, Finding] = getattr(self, "_open_verification_findings", None) or {}
        return tuple(open_map.values())

    def verification_resolutions(self) -> dict[str, FindingResolution]:
        """Return recorded resolutions keyed by finding id."""
        recorded: Mapping[str, FindingResolution] = getattr(self, "_verification_resolutions", None) or {}
        return dict(recorded)

    def unresolved_verification_finding_ids(self) -> tuple[str, ...]:
        """Return open blocking finding ids that do not have a resolution yet."""
        open_map: Mapping[str, Finding] = getattr(self, "_open_verification_findings", None) or {}
        recorded: Mapping[str, FindingResolution] = getattr(self, "_verification_resolutions", None) or {}
        return tuple(fid for fid in open_map if fid not in recorded)

    def resolve_findings(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        open_map: dict[str, Finding] = getattr(self, "_open_verification_findings", None) or {}
        try:
            resolutions = validate_resolutions(items, open_finding_ids=frozenset(open_map))
        except ValueError as error:
            raise AgentToolError(str(error), error_code="INVALID_FINDING_RESOLUTION") from error

        recorded: dict[str, FindingResolution] = getattr(self, "_verification_resolutions", None)
        if recorded is None:
            recorded = {}
            self._verification_resolutions = recorded
        for item in resolutions:
            recorded[item.id] = item

        callback = getattr(self, "_on_resolve_findings", None)
        if callable(callback):
            callback(resolutions)

        unresolved_ids = [fid for fid in open_map if fid not in recorded]
        all_resolved = not unresolved_ids
        lines = [
            f"- {item.id}: {item.status}" + (f" ({item.reason})" if item.reason else "")
            for item in resolutions
        ]
        if unresolved_ids:
            lines.append("Pendentes: " + ", ".join(unresolved_ids))
        summary = (
            f"Achados resolvidos ({len(recorded)}/{len(open_map)})"
            if all_resolved
            else f"Achados atualizados ({len(recorded)}/{len(open_map)}; pendentes: {', '.join(unresolved_ids)})"
        )
        return {
            "summary": summary,
            "content": "\n".join(lines),
            "payload": {
                "resolutions": [item.to_dict() for item in resolutions],
                "all_resolutions": {fid: res.to_dict() for fid, res in recorded.items()},
                "unresolved_ids": unresolved_ids,
                "all_resolved": all_resolved,
            },
        }


_RESOLUTION_ITEM_SCHEMA = _schema(
    {
        "id": {**_TEXT, "description": "Open blocking finding id (e.g. 'F1')."},
        "status": {
            "type": "string",
            "enum": sorted(VALID_RESOLUTION_STATUSES),
            "description": "confirmed, rejected (requires non-empty reason), or fixed.",
        },
        "reason": {
            **_TEXT,
            "description": "Short explanation of the fix, confirmation, or why it was rejected (required for 'rejected').",
        },
    },
    ("id", "status"),
)


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="resolve_findings",
        description="Respond to proven blocker/major findings from independent verification: mark each open finding id "
        "as 'confirmed', 'rejected' (requires non-empty 'reason'), or 'fixed'.",
        legacy_schema=_schema(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": _RESOLUTION_ITEM_SCHEMA,
                    "description": "Resolution entries for open proven blocker/major findings.",
                },
            },
            ("items",),
        ),
        handler="resolve_findings",
        kind="planning",
        read_only=True,
        available=lambda self: bool(getattr(self, "_open_verification_findings", None)),
        order=112,
    ),
)


__all__ = ["ResolveFindingsTools", "SPECS"]
