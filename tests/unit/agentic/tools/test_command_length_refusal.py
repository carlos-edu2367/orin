"""An over-long run_command is refused with one concrete step to take instead, and the model is told the limit."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace

LONG_SCRIPT = "node -e \"" + "console.log(document.querySelector('.card').getBoundingClientRect());" * 80 + "\""


def test_a_long_command_is_refused_with_the_file_to_write_and_the_command_to_run(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), enable_terminal=True)
    outcome = toolset.invoke("run_command", {"command": LONG_SCRIPT, "description": "measure"})
    assert outcome.status == "failed"
    text = outcome.content
    assert f"({len(LONG_SCRIPT)} > 4096 characters)" in text
    assert "did not run" in text
    assert "write_file" in text and ".orin/tmp/<name>.mjs" in text
    assert "`node .orin/tmp/<name>.mjs`" in text


def test_code_mode_names_the_tools_the_model_sees(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), public_tool_names=True, enable_terminal=True)
    outcome = toolset.invoke("Bash", {"command": LONG_SCRIPT, "description": "measure"})
    assert outcome.status == "failed"
    assert "Write" in outcome.content and "write_file" not in outcome.content
    assert "`node .orin/tmp/<name>.mjs`" in outcome.content


def test_the_tool_description_states_the_limit_before_the_model_hits_it(tmp_path: Path) -> None:
    for public in (False, True):
        toolset = AgentToolset(ConversationWorkspace.at_root(tmp_path), public_tool_names=public, enable_terminal=True)
        schemas = toolset.schemas()
        if toolset.name_translator is not None:
            schemas = toolset.name_translator.schemas_out(list(schemas))
        wanted = "Bash" if public else "run_command"
        description = next(
            str(item["function"]["description"]) for item in schemas if item["function"]["name"] == wanted
        )
        assert "4096 characters" in description, description
        assert ".orin/tmp/" in description
