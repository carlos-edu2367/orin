from pathlib import Path

from agentos.plugins.command_library import CommandLibrary
from agentos.plugins.models import CommandContribution


def _command(plugin_id, slug):
    return CommandContribution(f"{plugin_id}:{slug}", slug, "d", "", f"commands/{slug}.md")


def test_resolves_a_bare_slug_when_it_is_unique():
    library = CommandLibrary()
    library.install_plugin_commands(
        user_id="u1", plugin_id="demo", install_path=Path("/pkg"), commands=(_command("demo", "daily"),)
    )

    resolved = library.resolve("u1", "daily")

    assert resolved is not None
    assert resolved.command_id == "demo:daily"
    assert resolved.path == Path("/pkg") / "commands" / "daily.md"


def test_an_ambiguous_bare_slug_resolves_to_nothing_but_the_qualified_form_works():
    library = CommandLibrary()
    for plugin_id in ("alpha", "beta"):
        library.install_plugin_commands(
            user_id="u1", plugin_id=plugin_id, install_path=Path("/pkg"),
            commands=(_command(plugin_id, "daily"),),
        )

    assert library.resolve("u1", "daily") is None
    assert library.resolve("u1", "alpha:daily").command_id == "alpha:daily"
    assert library.resolve("u1", "beta:daily").command_id == "beta:daily"


def test_listing_marks_the_ambiguous_commands():
    library = CommandLibrary()
    for plugin_id in ("alpha", "beta"):
        library.install_plugin_commands(
            user_id="u1", plugin_id=plugin_id, install_path=Path("/pkg"),
            commands=(_command(plugin_id, "daily"), _command(plugin_id, f"{plugin_id}-only")),
        )

    listed = {item["command_id"]: item["qualified"] for item in library.list("u1")}

    assert listed["alpha:daily"] is True
    assert listed["alpha:alpha-only"] is False


def test_removing_a_plugin_removes_only_its_commands():
    library = CommandLibrary()
    library.install_plugin_commands(user_id="u1", plugin_id="alpha", install_path=Path("/pkg"), commands=(_command("alpha", "a"),))
    library.install_plugin_commands(user_id="u1", plugin_id="beta", install_path=Path("/pkg"), commands=(_command("beta", "b"),))

    library.remove_plugin_commands(user_id="u1", plugin_id="alpha")

    assert library.resolve("u1", "a") is None
    assert library.resolve("u1", "b") is not None


def test_users_do_not_see_each_other_commands():
    library = CommandLibrary()
    library.install_plugin_commands(user_id="u1", plugin_id="demo", install_path=Path("/pkg"), commands=(_command("demo", "daily"),))

    assert library.resolve("u2", "daily") is None
