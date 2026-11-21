"""WS-A4 tests: resolve_findings tool availability, validation, and state recording."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.tools.public_names import internal_name, public_name
from agentos.agentic.verification_findings import FindingResolution, validate_report
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.permissions import READ_ONLY_TOOLS as CODE_READ_ONLY_TOOLS
from agentos.permissions.engine import READ_ONLY_TOOLS as ENGINE_READ_ONLY_TOOLS


def _toolset(tmp_path: Path, **kwargs: Any) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_resolve_findings"), **kwargs)


def _proven_report() -> Any:
    return validate_report({
        "verdict": "fail",
        "findings": [
            {
                "id": "F1",
                "severity": "blocker",
                "category": "bug",
                "claim": "Login crashes on empty email",
                "evidence": {"kind": "failing_test", "command": "pytest -q", "exit_code": 1},
            },
            {
                "id": "F2",
                "severity": "major",
                "category": "security",
                "claim": "Missing CSRF check",
                "evidence": {"kind": "code_ref", "file": "src/auth.py", "line": 42, "reason": "No token validation"},
            },
            {
                "id": "F3",
                "severity": "blocker",
                "category": "bug",
                "claim": "Unproven guess without evidence",
            },
            {
                "id": "F4",
                "severity": "minor",
                "category": "consistency",
                "claim": "Minor naming nit",
                "evidence": {"kind": "code_ref", "file": "src/auth.py", "line": 10, "reason": "CamelCase"},
            },
        ],
    })


def test_resolve_findings_hidden_by_default_and_for_unproven_only_reports(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    names = {item.name for item in toolset.definitions()}
    assert "resolve_findings" not in names

    # Criterion 4: blocker without evidence becomes unproven -> does NOT unlock resolve_findings
    unproven_only = validate_report({
        "verdict": "fail",
        "findings": [
            {"id": "F1", "severity": "blocker", "category": "bug", "claim": "No evidence provided"},
            {
                "id": "F2",
                "severity": "minor",
                "category": "consistency",
                "claim": "Minor style issue",
                "evidence": {"kind": "code_ref", "file": "a.py", "line": 1, "reason": "style"},
            },
        ],
    })
    toolset.set_open_verification_findings(unproven_only)
    assert "resolve_findings" not in {item.name for item in toolset.definitions()}


def test_resolve_findings_appears_when_blocking_findings_are_open_and_updates_snapshot(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    executor = SimpleNamespace(runtime=SimpleNamespace(toolset=toolset))
    before = RuntimeToolExecutor._snapshot_discovered(executor)

    toolset.set_open_verification_findings(_proven_report())
    names = {item.name for item in toolset.definitions()}
    assert "resolve_findings" in names
    after = RuntimeToolExecutor._snapshot_discovered(executor)
    assert before != after

    toolset.clear_open_verification_findings()
    assert "resolve_findings" not in {item.name for item in toolset.definitions()}
    assert RuntimeToolExecutor._snapshot_discovered(executor) == before


def test_resolve_findings_validates_ids_statuses_and_rejected_reason(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    toolset.set_open_verification_findings(_proven_report())

    # Empty items -> fail
    empty = toolset.invoke("resolve_findings", {"items": []})
    assert empty.status == "failed"

    # Nonexistent id (including F3 which was unproven and not blocking, or F99) -> fail
    unknown = toolset.invoke("resolve_findings", {"items": [{"id": "F99", "status": "confirmed"}]})
    assert unknown.status == "failed"
    assert "F99" in unknown.content

    unproven_id = toolset.invoke("resolve_findings", {"items": [{"id": "F3", "status": "confirmed"}]})
    assert unproven_id.status == "failed"

    # Invalid status -> fail
    bad_status = toolset.invoke("resolve_findings", {"items": [{"id": "F1", "status": "ignored"}]})
    assert bad_status.status == "failed"

    # Rejected without reason -> fail
    no_reason = toolset.invoke("resolve_findings", {"items": [{"id": "F2", "status": "rejected"}]})
    assert no_reason.status == "failed"
    assert "reason" in no_reason.content.lower()

    blank_reason = toolset.invoke("resolve_findings", {"items": [{"id": "F2", "status": "rejected", "reason": "   "}]})
    assert blank_reason.status == "failed"


def test_resolve_findings_records_resolutions_and_invokes_callback(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    callback_calls: list[tuple[FindingResolution, ...]] = []
    toolset.set_open_verification_findings(_proven_report(), on_resolve=callback_calls.append)

    # Partial resolution
    first = toolset.invoke("resolve_findings", {
        "items": [{"id": "F1", "status": "fixed", "reason": "Added empty-email guard"}],
    })
    assert first.status == "succeeded"
    assert first.payload["all_resolved"] is False
    assert first.payload["unresolved_ids"] == ["F2"]

    # Resolve second via public name
    second = toolset.invoke(public_name("resolve_findings"), {
        "items": [{"id": "F2", "status": "rejected", "reason": "Public read-only endpoint does not mutate state"}],
    })
    assert second.status == "succeeded"
    assert second.payload["all_resolved"] is True
    assert second.payload["unresolved_ids"] == []

    resolutions = toolset.verification_resolutions()
    assert set(resolutions) == {"F1", "F2"}
    assert resolutions["F1"] == FindingResolution(id="F1", status="fixed", reason="Added empty-email guard")
    assert resolutions["F2"] == FindingResolution(
        id="F2", status="rejected", reason="Public read-only endpoint does not mutate state"
    )
    assert len(callback_calls) == 2


def test_resolve_findings_public_name_permissions_and_cli_schema(tmp_path: Path) -> None:
    pub = public_name("resolve_findings")
    assert pub == "ResolveFindings"
    assert internal_name(pub) == "resolve_findings"
    assert "resolve_findings" in ENGINE_READ_ONLY_TOOLS
    assert "resolve_findings" in CODE_READ_ONLY_TOOLS

    toolset = _toolset(tmp_path)
    toolset.set_open_verification_findings(_proven_report())
    defn = toolset.resolve("resolve_findings")
    schema = dict(defn.parameters)
    text = repr(schema)
    for forbidden in ("'$ref'", "'$defs'", "'title'", "'oneOf'", "'anyOf'", "'allOf'"):
        assert forbidden not in text
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]
    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["type"] == "object"
    assert item_schema["additionalProperties"] is False
    assert item_schema["required"] == ["id", "status"]
