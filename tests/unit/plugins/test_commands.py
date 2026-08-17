from agentos.plugins.commands import parse_commands

REAL_COMMAND = """---
description: Create or update today's daily note
category: vault
trigger-mode: proactive
triggers_pt: ["nota de hoje", "abra a diária"]
---

Use the obsidian-second-brain skill. Execute `/obsidian-daily`:

1. Read `_CLAUDE.md` first if it exists in the vault root
"""


def test_unknown_frontmatter_keys_do_not_break_a_command(tmp_path):
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "obsidian-daily.md").write_text(REAL_COMMAND, encoding="utf-8")

    commands, warnings = parse_commands(tmp_path / "commands", plugin_id="obsidian-second-brain")

    assert warnings == ()
    assert commands[0].command_id == "obsidian-second-brain:obsidian-daily"
    assert commands[0].slug == "obsidian-daily"
    assert commands[0].description == "Create or update today's daily note"
    assert commands[0].argument_hint == ""
    assert commands[0].relative_path == "obsidian-daily.md"


def test_argument_hint_is_read_when_declared(tmp_path):
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "new.md").write_text(
        "---\ndescription: d\nargument-hint: [project-name]\n---\n\nbody", encoding="utf-8"
    )

    commands, _ = parse_commands(tmp_path / "commands", plugin_id="demo")

    assert commands[0].argument_hint == "[project-name]"


def test_a_command_without_frontmatter_is_still_valid(tmp_path):
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "bare.md").write_text("just a prompt body", encoding="utf-8")

    commands, warnings = parse_commands(tmp_path / "commands", plugin_id="demo")

    assert warnings == ()
    assert commands[0].slug == "bare" and commands[0].description == ""


def test_a_missing_directory_contributes_nothing(tmp_path):
    assert parse_commands(tmp_path / "commands", plugin_id="demo") == ((), ())


def test_commands_are_capped_at_two_hundred(tmp_path):
    (tmp_path / "commands").mkdir()
    for index in range(205):
        (tmp_path / "commands" / f"c{index:03d}.md").write_text("body", encoding="utf-8")

    commands, warnings = parse_commands(tmp_path / "commands", plugin_id="demo")

    assert len(commands) == 200
    assert any("200" in warning for warning in warnings)
