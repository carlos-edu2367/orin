from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.pipelines.approvals import ApprovalStore
from agentos.pipelines.model import PipelineDefinition
from agentos.pipelines.validator import validate_structure

USER = "user-1"


def make(source: str, command: str = "pytest -q ${{ inputs.arquivo }}", pipeline_id: str = "meu") -> PipelineDefinition:
    text = (
        f"version: 1\nid: {pipeline_id}\nname: N\nbudget: {{max_tokens: 1000}}\n"
        f"inputs:\n  arquivo: {{type: string}}\n  testes: {{type: string, default: pytest}}\n"
        f"steps:\n  - id: verificar\n    gate:\n      all:\n        - command: {command}\n"
    )
    definition, report = validate_structure(text, source)
    assert definition is not None, report.errors
    return definition


@pytest.fixture
def store(tmp_path: Path) -> ApprovalStore:
    return ApprovalStore(tmp_path / "pipeline-approvals.json")


def test_project_pipeline_needs_approval_by_hash(store: ApprovalStore, tmp_path: Path) -> None:
    definition = make("project")
    assert not store.is_approved(USER, tmp_path, definition)
    store.approve(USER, tmp_path, definition)
    assert store.is_approved(USER, tmp_path, definition)


def test_one_changed_byte_requires_a_new_approval(store: ApprovalStore, tmp_path: Path) -> None:
    store.approve(USER, tmp_path, make("project"))
    edited = make("project", command="pytest -x ${{ inputs.arquivo }}")
    assert edited.definition_hash != make("project").definition_hash
    assert not store.is_approved(USER, tmp_path, edited)
    store.approve(USER, tmp_path, edited)
    assert store.is_approved(USER, tmp_path, edited)
    assert not store.is_approved(USER, tmp_path, make("project"))  # the old hash is replaced, not accumulated


def test_approval_is_per_workspace_user_and_pipeline(store: ApprovalStore, tmp_path: Path) -> None:
    definition = make("project")
    store.approve(USER, tmp_path / "a", definition)
    assert not store.is_approved(USER, tmp_path / "b", definition)
    assert not store.is_approved("user-2", tmp_path / "a", definition)
    assert not store.is_approved(USER, tmp_path / "a", make("project", pipeline_id="outro"))


def test_user_and_builtin_do_not_need_approval_when_the_user_starts(store: ApprovalStore, tmp_path: Path) -> None:
    assert store.is_approved(USER, tmp_path, make("user"))
    assert store.is_approved(USER, tmp_path, make("builtin"))
    assert store.is_approved(USER, None, make("user"))


def test_when_the_agent_starts_only_builtin_is_free(store: ApprovalStore, tmp_path: Path) -> None:
    assert store.is_approved(USER, tmp_path, make("builtin"), for_agent=True)
    assert not store.is_approved(USER, tmp_path, make("user"), for_agent=True)
    assert not store.is_approved(USER, tmp_path, make("project"), for_agent=True)
    store.approve(USER, tmp_path, make("user"))
    assert store.is_approved(USER, tmp_path, make("user"), for_agent=True)
    assert store.is_approved(USER, tmp_path / "elsewhere", make("user"), for_agent=True)  # user level has no workspace


def test_user_level_approval_is_invalidated_by_a_changed_file(store: ApprovalStore, tmp_path: Path) -> None:
    store.approve(USER, tmp_path, make("user"))
    assert not store.is_approved(USER, tmp_path, make("user", command="pytest; echo x"), for_agent=True)


def test_revoke(store: ApprovalStore, tmp_path: Path) -> None:
    definition = make("project")
    store.approve(USER, tmp_path, definition)
    store.revoke(USER, tmp_path, definition)
    assert not store.is_approved(USER, tmp_path, definition)
    store.revoke(USER, tmp_path, definition)  # idempotent


def test_corrupt_file_means_no_approval_and_is_replaced_on_the_next_one(store: ApprovalStore, tmp_path: Path) -> None:
    definition = make("project")
    store.path.write_text("{ not json", encoding="utf-8")
    assert not store.is_approved(USER, tmp_path, definition)
    store.approve(USER, tmp_path, definition)
    assert store.is_approved(USER, tmp_path, definition)
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 1


def test_file_shape_and_atomic_write(store: ApprovalStore, tmp_path: Path) -> None:
    definition = make("project")
    store.approve(USER, tmp_path, definition)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    (entry,) = data["users"][USER]
    assert entry["pipeline_id"] == "meu" and entry["definition_hash"] == definition.definition_hash
    assert entry["root_key"] and entry["approved_at"]
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]  # no temporary files left


def test_default_path_sits_next_to_trusted_workspaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "orin"))
    assert ApprovalStore().path == tmp_path / "orin" / "pipeline-approvals.json"


def test_commands_for_review_lists_raw_commands_and_quoted_inputs() -> None:
    definition = make("project")
    review = ApprovalStore.commands_for_review(definition)
    (item,) = review.commands
    assert item.step_id == "verificar"
    assert item.command == "pytest -q ${{ inputs.arquivo }}"
    assert item.expect == "zero" and item.timeout_seconds == 600
    assert item.inputs == ("arquivo",) and item.whole_value is False
    assert "entre aspas" in review.notice
    assert "arquivo" in review.notice


def test_commands_for_review_flags_a_whole_command_input() -> None:
    review = ApprovalStore.commands_for_review(make("project", command="${{ inputs.testes }}"))
    (item,) = review.commands
    assert item.whole_value is True and item.inputs == ("testes",)
    assert "comando inteiro" in review.notice


def test_commands_for_review_without_commands() -> None:
    text = "version: 1\nid: x\nname: N\nbudget: {max_tokens: 5}\nsteps:\n  - id: a\n    agent: general\n    prompt: x\n"
    definition, _ = validate_structure(text, "project")
    assert definition is not None
    review = ApprovalStore.commands_for_review(definition)
    assert review.commands == () and "nenhum comando" in review.notice.lower()
