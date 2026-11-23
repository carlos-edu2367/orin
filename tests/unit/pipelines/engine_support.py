"""Shared harness of the engine tests: real ``PipelineStore`` on SQLite, fakes for every port, no real sleeping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine, sqlite_url
from agentos.pipelines.engine import PipelineEngine, RunOutcome
from agentos.pipelines.fakes import (
    FakeChangeScope,
    FakeClock,
    FakeGateRunner,
    FakeLauncher,
    FakeQuota,
    FakeVerifier,
    RecordingSink,
)
from agentos.pipelines.ports import EngineOptions
from agentos.pipelines.store import PipelineStore
from agentos.pipelines.validator import validate_structure

from .helpers import fixture_text


@dataclass
class Harness:
    definition: Any
    store: PipelineStore
    sqlite: Any
    workspace: Path
    clock: FakeClock
    launcher: FakeLauncher
    gates: FakeGateRunner
    verifier: FakeVerifier
    quota: FakeQuota
    scope: FakeChangeScope
    sink: RecordingSink
    run_id: str = "run-1"
    inputs: dict[str, Any] = field(default_factory=dict)
    engines: list[PipelineEngine] = field(default_factory=list)

    def engine(self, *, resume: bool = False, sink: RecordingSink | None = None, **options: Any) -> PipelineEngine:
        base = dict(poll_seconds=0.05, monitor_seconds=0.02, backoff_base_seconds=5.0)
        base.update(options)
        built = PipelineEngine(
            self.definition,
            self.run_id,
            store=self.store,
            launcher=self.launcher,
            gate_runner=self.gates,
            sink=sink or self.sink,
            clock=self.clock,
            inputs=self.inputs,
            verifier=self.verifier,
            change_scope=self.scope,
            quota=self.quota,
            options=EngineOptions(**base),
            resume=resume,
        )
        self.engines.append(built)
        return built

    def run(self, **options: Any) -> RunOutcome:
        return self.engine(**options).execute()

    def resume(self, **options: Any) -> RunOutcome:
        self.store.interrupt_orphans(self.run_id)
        return self.engine(resume=True, **options).execute()

    def rows(self) -> list[Any]:
        return self.store.steps_of(self.run_id)

    def state(self) -> str:
        return self.store.get_run(self.run_id).state


def definition_of(text: str, source: str = "user"):
    definition, report = validate_structure(text, source)
    assert definition is not None, report.errors
    return definition


def make_harness(
    tmp_path: Path,
    text: str | None = None,
    *,
    inputs: dict[str, Any] | None = None,
    run_id: str = "run-1",
    advance_on_wait: bool = False,
) -> Harness:
    tmp_path.mkdir(parents=True, exist_ok=True)
    sqlite = create_local_engine(sqlite_url(tmp_path / "orin.db"))
    metadata.create_all(sqlite)
    store = PipelineStore(sqlite)
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    definition = definition_of(text if text is not None else fixture_text("implementar-verificar.yaml"))
    values = dict(inputs or {})
    for spec in definition.inputs:
        values.setdefault(spec.name, spec.default if spec.default is not None else ("x" if spec.required else None))
    store.create_run(
        run_id=run_id,
        user_id="u1",
        conversation_id="c1",
        workspace_root=workspace.as_posix(),
        pipeline_id=definition.id,
        definition_hash=definition.definition_hash,
        source="user",
        inputs=values,
        definition_snapshot=text or "",
    )
    clock = FakeClock()
    return Harness(
        definition=definition,
        store=store,
        sqlite=sqlite,
        workspace=workspace,
        clock=clock,
        launcher=FakeLauncher(clock, advance_on_wait=advance_on_wait),
        gates=FakeGateRunner(),
        verifier=FakeVerifier(),
        quota=FakeQuota(),
        scope=FakeChangeScope(),
        sink=RecordingSink(),
        run_id=run_id,
        inputs=values,
    )


PLAN_OUTPUT = {"tarefas": [{"titulo": "a", "arquivos": ["a.py"]}, {"titulo": "b", "arquivos": ["b.py"]}]}
