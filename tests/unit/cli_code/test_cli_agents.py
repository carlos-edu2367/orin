"""W6 Task 17: agents from --agents and from an SDK host's initialize."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_definitions import MAX_CLI_AGENTS, agents_from_mapping
from agentos.cli_code.args import parse_code_arguments
from agentos.cli_code.turn_options import build_turn_options
from agentos.code_mode.cli_options import CliTurnOptions

from .test_cli_turn_session import _session


def test_agents_are_read_like_agent_files() -> None:
    found, errors = agents_from_mapping({
        "Reviewer": {"description": "Reviews diffs", "prompt": "Review carefully.", "tools": ["Read", "Grep"], "maxTurns": 5},
        "bad name!": {"description": "x", "prompt": "y"},
        "empty": {"description": "no prompt"},
        "notmap": "x",
    })
    assert [item.name for item in found] == ["reviewer"]
    assert found[0].origin == "cli" and found[0].tools == ("Read", "Grep") and found[0].max_turns == 5
    assert len(errors) == 3
    many, problems = agents_from_mapping({f"a{n}": {"description": "d", "prompt": "p"} for n in range(MAX_CLI_AGENTS + 3)})
    assert len(many) == MAX_CLI_AGENTS and problems
    assert agents_from_mapping(None) == ([], [])


def test_the_flag_and_the_host_merge(tmp_path: Path) -> None:
    (tmp_path / "agents.json").write_text(json.dumps({"a": {"description": "from file", "prompt": "p"}}), encoding="utf-8")
    options = parse_code_arguments(["-p", "x", "--agents", "agents.json"])[0]
    built = build_turn_options(options, tmp_path, host_answers_prompts=False,
                               sdk_agents={"a": {"description": "from host", "prompt": "q"}, "b": {"description": "d", "prompt": "p"}})
    assert built.agents == {"a": {"description": "from host", "prompt": "q"}, "b": {"description": "d", "prompt": "p"}}


def test_cli_agents_join_the_turns_catalog_above_the_folders(tmp_path: Path) -> None:
    session = _session(tmp_path, CliTurnOptions(agents={"verifier": {"description": "mine", "prompt": "My checks."},
                                                        "reviewer": {"description": "r", "prompt": "Review."}}))
    catalog = session._load_agent_definitions(False)
    assert catalog.get("reviewer").origin == "cli" and catalog.get("verifier").prompt == "My checks."
    assert ("verifier", "builtin", "cli") in catalog.shadowed
