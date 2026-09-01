"""The cached half of the system prompt has to actually be stable.

Only the first system block carries cache_control. Until now that block also
carried the workspace tree, the tool ledger, the retrieved skill catalog and
the current date -- so between two turns of the same conversation it was
almost never byte-identical, and the entry it anchored (along with the tool
schemas that follow it) died with it.
"""
from __future__ import annotations

from agentos.agentic.session import build_system_prompt


def _prompt(**overrides: object):
    values: dict[str, object] = {
        "tool_names": ("read_file", "write_file"),
        "memories": [],
        "agents": [],
        "workspace_hint": "Files you create there persist for the whole conversation.",
        "subagents_enabled": False,
    }
    values.update(overrides)
    return build_system_prompt(**values)  # type: ignore[arg-type]


def test_the_prompt_comes_back_in_two_layers() -> None:
    stable, volatile = _prompt()
    assert "You are the main agent of Orin" in stable
    assert isinstance(volatile, str)


def test_a_new_file_in_the_workspace_does_not_disturb_the_cached_layer() -> None:
    before, _ = _prompt(workspace_tree=("f notes.md",))
    after, _ = _prompt(workspace_tree=("f notes.md", "f relatorio.xlsx"))
    assert before == after


def test_the_workspace_tree_lives_in_the_volatile_layer() -> None:
    _, before = _prompt(workspace_tree=("f notes.md",))
    _, after = _prompt(workspace_tree=("f notes.md", "f relatorio.xlsx"))
    assert before != after
    assert "relatorio.xlsx" in after


def test_calling_a_tool_does_not_disturb_the_cached_layer() -> None:
    ledger = ({"tool_name": "read_file", "arguments": "{}", "status": "succeeded", "summary": "lido"},)
    before, _ = _prompt()
    after, _ = _prompt(tool_ledger=ledger)
    assert before == after


def test_remembering_something_does_not_disturb_the_cached_layer() -> None:
    before, _ = _prompt()
    after, _ = _prompt(memories=[{"fact": "prefere planilhas em pt-BR"}])
    assert before == after


def test_the_current_date_lives_in_the_volatile_layer() -> None:
    stable, volatile = _prompt()
    assert "Current date" not in stable
    assert "Current date" in volatile


def test_a_retrieved_skill_does_not_disturb_the_cached_layer() -> None:
    class _Skill:
        id, name, description = "s1", "Orçamentos", "como formatar um orçamento"

    before, _ = _prompt()
    after, _ = _prompt(skill_catalog=(_Skill(),))
    assert before == after


def test_the_reference_material_stays_in_the_cached_layer() -> None:
    """Tool guidance is long and unchanging; it is exactly what should be cached."""
    stable, _ = _prompt(tool_names=("browse_page", "transcribe_pdf", "ask_user", "create_skill"))
    assert "## Browser" in stable
    assert "## PDFs" in stable
    assert "## Asking the person" in stable


def test_two_turns_that_differ_only_in_volatile_context_share_the_cached_layer() -> None:
    """The property the cache actually depends on."""
    class _Skill:
        id, name, description = "s1", "Orçamentos", "como formatar"

    first, _ = _prompt(
        workspace_tree=("f a.md",), memories=[{"fact": "x"}],
        tool_ledger=({"tool_name": "read_file", "arguments": "{}", "status": "ok", "summary": "s"},),
    )
    second, _ = _prompt(
        workspace_tree=("f a.md", "f b.md", "f c.md"), memories=[{"fact": "x"}, {"fact": "y"}],
        tool_ledger=(
            {"tool_name": "write_file", "arguments": "{}", "status": "ok", "summary": "s"},
            {"tool_name": "run_command", "arguments": "{}", "status": "ok", "summary": "s"},
        ),
        skill_catalog=(_Skill(),),
    )
    assert first == second


def test_the_volatile_layer_carries_only_what_actually_changes() -> None:
    """An empty workspace is still a fact about the workspace, so it belongs here."""
    _, volatile = _prompt()
    assert "## Workspace contents" in volatile
    assert "Current date" in volatile
    assert "You are the main agent" not in volatile


def test_the_memory_instruction_is_its_own_section_with_concrete_triggers() -> None:
    """It used to be a lone bullet appended after the Subagents block, which
    read as an instruction about subagents and never fired."""
    stable, _ = _prompt(tool_names=("remember", "recall"))

    assert "## Memória" in stable
    section = stable.split("## Memória", 1)[1]
    assert "corrige" in section
    assert "convenção" in section
