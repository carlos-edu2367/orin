"""W6-R5: `--tools default` is Orin's normal tool set, and a name Orin does not publish is a usage error."""
from __future__ import annotations

import pytest

from agentos.agentic.cli_turn import tool_selection
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.cli_code.args import CliUsageError, parse_code_arguments
from agentos.code_mode.cli_options import CliTurnOptions


@pytest.mark.parametrize("tools", [["default"], ["default", "Bash"], ["Bash", "default", "Read"]])
def test_default_means_orins_normal_set(tools: list[str]) -> None:
    assert tool_selection(CliTurnOptions(tools=tools), CODE_MODE_CORE_TOOLS) == (None, frozenset())


def test_an_empty_tools_value_still_withholds_every_core_tool() -> None:
    offered, withheld = tool_selection(CliTurnOptions(tools=[]), CODE_MODE_CORE_TOOLS)
    assert offered == frozenset()
    assert withheld == frozenset(CODE_MODE_CORE_TOOLS)


def test_default_is_accepted_on_the_command_line() -> None:
    options, _ = parse_code_arguments(["-p", "x", "--tools", "default"])
    assert options.tools == ["default"]


@pytest.mark.parametrize(("value", "unknown"), [
    ("Bassh", "Bassh"), ("Read,Bassh", "Bassh"), ("Bash(ls:*)", "Bash(ls:*)"), ("defaults", "defaults"),
], ids=["misspelled", "among-known", "a-rule-not-a-name", "near-default"])
def test_an_unknown_tool_name_is_a_usage_error_that_names_it(value: str, unknown: str) -> None:
    with pytest.raises(CliUsageError) as refused:
        parse_code_arguments(["-p", "x", "--tools", value])
    assert unknown in str(refused.value)


def test_published_and_mcp_names_are_accepted() -> None:
    options, _ = parse_code_arguments(["-p", "x", "--tools", "Read,Edit,Bash,WebFetch,mcp__calc__add"])
    assert options.tools == ["Read", "Edit", "Bash", "WebFetch", "mcp__calc__add"]


