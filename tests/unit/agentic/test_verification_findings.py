"""WS-A4 tests: verification findings schema, proof validation, and extended report_verification."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.verification_findings import (
    CheckedCommand,
    Evidence,
    Finding,
    FindingResolution,
    NormalizedReport,
    validate_report,
)
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path, **kwargs: Any) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_verify_findings"), **kwargs)


def _assert_cli_compatible_schema(schema: dict[str, Any]) -> None:
    """Ensure schema has no constructs rejected by Claude CLI, Codex CLI, or Antigravity/Gemini CLI."""
    text = repr(schema)
    for forbidden in ("'$ref'", "'$defs'", "'title'", "'oneOf'", "'anyOf'", "'allOf'"):
        assert forbidden not in text, f"schema contains {forbidden}: {schema}"

    def _walk(node: Any, path: str = "$") -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type is not None:
                assert isinstance(node_type, str), f"{path}: union type list not allowed: {node_type}"
            if node_type == "object":
                assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be False"
                assert isinstance(node.get("properties"), dict), f"{path}: object must declare properties"
                assert isinstance(node.get("required"), list), f"{path}: object must declare required list"
                for prop_name, prop_schema in node["properties"].items():
                    _walk(prop_schema, f"{path}.{prop_name}")
            elif node_type == "array":
                assert isinstance(node.get("items"), dict), f"{path}: array must declare items"
                assert isinstance(node["items"].get("type"), str), f"{path}: array items must declare concrete type"
                _walk(node["items"], f"{path}[]")

    assert schema.get("type") == "object"
    _walk(schema)


# -- Criterion 4 & evidence rules --------------------------------------


def test_blocker_without_evidence_becomes_unproven_and_is_not_blocking() -> None:
    report = validate_report({
        "verdict": "fail",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "bug",
                "claim": "Null pointer dereference on submit",
            }
        ],
    })
    assert isinstance(report, NormalizedReport)
    assert report.verdict == "fail"
    assert report.passed is False
    assert len(report.findings) == 1
    f1 = report.findings[0]
    assert f1.id == "F1"
    assert f1.severity == "unproven"
    assert f1.original_severity == "blocker"
    assert f1.has_evidence is False
    assert f1.evidence is None
    assert report.blocking_findings == ()
    assert report.requires_resolution is False


@pytest.mark.parametrize("kind", ["command", "failing_test"])
@pytest.mark.parametrize(
    "bad_evidence",
    [
        {"command": "pytest -q"},  # missing exit_code
        {"command": "pytest -q", "exit_code": True},  # bool is not a valid int exit_code
        {"command": "pytest -q", "exit_code": "1"},  # str is not int
        {"command": "   ", "exit_code": 1},  # empty command
        {"exit_code": 1},  # missing command
    ],
)
def test_command_or_failing_test_without_valid_fields_becomes_unproven(
    kind: str, bad_evidence: dict[str, Any]
) -> None:
    report = validate_report({
        "verdict": "fail",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "test",
                "claim": "Test suite fails",
                "evidence": {"kind": kind, **bad_evidence},
            }
        ],
    })
    finding = report.findings[0]
    assert finding.severity == "unproven"
    assert finding.original_severity == "blocker"
    assert finding.has_evidence is False
    assert report.blocking_findings == ()


@pytest.mark.parametrize(
    "bad_evidence",
    [
        {"file": "src/app.py", "line": 12},  # missing reason
        {"file": "src/app.py", "reason": "wrong condition"},  # missing line
        {"file": "src/app.py", "line": 0, "reason": "line < 1"},
        {"file": "src/app.py", "line": True, "reason": "bool line"},
        {"line": 10, "reason": "missing file"},
        {"file": "   ", "line": 10, "reason": "blank file"},
    ],
)
def test_code_ref_without_valid_fields_becomes_unproven(bad_evidence: dict[str, Any]) -> None:
    report = validate_report({
        "verdict": "fail",
        "findings": [
            {
                "id": "F2",
                "severity": "major",
                "category": "security",
                "claim": "SQL built by string concat",
                "evidence": {"kind": "code_ref", **bad_evidence},
            }
        ],
    })
    finding = report.findings[0]
    assert finding.severity == "unproven"
    assert finding.original_severity == "major"
    assert finding.has_evidence is False
    assert report.blocking_findings == ()


def test_proven_findings_keep_severity_and_only_blocker_and_major_block() -> None:
    report = validate_report({
        "verdict": "fail",
        "checked": [{"command": "pytest -q", "exit_code": 1, "summary": "1 failed"}],
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "test",
                "claim": "test_login fails",
                "evidence": {
                    "kind": "failing_test",
                    "command": "pytest tests/test_auth.py::test_login -q",
                    "exit_code": 1,
                    "output_excerpt": "AssertionError: expected 200 got 500",
                },
                "suggested_fix": "Handle empty token",
            },
            {
                "id": "F2",
                "severity": "major",
                "category": "security",
                "claim": "Missing auth check",
                "evidence": {
                    "kind": "code_ref",
                    "file": "src/routes.py",
                    "line": 44,
                    "reason": "Endpoint skips require_user",
                },
            },
            {
                "id": "F3",
                "severity": "minor",
                "category": "consistency",
                "claim": "Inconsistent log level",
                "evidence": {
                    "kind": "code_ref",
                    "file": "src/routes.py",
                    "line": 80,
                    "reason": "Uses print instead of logger",
                },
            },
            {
                "id": "F4",
                "severity": "nit",
                "category": "consistency",
                "claim": "Ruff warning",
                "evidence": {"kind": "command", "command": "ruff check .", "exit_code": 1},
            },
        ],
        "unverified": ["Browser E2E skipped (no server)"],
    })
    assert report.verdict == "fail"
    assert report.passed is False
    assert len(report.checked) == 1
    assert report.checked[0] == CheckedCommand(command="pytest -q", exit_code=1, summary="1 failed")
    assert [f.id for f in report.blocking_findings] == ["F1", "F2"]
    assert report.requires_resolution is True
    assert report.findings[0].evidence == Evidence(
        kind="failing_test",
        command="pytest tests/test_auth.py::test_login -q",
        exit_code=1,
        output_excerpt="AssertionError: expected 200 got 500",
    )
    assert report.unverified == ("Browser E2E skipped (no server)",)
    serialized = report.to_dict()
    assert serialized["verdict"] == "fail"
    assert len(serialized["findings"]) == 4
    assert len(serialized["blocking_findings"]) == 2


def test_pass_verdict_rejects_proven_blocker() -> None:
    with pytest.raises(ValueError, match="pass.*blocker|blocker.*pass"):
        validate_report({
            "verdict": "pass",
            "findings": [
                {
                    "id": "F1",
                    "severity": "blocker",
                    "category": "bug",
                    "claim": "Crash on startup",
                    "evidence": {"kind": "command", "command": "python -m app", "exit_code": 1},
                }
            ],
        })


def test_pass_verdict_allows_unproven_blocker_because_it_downgrades() -> None:
    report = validate_report({
        "verdict": "pass",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "bug",
                "claim": "Suspected race condition without proof",
            }
        ],
    })
    assert report.verdict == "pass"
    assert report.passed is True
    assert report.findings[0].severity == "unproven"
    assert report.blocking_findings == ()


# -- Legacy format compatibility ---------------------------------------


def test_legacy_passed_true_and_false_formats_are_accepted() -> None:
    ok = validate_report({"passed": True, "findings": "build e testes ok"})
    assert ok.verdict == "pass"
    assert ok.passed is True
    assert ok.findings == ()
    assert ok.summary_text == "build e testes ok"

    failed = validate_report({"passed": False, "findings": "TypeError em App.tsx:12"})
    assert failed.verdict == "fail"
    assert failed.passed is False
    assert len(failed.findings) == 1
    assert failed.findings[0].id == "F1"
    assert failed.findings[0].severity == "unproven"
    assert failed.findings[0].has_evidence is False
    assert "TypeError" in failed.findings[0].claim
    assert failed.blocking_findings == ()
    assert failed.summary_text == "TypeError em App.tsx:12"


# -- report_verification tool & runtime integration --------------------


def test_report_verification_tool_structured_and_legacy(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)

    # Structured pass
    ok = toolset.invoke("report_verification", {
        "verdict": "pass",
        "checked": [{"command": "pytest -q", "exit_code": 0, "summary": "10 passed"}],
        "findings": [],
        "unverified": ["Manual UI check"],
    })
    assert ok.status == "succeeded"
    assert ok.payload["passed"] is True
    assert ok.payload["verdict"] == "pass"
    assert isinstance(ok.payload["findings"], str)
    assert ok.payload["report"]["verdict"] == "pass"
    assert ok.payload["checked"][0]["command"] == "pytest -q"

    # Structured pass with proven blocker -> rejected with AgentToolError
    rejected = toolset.invoke("report_verification", {
        "verdict": "pass",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "bug",
                "claim": "Fails on empty input",
                "evidence": {"kind": "failing_test", "command": "pytest -q", "exit_code": 1},
            }
        ],
    })
    assert rejected.status == "failed"
    assert "blocker" in rejected.content.lower()

    # Structured fail with proven blocker + unproven blocker
    failed = toolset.invoke("report_verification", {
        "verdict": "fail",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "bug",
                "claim": "Real failure",
                "evidence": {"kind": "command", "command": "pytest -q", "exit_code": 1},
            },
            {
                "id": "F2",
                "severity": "blocker",
                "category": "bug",
                "claim": "Hallucinated bug",
            },
        ],
    })
    assert failed.status == "succeeded"
    assert failed.payload["passed"] is False
    assert failed.payload["verdict"] == "fail"
    assert isinstance(failed.payload["findings"], str)
    assert "Real failure" in failed.payload["findings"]
    assert len(failed.payload["structured_findings"]) == 2
    assert failed.payload["structured_findings"][1]["severity"] == "unproven"
    assert len(failed.payload["blocking_findings"]) == 1
    assert failed.payload["blocking_findings"][0]["id"] == "F1"


def test_legacy_report_verification_feeds_runtime_absorb_verification(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    seen: list[bool] = []
    runtime = AgenticTurnRuntime(
        provider=MagicMock(),
        store=MagicMock(),
        toolset=toolset,
        on_verification=seen.append,
    )
    outcome_fail = toolset.invoke("report_verification", {"passed": False, "findings": "build quebrado"})
    runtime._absorb_verification("report_verification", outcome_fail)
    assert runtime._last_verification_passed is False
    assert runtime._pending_verification is not None
    assert runtime._pending_verification["passed"] is False
    assert "build quebrado" in str(runtime._pending_verification["findings"])

    outcome_pass = toolset.invoke("report_verification", {"verdict": "pass", "checked": [{"command": "pytest", "exit_code": 0}]})
    runtime._absorb_verification("report_verification", outcome_pass)
    assert runtime._last_verification_passed is True
    assert seen == [False, True]


def test_report_verification_schema_is_cli_compatible(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    defn = toolset.resolve("report_verification")
    _assert_cli_compatible_schema(dict(defn.parameters))
