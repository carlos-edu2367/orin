from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.session import build_retrieval_for_turn


def test_a_managed_workspace_gets_no_retrieval_service(tmp_path: Path) -> None:
    assert build_retrieval_for_turn(workspace_id="workspace:p", local_root=None, data_root=tmp_path) is None


def test_a_bound_local_folder_gets_a_service_and_a_started_worker(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    bundle = build_retrieval_for_turn(workspace_id="workspace:p", local_root=str(project), data_root=tmp_path)

    assert bundle is not None
    assert bundle.worker.wait_idle(timeout=10.0)
    assert bundle.service.status().files >= 1
    bundle.worker.stop()


def test_a_broken_configuration_never_raises_into_the_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("agentos.agentic.session.retrieval_service_for", explode)

    assert build_retrieval_for_turn(workspace_id="workspace:p", local_root=str(tmp_path), data_root=tmp_path) is None
