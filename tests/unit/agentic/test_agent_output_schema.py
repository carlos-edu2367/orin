"""WS-C4: ``Agent(output_schema=...)``: the child ends with JSON that validates, corrected by resuming the same agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.session import TurnSession
from agentos.agentic.structured_output import canonical_json
from agentos.agentic.tools.agents.agent import AgentTools
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_nested_subagents import _Scripted, _TreeStore
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _text_batch

SCHEMA = {
    "type": "object",
    "required": ["tarefas"],
    "properties": {
        "tarefas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "arquivos"],
                "properties": {"id": {"type": "string"}, "arquivos": {"type": "array", "items": {"type": "string"}}},
            },
        }
    },
}
GOOD_VALUE = {"tarefas": [{"id": "t1", "arquivos": ["a.py", "b.py"]}]}
GOOD = f"Plano pronto.\n\n```json\n{json.dumps(GOOD_VALUE)}\n```"
BAD = '```json\n{"tarefas": [{"id": 1}]}\n```'


@pytest.fixture()
def agents_store(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'schema.db').as_posix()}")
    metadata.create_all(engine)
    yield ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    engine.dispose()


def _flat(request: dict[str, Any]) -> str:
    return " ".join(str(message.get("content")) for message in request["messages"])


def _session(tmp_path: Path, agents_store, child: _Scripted) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=_TreeStore(), agents_store=agents_store, memory_store=None,
        provider_factory=lambda: _Scripted([]), workspace_root=tmp_path, child_provider_factory=lambda _m: child,
    )


def test_valid_first_answer_returns_structured_payload_and_canonical_content(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch(GOOD)])
    session = _session(tmp_path, agents_store, child)
    outcome = session._run_subagent("general", "Planeje", output_schema=SCHEMA)
    assert outcome.status == "succeeded"
    assert outcome.payload["structured_output"] == GOOD_VALUE
    assert outcome.payload["structured_output_attempts"] == 1
    assert outcome.content == canonical_json(GOOD_VALUE)
    assert outcome.payload["agent_id"]
    assert len(child.requests) == 1
    first_prompt = _flat(child.requests[0])
    assert "exactly one JSON value that validates against this JSON Schema" in first_prompt
    assert '"required":["tarefas"]' in first_prompt


def test_invalid_then_valid_resumes_the_same_agent_with_the_errors_in_its_history(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch(BAD), _text_batch(GOOD)])
    session = _session(tmp_path, agents_store, child)
    outcome = session._run_subagent("general", "Planeje", output_schema=SCHEMA)
    assert outcome.status == "succeeded" and outcome.payload["structured_output"] == GOOD_VALUE
    assert outcome.payload["structured_output_attempts"] == 2
    assert len(child.requests) == 2
    second = _flat(child.requests[1])
    assert "Planeje" in second and '"id": 1' in second  # the first answer is still in the history
    assert "$.tarefas[0].arquivos" in second and "is required" in second  # and so is the correction
    rows = agents_store.list()
    assert [row["name"] for row in rows] == ["general"]  # one agent, resumed: no second child was created
    assert outcome.payload["agent_id"] == rows[0]["agent_id"]


def test_sixth_invalid_answer_fails_with_schema_invalid_and_the_last_errors(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch(BAD) for _ in range(6)])
    session = _session(tmp_path, agents_store, child)
    outcome = session._run_subagent("general", "Planeje", output_schema=SCHEMA)
    assert outcome.status == "failed" and outcome.error_code == "SCHEMA_INVALID"
    assert len(child.requests) == 6  # 1 run + 5 corrections
    assert outcome.payload["structured_output_attempts"] == 6
    assert any("$.tarefas[0].arquivos" in line for line in outcome.payload["schema_errors"])
    assert "structured_output" not in outcome.payload


def test_text_without_json_is_corrected_too(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch("Terminei, sem dados."), _text_batch(GOOD)])
    outcome = _session(tmp_path, agents_store, child)._run_subagent("general", "Planeje", output_schema=SCHEMA)
    assert outcome.status == "succeeded" and len(child.requests) == 2
    assert "did not end with a JSON value" in _flat(child.requests[1])


def test_background_child_with_schema_writes_the_canonical_json_to_report_and_registry(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch(BAD), _text_batch(GOOD)])
    session = _session(tmp_path, agents_store, child)
    started = session._run_subagent("general", "Planeje", output_schema=SCHEMA, run_in_background=True)
    assert started.status == "succeeded" and started.payload["status"] == "running"
    agent_id = str(started.payload["agent_id"])
    entry = BACKGROUND_AGENTS.get(TURN["conversation_id"], agent_id)
    outcome = entry.future.result(timeout=20)
    assert outcome.status == "succeeded" and outcome.payload["structured_output"] == GOOD_VALUE
    saved, _truncated = session.workspace.read_text(str(started.payload["output_file"]))
    assert saved.strip() == canonical_json(GOOD_VALUE)
    assert BACKGROUND_AGENTS.get(TURN["conversation_id"], agent_id).status == "completed"


def test_without_output_schema_nothing_changes(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch("Resposta livre, sem JSON.")])
    outcome = _session(tmp_path, agents_store, child)._run_subagent("general", "Planeje")
    assert outcome.status == "succeeded" and "Resposta livre, sem JSON." in outcome.content
    assert "structured_output" not in outcome.payload
    assert "JSON Schema" not in _flat(child.requests[0])


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "banana"},
        {"$ref": "https://example.com/x.json"},
        {"type": "string", "description": "x" * 17_000},
        ["not", "an", "object"],
    ],
)
def test_invalid_output_schema_is_refused_before_a_child_starts(tmp_path: Path, agents_store, bad: Any) -> None:
    child = _Scripted([])
    outcome = _session(tmp_path, agents_store, child)._run_subagent("general", "Planeje", output_schema=bad)
    assert outcome.status == "failed" and outcome.error_code == "INVALID_ARGUMENTS"
    assert child.requests == [] and agents_store.list() == []


def test_agent_tool_passes_the_schema_and_validates_it(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class _Tools(AgentTools):
        def __init__(self) -> None:
            self._run_subagent = self._fake

        def _fake(self, subagent_type: str, task: str, **kwargs: Any):
            seen.update(kwargs)
            return "ok"

    tools = _Tools()
    assert tools.run_subagent("general", "tarefa", output_schema=SCHEMA, budget={"max_tokens": 5000}) == "ok"
    assert seen["output_schema"] == SCHEMA and seen["budget"] == {"max_tokens": 5000}
    seen.clear()
    tools.run_subagent("general", "tarefa")
    assert "output_schema" not in seen and "budget" not in seen
    from agentos.agentic.tools._shared import AgentToolError

    with pytest.raises(AgentToolError):
        tools.run_subagent("general", "tarefa", output_schema={"type": "banana"})
    with pytest.raises(AgentToolError):
        tools.run_subagent("general", "tarefa", budget={"max_tokens": -1})
    with pytest.raises(AgentToolError):
        tools.run_subagent("general", "tarefa", budget={"tokens": 10})
